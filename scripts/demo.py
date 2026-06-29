"""
AXIOM Interactive Demo

Demonstrates the full synthesis pipeline:
1. Register 5 example skills
2. Run a task that resolves to existing skills
3. Run a task that requires skill composition
4. Run a task that triggers synthesis
5. Show the full pipeline with streaming WebSocket feedback

Run:
    python scripts/demo.py [--axiom-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger


DEMO_SKILLS = [
    {
        "name": "compute_sma",
        "description": "Computes the simple moving average for a list of prices.",
        "tags": ["trading", "statistics", "sma"],
        "implementation": """
def run(prices: list, window: int = 10) -> dict:
    n = min(window, len(prices))
    sma = sum(prices[-n:]) / n if prices else 0.0
    return {"sma": round(sma, 4), "window": n}
""".strip(),
    },
    {
        "name": "format_price_table",
        "description": "Formats a list of price records as a markdown table.",
        "tags": ["formatting", "markdown", "prices"],
        "implementation": """
def run(records: list) -> dict:
    if not records:
        return {"markdown": "_empty_", "rows": 0}
    headers = list(records[0].keys())
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in records:
        lines.append("| " + " | ".join(str(row.get(h,"")) for h in headers) + " |")
    return {"markdown": "\\n".join(lines), "rows": len(records)}
""".strip(),
    },
    {
        "name": "detect_price_spike",
        "description": "Detects sudden price spikes above a percentage threshold.",
        "tags": ["trading", "anomaly", "spike", "alert"],
        "implementation": """
def run(prices: list, threshold_pct: float = 5.0) -> dict:
    if len(prices) < 2:
        return {"spikes": [], "count": 0}
    spikes = []
    for i in range(1, len(prices)):
        change_pct = abs(prices[i] - prices[i-1]) / (prices[i-1] or 1) * 100
        if change_pct > threshold_pct:
            spikes.append({"index": i, "value": prices[i], "change_pct": round(change_pct, 2)})
    return {"spikes": spikes, "count": len(spikes)}
""".strip(),
    },
    {
        "name": "aggregate_ohlcv",
        "description": "Aggregates tick data into OHLCV (open, high, low, close, volume) bars.",
        "tags": ["trading", "ohlcv", "data_transform", "candles"],
        "implementation": """
