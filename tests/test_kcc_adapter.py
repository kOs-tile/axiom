from axiom.integrations.kcc import export_kcc_snapshot, skill_to_mcp_tool
from axiom.models import IOSchema, SchemaField, Skill, SkillStatus


def make_skill(name: str, status: SkillStatus) -> Skill:
    return Skill(
        name=name,
        description=f"Skill {name} performs a bounded test transformation.",
        status=status,
        input_schema=IOSchema(
            fields=[
                SchemaField(name="value", type="float", required=True),
                SchemaField(name="label", type="str", required=False, default="x"),
            ]
        ),
    )


def test_snapshot_exports_only_active_skills_by_default():
    active = make_skill("active_skill", SkillStatus.ACTIVE)
    draft = make_skill("draft_skill", SkillStatus.DRAFT)
    snapshot = export_kcc_snapshot([draft, active])
    assert snapshot["server"]["name"] == "axiom-skills"
    assert [tool["name"] for tool in snapshot["tools"]] == ["active_skill"]


def test_adapter_preserves_schema_without_claiming_authority():
    skill = make_skill("bounded_transform", SkillStatus.ACTIVE)
    tool = skill_to_mcp_tool(skill)
    assert tool["annotations"] == {}
    schema = tool["inputSchema"]
    assert schema["properties"]["value"]["type"] == "number"
    assert schema["properties"]["label"]["type"] == "string"
    assert schema["required"] == ["value"]
    assert schema["additionalProperties"] is False


def test_unknown_python_type_is_preserved_as_extension():
    skill = Skill(
        name="custom_type_skill",
        description="Skill that accepts a custom domain-specific typed object.",
        status=SkillStatus.ACTIVE,
        input_schema=IOSchema(
            fields=[SchemaField(name="payload", type="PriceFrame", required=True)]
        ),
    )
    tool = skill_to_mcp_tool(skill)
    assert "type" not in tool["inputSchema"]["properties"]["payload"]
    assert tool["inputSchema"]["properties"]["payload"]["x-axiom-python-type"] == "PriceFrame"
