"""
AXIOM Core Pydantic v2 Models.

All models are shared across registry, resolver, synthesis, sandbox, and API layers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ───────────────────────────────────────────────────────────────

class SkillStatus(str, Enum):
    DRAFT = "draft"
    SANDBOX_PENDING = "sandbox_pending"
    SANDBOX_FAILED = "sandbox_failed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    FLAGGED = "flagged"  # decay monitor flagged for review


class SkillCategory(str, Enum):
    TRADING = "trading"
    DATA_FETCH = "data_fetch"
    DATA_TRANSFORM = "data_transform"
    ANALYSIS = "analysis"
    NOTIFICATION = "notification"
    FORMATTING = "formatting"
    UTILITY = "utility"
    SYNTHESIS = "synthesis"  # meta: synthesized by AXIOM
    UNKNOWN = "unknown"


class SynthesisStep(str, Enum):
    ANALYZING = "analyzing"
    RETRIEVING_EXAMPLES = "retrieving_examples"
    CHECKING_DUPLICATES = "checking_duplicates"
    GENERATING_SCHEMA = "generating_schema"
    GENERATING_IMPLEMENTATION = "generating_implementation"
    GENERATING_TESTS = "generating_tests"
    SANDBOX_SECURITY_SCAN = "sandbox_security_scan"
    SANDBOX_EXECUTION = "sandbox_execution"
    PROMOTING = "promoting"
    COMPLETE = "complete"
    FAILED = "failed"


class SecurityIssueSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ── JSON Schema helpers ────────────────────────────────────────────────────────

class SchemaField(BaseModel):
    """A single field in a skill's input or output schema."""
    name: str
    type: str  # Python type hint string e.g. "str", "int", "list[dict]"
    description: str = ""
    required: bool = True
    default: Any = None
    example: Any = None


class IOSchema(BaseModel):
    """Typed input/output schema for a skill."""
    fields: list[SchemaField] = Field(default_factory=list)
    description: str = ""

    def type_signature(self) -> dict[str, str]:
        """Return {field_name: type_string} for graph matching."""
        return {f.name: f.type for f in self.fields}

    def is_compatible_with(self, other: "IOSchema") -> bool:
        """
        Returns True if this schema's outputs can satisfy the other schema's
        required inputs (subset match on field names and types).
        """
        my_types = self.type_signature()
        for field in other.fields:
            if field.required:
                if field.name not in my_types:
                    return False
                # Rough type compatibility: exact match or "Any"
                if my_types[field.name] not in (field.type, "Any", "any"):
                    if field.type not in ("Any", "any"):
                        return False
        return True


# ── Core Skill Model ───────────────────────────────────────────────────────────

