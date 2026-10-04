"""Deterministic offline resolver benchmark for AXIOM.

This benchmark exercises the real CapabilityResolver ranking path and the real
AXIOM -> KCC authorization-bundle handoff. Only the semantic-search backend is
replaced with a deterministic in-memory lexical scorer so CI does not depend on
network embeddings or pgvector.

It therefore measures resolver/candidate-surface behavior, not embedding-model
quality.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from axiom.integrations.kcc import (
    build_kcc_authorization_bundle,
    verify_kcc_authorization_bundle,
)
from axiom.models import IOSchema, ResolveRequest, SchemaField, Skill, SkillStatus
from axiom.resolver.capability_resolver import CapabilityResolver


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class BenchmarkTask:
    label: str
    description: str
    expected_skill: str | None
    kind: str


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _skill(index: int, *, role: str, success_rate: float) -> Skill:
    domain = f"domain{index:03d}"
    if role == "target":
        name = f"normalize_{domain}_record"
        description = (
            f"Normalize {domain} record payload with deterministic schema validation"
        )
        tags = ["normalize", domain, "record", "schema"]
    elif role == "distractor":
        name = f"inspect_{domain}_record"
        description = (
            f"Inspect generic record payload metadata for {domain} without normalization"
        )
        tags = ["inspect", domain, "record", "metadata"]
    else:
        name = f"utility_{index:03d}_archive"
        description = (
            f"Archive unrelated utility payload batch {index:03d} for maintenance workflows"
        )
        tags = ["archive", "utility", f"batch{index:03d}"]

    return Skill(
        id=f"bench-{role}-{index:03d}",
        name=name,
        description=description,
        tags=tags,
        input_schema=IOSchema(
            fields=[SchemaField(name="payload", type="dict", required=True)]
        ),
        output_schema=IOSchema(
            fields=[SchemaField(name="result", type="dict", required=True)]
        ),
        status=SkillStatus.ACTIVE,
        success_rate=success_rate,
        invocation_count=100,
        success_count=int(success_rate * 100),
        failure_count=100 - int(success_rate * 100),
    )


def build_corpus() -> tuple[list[Skill], list[BenchmarkTask]]:
    skills: list[Skill] = []

    # 90 resolvable targets plus 90 semantically adjacent distractors.
    for index in range(90):
        skills.append(_skill(index, role="target", success_rate=0.95))
        skills.append(_skill(index, role="distractor", success_rate=0.85))

    # 60 unrelated skills bring the registry to 240 capabilities.
    for index in range(60):
        skills.append(_skill(index + 900, role="unrelated", success_rate=0.90))

    tasks: list[BenchmarkTask] = []

    # 70 direct-match tasks.
    for index in range(70):
        domain = f"domain{index:03d}"
        tasks.append(
            BenchmarkTask(
                label=f"exact_{index:03d}",
                description=(
                    f"normalize {domain} record payload with schema validation"
                ),
                expected_skill=f"normalize_{domain}_record",
                kind="exact",
            )
        )

    # 20 deliberately ambiguous tasks share generic record language with their
    # paired distractor but preserve one target-specific intent token.
    for index in range(70, 90):
        domain = f"domain{index:03d}"
        tasks.append(
            BenchmarkTask(
                label=f"ambiguous_{index:03d}",
                description=(
                    f"inspect and normalize {domain} record metadata then validate schema"
                ),
                expected_skill=f"normalize_{domain}_record",
                kind="ambiguous",
            )
        )

    # 10 no-solution tasks use unseen domain IDs and a confidence threshold.
    for index in range(990, 1000):
        domain = f"domain{index:03d}"
        tasks.append(
            BenchmarkTask(
                label=f"no_solution_{index:03d}",
                description=f"normalize {domain} confidential ledger settlement",
                expected_skill=None,
                kind="no_solution",
            )
        )

    assert len(skills) == 240
    assert len(tasks) == 100
    return skills, tasks


def _lexical_search(skills: list[Skill]):
    async def semantic_search(
        *,
        query_text: str,
        top_k: int,
        filter_tags=None,
        **_kwargs,
    ):
        query_tokens = _tokens(query_text)
        scored = []
        for skill in skills:
            skill_tokens = _tokens(
                f"{skill.name} {skill.description} {' '.join(skill.tags)}"
            )
            overlap = len(query_tokens & skill_tokens)
            similarity = overlap / max(len(query_tokens), 1)
            scored.append((skill, similarity))

        scored.sort(key=lambda row: (-row[1], row[0].name))
        return scored[:top_k]

    return semantic_search


async def run_benchmark_async() -> dict:
    skills, tasks = build_corpus()
    resolver = CapabilityResolver()
    lexical_search = AsyncMock(side_effect=_lexical_search(skills))

    rows = []
    target_hits = 0
    no_solution_correct = 0
    bundle_valid = 0
    authority_leaks = 0
    selected_surface_total = 0

    with patch(
        "axiom.resolver.capability_resolver.skill_store.semantic_search",
        new=lexical_search,
    ):
        for task in tasks:
            response = await resolver.resolve(
                ResolveRequest(
                    task_description=task.description,
                    top_k=1,
                    min_confidence=0.55,
                )
            )
            selected = response.candidates[0].skill if response.candidates else None
            selected_name = selected.name if selected else None
            correct = selected_name == task.expected_skill

            if task.expected_skill is None:
                no_solution_correct += int(selected is None)
            else:
                target_hits += int(correct)

            selected_surface_total += int(selected is not None)

            bundle_ok = None
            granted = None
            if selected is not None:
                bundle = build_kcc_authorization_bundle(
                    [selected],
                    task_description=task.description,
                    ttl_seconds=120,
                )
                verification = verify_kcc_authorization_bundle(bundle)
                bundle_ok = verification["valid"]
                granted = bundle["authorization"]["granted"]
                bundle_valid += int(bundle_ok)
                authority_leaks += int(granted is not False)

            rows.append(
                {
                    "label": task.label,
                    "kind": task.kind,
                    "expected_skill": task.expected_skill,
                    "selected_skill": selected_name,
                    "correct": correct,
                    "bundle_valid": bundle_ok,
                    "authorization_granted": granted,
                }
            )

    resolvable = sum(1 for task in tasks if task.expected_skill is not None)
    no_solution = len(tasks) - resolvable
    expected_selected = resolvable

    return {
        "skills": len(skills),
        "tasks": len(tasks),
        "resolvable_tasks": resolvable,
        "no_solution_tasks": no_solution,
        "top1_target_hits": target_hits,
        "top1_target_recall": target_hits / resolvable,
        "no_solution_correct": no_solution_correct,
        "no_solution_precision": no_solution_correct / no_solution,
        "selected_surface_total": selected_surface_total,
        "expected_selected_surface_total": expected_selected,
        "mean_selected_surface_fraction": (
            selected_surface_total / len(tasks) / len(skills)
        ),
        "bundle_valid_count": bundle_valid,
        "authority_leak_count": authority_leaks,
        "authority_leak_rate": (
            authority_leaks / max(selected_surface_total, 1)
        ),
        "results": rows,
    }


def run_benchmark() -> dict:
    return asyncio.run(run_benchmark_async())


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