def run(ticks: list) -> dict:
    if not ticks:
        return {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
    prices = [float(t.get("price", 0)) for t in ticks]
    volumes = [float(t.get("volume", 0)) for t in ticks]
    return {
        "open": prices[0], "high": max(prices), "low": min(prices),
        "close": prices[-1], "volume": round(sum(volumes), 4),
    }
""".strip(),
    },
    {
        "name": "normalise_price_series",
        "description": "Normalises a price series to [0, 1] range using min-max scaling.",
        "tags": ["data_transform", "normalisation", "prices", "ml"],
        "implementation": """
def run(prices: list) -> dict:
    if not prices:
        return {"normalised": [], "min": 0.0, "max": 0.0}
    mn, mx = min(prices), max(prices)
    rng = mx - mn
    normalised = [round((p - mn) / rng, 6) if rng else 0.0 for p in prices]
    return {"normalised": normalised, "min": mn, "max": mx}
""".strip(),
    },
]


async def run_demo(axiom_url: str) -> None:
    from axiom.models import IOSchema, Skill, SkillStatus
    from axiom.sdk.client import AxiomClient

    print("\n" + "=" * 60)
    print("  AXIOM — Living Skill Marketplace Demo")
    print("=" * 60 + "\n")

    async with AxiomClient(base_url=axiom_url) as axiom:
        # ── Step 1: Health check ──────────────────────────────────────────────
        print("[ 1/5 ] Checking AXIOM health…")
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{axiom_url}/api/v1/health", timeout=5.0)
                health = resp.json()
            print(f"       Status: {health.get('status')} | Version: {health.get('version')}\n")
        except Exception as exc:
            print(f"       Warning: Could not connect to AXIOM at {axiom_url}: {exc}")
            print("       Running in local demo mode (no registry)\n")

        # ── Step 2: Register demo skills ──────────────────────────────────────
        print("[ 2/5 ] Registering 5 demo skills…")
        registered_ids: list[str] = []

        for skill_data in DEMO_SKILLS:
            try:
                skill = Skill(
                    name=skill_data["name"],
                    description=skill_data["description"],
                    tags=skill_data["tags"],
                    implementation=skill_data["implementation"],
                    status=SkillStatus.ACTIVE,
                    success_count=5,
                    invocation_count=5,
                )
                result = await axiom.register_skill(skill)
                registered_ids.append(result.id)
                print(f"       ✓ Registered '{skill.name}' (id={result.id[:8]}…)")
            except Exception as exc:
                print(f"       ! Could not register '{skill_data['name']}': {exc}")

        print()

        # ── Step 3: Resolve existing skill ────────────────────────────────────
        print("[ 3/5 ] Resolving task: 'compute moving average for crypto prices'")
        try:
            candidates = await axiom.resolve(
                "compute moving average for crypto prices",
                top_k=3,
            )
            if candidates:
                print(f"       Found {len(candidates)} matching skill(s):")
                for r in candidates:
                    print(f"         [{r.rank}] '{r.skill.name}' — confidence={r.confidence:.3f}")
            else:
                print("       No matching skills found in registry")
        except Exception as exc:
            print(f"       Error: {exc}")
        print()

        # ── Step 4: Compose a multi-step chain ────────────────────────────────
        print("[ 4/5 ] Composing chain: 'aggregate tick data then detect price spikes'")
        try:
            chains = await axiom.compose(
                "aggregate tick data then detect price spikes",
                max_chain_length=3,
            )
            if chains:
                print(f"       Found {len(chains)} composition chain(s):")
                for i, chain in enumerate(chains[:2]):
                    skill_names = " → ".join(s.name for s in chain.skills)
                    print(f"         [{i+1}] {skill_names}")
                    print(f"              joint_success={chain.combined_success_rate:.3f}")
            else:
                print("       No valid composition chains found")
        except Exception as exc:
            print(f"       Error: {exc}")
        print()

        # ── Step 5: Trigger synthesis ─────────────────────────────────────────
        print("[ 5/5 ] Synthesizing new skill: 'compute Bollinger Bands for a price series'")
        print("        Streaming progress via WebSocket…\n")

        try:
            async for event in axiom.synthesize_stream(
                "compute Bollinger Bands for a price series",
                preferred_tags=["trading", "bollinger", "statistics"],
            ):
                progress_bar = "█" * (event.progress_pct // 10) + "░" * (10 - event.progress_pct // 10)
                print(f"        [{progress_bar}] {event.progress_pct:3d}% | {event.step.value:<30} {event.message}")

                if event.step.value in ("complete", "failed"):
                    if event.detail and "result" in event.detail:
                        result = event.detail["result"]
                        print(f"\n        Result: status={result.get('status')}")
                        if result.get("skill"):
                            print(f"        New skill: '{result['skill']['name']}' promoted to ACTIVE")
                    break

        except Exception as exc:
            print(f"\n        Synthesis demo (WebSocket): {exc}")
            print("        Falling back to REST synthesis…")
            try:
                result = await axiom.synthesize("compute Bollinger Bands for a price series")
                print(f"        REST result: status={result.status}")
                if result.skill:
                    print(f"        New skill: '{result.skill.name}' status={result.skill.status.value}")
            except Exception as exc2:
                print(f"        REST synthesis also failed: {exc2}")

    print("\n" + "=" * 60)
    print("  Demo complete.")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="AXIOM interactive demo")
    parser.add_argument("--axiom-url", default="http://localhost:8000", help="AXIOM base URL")
    args = parser.parse_args()
    asyncio.run(run_demo(args.axiom_url))


if __name__ == "__main__":
    main()