class Skill(BaseModel):
    """A registered skill in the AXIOM marketplace."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=2, max_length=128)
    description: str = Field(..., min_length=10)
    tags: list[str] = Field(default_factory=list)
    category: SkillCategory = SkillCategory.UNKNOWN

    # Type-safe I/O contract
    input_schema: IOSchema = Field(default_factory=IOSchema)
    output_schema: IOSchema = Field(default_factory=IOSchema)

    # The actual Python implementation (function body as string)
    implementation: str = Field(default="")
    entry_point: str = Field(default="run", description="Name of the callable in implementation")

    # Metadata
    status: SkillStatus = SkillStatus.DRAFT
    version: str = "1.0.0"
    author: str = "axiom-synthesizer"
    hermes_compatible: bool = True

    # Performance metrics
    invocation_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    avg_latency_ms: float = Field(default=0.0, ge=0.0)
    success_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_invoked_at: Optional[datetime] = None
    promoted_at: Optional[datetime] = None

    # Vector embedding (not serialised to API responses by default)
    embedding: Optional[list[float]] = Field(default=None, exclude=True)

    @field_validator("tags")
    @classmethod
    def normalise_tags(cls, v: list[str]) -> list[str]:
        return [tag.lower().strip() for tag in v if tag.strip()]

    @model_validator(mode="after")
    def recompute_success_rate(self) -> "Skill":
        total = self.success_count + self.failure_count
        if total > 0:
            self.success_rate = self.success_count / total
        return self

    def to_registry_dict(self) -> dict[str, Any]:
        """Serialise for Supabase upsert."""
        d = self.model_dump(exclude={"embedding"})
        d["input_schema"] = self.input_schema.model_dump()
        d["output_schema"] = self.output_schema.model_dump()
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        if self.last_invoked_at:
            d["last_invoked_at"] = self.last_invoked_at.isoformat()
        if self.promoted_at:
            d["promoted_at"] = self.promoted_at.isoformat()
        return d


# ── Skill Chain ────────────────────────────────────────────────────────────────

class SkillChain(BaseModel):
    """An ordered sequence of skills that collectively solve a task."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_description: str
    skills: list[Skill]
    handoff_map: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {source_field: target_field} mappings between chain steps",
    )
    combined_success_rate: float = Field(default=0.0)
    estimated_latency_ms: float = Field(default=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def compute_chain_metrics(self) -> "SkillChain":
        if self.skills:
            rates = [s.success_rate for s in self.skills]
            # Joint probability for serial chain
            joint = 1.0
            for r in rates:
                joint *= r
            self.combined_success_rate = joint
            self.estimated_latency_ms = sum(s.avg_latency_ms for s in self.skills)
        return self


# ── Synthesis ─────────────────────────────────────────────────────────────────

class SynthesisRequest(BaseModel):
    """Request to synthesize a new skill."""

    task_description: str = Field(..., min_length=10)
    preferred_tags: list[str] = Field(default_factory=list)
    preferred_category: Optional[SkillCategory] = None
    example_input: Optional[dict[str, Any]] = None
    example_output: Optional[dict[str, Any]] = None
    force_synthesize: bool = Field(
        default=False,
        description="Skip deduplication check and force new synthesis",
    )
    requester: str = Field(default="hermes")


class SynthesisProgressEvent(BaseModel):
    """WebSocket streaming event during synthesis."""

    step: SynthesisStep
    message: str
    progress_pct: int = Field(default=0, ge=0, le=100)
    detail: Optional[dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SynthesisResult(BaseModel):
    """Final result of a synthesis run."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: Literal["success", "duplicate", "failed"]  # type: ignore[valid-type]
    skill: Optional[Skill] = None
    duplicate_of: Optional[Skill] = None
    evaluation_report: Optional["EvaluationReport"] = None
    steps_completed: list[SynthesisStep] = Field(default_factory=list)
    error_message: Optional[str] = None
    synthesis_duration_seconds: float = Field(default=0.0)

    # Keep Literal working with from __future__ import annotations
    model_config = {"arbitrary_types_allowed": True}


# Forward reference is rebuilt after EvaluationReport is defined.


# ── Sandbox / Evaluation ──────────────────────────────────────────────────────

class SecurityIssue(BaseModel):
    """A single issue reported by bandit or the AST permission checker."""

    severity: SecurityIssueSeverity
    confidence: str  # bandit confidence: HIGH/MEDIUM/LOW
    issue_id: str  # e.g. B101
    description: str
    line_number: int = 0
    code_snippet: str = ""


class TestCase(BaseModel):
    """An auto-generated test case for a synthesised skill."""

    name: str
    input_data: dict[str, Any]
    expected_output: Optional[Any] = None
    expected_type: Optional[str] = None  # Python type name


class TestResult(BaseModel):
    """Result of running a single test case."""

    test_case: TestCase
    passed: bool
    actual_output: Optional[Any] = None
    error_message: Optional[str] = None
    execution_time_ms: float = 0.0


class EvaluationReport(BaseModel):
    """Full sandbox evaluation report for a synthesised skill."""

    skill_id: str
    skill_name: str

    # Security scan
    bandit_passed: bool = False
    permission_check_passed: bool = False
    security_issues: list[SecurityIssue] = Field(default_factory=list)

    # Execution
    test_results: list[TestResult] = Field(default_factory=list)
    tests_passed: int = 0
    tests_failed: int = 0
    pass_rate: float = 0.0

    # Resource usage
    peak_memory_mb: float = 0.0
    total_execution_ms: float = 0.0

    # Verdict
    promotion_recommended: bool = False
    failure_reason: Optional[str] = None
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def compute_pass_rate(self) -> "EvaluationReport":
        total = self.tests_passed + self.tests_failed
        self.pass_rate = (self.tests_passed / total) if total > 0 else 0.0
        return self


# Resolve SynthesisResult.evaluation_report only after EvaluationReport exists.
SynthesisResult.model_rebuild()


# ── API Request / Response helpers ────────────────────────────────────────────

class ResolveRequest(BaseModel):
    task_description: str = Field(..., min_length=5)
    top_k: int = Field(default=5, ge=1, le=20)
    filter_tags: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ResolvedSkill(BaseModel):
    skill: Skill
    confidence: float = Field(..., ge=0.0, le=1.0)
    semantic_similarity: float
    rank: int


class ResolveResponse(BaseModel):
    task_description: str
    candidates: list[ResolvedSkill]
    total_searched: int
    resolved_in_ms: float


class ComposeRequest(BaseModel):
    task_description: str = Field(..., min_length=5)
    input_context: Optional[dict[str, Any]] = None
    max_chain_length: int = Field(default=5, ge=2, le=10)


class ComposeResponse(BaseModel):
    task_description: str
    chains: list[SkillChain]
    composed_in_ms: float


class SkillInvokeRequest(BaseModel):
    input_data: dict[str, Any]
    timeout_seconds: Optional[int] = None


class SkillInvokeResponse(BaseModel):
    skill_id: str
    skill_name: str
    output: Any
    success: bool
    execution_ms: float
    error_message: Optional[str] = None


class SkillMetrics(BaseModel):
    skill_id: str
    skill_name: str
    invocation_count: int
    success_rate: float
    avg_latency_ms: float
    last_invoked_at: Optional[datetime]
    status: SkillStatus
    decay_risk: bool = False
