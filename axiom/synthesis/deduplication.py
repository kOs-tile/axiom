"""
AXIOM Deduplication — prevents re-synthesising semantically equivalent skills.

Compares a proposed skill description against all active skills using cosine
similarity on OpenAI embeddings. If the closest match exceeds the configured
threshold, synthesis is aborted and the existing skill is returned.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from axiom.config import settings
from axiom.models import Skill, SkillStatus
from axiom.registry.embedder import embedder
from axiom.registry.skill_store import skill_store


class DeduplicationChecker:
    """
    Checks whether a proposed new skill is semantically equivalent to an
    existing registered skill.

    Uses embedding cosine similarity with a configurable threshold.
    """

    async def check(
        self,
        proposed_name: str,
        proposed_description: str,
        proposed_tags: list[str],
    ) -> tuple[bool, Optional[Skill]]:
        """
        Returns (is_duplicate: bool, closest_match: Skill | None).

        A skill is considered a duplicate if the cosine similarity between
        its description embedding and the proposed description embedding
        exceeds `settings.dedup_cosine_threshold`.
        """
        logger.debug(
            f"Dedup check for proposed skill '{proposed_name}' "
            f"(threshold={settings.dedup_cosine_threshold})"
        )

        # Get embedding for proposed description
        proposed_text = embedder.build_skill_text(
            proposed_name, proposed_description, proposed_tags
        )
        proposed_vector = await embedder.embed(proposed_text)

        # Fetch all active skills
        active_skills = await skill_store.list_skills(status=SkillStatus.ACTIVE, limit=500)

        if not active_skills:
            logger.debug("No active skills in registry — no duplicates possible")
            return False, None

        # Embed all active skills in batch (uses cache for previously embedded)
        skill_texts = [
            embedder.build_skill_text(s.name, s.description, s.tags)
            for s in active_skills
        ]
        skill_vectors = await embedder.embed_batch(skill_texts)

        # Find the closest skill
        best_similarity = -1.0
        best_skill: Optional[Skill] = None

        for skill, vector in zip(active_skills, skill_vectors):
            similarity = embedder.cosine_similarity(proposed_vector, vector)
            if similarity > best_similarity:
                best_similarity = similarity
                best_skill = skill

        logger.debug(
            f"Closest match: '{best_skill.name if best_skill else 'none'}' "
            f"similarity={best_similarity:.4f}"
        )

        is_dup = best_similarity >= settings.dedup_cosine_threshold
        if is_dup:
            logger.info(
                f"Duplicate detected: proposed '{proposed_name}' ~ "
                f"existing '{best_skill.name}' (sim={best_similarity:.4f})"
            )
        return is_dup, best_skill if is_dup else None

    async def find_similar(
        self,
        description: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[tuple[Skill, float]]:
        """
        Return the top-K most similar existing skills above a threshold.
        Used by the synthesizer to pull few-shot examples.
        """
        results = await skill_store.semantic_search(
            query_text=description,
            top_k=top_k,
            status_filter=SkillStatus.ACTIVE,
        )
        return [(skill, sim) for skill, sim in results if sim >= threshold]


# Module-level singleton
deduplication_checker = DeduplicationChecker()
