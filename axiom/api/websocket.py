"""
AXIOM WebSocket Synthesis Endpoint

Provides real-time streaming synthesis progress to clients.

Endpoint: ws://host/ws/synthesize

Message protocol:
  → Client sends JSON: SynthesisRequest fields
  ← Server streams JSON: SynthesisProgressEvent objects
  ← Server closes connection after COMPLETE or FAILED step

Example client (Python):
    import asyncio, websockets, json
    async def main():
        async with websockets.connect("ws://localhost:8000/ws/synthesize") as ws:
            await ws.send(json.dumps({"task_description": "fetch BTC price from CoinGecko"}))
            async for msg in ws:
                event = json.loads(msg)
                print(f"[{event['step']}] {event['message']} ({event['progress_pct']}%)")

Example client (JavaScript):
    const ws = new WebSocket("ws://localhost:8000/ws/synthesize");
    ws.onopen = () => ws.send(JSON.stringify({task_description: "compute moving average"}));
    ws.onmessage = (e) => { const ev = JSON.parse(e.data); console.log(ev); };
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import ValidationError

from axiom.models import SynthesisProgressEvent, SynthesisRequest, SynthesisStep
from axiom.synthesis.synthesizer import skill_synthesizer

ws_router = APIRouter()


@ws_router.websocket("/ws/synthesize")
async def websocket_synthesize(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for streaming multi-step synthesis progress.

    Flow:
    1. Accept connection
    2. Receive SynthesisRequest JSON from client
    3. Stream SynthesisProgressEvent JSON messages back
    4. Close when synthesis is COMPLETE or FAILED
    """
    await websocket.accept()
    logger.info(f"WebSocket connection opened from {websocket.client}")

    try:
        # Receive the synthesis request
        raw = await websocket.receive_text()

        try:
            request_data = json.loads(raw)
            request = SynthesisRequest(**request_data)
        except (json.JSONDecodeError, ValidationError) as exc:
            error_event = SynthesisProgressEvent(
                step=SynthesisStep.FAILED,
                message=f"Invalid request: {exc}",
                progress_pct=0,
            )
            await websocket.send_text(error_event.model_dump_json())
            await websocket.close(code=1003, reason="Invalid request format")
            return

        logger.info(f"WS synthesis: '{request.task_description[:60]}…'")

        # Stream synthesis events
        async for event in skill_synthesizer.synthesize(request):
            try:
                await websocket.send_text(event.model_dump_json())
            except WebSocketDisconnect:
                logger.info("WebSocket client disconnected during synthesis")
                return

            # Close after terminal states
            if event.step in (SynthesisStep.COMPLETE, SynthesisStep.FAILED):
                logger.info(f"Synthesis terminal state reached: {event.step.value}")
                break

        await websocket.close(code=1000, reason="Synthesis complete")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.exception(f"WebSocket error: {exc}")
        try:
            error_event = SynthesisProgressEvent(
                step=SynthesisStep.FAILED,
                message=f"Internal error: {exc}",
                progress_pct=0,
            )
            await websocket.send_text(error_event.model_dump_json())
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass


@ws_router.websocket("/ws/monitor")
async def websocket_monitor(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for live decay monitor events.

    Clients receive a JSON message whenever a skill is flagged or deprecated.
    The connection stays open until the client disconnects.

    This is a broadcast stub — in production, wire to a Redis pub/sub channel.
    """
    await websocket.accept()
    logger.info(f"Monitor WebSocket connection from {websocket.client}")

    try:
        await websocket.send_text(
            json.dumps({"type": "connected", "message": "AXIOM decay monitor stream active"})
        )
        # Keep alive — in production, subscribe to Redis channel and push events
        while True:
            # Wait for client ping or disconnect
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        logger.info("Monitor WebSocket client disconnected")
