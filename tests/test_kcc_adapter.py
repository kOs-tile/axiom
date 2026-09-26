from axiom.integrations.kcc import (\n    build_kcc_authorization_bundle,\n    export_kcc_snapshot,\n    skill_chain_to_kcc_bundle,\n    skill_to_mcp_tool,\n)
from axiom.models import IOSchema, SchemaField, Skill, SkillChain, SkillStatus


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



def test_ready_skill_can_be_staged_for_kcc_without_authority():
    skill = make_skill("candidate_transform", SkillStatus.READY_FOR_AUTHORIZATION)
    bundle = build_kcc_authorization_bundle(
        [skill],
        task_description="Transform one bounded payload",
        ttl_seconds=120,
    )

    assert [tool["name"] for tool in bundle["snapshot"]["tools"]] == ["candidate_transform"]
    assert bundle["intent"]["capabilities"] == [
        "mcp:axiom-skills:candidate_transform"
    ]
    assert bundle["intent"]["ttl_seconds"] == 120
    assert bundle["authorization"]["granted"] is False
    assert bundle["authorization"]["requires_kcc_compile"] is True


def test_kcc_bundle_excludes_failed_or_draft_skills():
    active = make_skill("active_skill", SkillStatus.ACTIVE)
    ready = make_skill("ready_skill", SkillStatus.READY_FOR_AUTHORIZATION)
    failed = make_skill("failed_skill", SkillStatus.SANDBOX_FAILED)
    draft = make_skill("draft_skill", SkillStatus.DRAFT)

    bundle = build_kcc_authorization_bundle(
        [failed, draft, ready, active],
        task_description="Use only evaluated capability candidates",
    )

    names = [tool["name"] for tool in bundle["snapshot"]["tools"]]
    assert names == ["active_skill", "ready_skill"]
    assert len(bundle["intent"]["capabilities"]) == 2


def test_skill_chain_bundle_exposes_only_chain_surface():
    first = make_skill("fetch_bounded", SkillStatus.ACTIVE)
    second = make_skill("transform_bounded", SkillStatus.READY_FOR_AUTHORIZATION)
    chain = SkillChain(
        task_description="Fetch then transform bounded data",
        skills=[first, second],
        handoff_map=[{"value": "value"}],
    )

    bundle = skill_chain_to_kcc_bundle(chain, ttl_seconds=300)

    assert len(bundle["snapshot"]["tools"]) == 2
    assert bundle["intent"]["constraints"]["axiom_chain_id"] == chain.id
    assert bundle["intent"]["constraints"]["max_chain_length"] == 2
    assert bundle["authorization"]["granted"] is False
