"""
AXIOM Registry Seeder

Populates the AXIOM registry with 10 example skills spanning:
    - Trading analytics
    - Data fetching
    - Data formatting
    - Notifications
    - Analysis

Run:
    python scripts/seed_registry.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the repo root is in the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from axiom.models import IOSchema, SchemaField, Skill, SkillCategory, SkillStatus


SEED_SKILLS: list[dict] = [
    {
        "name": "fetch_crypto_price",
        "description": "Fetches the current price of a cryptocurrency from CoinGecko's public API.",
        "tags": ["crypto", "price", "data_fetch", "coingecko"],
        "category": SkillCategory.DATA_FETCH,
        "input_schema": IOSchema(
            description="Cryptocurrency identifier",
            fields=[
                SchemaField(name="coin_id", type="str", description="CoinGecko coin ID e.g. 'bitcoin'", example="bitcoin"),
                SchemaField(name="vs_currency", type="str", description="Quote currency", required=False, default="usd", example="usd"),
            ],
        ),
        "output_schema": IOSchema(
            description="Current price data",
            fields=[
                SchemaField(name="coin_id", type="str", description="Coin identifier"),
                SchemaField(name="price", type="float", description="Current price in vs_currency"),
                SchemaField(name="currency", type="str", description="Quote currency"),
                SchemaField(name="fetched_at", type="str", description="ISO timestamp"),
            ],
        ),
        "implementation": '''
async def run(coin_id: str = "bitcoin", vs_currency: str = "usd") -> dict:
    """Fetches current cryptocurrency price from CoinGecko API."""
    import urllib.request, json
    from datetime import datetime

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies={vs_currency}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        price = data.get(coin_id, {}).get(vs_currency, 0.0)
    except Exception:
        price = 0.0  # graceful degradation

    return {
        "coin_id": coin_id,
        "price": price,
        "currency": vs_currency,
        "fetched_at": datetime.utcnow().isoformat(),
    }
'''.strip(),
    },
    {
        "name": "compute_moving_average",
        "description": "Computes simple moving average (SMA) and exponential moving average (EMA) for a price series.",
        "tags": ["trading", "statistics", "moving_average", "sma", "ema"],
        "category": SkillCategory.ANALYSIS,
        "input_schema": IOSchema(
            description="Price series and window parameters",
            fields=[
                SchemaField(name="prices", type="list[float]", description="List of closing prices", example=[100.0, 102.5, 99.8]),
                SchemaField(name="window", type="int", description="Lookback period", required=False, default=14, example=14),
            ],
        ),
        "output_schema": IOSchema(
            description="Moving average results",
            fields=[
                SchemaField(name="sma", type="float", description="Simple moving average over window"),
                SchemaField(name="ema", type="float", description="Exponential moving average"),
                SchemaField(name="window_used", type="int", description="Actual window used"),
                SchemaField(name="data_points", type="int", description="Number of input data points"),
            ],
        ),
        "implementation": '''
def run(prices: list, window: int = 14) -> dict:
    """Computes SMA and EMA for a price series."""
    if not prices:
        return {"sma": 0.0, "ema": 0.0, "window_used": window, "data_points": 0}

    n = min(window, len(prices))
    recent = prices[-n:]

    # SMA
    sma = sum(recent) / len(recent)

    # EMA with smoothing factor 2/(n+1)
    k = 2.0 / (n + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)

    return {
        "sma": round(sma, 6),
        "ema": round(ema, 6),
        "window_used": n,
        "data_points": len(prices),
    }
'''.strip(),
    },
    {
        "name": "format_as_markdown_table",
        "description": "Formats a list of dictionaries as a Markdown table with aligned columns.",
        "tags": ["formatting", "markdown", "table", "output"],
        "category": SkillCategory.FORMATTING,
        "input_schema": IOSchema(
            description="Table data",
            fields=[
                SchemaField(name="rows", type="list[dict]", description="List of row dicts", example=[{"name": "BTC", "price": 65000}]),
                SchemaField(name="headers", type="list[str]", description="Column headers (auto-detected if omitted)", required=False, default=None),
            ],
        ),
        "output_schema": IOSchema(
            description="Formatted markdown",
            fields=[
                SchemaField(name="markdown", type="str", description="Markdown table string"),
                SchemaField(name="row_count", type="int", description="Number of data rows"),
            ],
        ),
        "implementation": '''
def run(rows: list, headers: list = None) -> dict:
    """Formats a list of dicts as a Markdown table."""
    if not rows:
        return {"markdown": "_No data_", "row_count": 0}

    if headers is None:
        headers = list(rows[0].keys())

    col_widths = {h: len(str(h)) for h in headers}
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row.get(h, ""))))

    def fmt_row(values):
        return "| " + " | ".join(str(v).ljust(col_widths[h]) for h, v in zip(headers, values)) + " |"

    header_line = fmt_row(headers)
    sep_line = "| " + " | ".join("-" * col_widths[h] for h in headers) + " |"
    data_lines = [fmt_row([row.get(h, "") for h in headers]) for row in rows]

    return {
        "markdown": "\\n".join([header_line, sep_line] + data_lines),
        "row_count": len(rows),
    }
'''.strip(),
    },
    {
        "name": "compute_rsi",
        "description": "Computes the Relative Strength Index (RSI) for a list of closing prices.",
        "tags": ["trading", "rsi", "technical_analysis", "momentum"],
        "category": SkillCategory.ANALYSIS,
        "input_schema": IOSchema(
            description="RSI parameters",
            fields=[
                SchemaField(name="prices", type="list[float]", description="List of closing prices (minimum 15 required)"),
                SchemaField(name="period", type="int", description="RSI period", required=False, default=14),
            ],
        ),
        "output_schema": IOSchema(
            description="RSI result",
            fields=[
                SchemaField(name="rsi", type="float", description="RSI value (0–100)"),
                SchemaField(name="overbought", type="bool", description="True if RSI > 70"),
                SchemaField(name="oversold", type="bool", description="True if RSI < 30"),
                SchemaField(name="period", type="int", description="Period used"),
            ],
        ),
        "implementation": '''
def run(prices: list, period: int = 14) -> dict:
    """Computes RSI for a price series."""
    if len(prices) < period + 1:
        return {"rsi": 50.0, "overbought": False, "oversold": False, "period": period}

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = round(100 - (100 / (1 + rs)), 2)

    return {
        "rsi": rsi,
        "overbought": rsi > 70,
        "oversold": rsi < 30,
        "period": period,
    }
'''.strip(),
    },
    {
        "name": "send_slack_notification",
        "description": "Sends a formatted notification message to a Slack webhook URL.",
        "tags": ["notification", "slack", "webhook", "alert"],
        "category": SkillCategory.NOTIFICATION,
        "input_schema": IOSchema(
            description="Slack notification payload",
            fields=[
                SchemaField(name="webhook_url", type="str", description="Slack incoming webhook URL"),
                SchemaField(name="message", type="str", description="Message text"),
                SchemaField(name="title", type="str", description="Block title", required=False, default="AXIOM Alert"),
                SchemaField(name="color", type="str", description="Attachment color hex", required=False, default="#36a64f"),
            ],
        ),
        "output_schema": IOSchema(
            description="Send result",
            fields=[
                SchemaField(name="sent", type="bool", description="True if sent successfully"),
                SchemaField(name="status_code", type="int", description="HTTP status code"),
            ],
        ),
        "implementation": '''
def run(webhook_url: str, message: str, title: str = "AXIOM Alert", color: str = "#36a64f") -> dict:
    """Sends a notification to Slack via webhook."""
    import urllib.request, json

    payload = {
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": message,
                "footer": "AXIOM Skill Marketplace",
            }
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"sent": True, "status_code": resp.getcode()}
    except Exception as e:
        return {"sent": False, "status_code": 0}
'''.strip(),
    },
    {
        "name": "parse_json_response",
        "description": "Parses a JSON string and extracts specified fields using dot-notation paths.",
        "tags": ["data_transform", "json", "parsing", "extraction"],
        "category": SkillCategory.DATA_TRANSFORM,
        "input_schema": IOSchema(
            description="JSON parsing parameters",
            fields=[
                SchemaField(name="json_string", type="str", description="Raw JSON string to parse"),
                SchemaField(name="fields", type="list[str]", description="Dot-notation field paths e.g. ['data.price', 'meta.ts']", required=False, default=None),
            ],
        ),
        "output_schema": IOSchema(
            description="Parsed result",
            fields=[
                SchemaField(name="parsed", type="dict", description="Full parsed object"),
                SchemaField(name="extracted", type="dict", description="Extracted fields (if paths provided)"),
                SchemaField(name="success", type="bool", description="True if parsing succeeded"),
            ],
        ),
        "implementation": '''
def run(json_string: str, fields: list = None) -> dict:
    """Parses JSON and extracts specified field paths."""
    import json as _json

    try:
        parsed = _json.loads(json_string)
    except Exception as e:
        return {"parsed": {}, "extracted": {}, "success": False}

    extracted = {}
    if fields:
        for path in fields:
            parts = path.split(".")
            val = parsed
            try:
                for p in parts:
                    val = val[p] if isinstance(val, dict) else val[int(p)]
                extracted[path] = val
            except (KeyError, IndexError, ValueError):
                extracted[path] = None

    return {"parsed": parsed, "extracted": extracted, "success": True}
'''.strip(),
    },
    {
        "name": "calculate_portfolio_pnl",
        "description": "Calculates unrealised P&L for a portfolio of positions given current prices.",
        "tags": ["trading", "portfolio", "pnl", "finance"],
        "category": SkillCategory.TRADING,
        "input_schema": IOSchema(
            description="Portfolio and price data",
            fields=[
                SchemaField(name="positions", type="list[dict]", description="List of {symbol, quantity, avg_cost} dicts"),
                SchemaField(name="current_prices", type="dict[str, float]", description="Current price per symbol {symbol: price}"),
            ],
        ),
        "output_schema": IOSchema(
            description="P&L calculation",
            fields=[
                SchemaField(name="total_cost", type="float", description="Total cost basis"),
                SchemaField(name="total_value", type="float", description="Current portfolio value"),
                SchemaField(name="unrealised_pnl", type="float", description="Unrealised P&L"),
                SchemaField(name="pnl_pct", type="float", description="P&L as percentage of cost"),
                SchemaField(name="positions", type="list[dict]", description="Per-position P&L breakdown"),
            ],
        ),
        "implementation": '''
def run(positions: list, current_prices: dict) -> dict:
    """Calculates portfolio P&L."""
    total_cost = 0.0
    total_value = 0.0
    position_details = []

    for pos in positions:
        symbol = pos.get("symbol", "")
        qty = float(pos.get("quantity", 0))
        avg_cost = float(pos.get("avg_cost", 0))
        current = float(current_prices.get(symbol, avg_cost))

        cost = qty * avg_cost
        value = qty * current
        pnl = value - cost
        pct = (pnl / cost * 100) if cost else 0.0

        total_cost += cost
        total_value += value
        position_details.append({
            "symbol": symbol, "quantity": qty, "avg_cost": avg_cost,
            "current_price": current, "cost_basis": round(cost, 2),
            "market_value": round(value, 2), "pnl": round(pnl, 2), "pnl_pct": round(pct, 2),
        })

    pnl = total_value - total_cost
    pnl_pct = (pnl / total_cost * 100) if total_cost else 0.0
    return {
        "total_cost": round(total_cost, 2), "total_value": round(total_value, 2),
        "unrealised_pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
        "positions": position_details,
    }
'''.strip(),
    },
    {
        "name": "summarise_text",
        "description": "Produces an extractive summary of a text by scoring sentences using TF-IDF-like term frequency.",
        "tags": ["nlp", "summarisation", "text", "analysis"],
        "category": SkillCategory.ANALYSIS,
        "input_schema": IOSchema(
            description="Text and summary parameters",
            fields=[
                SchemaField(name="text", type="str", description="Text to summarise"),
                SchemaField(name="num_sentences", type="int", description="Target summary length in sentences", required=False, default=3),
            ],
        ),
        "output_schema": IOSchema(
            description="Summary result",
            fields=[
                SchemaField(name="summary", type="str", description="Extracted summary"),
                SchemaField(name="compression_ratio", type="float", description="Ratio of summary to original length"),
                SchemaField(name="sentences_selected", type="int", description="Number of sentences in summary"),
            ],
        ),
        "implementation": '''
def run(text: str, num_sentences: int = 3) -> dict:
    """Extractive text summarisation using term frequency scoring."""
    import re
    from collections import Counter

    if not text.strip():
        return {"summary": "", "compression_ratio": 0.0, "sentences_selected": 0}

    sentences = re.split(r"(?<=[.!?])\\s+", text.strip())
    if len(sentences) <= num_sentences:
        return {"summary": text, "compression_ratio": 1.0, "sentences_selected": len(sentences)}

    # Term frequency
    words = re.findall(r"\\b\\w+\\b", text.lower())
    stop_words = {"the","a","an","and","or","but","in","on","at","to","for","of","with","is","are","was","were","it","this","that"}
    freq = Counter(w for w in words if w not in stop_words and len(w) > 2)

    # Score sentences
    def score(sent):
        ws = re.findall(r"\\b\\w+\\b", sent.lower())
        return sum(freq.get(w, 0) for w in ws) / (len(ws) + 1)

    scored = sorted(enumerate(sentences), key=lambda x: score(x[1]), reverse=True)
    selected_idx = sorted(i for i, _ in scored[:num_sentences])
    summary = " ".join(sentences[i] for i in selected_idx)

    return {
        "summary": summary,
        "compression_ratio": round(len(summary) / len(text), 3),
        "sentences_selected": len(selected_idx),
    }
'''.strip(),
    },
    {
        "name": "detect_anomalies",
        "description": "Detects statistical anomalies in a numeric time series using Z-score method.",
        "tags": ["analysis", "anomaly_detection", "statistics", "monitoring"],
        "category": SkillCategory.ANALYSIS,
        "input_schema": IOSchema(
            description="Time series data",
            fields=[
                SchemaField(name="values", type="list[float]", description="Numeric time series"),
                SchemaField(name="z_threshold", type="float", description="Z-score threshold for anomaly (default 2.5)", required=False, default=2.5),
            ],
        ),
        "output_schema": IOSchema(
            description="Anomaly detection result",
            fields=[
                SchemaField(name="anomaly_indices", type="list[int]", description="Indices of detected anomalies"),
                SchemaField(name="anomaly_values", type="list[float]", description="Values at anomaly indices"),
                SchemaField(name="mean", type="float", description="Series mean"),
                SchemaField(name="std", type="float", description="Series std deviation"),
                SchemaField(name="anomaly_count", type="int", description="Number of anomalies detected"),
            ],
        ),
        "implementation": '''
def run(values: list, z_threshold: float = 2.5) -> dict:
    """Detects anomalies using Z-score method."""
    import math

    if len(values) < 3:
        return {"anomaly_indices": [], "anomaly_values": [], "mean": 0.0, "std": 0.0, "anomaly_count": 0}

    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 1e-10

    anomaly_indices = [i for i, v in enumerate(values) if abs((v - mean) / std) > z_threshold]
    anomaly_values = [values[i] for i in anomaly_indices]

    return {
        "anomaly_indices": anomaly_indices,
        "anomaly_values": anomaly_values,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "anomaly_count": len(anomaly_indices),
    }
'''.strip(),
    },
    {
        "name": "convert_currency",
        "description": "Converts an amount from one currency to another using a provided exchange rate.",
        "tags": ["finance", "currency", "conversion", "utility"],
        "category": SkillCategory.UTILITY,
        "input_schema": IOSchema(
            description="Currency conversion parameters",
            fields=[
                SchemaField(name="amount", type="float", description="Amount to convert"),
                SchemaField(name="from_currency", type="str", description="Source currency code e.g. 'USD'"),
                SchemaField(name="to_currency", type="str", description="Target currency code e.g. 'EUR'"),
                SchemaField(name="exchange_rate", type="float", description="Exchange rate (1 from_currency = X to_currency)"),
            ],
        ),
        "output_schema": IOSchema(
            description="Conversion result",
            fields=[
                SchemaField(name="converted_amount", type="float", description="Converted amount"),
                SchemaField(name="from_currency", type="str", description="Source currency"),
                SchemaField(name="to_currency", type="str", description="Target currency"),
                SchemaField(name="exchange_rate", type="float", description="Rate used"),
                SchemaField(name="formatted", type="str", description="Human-readable result string"),
            ],
        ),
        "implementation": '''
def run(amount: float, from_currency: str, to_currency: str, exchange_rate: float) -> dict:
    """Converts an amount using a provided exchange rate."""
    converted = round(amount * exchange_rate, 4)
    return {
        "converted_amount": converted,
        "from_currency": from_currency.upper(),
        "to_currency": to_currency.upper(),
        "exchange_rate": exchange_rate,
        "formatted": f"{amount} {from_currency.upper()} = {converted} {to_currency.upper()}",
    }
'''.strip(),
    },
]


async def seed() -> None:
    from axiom.registry.skill_store import skill_store

    logger.info(f"Seeding {len(SEED_SKILLS)} skills into AXIOM registry…")
    success = 0
    failed = 0

    for skill_data in SEED_SKILLS:
        try:
            skill = Skill(
                **skill_data,
                status=SkillStatus.ACTIVE,
                success_count=10,  # give seeds some initial trust
                invocation_count=10,
            )
            await skill_store.register_skill(skill, embed=True)
            logger.success(f"  ✓ {skill.name}")
            success += 1
        except Exception as exc:
            logger.error(f"  ✗ {skill_data['name']}: {exc}")
            failed += 1

    logger.info(f"Seeding complete: {success} succeeded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(seed())
