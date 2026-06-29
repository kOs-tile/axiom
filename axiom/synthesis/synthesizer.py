"""
AXIOM Skill Synthesizer

Multi-step DeepSeek-V3 agent that generates new skill implementations.

Pipeline:
    Step 1 — Analyze task requirements (structured output)
    Step 2 — Retrieve 3 similar existing skills as few-shot examples
    Step 3 — Check for semantic duplicates
    Step 4 — Generate input/output schema
    Step 5 — Generate implementation
    Step 6 — Generate unit tests
    Step 7 — Sandbox security scan + execution
    Step 8 — Promote to registry if pass rate meets threshold

Streams progress events via an async generator consumed by the WebSocket handler.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Optional

from loguru import logger
from openai import AsyncOpenAI

from axiom.config import settings
from axiom.models import (
    EvaluationReport,
    IOSchema,
    SchemaField,
    Skill,
    SkillCategory,
    SkillStatus,
    SynthesisProgressEvent,
    SynthesisRequest,
    SynthesisResult,
    SynthesisStep,
    TestCase,
)
from axiom.registry.skill_store import skill_store
from axiom.synthesis.deduplication import deduplication_checker


class SkillSynthesizer:
    """
    Orchestrates multi-step skill synthesis using DeepSeek-V3.
    """

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        return self._client

    # ── Main Entry Point ──────────────────────────────────────────────────────

    async def synthesize(
        self,
        request: SynthesisRequest,
    ) -> AsyncGenerator[SynthesisProgressEvent, None]:
        """
        Async generator that yields SynthesisProgressEvents as synthesis proceeds.
        The final event carries a SynthesisResult in its detail field.
        """
        t0 = time.perf_counter()
        request_id = str(uuid.uuid4())
        steps_completed: list[SynthesisStep] = []

        try:
            # ── Step 1: Analyze task ──────────────────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.ANALYZING,
                message="Analyzing task requirements…",
                progress_pct=10,
            )
            analysis = await self._analyze_task(request.task_description)
            steps_completed.append(SynthesisStep.ANALYZING)
            logger.debug(f"Task analysis complete: {analysis}")

            # ── Step 2: Retrieve few-shot examples ────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.RETRIEVING_EXAMPLES,
                message=f"Retrieving {settings.synthesis_few_shot_k} similar skills as examples…",
                progress_pct=20,
                detail={"analysis": analysis},
            )
            examples = await self._retrieve_examples(request.task_description)
            steps_completed.append(SynthesisStep.RETRIEVING_EXAMPLES)

            # ── Step 3: Deduplication check ───────────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.CHECKING_DUPLICATES,
                message="Checking for semantic duplicates…",
                progress_pct=30,
                detail={"examples_found": len(examples)},
            )

            if not request.force_synthesize:
                proposed_name = analysis.get("skill_name", request.task_description[:40])
                is_dup, closest = await deduplication_checker.check(
                    proposed_name=proposed_name,
                    proposed_description=analysis.get("description", request.task_description),
                    proposed_tags=analysis.get("tags", []),
                )
                if is_dup and closest:
                    yield SynthesisProgressEvent(
                        step=SynthesisStep.COMPLETE,
                        message=f"Duplicate detected — existing skill '{closest.name}' satisfies this request.",
                        progress_pct=100,
                        detail={
                            "result": SynthesisResult(
                                request_id=request_id,
                                status="duplicate",
                                duplicate_of=closest,
                                steps_completed=steps_completed,
                                synthesis_duration_seconds=time.perf_counter() - t0,
                            ).model_dump()
                        },
                    )
                    return

            steps_completed.append(SynthesisStep.CHECKING_DUPLICATES)

            # ── Step 4: Generate schema ───────────────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.GENERATING_SCHEMA,
                message="Generating typed input/output schema…",
                progress_pct=40,
            )
            schema_result = await self._generate_schema(
                analysis=analysis,
                examples=examples,
                task_description=request.task_description,
            )
            steps_completed.append(SynthesisStep.GENERATING_SCHEMA)

            # ── Step 5: Generate implementation ──────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.GENERATING_IMPLEMENTATION,
                message="Generating Python implementation…",
                progress_pct=55,
                detail={"schema": schema_result},
            )
            implementation = await self._generate_implementation(
                analysis=analysis,
                schema=schema_result,
                examples=examples,
                task_description=request.task_description,
            )
            steps_completed.append(SynthesisStep.GENERATING_IMPLEMENTATION)

            # ── Step 6: Generate tests ────────────────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.GENERATING_TESTS,
                message="Generating unit test cases…",
                progress_pct=65,
            )
            test_cases = await self._generate_tests(
                analysis=analysis,
                implementation=implementation,
                schema=schema_result,
            )
            steps_completed.append(SynthesisStep.GENERATING_TESTS)

            # ── Build Skill object ────────────────────────────────────────────
            skill_id = str(uuid.uuid4())
            skill = Skill(
                id=skill_id,
                name=analysis.get("skill_name", f"synthesized_{skill_id[:8]}"),
                description=analysis.get("description", request.task_description),
                tags=analysis.get("tags", []) + (request.preferred_tags or []),
                category=SkillCategory(
                    analysis.get("category", request.preferred_category or SkillCategory.UNKNOWN)
                ),
                input_schema=self._parse_schema(schema_result.get("input_schema", {})),
                output_schema=self._parse_schema(schema_result.get("output_schema", {})),
                implementation=implementation,
                entry_point="run",
                status=SkillStatus.SANDBOX_PENDING,
                author=f"axiom-synthesizer/{request.requester}",
            )

            # Register as draft before sandbox
            await skill_store.register_skill(skill, embed=True)

            # ── Step 7: Sandbox ───────────────────────────────────────────────
            yield SynthesisProgressEvent(
                step=SynthesisStep.SANDBOX_SECURITY_SCAN,
                message="Running security scan (bandit + AST permission check)…",
                progress_pct=75,
            )

            # Lazy import to avoid circular deps
            from axiom.sandbox.evaluator import sandbox_evaluator

            yield SynthesisProgressEvent(
                step=SynthesisStep.SANDBOX_EXECUTION,
                message="Executing in isolated sandbox…",
                progress_pct=85,
            )
            evaluation = await sandbox_evaluator.evaluate(skill, test_cases)
            steps_completed.extend([SynthesisStep.SANDBOX_SECURITY_SCAN, SynthesisStep.SANDBOX_EXECUTION])

            # ── Step 8: Promote ───────────────────────────────────────────────
            final_status: str
            if evaluation.promotion_recommended:
                yield SynthesisProgressEvent(
                    step=SynthesisStep.PROMOTING,
                    message="Sandbox passed — promoting skill to ACTIVE registry…",
                    progress_pct=95,
                )
                await skill_store.promote_skill(skill.id)
                skill.status = SkillStatus.ACTIVE
                steps_completed.append(SynthesisStep.PROMOTING)
                final_status = "success"
            else:
                await skill_store.update_skill_status(skill.id, SkillStatus.SANDBOX_FAILED)
                skill.status = SkillStatus.SANDBOX_FAILED
                final_status = "failed"

            duration = time.perf_counter() - t0
            result = SynthesisResult(
                request_id=request_id,
                status=final_status,  # type: ignore[arg-type]
                skill=skill,
                evaluation_report=evaluation,
                steps_completed=steps_completed,
                synthesis_duration_seconds=round(duration, 2),
                error_message=evaluation.failure_reason if final_status == "failed" else None,
            )

            yield SynthesisProgressEvent(
                step=SynthesisStep.COMPLETE,
                message=(
                    f"Synthesis complete — skill '{skill.name}' "
                    + ("promoted to ACTIVE" if final_status == "success" else "failed sandbox")
                ),
                progress_pct=100,
                detail={"result": result.model_dump()},
            )

        except Exception as exc:
            logger.exception(f"Synthesis failed: {exc}")
            yield SynthesisProgressEvent(
                step=SynthesisStep.FAILED,
                message=f"Synthesis failed: {exc}",
                progress_pct=100,
                detail={
                    "result": SynthesisResult(
                        request_id=request_id,
                        status="failed",
                        steps_completed=steps_completed,
                        error_message=str(exc),
                        synthesis_duration_seconds=time.perf_counter() - t0,
                    ).model_dump()
                },
            )

    # ── LLM Helpers ───────────────────────────────────────────────────────────

    async def _chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        """Single DeepSeek chat completion."""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": settings.deepseek_model,
            "messages": messages,
            "temperature": settings.synthesis_temperature,
            "max_tokens": settings.synthesis_max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def _analyze_task(self, task_description: str) -> dict[str, Any]:
        """Step 1: Extract structured requirements from the task description."""
        prompt = f"""You are an expert software engineer analyzing a task to build a reusable skill.

