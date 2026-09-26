"""
Tests for the AXIOM Skill Synthesizer.

Mocks DeepSeek API to test:
- Prompt construction
- Output parsing
- Step sequencing
- Duplicate detection integration
- Schema parsing
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from axiom.models import (
    EvaluationReport,
    Skill,
    SkillCategory,
    SkillStatus,
    SynthesisRequest,
    SynthesisStep,
)
from axiom.synthesis.synthesizer import SkillSynthesizer


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_mock_analysis() -> dict:
    return {
        "skill_name": "compute_bollinger_bands",
        "description": "Computes Bollinger Bands for a price series.",
        "tags": ["trading", "bollinger", "statistics"],
        "category": "analysis",
        "required_inputs": ["prices (list[float])", "window (int)"],
        "expected_outputs": ["upper_band (float)", "lower_band (float)", "middle_band (float)"],
        "complexity": "moderate",
        "external_dependencies": [],
    }


def make_mock_schema() -> dict:
    return {
        "input_schema": {
            "description": "Price series and window",
            "fields": [
                {"name": "prices", "type": "list[float]", "description": "Closing prices", "required": True},
                {"name": "window", "type": "int", "description": "Band window", "required": False, "default": 20},
            ],
        },
        "output_schema": {
            "description": "Bollinger Band values",
            "fields": [
                {"name": "upper_band", "type": "float", "description": "Upper band", "required": True},
                {"name": "middle_band", "type": "float", "description": "Middle SMA", "required": True},
                {"name": "lower_band", "type": "float", "description": "Lower band", "required": True},
            ],
        },
    }


MOCK_IMPLEMENTATION = """
def run(prices: list, window: int = 20) -> dict:
    import math
    n = min(window, len(prices))
    if not prices or n < 2:
        return {"upper_band": 0.0, "middle_band": 0.0, "lower_band": 0.0}
    recent = prices[-n:]
    mean = sum(recent) / len(recent)
    variance = sum((p - mean) ** 2 for p in recent) / len(recent)
    std = math.sqrt(variance)
    return {
        "upper_band": round(mean + 2 * std, 4),
        "middle_band": round(mean, 4),
        "lower_band": round(mean - 2 * std, 4),
    }
