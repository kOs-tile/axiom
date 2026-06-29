"""
AXIOM Hermes Skill Adapter

Intercepts Hermes skill lookups and resolves them through AXIOM first,
falling back to local skill files. Auto-ingests new local skills found
into the AXIOM registry.

Drop-in usage with Hermes:

    from axiom.sdk.hermes_adapter import HermesSkillAdapter

    # Replace Hermes' default skill loader with the AXIOM adapter
    adapter = HermesSkillAdapter(axiom_url="http://localhost:8000")
    hermes.skill_loader = adapter.load

    # All hermes.run() calls will now route through AXIOM first
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from axiom.config import settings
from axiom.models import IOSchema, Skill, SkillCategory, SkillStatus
from axiom.sdk.client import AxiomClient

# Cache of locally discovered + ingested skill names
_ingested_local_skills: set[str] = set()


class HermesSkillAdapter:
    """
    Bridges the Hermes skill loader interface with AXIOM.

    Resolution order:
    1. Query AXIOM registry (semantic search + ranking)
    2. If no match → scan local skill directory for a matching .py file
    3. If local skill found → auto-ingest into AXIOM → return skill
    4. If nothing found → trigger AXIOM synthesis
    """

    def __init__(
        self,
        axiom_url: str = "http://localhost:8000",
        skill_dir: Optional[str] = None,
        auto_ingest: bool = True,
        api_key: Optional[str] = None,
    ) -> None:
        self._axiom_url = axiom_url
        self._skill_dir = Path(skill_dir or settings.hermes_skill_dir)
        self._auto_ingest = auto_ingest
        self._client = AxiomClient(base_url=axiom_url, api_key=api_key)

    async def load(self, task_description: str) -> Optional[Skill]:
        """
        Main Hermes hook. Given a task description, return the best available Skill.
        """
        logger.debug(f"HermesAdapter: resolving '{task_description[:60]}'")

        # 1. Try AXIOM registry
        axiom_skill = await self._resolve_from_axiom(task_description)
        if axiom_skill:
            return axiom_skill

        # 2. Scan local skill files
        local_skill = await self._discover_local_skill(task_description)
        if local_skill:
            # Auto-ingest into AXIOM
            if self._auto_ingest:
                await self._ingest_skill(local_skill)
            return local_skill

        # 3. Trigger synthesis as last resort
        logger.info(f"HermesAdapter: triggering synthesis for '{task_description[:60]}'")
        result = await self._client.synthesize(task_description)
        if result.status == "success" and result.skill:
            return result.skill
        if result.status == "duplicate" and result.duplicate_of:
            return result.duplicate_of

        logger.warning(f"HermesAdapter: could not resolve '{task_description[:60]}'")
        return None

    async def load_by_name(self, skill_name: str) -> Optional[Skill]:
        """Load a skill by exact name (Hermes direct-name lookup)."""
        skills = await self._client.list_skills(limit=200)
        for s in skills:
            if s.name.lower() == skill_name.lower():
                return s

        # Try local file
        local_path = self._skill_dir / f"{skill_name}.py"
        if local_path.exists():
            return await self._load_local_file(local_path)

        return None

    async def ingest_directory(self) -> list[Skill]:
        """
        Scan the Hermes skill directory and ingest all discovered .py skills.
        Returns list of ingested Skills.
        """
        if not self._skill_dir.is_dir():
            logger.warning(f"Skill directory not found: {self._skill_dir}")
            return []

        ingested: list[Skill] = []
        for path in self._skill_dir.glob("*.py"):
            if path.stem.startswith("_"):
                continue
            try:
                skill = await self._load_local_file(path)
                if skill:
                    await self._ingest_skill(skill)
                    ingested.append(skill)
            except Exception as exc:
                logger.warning(f"Failed to ingest {path.name}: {exc}")

        logger.info(f"Ingested {len(ingested)} local skills from {self._skill_dir}")
        return ingested

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _resolve_from_axiom(self, task_description: str) -> Optional[Skill]:
        try:
            candidates = await self._client.resolve(
                task_description, top_k=1, min_confidence=0.5
            )
            if candidates:
                return candidates[0].skill
        except Exception as exc:
            logger.debug(f"AXIOM resolve failed: {exc}")
        return None

    async def _discover_local_skill(self, task_description: str) -> Optional[Skill]:
        """
        Keyword-match task description against local .py filenames and docstrings.
        """
        if not self._skill_dir.is_dir():
            return None

        task_words = set(task_description.lower().split())
        best_match: Optional[tuple[Path, int]] = None

        for path in self._skill_dir.glob("*.py"):
            if path.stem.startswith("_"):
                continue
            file_words = set(path.stem.replace("_", " ").lower().split())
            overlap = len(task_words & file_words)
            if overlap > 0:
                if best_match is None or overlap > best_match[1]:
                    best_match = (path, overlap)

        if best_match:
            return await self._load_local_file(best_match[0])
        return None

    async def _load_local_file(self, path: Path) -> Optional[Skill]:
        """
        Load a local .py skill file and extract its metadata.

        Expects files to either:
        a) Define a `SKILL_METADATA` dict at module level, OR
        b) Define a `run` async function with a docstring
        """
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[path.stem] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]

            # Try SKILL_METADATA first
            if hasattr(module, "SKILL_METADATA"):
                meta = module.SKILL_METADATA
                return Skill(
                    name=meta.get("name", path.stem),
                    description=meta.get("description", ""),
                    tags=meta.get("tags", []),
                    category=SkillCategory(meta.get("category", "utility")),
                    implementation=path.read_text(),
                    status=SkillStatus.ACTIVE,
                    author="hermes-local",
                )

            # Fall back to introspecting the `run` function
            run_fn: Optional[Callable[..., Any]] = getattr(module, "run", None)
            if run_fn and callable(run_fn):
                doc = inspect.getdoc(run_fn) or path.stem.replace("_", " ")
                return Skill(
                    name=path.stem,
                    description=doc[:500],
                    tags=[path.stem],
                    implementation=path.read_text(),
                    status=SkillStatus.ACTIVE,
                    author="hermes-local",
                )

        except Exception as exc:
            logger.warning(f"Failed to load local skill {path}: {exc}")

        return None

    async def _ingest_skill(self, skill: Skill) -> None:
        """Register a locally discovered skill into AXIOM."""
        if skill.name in _ingested_local_skills:
            return
        try:
            await self._client.register_skill(skill)
            _ingested_local_skills.add(skill.name)
            logger.info(f"Auto-ingested local skill '{skill.name}' into AXIOM")
        except Exception as exc:
            logger.warning(f"Failed to ingest skill '{skill.name}': {exc}")
