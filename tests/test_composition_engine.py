"""
Tests for the AXIOM Skill Composition Engine.

Uses mock skill registry to test:
- Graph construction from skills
- Type-compatibility edge detection
- DFS path search
- Handoff map generation
- Chain confidence scoring
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from axiom.models import (
    IOSchema,
    SchemaField,
    Skill,
    SkillCategory,
    SkillStatus,
)
from axiom.resolver.composition_engine import SkillCompositionEngine


# ── Test fixtures ──────────────────────────────────────────────────────────────

def make_skill(
    name: str,
    input_fields: list[tuple[str, str]],
    output_fields: list[tuple[str, str]],
    success_rate: float = 0.9,
    tags: list[str] | None = None,
) -> Skill:
    """Helper to create a Skill with typed I/O fields."""
    return Skill(
        name=name,
        description=f"Test skill: {name}",
        tags=tags or [name],
        category=SkillCategory.UTILITY,
        input_schema=IOSchema(
            fields=[SchemaField(name=n, type=t, description="") for n, t in input_fields]
        ),
        output_schema=IOSchema(
            fields=[SchemaField(name=n, type=t, description="") for n, t in output_fields]
        ),
        implementation=f"def run(**kwargs): return {{}}",
        status=SkillStatus.ACTIVE,
        success_count=int(success_rate * 10),
        failure_count=int((1 - success_rate) * 10),
        invocation_count=10,
        last_invoked_at=datetime.utcnow(),
    )


@pytest.fixture
def engine() -> SkillCompositionEngine:
    return SkillCompositionEngine()


@pytest.fixture
def skill_chain_trio():
    """Three skills that form a valid A → B → C chain."""
    skill_a = make_skill(
        "fetch_prices",
        input_fields=[("symbol", "str")],
        output_fields=[("prices", "list[float]"), ("symbol", "str")],
        success_rate=0.95,
        tags=["fetch", "prices", "trading"],
    )
    skill_b = make_skill(
        "compute_sma",
        input_fields=[("prices", "list[float]")],
        output_fields=[("sma", "float"), ("window", "int")],
        success_rate=0.98,
        tags=["compute", "sma", "moving_average"],
    )
    skill_c = make_skill(
        "format_result",
        input_fields=[("sma", "float")],
        output_fields=[("markdown", "str")],
        success_rate=0.99,
        tags=["format", "markdown", "output"],
    )
    return [skill_a, skill_b, skill_c]


@pytest.fixture
def incompatible_skills():
    """Skills with non-overlapping I/O types that cannot be chained."""
    skill_x = make_skill(
        "string_producer",
        input_fields=[("text", "str")],
        output_fields=[("result", "str")],
    )
    skill_y = make_skill(
        "number_consumer",
        input_fields=[("value", "int")],
        output_fields=[("squared", "int")],
    )
    return [skill_x, skill_y]


# ── Graph Construction Tests ──────────────────────────────────────────────────

class TestGraphConstruction:
    def test_builds_graph_with_correct_node_count(self, engine, skill_chain_trio):
        G = engine._build_graph(skill_chain_trio)
        assert G.number_of_nodes() == 3

    def test_compatible_skills_have_edges(self, engine, skill_chain_trio):
        """fetch_prices → compute_sma should have an edge (prices: list[float])."""
        G = engine._build_graph(skill_chain_trio)
        fetch_id = skill_chain_trio[0].id
        compute_id = skill_chain_trio[1].id
        assert G.has_edge(fetch_id, compute_id), "fetch_prices → compute_sma edge missing"

    def test_incompatible_skills_have_no_edges(self, engine, incompatible_skills):
        """str output cannot connect to int input."""
        G = engine._build_graph(incompatible_skills)
        ids = [s.id for s in incompatible_skills]
        assert not G.has_edge(ids[0], ids[1]), "Incompatible skills should not have an edge"
        assert not G.has_edge(ids[1], ids[0])

    def test_no_self_edges(self, engine, skill_chain_trio):
        G = engine._build_graph(skill_chain_trio)
        for skill in skill_chain_trio:
            assert not G.has_edge(skill.id, skill.id)

    def test_edge_weight_reflects_unreliability(self, engine, skill_chain_trio):
        """Edge weight = 1.0 - success_rate (lower is better path)."""
        G = engine._build_graph(skill_chain_trio)
        fetch_id = skill_chain_trio[0].id
        compute_id = skill_chain_trio[1].id
        edge_data = G.get_edge_data(fetch_id, compute_id)
        expected_weight = 1.0 - skill_chain_trio[0].success_rate
        assert abs(edge_data["weight"] - expected_weight) < 0.01


# ── Path Search Tests ─────────────────────────────────────────────────────────

class TestPathSearch:
    def test_finds_valid_chain(self, engine, skill_chain_trio):
        G = engine._build_graph(skill_chain_trio)
        skill_by_id = {s.id: s for s in skill_chain_trio}
        relevance = {s.id: 0.8 for s in skill_chain_trio}

        chains = engine._find_chains(
            G=G,
            skills=skill_chain_trio,
            task_description="fetch prices and compute moving average then format",
            max_length=5,
            input_context=None,
        )
        assert len(chains) > 0, "Should find at least one valid chain"

    def test_chain_length_respects_max(self, engine, skill_chain_trio):
        chains = engine._find_chains(
            G=engine._build_graph(skill_chain_trio),
            skills=skill_chain_trio,
            task_description="any task",
            max_length=2,
            input_context=None,
        )
        for chain in chains:
            assert len(chain.skills) <= 2

    def test_no_chains_for_incompatible_skills(self, engine, incompatible_skills):
        chains = engine._find_chains(
            G=engine._build_graph(incompatible_skills),
            skills=incompatible_skills,
            task_description="compute something",
            max_length=5,
            input_context=None,
        )
        assert chains == [], "Incompatible skills should produce no chains"

    def test_chains_sorted_by_composite_score(self, engine, skill_chain_trio):
        chains = engine._find_chains(
            G=engine._build_graph(skill_chain_trio),
            skills=skill_chain_trio,
            task_description="trading moving average analysis",
            max_length=5,
            input_context=None,
        )
        if len(chains) >= 2:
            # First chain should have higher or equal composite score
            score0 = chains[0].combined_success_rate * chains[0].confidence
            score1 = chains[1].combined_success_rate * chains[1].confidence
            assert score0 >= score1


# ── Handoff Map Tests ─────────────────────────────────────────────────────────

class TestHandoffMap:
    def test_exact_name_mapping(self):
        """Output field 'prices' maps to input field 'prices' by exact name."""
        skill_a = make_skill(
            "skill_a",
            input_fields=[("x", "float")],
            output_fields=[("prices", "list[float]")],
        )
        skill_b = make_skill(
            "skill_b",
            input_fields=[("prices", "list[float]")],
            output_fields=[("result", "float")],
        )
        handoff = SkillCompositionEngine._build_handoff_map([skill_a, skill_b])
        assert len(handoff) == 1
        assert handoff[0].get("prices") == "prices", "Exact name mapping should match 'prices' → 'prices'"

    def test_empty_handoff_for_single_skill(self):
        skill = make_skill("solo", [("x", "str")], [("y", "str")])
        handoff = SkillCompositionEngine._build_handoff_map([skill])
        assert handoff == []


# ── Async Integration Test ────────────────────────────────────────────────────

class TestComposeAsync:
    @pytest.mark.asyncio
    async def test_compose_returns_response(self, engine, skill_chain_trio):
        """End-to-end compose() using mock skill_store."""
        from axiom.models import ComposeRequest

        with patch(
            "axiom.resolver.composition_engine.skill_store.list_skills",
            new_callable=AsyncMock,
            return_value=skill_chain_trio,
        ):
            request = ComposeRequest(
                task_description="fetch prices compute moving average format output",
                max_chain_length=5,
            )
            response = await engine.compose(request)

        assert response.task_description == request.task_description
        assert response.composed_in_ms >= 0

    @pytest.mark.asyncio
    async def test_compose_empty_registry_returns_empty_chains(self, engine):
        from axiom.models import ComposeRequest

        with patch(
            "axiom.resolver.composition_engine.skill_store.list_skills",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = await engine.compose(
                ComposeRequest(task_description="some task")
            )
        assert response.chains == []


# ── IOSchema Compatibility Tests ──────────────────────────────────────────────

class TestIOSchemaCompatibility:
    def test_identical_schemas_compatible(self):
        schema = IOSchema(fields=[SchemaField(name="x", type="float")])
        assert schema.is_compatible_with(schema)

    def test_missing_required_field_incompatible(self):
        out_schema = IOSchema(fields=[SchemaField(name="y", type="str")])
        in_schema = IOSchema(fields=[SchemaField(name="x", type="float", required=True)])
        assert not out_schema.is_compatible_with(in_schema)

    def test_optional_missing_field_compatible(self):
        out_schema = IOSchema(fields=[SchemaField(name="y", type="str")])
        in_schema = IOSchema(fields=[SchemaField(name="x", type="float", required=False)])
        assert out_schema.is_compatible_with(in_schema)

    def test_any_type_always_compatible(self):
        out_schema = IOSchema(fields=[SchemaField(name="data", type="str")])
        in_schema = IOSchema(fields=[SchemaField(name="data", type="Any", required=True)])
        assert out_schema.is_compatible_with(in_schema)