""".strip()


MOCK_TEST_CASES_JSON = json.dumps([
    {
        "name": "basic_bollinger",
        "input_data": {"prices": [100.0, 102.0, 98.0, 101.0, 103.0, 99.0, 100.5], "window": 5},
        "expected_type": "dict",
    },
    {
        "name": "empty_prices",
        "input_data": {"prices": [], "window": 20},
        "expected_type": "dict",
    },
    {
        "name": "single_price",
        "input_data": {"prices": [100.0], "window": 20},
        "expected_type": "dict",
    },
])


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def synthesizer() -> SkillSynthesizer:
    return SkillSynthesizer()


@pytest.fixture
def mock_synthesis_request() -> SynthesisRequest:
    return SynthesisRequest(
        task_description="Compute Bollinger Bands for a list of closing prices",
        preferred_tags=["trading"],
        requester="test-harness",
    )


# ── Unit Tests: Prompt Construction ──────────────────────────────────────────

class TestPromptConstruction:
    @pytest.mark.asyncio
    async def test_analyze_task_calls_llm(self, synthesizer):
        """_analyze_task should call the DeepSeek API and return a dict."""
        with patch.object(
            synthesizer,
            "_chat",
            new_callable=AsyncMock,
            return_value=json.dumps(make_mock_analysis()),
        ) as mock_chat:
            result = await synthesizer._analyze_task("Compute Bollinger Bands")

        mock_chat.assert_called_once()
        call_args = mock_chat.call_args[0]
        messages = call_args[0]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "Bollinger Bands" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_analyze_task_returns_dict(self, synthesizer):
        with patch.object(synthesizer, "_chat", new_callable=AsyncMock,
                          return_value=json.dumps(make_mock_analysis())):
            result = await synthesizer._analyze_task("any task")

        assert isinstance(result, dict)
        assert "skill_name" in result

    @pytest.mark.asyncio
    async def test_analyze_task_handles_invalid_json(self, synthesizer):
        """Should return a fallback dict when LLM returns non-JSON."""
        with patch.object(synthesizer, "_chat", new_callable=AsyncMock,
                          return_value="Not JSON at all"):
            result = await synthesizer._analyze_task("some task description")

        assert isinstance(result, dict)
        assert "skill_name" in result  # fallback populates skill_name

    @pytest.mark.asyncio
    async def test_generate_schema_includes_input_and_output(self, synthesizer):
        with patch.object(synthesizer, "_chat", new_callable=AsyncMock,
                          return_value=json.dumps(make_mock_schema())):
            result = await synthesizer._generate_schema(
                analysis=make_mock_analysis(),
                examples=[],
                task_description="Compute Bollinger Bands",
            )

        assert "input_schema" in result
        assert "output_schema" in result
        assert len(result["input_schema"]["fields"]) > 0

    @pytest.mark.asyncio
    async def test_generate_implementation_strips_markdown_fences(self, synthesizer):
        fenced = f"```python\n{MOCK_IMPLEMENTATION}\n```"
        with patch.object(synthesizer, "_chat", new_callable=AsyncMock,
                          return_value=fenced):
            result = await synthesizer._generate_implementation(
                analysis=make_mock_analysis(),
                schema=make_mock_schema(),
                examples=[],
                task_description="Bollinger Bands",
            )

        assert not result.startswith("```")
        assert "def run" in result

    @pytest.mark.asyncio
    async def test_generate_tests_returns_test_cases(self, synthesizer):
        with patch.object(synthesizer, "_chat", new_callable=AsyncMock,
                          return_value=MOCK_TEST_CASES_JSON):
            result = await synthesizer._generate_tests(
                analysis=make_mock_analysis(),
                implementation=MOCK_IMPLEMENTATION,
                schema=make_mock_schema(),
            )

        assert len(result) == 3
        assert result[0].name == "basic_bollinger"
        assert result[1].input_data == {"prices": [], "window": 20}


# ── Unit Tests: Schema Parsing ────────────────────────────────────────────────

class TestSchemaParsing:
    def test_parse_schema_with_fields(self, synthesizer):
        raw = {
            "description": "Test schema",
            "fields": [
                {"name": "x", "type": "float", "description": "input x", "required": True},
                {"name": "y", "type": "str", "description": "input y", "required": False},
            ],
        }
        schema = synthesizer._parse_schema(raw)
        assert len(schema.fields) == 2
        assert schema.fields[0].name == "x"
        assert schema.fields[1].type == "str"

    def test_parse_empty_schema(self, synthesizer):
        schema = synthesizer._parse_schema({})
        assert schema.fields == []

    def test_parse_schema_ignores_unknown_fields(self, synthesizer):
        raw = {
            "fields": [
                {"name": "x", "type": "float", "description": "ok", "required": True, "EXTRA_KEY": "ignored"}
            ]
        }
        schema = synthesizer._parse_schema(raw)
        assert len(schema.fields) == 1


# ── Integration: Synthesis Pipeline Step Sequencing ──────────────────────────

class TestSynthesisPipeline:
    @pytest.mark.asyncio
    async def test_synthesis_yields_expected_steps(
        self, synthesizer, mock_synthesis_request
    ):
        """Mock all external calls and verify the pipeline emits steps in order."""
        mock_skill = Skill(
            name="compute_bollinger_bands",
            description="Test synthesized skill",
            tags=["trading"],
            status=SkillStatus.SANDBOX_PENDING,
            implementation=MOCK_IMPLEMENTATION,
        )
        mock_report = EvaluationReport(
            skill_id=mock_skill.id,
            skill_name=mock_skill.name,
            promotion_recommended=True,
            failure_reason=None,
        )

        with (
            patch.object(synthesizer, "_analyze_task", new_callable=AsyncMock,
                         return_value=make_mock_analysis()),
            patch.object(synthesizer, "_retrieve_examples", new_callable=AsyncMock,
                         return_value=[]),
            patch.object(synthesizer, "_generate_schema", new_callable=AsyncMock,
                         return_value=make_mock_schema()),
            patch.object(synthesizer, "_generate_implementation", new_callable=AsyncMock,
                         return_value=MOCK_IMPLEMENTATION),
            patch.object(synthesizer, "_generate_tests", new_callable=AsyncMock,
                         return_value=[]),
            patch("axiom.synthesis.synthesizer.deduplication_checker.check",
                  new_callable=AsyncMock, return_value=(False, None)),
            patch("axiom.synthesis.synthesizer.skill_store.register_skill",
                  new_callable=AsyncMock, return_value=mock_skill),
            patch("axiom.synthesis.synthesizer.skill_store.promote_skill",
                  new_callable=AsyncMock) as mock_promote,
            patch("axiom.synthesis.synthesizer.skill_store.update_skill_status",
                  new_callable=AsyncMock) as mock_update_status,
            patch("axiom.sandbox.evaluator.sandbox_evaluator.evaluate",
                  new_callable=AsyncMock, return_value=mock_report),
        ):
            steps_seen: list[SynthesisStep] = []
            events = []
            async for event in synthesizer.synthesize(mock_synthesis_request):
                events.append(event)
                steps_seen.append(event.step)

        assert SynthesisStep.ANALYZING in steps_seen
        assert SynthesisStep.RETRIEVING_EXAMPLES in steps_seen
        assert SynthesisStep.CHECKING_DUPLICATES in steps_seen
        assert SynthesisStep.GENERATING_SCHEMA in steps_seen
        assert SynthesisStep.GENERATING_IMPLEMENTATION in steps_seen
        assert SynthesisStep.COMPLETE in steps_seen
        assert SynthesisStep.AWAITING_AUTHORIZATION in steps_seen
        result = events[-1].detail["result"]
        assert result["skill"]["status"] == SkillStatus.READY_FOR_AUTHORIZATION.value
        mock_promote.assert_not_awaited()
        staged_skill_id = mock_update_status.await_args.args[0]
        mock_update_status.assert_awaited_with(
            staged_skill_id, SkillStatus.READY_FOR_AUTHORIZATION
        )

    @pytest.mark.asyncio
    async def test_duplicate_detection_short_circuits_synthesis(
        self, synthesizer, mock_synthesis_request
    ):
        """When a duplicate is detected, synthesis stops early."""
        existing_skill = Skill(
            name="existing_bollinger",
            description="Existing Bollinger Bands skill",
            tags=["trading"],
            status=SkillStatus.ACTIVE,
        )

        with (
            patch.object(synthesizer, "_analyze_task", new_callable=AsyncMock,
                         return_value=make_mock_analysis()),
            patch.object(synthesizer, "_retrieve_examples", new_callable=AsyncMock,
                         return_value=[]),
            patch("axiom.synthesis.synthesizer.deduplication_checker.check",
                  new_callable=AsyncMock, return_value=(True, existing_skill)),
        ):
            events = []
            async for event in synthesizer.synthesize(mock_synthesis_request):
                events.append(event)

        # Last event should be COMPLETE with duplicate status
        last = events[-1]
        assert last.step == SynthesisStep.COMPLETE
        assert "duplicate" in last.message.lower()

        # Should NOT have called generate_implementation
        impl_steps = [e for e in events if e.step == SynthesisStep.GENERATING_IMPLEMENTATION]
        assert len(impl_steps) == 0

    @pytest.mark.asyncio
    async def test_force_synthesize_skips_dedup(
        self, synthesizer
    ):
        """force_synthesize=True should skip deduplication."""
        request = SynthesisRequest(
            task_description="Compute Bollinger Bands",
            force_synthesize=True,  # Skip dedup
        )

        mock_skill = Skill(
            name="forced_bollinger",
            description="Force-synthesized skill",
            tags=[],
            status=SkillStatus.SANDBOX_PENDING,
        )
        mock_report = MagicMock()
        mock_report.promotion_recommended = False
        mock_report.failure_reason = "test"
        mock_report.model_dump.return_value = {}

        with (
            patch.object(synthesizer, "_analyze_task", new_callable=AsyncMock,
                         return_value=make_mock_analysis()),
            patch.object(synthesizer, "_retrieve_examples", new_callable=AsyncMock,
                         return_value=[]),
            patch.object(synthesizer, "_generate_schema", new_callable=AsyncMock,
                         return_value=make_mock_schema()),
            patch.object(synthesizer, "_generate_implementation", new_callable=AsyncMock,
                         return_value=MOCK_IMPLEMENTATION),
            patch.object(synthesizer, "_generate_tests", new_callable=AsyncMock,
                         return_value=[]),
            patch("axiom.synthesis.synthesizer.skill_store.register_skill",
                  new_callable=AsyncMock, return_value=mock_skill),
            patch("axiom.synthesis.synthesizer.skill_store.update_skill_status",
                  new_callable=AsyncMock),
            patch("axiom.sandbox.evaluator.sandbox_evaluator.evaluate",
                  new_callable=AsyncMock, return_value=mock_report),
        ):
            steps_seen = []
            async for event in synthesizer.synthesize(request):
                steps_seen.append(event.step)

        # Dedup check step should still be present (it's yielded before the check)
        # but since force=True, it should proceed to GENERATING_SCHEMA
        assert SynthesisStep.GENERATING_SCHEMA in steps_seen
