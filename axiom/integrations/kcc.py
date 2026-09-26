"""KCC interoperability adapter.

AXIOM does not grant runtime authority. This module exports promoted AXIOM skills
as an MCP-shaped observed capability snapshot that KAVI Capability Compiler can
scan, classify, audit, and bind into a task-scoped execution capsule.
"""

from __future__ import annotations

from typing import Any, Iterable

from axiom.models import IOSchema, Skill, SkillStatus


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
