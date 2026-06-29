"""
AXIOM Python SDK Client

Drop-in replacement for Hermes skill loader.

Usage:
    from axiom.sdk.client import AxiomClient

    axiom = AxiomClient(base_url="http://localhost:8000")

    # Resolve existing skills
    skills = await axiom.resolve("fetch current BTC price", top_k=3)

    # Get a skill chain for multi-step task
    chain = await axiom.compose("fetch BTC price then format as markdown table")

    # Synthesize a new skill
    result = await axiom.synthesize("compute RSI for a list of closing prices")

    # Invoke a skill
    output = await axiom.invoke_skill(skill_id, {"prices": [100, 102, 98]})
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Optional

import httpx
from loguru import logger

from axiom.models import (
    ComposeRequest,
    ComposeResponse,
    ResolveRequest,
    ResolveResponse,
    ResolvedSkill,
    Skill,
    SkillChain,
    SkillInvokeRequest,
    SkillInvokeResponse,
    SkillMetrics,
    SynthesisProgressEvent,
    SynthesisRequest,
    SynthesisResult,
)


class AxiomClient:
    """
    Async HTTP client for the AXIOM API.

    All methods are async and return typed Pydantic models.
    Designed as a drop-in skill loader for the Hermes agent framework.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_prefix: str = "/api/v1",
        timeout: float = 60.0,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix
        self._timeout = timeout
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._get_headers(),
                timeout=self._timeout,
            )
        return self._client

    def _url(self, path: str) -> str:
        return f"{self.api_prefix}{path}"

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "AxiomClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Resolve ───────────────────────────────────────────────────────────────

    async def resolve(
        self,
        task_description: str,
        top_k: int = 5,
        filter_tags: Optional[list[str]] = None,
        min_confidence: float = 0.0,
    ) -> list[ResolvedSkill]:
        """
        Resolve a task to ranked existing skills.

        Returns a list of ResolvedSkill ordered by confidence descending.
        """
        client = await self._http()
        request = ResolveRequest(
            task_description=task_description,
            top_k=top_k,
            filter_tags=filter_tags or [],
            min_confidence=min_confidence,
        )
        response = await client.post(
            self._url("/resolve"),
            content=request.model_dump_json(),
        )
        response.raise_for_status()
        result = ResolveResponse(**response.json())
        logger.debug(
            f"Resolved {len(result.candidates)} skill(s) for '{task_description[:40]}…'"
        )
        return result.candidates

    # ── Compose ───────────────────────────────────────────────────────────────

    async def compose(
        self,
        task_description: str,
        max_chain_length: int = 5,
        input_context: Optional[dict[str, Any]] = None,
    ) -> list[SkillChain]:
        """
        Find skill chains that compose to solve the task.

        Returns a list of SkillChain ordered by joint success rate.
        """
        client = await self._http()
        request = ComposeRequest(
            task_description=task_description,
            max_chain_length=max_chain_length,
            input_context=input_context,
        )
        response = await client.post(
            self._url("/compose"),
            content=request.model_dump_json(),
        )
        response.raise_for_status()
        result = ComposeResponse(**response.json())
        logger.debug(f"Found {len(result.chains)} chain(s) for '{task_description[:40]}…'")
        return result.chains

    # ── Synthesize ────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        task_description: str,
        preferred_tags: Optional[list[str]] = None,
        force: bool = False,
    ) -> SynthesisResult:
        """
        Synthesize a new skill for the given task (non-streaming REST call).
        For streaming, use synthesize_stream().
        """
        client = await self._http()
        request = SynthesisRequest(
            task_description=task_description,
            preferred_tags=preferred_tags or [],
            force_synthesize=force,
        )
        response = await client.post(
            self._url("/synthesize"),
            content=request.model_dump_json(),
        )
        response.raise_for_status()
        return SynthesisResult(**response.json())

    async def synthesize_stream(
        self,
        task_description: str,
        preferred_tags: Optional[list[str]] = None,
        force: bool = False,
    ) -> AsyncGenerator[SynthesisProgressEvent, None]:
        """
        Synthesize with streaming progress via WebSocket.

        Yields SynthesisProgressEvent objects as the pipeline proceeds.
        """
        try:
            import websockets
        except ImportError:
            raise ImportError("Install websockets: pip install websockets")

        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/synthesize"

        request = SynthesisRequest(
            task_description=task_description,
            preferred_tags=preferred_tags or [],
            force_synthesize=force,
        )

        async with websockets.connect(ws_url) as ws:  # type: ignore[attr-defined]
            await ws.send(request.model_dump_json())
            async for raw_message in ws:
                event = SynthesisProgressEvent(**json.loads(raw_message))
                yield event
                if event.step.value in ("complete", "failed"):
                    break

    # ── Invoke ────────────────────────────────────────────────────────────────

    async def invoke_skill(
        self,
        skill_id: str,
        input_data: dict[str, Any],
        timeout_seconds: Optional[int] = None,
    ) -> SkillInvokeResponse:
        """Invoke a registered skill by ID."""
        client = await self._http()
        request = SkillInvokeRequest(
            input_data=input_data,
            timeout_seconds=timeout_seconds,
        )
        response = await client.post(
            self._url(f"/skills/{skill_id}/invoke"),
            content=request.model_dump_json(),
        )
        response.raise_for_status()
        return SkillInvokeResponse(**response.json())

    # ── Registry ──────────────────────────────────────────────────────────────

    async def get_skill(self, skill_id: str) -> Skill:
        """Fetch a skill by ID."""
        client = await self._http()
        response = await client.get(self._url(f"/skills/{skill_id}"))
        response.raise_for_status()
        return Skill(**response.json())

    async def list_skills(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Skill]:
        """List all skills with optional filters."""
        client = await self._http()
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if category:
            params["category"] = category
        if tags:
            params["tags"] = ",".join(tags)

        response = await client.get(self._url("/skills"), params=params)
        response.raise_for_status()
        return [Skill(**s) for s in response.json()]

    async def register_skill(self, skill: Skill) -> Skill:
        """Register a skill manually."""
        client = await self._http()
        response = await client.post(
            self._url("/skills"),
            content=skill.model_dump_json(),
        )
        response.raise_for_status()
        return Skill(**response.json())

    async def delete_skill(self, skill_id: str) -> None:
        """Delete a skill by ID."""
        client = await self._http()
        response = await client.delete(self._url(f"/skills/{skill_id}"))
        response.raise_for_status()

    async def get_metrics(self, skill_id: str) -> SkillMetrics:
        """Get performance metrics for a skill."""
        client = await self._http()
        response = await client.get(self._url(f"/skills/{skill_id}/metrics"))
        response.raise_for_status()
        return SkillMetrics(**response.json())

    # ── Hermes-compatible interface ───────────────────────────────────────────

    async def load_skill(self, task_description: str) -> Optional[Skill]:
        """
        Hermes-compatible skill loader interface.

        Resolves the best skill for the task. If no skill meets the confidence
        threshold, triggers synthesis and returns the new skill.
        """
        candidates = await self.resolve(task_description, top_k=1, min_confidence=0.5)

        if candidates:
            logger.info(f"AXIOM: loaded existing skill '{candidates[0].skill.name}'")
            return candidates[0].skill

        # No matching skill — synthesize a new one
        logger.info(f"AXIOM: no existing skill found, synthesizing for '{task_description[:50]}'")
        result = await self.synthesize(task_description)
        if result.status == "success" and result.skill:
            return result.skill
        if result.status == "duplicate" and result.duplicate_of:
            return result.duplicate_of

        logger.warning(f"AXIOM: synthesis failed for '{task_description[:50]}'")
        return None