Task: {task_description}

Return a JSON object with:
{{
  "skill_name": "snake_case name, max 5 words",
  "description": "1-2 sentence description of what the skill does",
  "tags": ["tag1", "tag2", "tag3"],
  "category": one of: trading|data_fetch|data_transform|analysis|notification|formatting|utility|synthesis|unknown,
  "required_inputs": ["brief description of each input"],
  "expected_outputs": ["brief description of each output"],
  "complexity": "simple|moderate|complex",
  "external_dependencies": ["list of third-party libraries needed, if any"]
}}"""

        content = await self._chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"skill_name": task_description[:40], "description": task_description, "tags": [], "category": "utility"}

    async def _retrieve_examples(self, task_description: str) -> list[Skill]:
        """Step 2: Fetch K most similar existing skills as few-shot examples."""
        try:
            results = await deduplication_checker.find_similar(
                description=task_description,
                top_k=settings.synthesis_few_shot_k,
                threshold=0.3,  # low threshold to get diverse examples
            )
            return [skill for skill, _ in results]
        except Exception as exc:
            logger.warning(f"Failed to retrieve few-shot examples: {exc}")
            return []

    async def _generate_schema(
        self,
        analysis: dict[str, Any],
        examples: list[Skill],
        task_description: str,
    ) -> dict[str, Any]:
        """Step 3: Generate typed I/O schema for the new skill."""
        example_schemas = "\n".join(
            f"- {s.name}: input={s.input_schema.model_dump()}, output={s.output_schema.model_dump()}"
            for s in examples[:3]
        )

        prompt = f"""You are designing a Python skill's type-safe I/O contract.

