# SKILL_SCHEMA.md — AXIOM Skill Format Reference

AXIOM uses a Hermes-compatible skill schema with typed I/O contracts and a rich
metadata envelope that enables semantic search, composition, and performance tracking.

---

## Skill Object

```json
{
  "id":           "uuid-v4",
  "name":         "compute_rsi",
  "description":  "Computes the Relative Strength Index for a list of closing prices.",
  "tags":         ["trading", "rsi", "technical_analysis"],
  "category":     "analysis",

  "input_schema": {
    "description": "Price data and RSI parameters",
    "fields": [
      {
        "name":        "prices",
        "type":        "list[float]",
        "description": "List of closing prices (minimum 15 values)",
        "required":    true,
        "example":     [100.0, 102.5, 99.8, 103.1]
      },
      {
        "name":        "period",
        "type":        "int",
        "description": "RSI period (default 14)",
        "required":    false,
        "default":     14,
        "example":     14
      }
    ]
  },

  "output_schema": {
    "description": "RSI calculation result",
    "fields": [
      { "name": "rsi",         "type": "float",  "description": "RSI value 0–100",   "required": true },
      { "name": "overbought",  "type": "bool",   "description": "RSI > 70",          "required": true },
      { "name": "oversold",    "type": "bool",   "description": "RSI < 30",          "required": true },
      { "name": "period",      "type": "int",    "description": "Period used",        "required": true }
    ]
  },

  "implementation": "def run(prices: list, period: int = 14) -> dict:\n    ...",
  "entry_point":    "run",

  "status":     "active",
  "version":    "1.0.0",
  "author":     "axiom-synthesizer",
  "hermes_compatible": true,

  "invocation_count": 142,
  "success_count":    138,
  "failure_count":    4,
  "avg_latency_ms":   12.4,
  "success_rate":     0.972,

  "created_at":       "2025-01-15T10:23:00Z",
  "updated_at":       "2025-06-20T08:11:00Z",
  "last_invoked_at":  "2025-06-29T00:30:00Z",
  "promoted_at":      "2025-01-15T10:28:00Z"
}
```

---

## Field Reference

### Metadata Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | `string (UUID)` | auto | Unique identifier, auto-generated |
| `name` | `string` | ✓ | Snake_case name, 2–128 chars |
| `description` | `string` | ✓ | Human-readable description used for semantic embedding |
| `tags` | `list[string]` | — | Lowercase tags for tag-intersection filtering |
| `category` | `enum` | — | See categories below |
| `status` | `enum` | auto | Lifecycle status |
| `version` | `string` | — | Semver string |
| `author` | `string` | — | Creator identifier |
| `hermes_compatible` | `bool` | — | Whether this skill follows the Hermes call convention |
| `entry_point` | `string` | — | Name of the callable in `implementation` (default: `run`) |

### Status Values

| Status | Description |
|---|---|
| `draft` | Registered but not yet evaluated |
| `sandbox_pending` | Awaiting sandbox evaluation |
| `sandbox_failed` | Failed security scan or unit tests |
| `ready_for_authorization` | Evaluation passed; no runtime authority has been granted yet |
| `active` | Explicitly promoted and available for use |
| `deprecated` | Not invoked in 30+ days |
| `flagged` | Success rate dropped below 70% threshold |

### Category Values

| Category | Description |
|---|---|
| `trading` | Trading algorithms, order management |
| `data_fetch` | External data retrieval |
| `data_transform` | Data parsing, normalisation, conversion |
| `analysis` | Statistical analysis, ML, indicators |
| `notification` | Alerts, messaging, webhooks |
| `formatting` | Output formatting (Markdown, JSON, HTML) |
| `utility` | General-purpose helpers |
| `synthesis` | Meta-skills synthesized by AXIOM |
| `unknown` | Category not determined |

---

## IOSchema Field Reference

Each field in `input_schema.fields` and `output_schema.fields`:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | ✓ | Field identifier |
| `type` | `string` | ✓ | Python type hint string (see type strings below) |
| `description` | `string` | — | Human-readable field description |
| `required` | `bool` | — | Whether the field is required (default: `true`) |
| `default` | `any` | — | Default value if not required |
| `example` | `any` | — | Example value for documentation |

### Supported Type Strings

```
str          int          float        bool
list[str]    list[int]    list[float]  list[dict]
dict         dict[str, Any]            dict[str, float]
Optional[str]             Optional[float]
Any          any
```

---

## Implementation Contract

The `implementation` field must contain a valid Python source string with a
callable named (by default) `run`. The function must:

1. Accept keyword arguments matching `input_schema.fields`
2. Return a `dict` with keys matching `output_schema.fields`
3. Handle errors gracefully — never raise unhandled exceptions

**Example:**

```python
def run(prices: list, period: int = 14) -> dict:
    """Computes RSI for a price series."""
    if len(prices) < period + 1:
        return {"rsi": 50.0, "overbought": False, "oversold": False, "period": period}

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    rs  = avg_gain / avg_loss if avg_loss else float('inf')
    rsi = round(100 - (100 / (1 + rs)), 2)

    return {"rsi": rsi, "overbought": rsi > 70, "oversold": rsi < 30, "period": period}
```

---

## Local Hermes Skill Files (SKILL_METADATA)

AXIOM's `HermesSkillAdapter` auto-ingests `.py` files from your local Hermes skill
directory. Add a `SKILL_METADATA` dict for richer metadata:

```python
# skills/compute_rsi.py

SKILL_METADATA = {
    "name":        "compute_rsi",
    "description": "Computes RSI for a list of closing prices.",
    "tags":        ["trading", "rsi"],
    "category":    "analysis",
}

def run(prices: list, period: int = 14) -> dict:
    """Computes Relative Strength Index."""
    ...
```

Without `SKILL_METADATA`, the adapter uses the `run` function's docstring as the
description and the filename (minus `.py`) as the skill name.

---

## Skill Composition I/O Compatibility

The `SkillCompositionEngine` chains skills by matching output fields to input fields.
Two adjacent skills are compatible if, for every **required** input field of `Skill[i+1]`,
there exists a field in `Skill[i]`'s output with:

1. The **same name**, OR
2. The **same type** (fallback positional match)

The `Any` type always matches any other type.

**Example compatible chain:**

```
fetch_prices         → compute_sma         → format_markdown
output: prices:list    input: prices:list    input: sma:float
        symbol:str     output: sma:float     output: markdown:str
                               window:int
```

---

## Performance Metrics

AXIOM tracks performance on every invocation:

| Metric | Description |
|---|---|
| `invocation_count` | Total times the skill has been called |
| `success_count` | Successful completions |
| `failure_count` | Failed executions |
| `success_rate` | `success_count / (success_count + failure_count)` |
| `avg_latency_ms` | Exponential moving average of execution time (α=0.1) |

### Decay Thresholds

| Condition | Action |
|---|---|
| `invocation_count >= 50` AND `success_rate < 0.70` | Status → `flagged` |
| Not invoked in 30 days | Status → `deprecated` |
