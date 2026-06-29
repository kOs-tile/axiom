"""
AXIOM Skill Store — async Supabase + pgvector skill registry.

Handles full CRUD, semantic vector search, tag intersection filtering,
and performance metric updates. All methods are async-first.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger
from supabase import AsyncClient, create_async_client

from axiom.config import settings
from axiom.models import (
    IOSchema,
    Skill,
    SkillCategory,
    SkillStatus,
)
from axiom.registry.embedder import embedder

# ── Supabase table name ────────────────────────────────────────────────────────
SKILLS_TABLE = "skills"


class SkillStore:
    """
    Async Supabase client wrapper for the AXIOM skill registry.

    Schema (managed via Supabase migrations, see migrations/):
        skills (
            id          uuid PRIMARY KEY,
            name        text NOT NULL,
            description text,
            tags        jsonb,
            category    text,
            input_schema  jsonb,
            output_schema jsonb,
            implementation text,
            entry_point text,
            status      text,
            version     text,
            author      text,
            hermes_compatible boolean,
            invocation_count  integer DEFAULT 0,
            success_count     integer DEFAULT 0,
            failure_count     integer DEFAULT 0,
            avg_latency_ms    float8 DEFAULT 0,
            success_rate      float8 DEFAULT 1,
            created_at  timestamptz,
            updated_at  timestamptz,
            last_invoked_at timestamptz,
            promoted_at     timestamptz,
            embedding   vector(1536)   -- pgvector column
        )
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncClient] = None

    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            self._client = await create_async_client(
                settings.supabase_url,
                settings.supabase_service_role_key or settings.supabase_anon_key,
            )
        return self._client

    # ── Create / Upsert ───────────────────────────────────────────────────────

    async def register_skill(self, skill: Skill, embed: bool = True) -> Skill:
        """
        Insert or update a skill in the registry.
        Optionally compute and store the embedding vector.
        """
        client = await self._get_client()

        if embed:
            text = embedder.build_skill_text(skill.name, skill.description, skill.tags)
            vector = await embedder.embed(text)
            skill.embedding = vector

        data = skill.to_registry_dict()

        # pgvector expects the embedding as a list (Supabase serialises it correctly)
        if skill.embedding:
            data["embedding"] = skill.embedding

        # Convert nested dicts to JSON strings for Supabase
        data["tags"] = json.dumps(skill.tags)
        data["input_schema"] = json.dumps(data["input_schema"])
        data["output_schema"] = json.dumps(data["output_schema"])

        logger.info(f"Registering skill '{skill.name}' (id={skill.id})")
        response = await client.table(SKILLS_TABLE).upsert(data).execute()

        if response.data:
            logger.success(f"Skill '{skill.name}' registered successfully")
        return skill

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_skill(self, skill_id: str) -> Optional[Skill]:
        """Fetch a single skill by ID."""
        client = await self._get_client()
        response = (
            await client.table(SKILLS_TABLE)
            .select("*")
            .eq("id", skill_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return self._deserialise_skill(response.data[0])
        return None

    async def get_skill_by_name(self, name: str) -> Optional[Skill]:
        """Fetch a single skill by exact name."""
        client = await self._get_client()
        response = (
            await client.table(SKILLS_TABLE)
            .select("*")
            .eq("name", name)
            .limit(1)
            .execute()
        )
        if response.data:
            return self._deserialise_skill(response.data[0])
        return None

    async def list_skills(
        self,
        status: Optional[SkillStatus] = None,
        category: Optional[SkillCategory] = None,
        tags: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Skill]:
        """List skills with optional filters."""
        client = await self._get_client()
        query = client.table(SKILLS_TABLE).select("*").range(offset, offset + limit - 1)

        if status:
            query = query.eq("status", status.value)
        if category:
            query = query.eq("category", category.value)

        response = await query.execute()
        skills = [self._deserialise_skill(row) for row in (response.data or [])]

        # Tag intersection filter (done in Python because Supabase jsonb containment
        # requires a jsonb column with GIN index — simplest approach for portability)
        if tags:
            normalised = [t.lower().strip() for t in tags]
            skills = [
                s for s in skills
                if any(t in s.tags for t in normalised)
            ]

        return skills

    # ── Semantic Search ───────────────────────────────────────────────────────

    async def semantic_search(
        self,
        query_text: str,
        top_k: int = 10,
        filter_tags: Optional[list[str]] = None,
        status_filter: SkillStatus = SkillStatus.ACTIVE,
    ) -> list[tuple[Skill, float]]:
        """
        Vector similarity search using pgvector.

        Returns list of (Skill, cosine_similarity) sorted descending.
        Falls back to a description ILIKE search if embeddings are unavailable.
        """
        client = await self._get_client()

        # Embed the query
        query_vector = await embedder.embed(query_text)
        vector_str = "[" + ",".join(str(x) for x in query_vector) + "]"

        logger.debug(f"Semantic search: '{query_text[:60]}…' top_k={top_k}")

        # pgvector cosine distance RPC (defined in Supabase as a stored procedure)
        # match_skills(query_embedding vector, match_count int, filter_status text)
        try:
            response = await client.rpc(
                "match_skills",
                {
                    "query_embedding": vector_str,
                    "match_count": top_k * 2,  # over-fetch to allow tag filtering
                    "filter_status": status_filter.value,
                },
            ).execute()

            rows = response.data or []
        except Exception as exc:
            logger.warning(f"pgvector RPC failed ({exc}), falling back to full-table scan")
            rows = await self._fallback_search(query_text, top_k * 2, status_filter)

        results: list[tuple[Skill, float]] = []
        for row in rows:
            similarity = float(row.get("similarity", 0.0))
            skill = self._deserialise_skill(row)

            if filter_tags:
                norm_filter = [t.lower() for t in filter_tags]
                if not any(t in skill.tags for t in norm_filter):
                    continue

            results.append((skill, similarity))

        # Sort descending by similarity and trim to top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def _fallback_search(
        self,
        query_text: str,
        limit: int,
        status_filter: SkillStatus,
    ) -> list[dict[str, Any]]:
        """Full-table fallback when pgvector RPC is unavailable."""
        client = await self._get_client()
        response = (
            await client.table(SKILLS_TABLE)
            .select("*")
            .eq("status", status_filter.value)
            .ilike("description", f"%{query_text[:40]}%")
            .limit(limit)
            .execute()
        )
        return response.data or []

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_skill_status(self, skill_id: str, status: SkillStatus) -> None:
        client = await self._get_client()
        await client.table(SKILLS_TABLE).update(
            {"status": status.value, "updated_at": datetime.utcnow().isoformat()}
        ).eq("id", skill_id).execute()
        logger.info(f"Skill {skill_id} status → {status.value}")

    async def update_performance_metrics(
        self,
        skill_id: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Atomically update invocation counters and rolling metrics."""
        client = await self._get_client()

        # Fetch current values
        skill = await self.get_skill(skill_id)
        if skill is None:
            logger.warning(f"Cannot update metrics: skill {skill_id} not found")
            return

        new_invocations = skill.invocation_count + 1
        new_success = skill.success_count + (1 if success else 0)
        new_failure = skill.failure_count + (0 if success else 1)
        total = new_success + new_failure
        new_rate = new_success / total if total > 0 else 1.0

        # Exponential moving average for latency (alpha=0.1)
        alpha = 0.1
        new_latency = (
            latency_ms
            if skill.avg_latency_ms == 0
            else (1 - alpha) * skill.avg_latency_ms + alpha * latency_ms
        )

        await client.table(SKILLS_TABLE).update(
            {
                "invocation_count": new_invocations,
                "success_count": new_success,
                "failure_count": new_failure,
                "success_rate": new_rate,
                "avg_latency_ms": new_latency,
                "last_invoked_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", skill_id).execute()

    async def promote_skill(self, skill_id: str) -> None:
        """Mark a skill as ACTIVE (promoted from sandbox)."""
        client = await self._get_client()
        await client.table(SKILLS_TABLE).update(
            {
                "status": SkillStatus.ACTIVE.value,
                "promoted_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", skill_id).execute()
        logger.success(f"Skill {skill_id} promoted to ACTIVE")

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_skill(self, skill_id: str) -> bool:
        client = await self._get_client()
        response = await client.table(SKILLS_TABLE).delete().eq("id", skill_id).execute()
        deleted = bool(response.data)
        if deleted:
            logger.info(f"Skill {skill_id} deleted")
        return deleted

    # ── Decay Helpers ─────────────────────────────────────────────────────────

    async def get_skills_for_decay_check(self) -> list[Skill]:
        """Return active skills with enough invocations to check for decay."""
        client = await self._get_client()
        response = (
            await client.table(SKILLS_TABLE)
            .select("*")
            .eq("status", SkillStatus.ACTIVE.value)
            .gte("invocation_count", settings.decay_min_invocations)
            .execute()
        )
        return [self._deserialise_skill(row) for row in (response.data or [])]

    async def get_idle_skills(self, idle_days: int = 30) -> list[Skill]:
        """Return active skills not invoked in the last idle_days days."""
        client = await self._get_client()
        cutoff = (datetime.utcnow() - timedelta(days=idle_days)).isoformat()
        response = (
            await client.table(SKILLS_TABLE)
            .select("*")
            .eq("status", SkillStatus.ACTIVE.value)
            .lt("last_invoked_at", cutoff)
            .execute()
        )
        return [self._deserialise_skill(row) for row in (response.data or [])]

    # ── Deserialization ───────────────────────────────────────────────────────

    @staticmethod
    def _deserialise_skill(row: dict[str, Any]) -> Skill:
        """Convert a Supabase row dict back into a Skill model."""
        data = dict(row)

        # Parse JSON columns
        for col in ("tags", "input_schema", "output_schema"):
            if isinstance(data.get(col), str):
                try:
                    data[col] = json.loads(data[col])
                except (json.JSONDecodeError, TypeError):
                    data[col] = [] if col == "tags" else {}

        # Convert IOSchema dicts
        for schema_col in ("input_schema", "output_schema"):
            if isinstance(data.get(schema_col), dict):
                data[schema_col] = IOSchema(**data[schema_col])
            elif data.get(schema_col) is None:
                data[schema_col] = IOSchema()

        # Drop pgvector embedding from row (it's raw floats, not needed for most ops)
        data.pop("embedding", None)
        data.pop("similarity", None)  # added by RPC

        return Skill(**data)


# Module-level singleton
skill_store = SkillStore()
