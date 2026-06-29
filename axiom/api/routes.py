"""
AXIOM FastAPI REST Routes

Endpoints:
    POST /resolve              — task description → ranked existing skills
    POST /compose              — task description → skill chain
    POST /synthesize           — task description → synthesize new skill (REST, non-streaming)
    GET  /skills               — list all skills
    GET  /skills/{id}          — get skill by ID
    POST /skills/{id}/invoke   — invoke a skill with input data
    GET  /skills/{id}/metrics  — get performance metrics for a skill
    DELETE /skills/{id}        — delete a skill
    GET  /health               — service health check
    GET  /monitor/status       — decay monitor status
    POST /monitor/run          — manually trigger decay check
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from loguru import logger

from axiom.models import (
    ComposeRequest,
    ComposeResponse,
    ResolveRequest,
    ResolveResponse,
    Skill,
    SkillCategory,
    SkillInvokeRequest,
    SkillInvokeResponse,
    SkillMetrics,
    SkillStatus,
    SynthesisRequest,
    SynthesisResult,
)
from axiom.config import settings
from axiom.monitor.decay_monitor import decay_monitor
from axiom.registry.skill_store import skill_store
from axiom.resolver.capability_resolver import capability_resolver
from axiom.resolver.composition_engine import composition_engine
from axiom.sandbox.evaluator import sandbox_evaluator

router = APIRouter()


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", tags=["system"])
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "AXIOM",
        "version": "0.1.0",
        "decay_monitor_running": decay_monitor.is_running,
    }


# ── Resolve ───────────────────────────────────────────────────────────────────

@router.post("/resolve", response_model=ResolveResponse, tags=["resolver"])
async def resolve_task(request: ResolveRequest) -> ResolveResponse:
    """
    Embed the task description and return the top-K most capable existing skills,
    ranked by composite score (semantic similarity + success rate + recency).
    """
    logger.info(f"POST /resolve: '{request.task_description[:60]}…'")
    return await capability_resolver.resolve(request)


# ── Compose ───────────────────────────────────────────────────────────────────

@router.post("/compose", response_model=ComposeResponse, tags=["composer"])
async def compose_task(request: ComposeRequest) -> ComposeResponse:
    """
    Find valid skill chains where skill[i].output satisfies skill[i+1].input.
    Returns chains ranked by joint success rate.
    """
    logger.info(f"POST /compose: '{request.task_description[:60]}…'")
    return await composition_engine.compose(request)


# ── Synthesize (REST, non-streaming) ─────────────────────────────────────────

@router.post(
    "/synthesize",
    response_model=SynthesisResult,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["synthesizer"],
)
async def synthesize_skill(request: SynthesisRequest) -> SynthesisResult:
    """
    Synthesize a new skill from a task description.

    This endpoint collects all synthesis events and returns the final result.
    For streaming progress use the WebSocket endpoint at /ws/synthesize.
    """
    from axiom.synthesis.synthesizer import skill_synthesizer

    logger.info(f"POST /synthesize: '{request.task_description[:60]}…'")

    final_result: Optional[SynthesisResult] = None

    async for event in skill_synthesizer.synthesize(request):
        logger.debug(f"Synthesis step: {event.step.value} — {event.message}")
        if event.detail and "result" in event.detail:
            # Parse result from the detail dict
            try:
                final_result = SynthesisResult(**event.detail["result"])
            except Exception:
                pass

    if final_result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Synthesis completed without producing a result",
        )

    return final_result


# ── Skills CRUD ───────────────────────────────────────────────────────────────

@router.get("/skills", response_model=list[Skill], tags=["registry"])
async def list_skills(
    status_filter: Optional[SkillStatus] = Query(default=None, alias="status"),
    category: Optional[SkillCategory] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tag filter"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Skill]:
    """List skills from the registry with optional filters."""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    return await skill_store.list_skills(
        status=status_filter,
        category=category,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )


@router.post("/skills", response_model=Skill, status_code=status.HTTP_201_CREATED, tags=["registry"])
async def register_skill(skill: Skill) -> Skill:
    """Manually register a new skill in the AXIOM registry."""
    logger.info(f"POST /skills: registering '{skill.name}'")
    return await skill_store.register_skill(skill)


@router.get("/skills/{skill_id}", response_model=Skill, tags=["registry"])
async def get_skill(skill_id: str) -> Skill:
    """Get a skill by ID."""
    skill = await skill_store.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return skill


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["registry"])
async def delete_skill(skill_id: str) -> None:
    """Delete a skill from the registry."""
    deleted = await skill_store.delete_skill(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")


# ── Invoke ────────────────────────────────────────────────────────────────────

@router.post("/skills/{skill_id}/invoke", response_model=SkillInvokeResponse, tags=["executor"])
async def invoke_skill(
    skill_id: str,
    request: SkillInvokeRequest,
    background_tasks: BackgroundTasks,
) -> SkillInvokeResponse:
    """
    Invoke a skill with the provided input data.
    Runs inside the sandbox evaluator for safety.
    Records invocation metrics in the background.
    """
    skill = await skill_store.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    if skill.status not in (SkillStatus.ACTIVE, SkillStatus.FLAGGED):
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{skill.name}' is not active (status={skill.status.value})",
        )

    t0 = time.perf_counter()
    success = False
    output: Any = None
    error_msg: Optional[str] = None

    try:
        from axiom.models import TestCase

        # Treat invocation as a single-run test case
        tc = TestCase(
            name="direct_invocation",
            input_data=request.input_data,
        )
        report = await sandbox_evaluator.evaluate(skill, [tc])

        if report.test_results:
            tr = report.test_results[0]
            success = tr.passed
            output = tr.actual_output
            error_msg = tr.error_message
        else:
            error_msg = "No test results returned from sandbox"

    except Exception as exc:
        error_msg = str(exc)
        logger.error(f"Invocation error for skill '{skill.name}': {exc}")

    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Update metrics asynchronously
    background_tasks.add_task(
        skill_store.update_performance_metrics,
        skill_id=skill_id,
        success=success,
        latency_ms=elapsed_ms,
    )

    return SkillInvokeResponse(
        skill_id=skill_id,
        skill_name=skill.name,
        output=output,
        success=success,
        execution_ms=round(elapsed_ms, 2),
        error_message=error_msg,
    )


# ── Metrics ───────────────────────────────────────────────────────────────────

@router.get("/skills/{skill_id}/metrics", response_model=SkillMetrics, tags=["metrics"])
async def get_skill_metrics(skill_id: str) -> SkillMetrics:
    """Get performance metrics for a skill."""
    skill = await skill_store.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    decay_risk = (
        skill.invocation_count >= settings.decay_min_invocations
        and skill.success_rate < settings.decay_success_threshold
    )

    return SkillMetrics(
        skill_id=skill.id,
        skill_name=skill.name,
        invocation_count=skill.invocation_count,
        success_rate=skill.success_rate,
        avg_latency_ms=skill.avg_latency_ms,
        last_invoked_at=skill.last_invoked_at,
        status=skill.status,
        decay_risk=decay_risk,
    )


# ── Monitor ───────────────────────────────────────────────────────────────────

@router.get("/monitor/status", tags=["monitor"])
async def monitor_status() -> dict:
    """Get decay monitor status and stats."""
    return decay_monitor.stats


@router.post("/monitor/run", tags=["monitor"])
async def trigger_decay_check() -> dict:
    """Manually trigger an immediate decay check cycle."""
    return await decay_monitor.run_now()