Task: {task_description}
Analysis: {json.dumps(analysis, indent=2)}

Existing similar skill schemas (for reference):
{example_schemas or "No similar skills found"}

Return a JSON object:
{{
  "input_schema": {{
    "description": "What this skill accepts",
    "fields": [
      {{"name": "field_name", "type": "Python type string", "description": "...", "required": true, "example": "example value"}}
    ]
  }},
  "output_schema": {{
    "description": "What this skill returns",
    "fields": [
      {{"name": "field_name", "type": "Python type string", "description": "...", "required": true, "example": "example value"}}
    ]
  }}
}}

Use Python type strings: str, int, float, bool, list[str], dict[str, Any], Optional[str], etc."""

        content = await self._chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"input_schema": {"fields": []}, "output_schema": {"fields": []}}

    async def _generate_implementation(
        self,
        analysis: dict[str, Any],
        schema: dict[str, Any],
        examples: list[Skill],
        task_description: str,
    ) -> str:
        """Step 4: Generate the Python implementation."""
        example_impls = "\n\n".join(
            f"# Example: {s.name}\n{s.implementation[:800]}"
            for s in examples[:2]
            if s.implementation
        )

        prompt = f"""You are a senior Python engineer. Write a clean, production-quality Python function.

Task: {task_description}
Skill name: {analysis.get('skill_name', 'unnamed')}
Description: {analysis.get('description', '')}

Input schema:
{json.dumps(schema.get('input_schema', {}), indent=2)}

Output schema:
{json.dumps(schema.get('output_schema', {}), indent=2)}

Similar skill implementations for reference:
{example_impls or "No examples available"}

Requirements:
- Write a single async function named `run(**kwargs) -> dict`
- The function must return a dict matching the output schema
- Add proper type hints and docstring
- Handle errors gracefully with try/except
- Do NOT use subprocess, os.system, open(), socket, or __import__
- Do NOT include import statements that use network access
- Keep it self-contained: standard library + common data libs only
- No file system writes

Return ONLY the Python code, no markdown fences."""

        implementation = await self._chat(
            [{"role": "user", "content": prompt}],
            json_mode=False,
        )
        # Strip markdown code fences if present
        if implementation.startswith("```"):
            lines = implementation.split("\n")
            implementation = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return implementation.strip()

    async def _generate_tests(
        self,
        analysis: dict[str, Any],
        implementation: str,
        schema: dict[str, Any],
    ) -> list[TestCase]:
        """Step 5: Generate test cases for the sandbox evaluator."""
        prompt = f"""Generate {settings.promotion_min_test_cases + 1} unit test cases for this Python skill.

Skill name: {analysis.get('skill_name', 'unnamed')}
Description: {analysis.get('description', '')}

Input schema:
{json.dumps(schema.get('input_schema', {}), indent=2)}

Output schema:
{json.dumps(schema.get('output_schema', {}), indent=2)}

Implementation:
{implementation[:2000]}

Return a JSON array of test cases:
[
  {{
    "name": "test_case_name",
    "input_data": {{"field": "value"}},
    "expected_type": "dict"
  }}
]

Make test inputs realistic. Test both typical and edge cases."""

        content = await self._chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
        )

        try:
            raw = json.loads(content)
            if isinstance(raw, dict) and "test_cases" in raw:
                raw = raw["test_cases"]
            return [TestCase(**tc) for tc in raw if isinstance(tc, dict)]
        except Exception as exc:
            logger.warning(f"Failed to parse test cases: {exc}")
            return [
                TestCase(
                    name="basic_invocation",
                    input_data={},
                    expected_type="dict",
                )
            ]

    @staticmethod
    def _parse_schema(schema_dict: dict[str, Any]) -> IOSchema:
        """Convert raw dict from LLM into an IOSchema model."""
        fields = []
        for f in schema_dict.get("fields", []):
            if isinstance(f, dict):
                fields.append(SchemaField(**{k: v for k, v in f.items() if k in SchemaField.model_fields}))
        return IOSchema(
            fields=fields,
            description=schema_dict.get("description", ""),
        )


# Module-level singleton
skill_synthesizer = SkillSynthesizer()
