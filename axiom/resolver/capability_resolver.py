"""
AXIOM Capability Resolver

Takes a natural language task description and returns a ranked list of existing skills
most likely to satisfy it.

Ranking formula:
    score = semantic_similarity * 0.6
           + success_rate       * 0.3
           + recency_score      * 0.1

recency_score decays exponentially with days since last invocation (half-life = 14 days).
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from axiom.config import settings
from axiom.models import ResolveRequest, ResolveResponse, ResolvedSkill, Skill
from axiom.registry.skill_store import skill_store

_RECENCY_HALF_LIFE_DAYS = 14.0


def _recency_score(last_invoked_at: Optional[datetime]) -> float:
    """Exponential decay score in [0, 1]. 1.0 = invoked today, decays by ~50% every 14 days."""
    if last_invoked_at is None:
        return 0.1  # Never invoked → small but non-zero signal
    now = datetime.now(timezone.utc)
    # Make timezone-aware if needed
    if last_invoked_at.tzinfo is None:
        last_invoked_at = last_invoked_at.replace(tzinfo=timezone.utc)
    days_ago = (now - last_invoked_at).total_seconds() / 86400
    return math.exp(-days_ago * math.log(2) / _RECENCY_HALF_LIFE_DAYS)


def _compute_score(
    semantic_similarity: float,
    success_rate: float,
    last_invoked_at: Optional[datetime],
    w_semantic: float = settings.resolver_semantic_weight,
    w_success: float = settings.resolver_success_weight,
    w_recency: float = settings.resolver_recency_weight,
) -> float:
    """Weighted composite ranking score in [0, 1]."""
    rec = _recency_score(last_invoked_at)
    return w_semantic * semantic_similarity + w_success * success_rate + w_recency * rec


class CapabilityResolver:
    """
    Resolves a task description to ranked, existing AXIOM skills.

    Workflow:
    1. Embed task description.
    2. Vector search for top-K semantically similar active skills.
    3. Re-rank results using composite score.
    4. Filter by confidence threshold.
    5. Return ranked ResolvedSkill list.
    """

    async def resolve(self, request: ResolveRequest) -> ResolveResponse:
        """
        Main entry point. Returns ranked candidates for the given task.
        """
        t0 = time.perf_counter()
        logger.info(f"Resolving task: '{request.task_description[:80]}…'")

        # Over-fetch from vector search then re-rank
        raw_results = await skill_store.semantic_search(
            query_text=request.task_description,
            top_k=settings.resolver_top_k,
            filter_tags=request.filter_tags if request.filter_tags else None,
        )

        logger.debug(f"Vector search returned {len(raw_results)} candidates")

        ranked: list[ResolvedSkill] = []
        for rank_idx, (skill, semantic_sim) in enumerate(raw_results):
            score = _compute_score(
                semantic_similarity=semantic_sim,
                success_rate=skill.success_rate,
                last_invoked_at=skill.last_invoked_at,
            )

            if score < request.min_confidence:
                continue

            ranked.append(
                ResolvedSkill(
                    skill=skill,
                    confidence=round(score, 4),
                    semantic_similarity=round(semantic_sim, 4),
                    rank=rank_idx + 1,
                )
            )

        # Sort by confidence descending
        ranked.sort(key=lambda r: r.confidence, reverse=True)

        # Re-assign ranks after sort
        for i, r in enumerate(ranked):
            r.rank = i + 1

        # Trim to requested top_k
        top_candidates = ranked[: request.top_k]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            f"Resolved {len(top_candidates)} candidates in {elapsed_ms:.1f}ms "
            f"(searched {len(raw_results)} total)"
        )

        return ResolveResponse(
            task_description=request.task_description,
            candidates=top_candidates,
            total_searched=len(raw_results),
            resolved_in_ms=round(elapsed_ms, 2),
        )

    async def resolve_simple(
        self,
        task_description: str,
        top_k: int = 5,
        filter_tags: Optional[list[str]] = None,
    ) -> list[tuple[Skill, float]]:
        """
        Convenience wrapper returning (Skill, confidence) pairs.
        Used internally by the composition engine and synthesizer.
        """
        request = ResolveRequest(
            task_description=task_description,
            top_k=top_k,
            filter_tags=filter_tags or [],
        )
        response = await self.resolve(request)
        return [(r.skill, r.confidence) for r in response.candidates]


# Module-level singleton
capability_resolver = CapabilityResolver()
