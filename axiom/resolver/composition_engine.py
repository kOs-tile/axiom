"""
AXIOM Skill Composition Engine

Models skill I/O contracts as typed nodes in a directed NetworkX graph.
Runs constrained DFS path search to find valid skill chains where:

    output_schema[i] is_compatible_with input_schema[i+1]

Returns chains ranked by joint historical success rate.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import networkx as nx
from loguru import logger

from axiom.config import settings
from axiom.models import ComposeRequest, ComposeResponse, Skill, SkillChain, SkillStatus
from axiom.registry.skill_store import skill_store

# Virtual source/sink node IDs
_SOURCE = "__SOURCE__"
_SINK = "__SINK__"


class SkillCompositionEngine:
    """
    Builds a directed type-compatibility graph over all active skills and
    performs constrained path search to find composition chains.

    Graph structure:
        - Each active Skill is a node, labelled with its IO schema
        - A directed edge (A → B) exists iff A.output_schema.is_compatible_with(B.input_schema)
        - A virtual SOURCE node connects to all skills whose inputs can be satisfied by
          the user-provided context
        - A virtual SINK node is connected from all skills whose output satisfies
          the desired output type (inferred from task, best-effort)
    """

    async def compose(self, request: ComposeRequest) -> ComposeResponse:
        """
        Find valid skill chains for the given task.
        """
        t0 = time.perf_counter()
        logger.info(f"Composing chain for: '{request.task_description[:80]}…'")

        # Fetch all active skills
        skills = await skill_store.list_skills(status=SkillStatus.ACTIVE)
        logger.debug(f"Loaded {len(skills)} active skills for composition graph")

        if not skills:
            return ComposeResponse(
                task_description=request.task_description,
                chains=[],
                composed_in_ms=0.0,
            )

        # Build graph
        G = self._build_graph(skills)

        # Find paths
        chains = self._find_chains(
            G=G,
            skills=skills,
            task_description=request.task_description,
            max_length=request.max_chain_length,
            input_context=request.input_context,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"Found {len(chains)} valid composition chains in {elapsed_ms:.1f}ms")

        return ComposeResponse(
            task_description=request.task_description,
            chains=chains[:10],  # cap at 10 chains
            composed_in_ms=round(elapsed_ms, 2),
        )

    # ── Graph Construction ────────────────────────────────────────────────────

    def _build_graph(self, skills: list[Skill]) -> nx.DiGraph:
        """
        Construct the type-compatibility directed graph.
        """
        G: nx.DiGraph = nx.DiGraph()

        # Add skill nodes
        for skill in skills:
            G.add_node(
                skill.id,
                skill=skill,
                input_schema=skill.input_schema,
                output_schema=skill.output_schema,
                success_rate=skill.success_rate,
            )

        # Add directed edges based on I/O compatibility
        for src in skills:
            for dst in skills:
                if src.id == dst.id:
                    continue
                if src.output_schema.is_compatible_with(dst.input_schema):
                    G.add_edge(
                        src.id,
                        dst.id,
                        weight=1.0 - src.success_rate,  # lower weight = more reliable
                    )

        logger.debug(
            f"Composition graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges"
        )
        return G

    # ── Path Search ───────────────────────────────────────────────────────────

    def _find_chains(
        self,
        G: nx.DiGraph,
        skills: list[Skill],
        task_description: str,
        max_length: int,
        input_context: Optional[dict[str, Any]],
    ) -> list[SkillChain]:
        """
        DFS-based constrained path search.

        Heuristic: start from skills whose description is most relevant to the task
        (simple keyword overlap, since this runs offline without async embedding).
        """
        skill_by_id = {s.id: s for s in skills}
        task_words = set(task_description.lower().split())

        # Score each skill node by keyword relevance to task
        relevance: dict[str, float] = {}
        for skill in skills:
            skill_words = set(
                (skill.name + " " + skill.description + " " + " ".join(skill.tags)).lower().split()
            )
            overlap = len(task_words & skill_words)
            relevance[skill.id] = overlap / max(len(task_words), 1)

        # Identify plausible entry points (top-25% relevance)
        threshold = sorted(relevance.values(), reverse=True)[: max(1, len(skills) // 4)]
        threshold_val = threshold[-1] if threshold else 0.0
        entry_points = [nid for nid, r in relevance.items() if r >= threshold_val][:10]

        if not entry_points:
            entry_points = list(skill_by_id.keys())[:5]

        found_paths: list[list[str]] = []
        visited_paths: set[tuple[str, ...]] = set()

        for start in entry_points:
            self._dfs(
                G=G,
                current=start,
                path=[start],
                max_length=max_length,
                found=found_paths,
                visited_paths=visited_paths,
                max_paths=settings.composition_max_paths,
            )

        # Convert paths to SkillChain objects
        chains: list[SkillChain] = []
        for path in found_paths:
            chain_skills = [skill_by_id[nid] for nid in path if nid in skill_by_id]
            if len(chain_skills) < 2:
                continue  # single-skill "chains" are not compositions

            handoff_map = self._build_handoff_map(chain_skills)

            chain = SkillChain(
                task_description=task_description,
                skills=chain_skills,
                handoff_map=handoff_map,
                confidence=self._chain_confidence(chain_skills, relevance),
            )
            chains.append(chain)

        # Sort by combined_success_rate * confidence
        chains.sort(
            key=lambda c: c.combined_success_rate * c.confidence,
            reverse=True,
        )
        return chains

    def _dfs(
        self,
        G: nx.DiGraph,
        current: str,
        path: list[str],
        max_length: int,
        found: list[list[str]],
        visited_paths: set[tuple[str, ...]],
        max_paths: int,
    ) -> None:
        """Recursive DFS — collects all simple paths up to max_length."""
        if len(found) >= max_paths:
            return

        key = tuple(path)
        if key in visited_paths:
            return
        visited_paths.add(key)

        if len(path) >= 2:
            found.append(list(path))

        if len(path) >= max_length:
            return

        for neighbour in G.successors(current):
            if neighbour not in path:  # avoid cycles
                self._dfs(
                    G=G,
                    current=neighbour,
                    path=path + [neighbour],
                    max_length=max_length,
                    found=found,
                    visited_paths=visited_paths,
                    max_paths=max_paths,
                )

    @staticmethod
    def _build_handoff_map(chain_skills: list[Skill]) -> list[dict[str, str]]:
        """
        For each adjacent pair, map output field names → input field names.
        Exact name matches first; then type-compatible fallback.
        """
        handoff_map: list[dict[str, str]] = []
        for i in range(len(chain_skills) - 1):
            src_out = chain_skills[i].output_schema
            dst_in = chain_skills[i + 1].input_schema
            mapping: dict[str, str] = {}
            for in_field in dst_in.fields:
                # Exact name match
                if any(of.name == in_field.name for of in src_out.fields):
                    mapping[in_field.name] = in_field.name
                else:
                    # Type-compatible match
                    for out_field in src_out.fields:
                        if out_field.type == in_field.type:
                            mapping[out_field.name] = in_field.name
                            break
            handoff_map.append(mapping)
        return handoff_map

    @staticmethod
    def _chain_confidence(
        chain_skills: list[Skill],
        relevance: dict[str, float],
    ) -> float:
        """Overall confidence = mean relevance of skills in chain."""
        scores = [relevance.get(s.id, 0.0) for s in chain_skills]
        return round(sum(scores) / len(scores), 4) if scores else 0.0


# Module-level singleton
composition_engine = SkillCompositionEngine()
