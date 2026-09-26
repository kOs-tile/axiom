"""
Tests for the AXIOM Capability Resolver.

Tests:
- Composite scoring formula (semantic * 0.6 + success * 0.3 + recency * 0.1)
- Recency decay function
- Top-K trimming
- Confidence threshold filtering
- Tag intersection filtering (via mock skill_store)
- ResolveResponse structure
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from axiom.models import (
    IOSchema,
    ResolveRequest,
    ResolvedSkill,
    Skill,
    SkillCategory,
    SkillStatus,
)
from axiom.resolver.capability_resolver import (
    CapabilityResolver,
    _compute_score,
    _recency_score,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_skill(
    name: str,
    success_rate: float = 0.9,
    tags: list[str] | None = None,
    last_invoked_days_ago: int | None = 0,
) -> Skill:
    last_invoked = None
    if last_invoked_days_ago is not None:
        last_invoked = datetime.now(timezone.utc) - timedelta(days=last_invoked_days_ago)
    return Skill(
        name=name,
        description=f"Test skill: {name}",
        tags=tags or [],
        category=SkillCategory.UTILITY,
        status=SkillStatus.ACTIVE,
        success_rate=success_rate,
        success_count=int(success_rate * 10),
        failure_count=int((1 - success_rate) * 10),
        invocation_count=10,
        last_invoked_at=last_invoked,
    )


# ── Unit Tests: Recency Score ─────────────────────────────────────────────────

class TestRecencyScore:
    def test_invoked_today_is_near_one(self):
        score = _recency_score(datetime.now(timezone.utc))
        assert score > 0.95

    def test_never_invoked_returns_small_score(self):
        score = _recency_score(None)
        assert 0 < score <= 0.2

    def test_14_days_ago_is_half_decay(self):
        """Half-life is 14 days, so 14 days ago → ~0.5."""
        score = _recency_score(datetime.now(timezone.utc) - timedelta(days=14))
        assert 0.45 < score < 0.55

    def test_very_old_skill_approaches_zero(self):
        score = _recency_score(datetime.now(timezone.utc) - timedelta(days=365))
        assert score < 0.01

    def test_naive_datetime_handled(self):
        """Naive datetimes (no tzinfo) should not raise."""
        naive_dt = datetime.utcnow() - timedelta(days=7)
        score = _recency_score(naive_dt)
        assert 0 < score < 1.0


# ── Unit Tests: Composite Score ───────────────────────────────────────────────

class TestCompositeScore:
    def test_perfect_skill_scores_near_one(self):
        score = _compute_score(
            semantic_similarity=1.0,
            success_rate=1.0,
            last_invoked_at=datetime.now(timezone.utc),
        )
        assert score > 0.95

    def test_weights_sum_to_at_most_one(self):
        """With max inputs, score should not exceed 1.0."""
        score = _compute_score(
            semantic_similarity=1.0,
            success_rate=1.0,
            last_invoked_at=datetime.now(timezone.utc),
            w_semantic=0.6,
            w_success=0.3,
            w_recency=0.1,
        )
        assert score <= 1.01  # slight float tolerance

    def test_low_success_rate_penalises_score(self):
        high_score = _compute_score(1.0, 1.0, datetime.now(timezone.utc))
        low_score = _compute_score(1.0, 0.1, datetime.now(timezone.utc))
        assert high_score > low_score

    def test_semantic_similarity_dominates(self):
        """Semantic weight (0.6) is largest; high sim should beat low sim despite other factors."""
        high_sim = _compute_score(0.9, 0.5, None)
        low_sim = _compute_score(0.1, 1.0, datetime.now(timezone.utc))
        assert high_sim > low_sim

    def test_custom_weights(self):
        score = _compute_score(
            semantic_similarity=0.8,
            success_rate=0.9,
            last_invoked_at=None,
            w_semantic=0.5,
            w_success=0.4,
            w_recency=0.1,
        )
        # Verify custom weights are applied
        expected_min = 0.5 * 0.8 + 0.4 * 0.9  # + small recency
        assert score >= expected_min


# ── Unit Tests: Resolver ──────────────────────────────────────────────────────

@pytest.fixture
def resolver() -> CapabilityResolver:
    return CapabilityResolver()


class TestCapabilityResolver:
    @pytest.mark.asyncio
    async def test_resolve_returns_candidates_sorted_by_confidence(self, resolver):
        """Results should be sorted descending by confidence."""
        skills_with_sims = [
            (make_skill("low_sim", success_rate=0.95, last_invoked_days_ago=0), 0.5),
            (make_skill("high_sim", success_rate=0.95, last_invoked_days_ago=0), 0.9),
            (make_skill("mid_sim", success_rate=0.95, last_invoked_days_ago=0), 0.7),
        ]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            request = ResolveRequest(task_description="any task", top_k=5)
            response = await resolver.resolve(request)

        confs = [r.confidence for r in response.candidates]
        assert confs == sorted(confs, reverse=True)

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self, resolver):
        """Should never return more than top_k results."""
        skills_with_sims = [
            (make_skill(f"skill_{i}", last_invoked_days_ago=0), 0.8 - i * 0.05)
            for i in range(10)
        ]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            response = await resolver.resolve(
                ResolveRequest(task_description="test task", top_k=3)
            )

        assert len(response.candidates) <= 3

    @pytest.mark.asyncio
    async def test_confidence_threshold_filters_low_scores(self, resolver):
        """Candidates below min_confidence should be excluded."""
        skills_with_sims = [
            (make_skill("high", last_invoked_days_ago=0, success_rate=0.95), 0.9),
            (make_skill("low", last_invoked_days_ago=0, success_rate=0.4), 0.1),
        ]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            response = await resolver.resolve(
                ResolveRequest(
                    task_description="test task",
                    top_k=10,
                    min_confidence=0.5,
                )
            )

        # The low-confidence skill should be filtered
        names = [r.skill.name for r in response.candidates]
        assert "high" in names
        assert "low" not in names

    @pytest.mark.asyncio
    async def test_ranks_are_1_based_sequential(self, resolver):
        skills_with_sims = [
            (make_skill(f"s{i}", last_invoked_days_ago=0), 0.9 - i * 0.1)
            for i in range(5)
        ]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            response = await resolver.resolve(
                ResolveRequest(task_description="test task", top_k=5)
            )

        ranks = [r.rank for r in response.candidates]
        assert ranks == list(range(1, len(ranks) + 1))

    @pytest.mark.asyncio
    async def test_empty_registry_returns_empty_candidates(self, resolver):
        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = await resolver.resolve(
                ResolveRequest(task_description="anything")
            )

        assert response.candidates == []
        assert response.total_searched == 0

    @pytest.mark.asyncio
    async def test_resolve_response_metadata(self, resolver):
        """Response should include task description, total_searched, and timing."""
        skills_with_sims = [(make_skill("s1", last_invoked_days_ago=1), 0.8)]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            response = await resolver.resolve(
                ResolveRequest(task_description="compute something useful")
            )

        assert response.task_description == "compute something useful"
        assert response.total_searched == 1
        assert response.resolved_in_ms >= 0

    @pytest.mark.asyncio
    async def test_resolve_simple_returns_tuples(self, resolver):
        """resolve_simple() should return (Skill, confidence) tuples."""
        skills_with_sims = [(make_skill("simple_skill", last_invoked_days_ago=0), 0.85)]

        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=skills_with_sims,
        ):
            results = await resolver.resolve_simple("any task", top_k=5)

        assert len(results) == 1
        skill, confidence = results[0]
        assert isinstance(skill, Skill)
        assert 0 <= confidence <= 1.0


# ── Resolve with Tag Filtering ────────────────────────────────────────────────

class TestTagFiltering:
    @pytest.mark.asyncio
    async def test_tag_filter_passed_to_semantic_search(self, resolver):
        """filter_tags should be forwarded to skill_store.semantic_search."""
        with patch(
            "axiom.resolver.capability_resolver.skill_store.semantic_search",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            await resolver.resolve(
                ResolveRequest(
                    task_description="task with tags",
                    filter_tags=["trading", "crypto"],
                )
            )

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs.get("filter_tags") == ["trading", "crypto"]
