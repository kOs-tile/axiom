"""KCC interoperability adapter.

AXIOM does not grant runtime authority. This module exports promoted AXIOM skills
as an MCP-shaped observed capability snapshot that KAVI Capability Compiler can
scan, classify, audit, and bind into a task-scoped execution capsule.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from axiom.models import IOSchema, Skill, SkillChain, SkillStatus


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_JSON_TYPE_MAP = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "dict": "object",
    "object": "object",
    "list": "array",
    "tuple": "array",
    "set": "array",
}


def _json_type(type_hint: str) -> str | None:
    normalized = type_hint.strip().lower()
    if normalized.startswith(("list[", "tuple[", "set[")):
        return "array"
    if normalized.startswith(("dict[", "mapping[")):
        return "object"
    return _JSON_TYPE_MAP.get(normalized)


def ioschema_to_json_schema(schema: IOSchema) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for field in schema.fields:
        spec: dict[str, Any] = {}
        json_type = _json_type(field.type)
        if json_type:
            spec["type"] = json_type
        if field.description:
            spec["description"] = field.description
        if field.default is not None:
            spec["default"] = field.default
        if field.example is not None:
            spec["examples"] = [field.example]
        if not json_type:
            spec["x-axiom-python-type"] = field.type
        properties[field.name] = spec
        if field.required:
            required.append(field.name)

    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    if schema.description:
        result["description"] = schema.description
    return result


def skill_to_mcp_tool(skill: Skill) -> dict[str, Any]:
    """Export one skill as observed capability evidence, without authority hints."""
    return {
        "name": skill.name,
        "description": skill.description,
        "inputSchema": ioschema_to_json_schema(skill.input_schema),
        "annotations": {},
        "x-axiom": {
            "skill_id": skill.id,
            "version": skill.version,
            "category": skill.category.value,
            "status": skill.status.value,
            "entry_point": skill.entry_point,
            "success_rate": skill.success_rate,
        },
    }


def export_kcc_snapshot(
    skills: Iterable[Skill],
    *,
    server_name: str = "axiom-skills",
    include_non_active: bool = False,
) -> dict[str, Any]:
    """Build the MCP-shaped snapshot accepted by KCC scan_mcp_snapshot.

    By default only promoted ACTIVE skills are visible to the authority compiler.
    Draft, failed, deprecated, and flagged skills remain outside the observed
    execution surface unless explicitly requested for analysis.
    """
    selected = [
        skill
        for skill in skills
        if include_non_active or skill.status == SkillStatus.ACTIVE
    ]
    selected.sort(key=lambda skill: (skill.name, skill.id))
    return {
        "server": {"name": server_name},
        "tools": [skill_to_mcp_tool(skill) for skill in selected],
    }



def _kcc_capability_id(skill: Skill, server_name: str) -> str:
    """Return the deterministic capability ID KCC assigns to this MCP-shaped tool."""
    return f"mcp:{server_name}:{skill.name}"


def build_kcc_authorization_bundle(
    skills: Iterable[Skill],
    *,
    task_description: str,
    server_name: str = "axiom-skills",
    ttl_seconds: int = 900,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare the smallest AXIOM capability surface for KCC compilation.

    A sandbox-passing skill is only code evidence. This bundle deliberately grants
    no authority. KCC must scan/audit/compile the returned snapshot before an
    execution capsule exists.
    """
    selected = [
        skill for skill in skills
        if skill.status in {
            SkillStatus.ACTIVE,
            SkillStatus.READY_FOR_AUTHORIZATION,
        }
    ]
    selected.sort(key=lambda skill: (skill.name, skill.id))
    snapshot = {
        "server": {"name": server_name},
        "tools": [skill_to_mcp_tool(skill) for skill in selected],
    }
    intent_constraints = {
        "axiom_skill_ids": [skill.id for skill in selected],
        "axiom_skill_versions": {
            skill.id: skill.version for skill in selected
        },
    }
    if constraints:
        intent_constraints.update(constraints)

    intent = {
        "task": task_description,
        "capabilities": [
            _kcc_capability_id(skill, server_name) for skill in selected
        ],
        "ttl_seconds": max(1, min(int(ttl_seconds), 86400)),
        "constraints": intent_constraints,
    }
    authorization = {
        "granted": False,
        "requires_kcc_compile": True,
        "reason": (
            "AXIOM selected and evaluated capability code; "
            "execution authority remains a KCC decision."
        ),
    }
    planning = {
        "version": "axiom.capability-plan.v0",
        "snapshot_digest": _digest(snapshot),
        "intent_digest": _digest(intent),
        "selected_skill_ids": [skill.id for skill in selected],
        "selected_skill_versions": {
            skill.id: skill.version for skill in selected
        },
        "authority_granted": False,
    }
    planning["plan_fingerprint"] = _digest(planning)

    return {
        "snapshot": snapshot,
        "intent": intent,
        "planning": planning,
        "authorization": authorization,
    }


def skill_chain_to_kcc_bundle(
    chain: SkillChain,
    *,
    server_name: str = "axiom-skills",
    ttl_seconds: int = 900,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert one composed chain into a least-surface KCC authorization request."""
    chain_constraints = {
        "axiom_chain_id": chain.id,
        "max_chain_length": len(chain.skills),
        "handoff_map": chain.handoff_map,
    }
    if constraints:
        chain_constraints.update(constraints)
    return build_kcc_authorization_bundle(
        chain.skills,
        task_description=chain.task_description,
        server_name=server_name,
        ttl_seconds=ttl_seconds,
        constraints=chain_constraints,
    )



def verify_kcc_authorization_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Verify AXIOM planning evidence without treating it as authority."""
    planning = bundle.get("planning") or {}
    body = dict(planning)
    claimed = body.pop("plan_fingerprint", None)
    snapshot = bundle.get("snapshot") or {}
    intent = bundle.get("intent") or {}
    authorization = bundle.get("authorization") or {}

    checks = [
        (
            "planning_version",
            body.get("version") == "axiom.capability-plan.v0",
        ),
        ("plan_integrity", claimed == _digest(body)),
        ("snapshot_digest", body.get("snapshot_digest") == _digest(snapshot)),
        ("intent_digest", body.get("intent_digest") == _digest(intent)),
        ("planning_non_authority", body.get("authority_granted") is False),
        ("bundle_non_authority", authorization.get("granted") is False),
        (
            "requires_kcc_compile",
            authorization.get("requires_kcc_compile") is True,
        ),
    ]
    return {
        "valid": all(ok for _, ok in checks),
        "plan_fingerprint": claimed,
        "checks": [{"name": name, "ok": ok} for name, ok in checks],
    }
