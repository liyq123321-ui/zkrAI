import asyncio
import json

import pytest

from app.ai.codex_provider import (
    CodexProvider,
    authoring_skills,
    build_operation_instructions,
    group_independent_revision_actions,
    normalize_revision_plan,
    normalize_revision_result,
    revision_skill_route,
)
from app.ai.provider import (
    BlockResult,
    DocumentReview,
    DocumentReviewIssue,
    DocumentRevision,
    DocumentRevisionAction,
    DocumentRevisionBlock,
    DocumentRevisionPlan,
    SpecFragment,
)
from app.services.ai_service import compact_document_context
from app.agents.progress import bind_agent_progress


def test_block_generation_uses_native_docx_authoring_rules():
    instructions = build_operation_instructions("generate", {})

    assert "The final publishing surface is DOCX" in instructions
    assert "native Microsoft Word DOCX" in instructions
    assert "visible `#`, `##`, `**`" in instructions
    assert "DOCX image relationship" in instructions
    assert "internal interchange format" in instructions
    assert "user's latest editing instruction" in instructions


def test_document_revision_uses_docx_rules_but_read_only_review_does_not():
    revision = build_operation_instructions("document_revision", {})
    review = build_operation_instructions("document_review", {})

    assert "<DOCX_AUTHORING_RULES>" in revision
    assert "<DOCX_AUTHORING_RULES>" not in review
    assert "do not return replacement content" in review


def test_diagram_skill_is_routed_only_when_the_block_contains_visual_evidence():
    assert "drawio-skill" not in authoring_skills({"title": "项目背景"})
    assert "drawio-skill" in authoring_skills({"title": "核心数据模型与实体关系"})


def test_local_revision_skill_routes_match_the_edited_material():
    base = {"content": "第一段\n第二段", "line_range": {"start_line": 2, "end_line": 2}}
    assert revision_skill_route({**base, "feedback": "只润色这段，不改变含义"}) == (
        "text_style", ("create-prd",),
    )
    assert revision_skill_route({**base, "feedback": "这段的权限需求增加验收条件"})[0] == "requirement"
    assert revision_skill_route({**base, "feedback": "把这段的上限改为20人"})[0] == "requirement"
    assert revision_skill_route({**base, "feedback": "只修改这张图的图注说明"})[0] == "text_local"
    assert revision_skill_route({
        **base, "content": "```mermaid\nflowchart LR\nA-->B\n```",
        "feedback": "把这张流程图的标签改清楚并美化",
    }) == ("visual_mermaid", ("mermaid-skill", "design-system", "diagram-design"))
    assert revision_skill_route({
        "content": "说明\n```mermaid\nA-->B\n```",
        "line_range": {"start_line": 3, "end_line": 3}, "feedback": "把选中标签改清楚",
    })[0] == "visual_mermaid"
    assert revision_skill_route({
        **base, "content": "![配图](/assets/example.png)", "feedback": "修改这张配图的颜色",
    }) == ("visual_asset", ())
    assert revision_skill_route({**base, "feedback": "全文所有章节都重新审核"}) == ("full", ())
    assert revision_skill_route({
        "content": "原有图示说明", "feedback": "产品概念设计里的信息结构图和产品结构图请重新生成",
    }) == ("visual_mermaid", ("mermaid-skill", "design-system"))


@pytest.mark.asyncio
async def test_local_text_revision_uses_one_skill_pass():
    provider = CodexProvider.__new__(CodexProvider)
    provider.runner = RecordingRunner()
    await provider.revise_block({
        "block_id": "block-1", "base_version": 1, "content": "原文",
        "feedback": "只润色这段，不改变含义", "line_range": {"start_line": 1, "end_line": 1},
    })
    assert len(provider.runner.calls) == 1
    assert provider.runner.calls[0][2] == ("create-prd",)


@pytest.mark.asyncio
async def test_generation_reports_real_skill_pass_boundaries():
    provider = CodexProvider.__new__(CodexProvider)
    provider.runner = RecordingRunner()
    progress = []
    with bind_agent_progress(lambda stage, message: progress.append((stage, message))):
        await provider.generate_block({"block_id": "block-1", "base_version": 1, "content": ""})
    steps = [message for stage, message in progress if stage == "workflow_step"]
    assert steps == [
        "正在生成章节初稿…",
        "生成章节初稿已完成，正在进入下一阶段。",
    ]


class RecordingRunner:
    def __init__(self, duplicate_plan=False):
        self.calls = []
        self.duplicate_plan = duplicate_plan

    async def run(self, prompt, contract, _cwd, *, bundled_skills=(), **_kwargs):
        self.calls.append((prompt, contract, bundled_skills))
        if contract is BlockResult:
            return BlockResult(
                block_id="block-1",
                base_version=1,
                content="合格正文",
                summary="摘要",
                affected_blocks=[],
                spec_fragment=SpecFragment(),
            )
        if contract is DocumentRevisionPlan:
            actions = [DocumentRevisionAction(
                block_id="block-1", base_version=1, issue_numbers=[1],
                instruction="统一需求并补全验收标准", preserve=["FR-001"],
                requirement_ids=["FR-001"],
            )]
            if self.duplicate_plan:
                actions.append(DocumentRevisionAction(
                    block_id="block-1", base_version=1, issue_numbers=[1],
                    instruction="补充异常路径验收", preserve=["现有范围"],
                    requirement_ids=["FR-001"],
                ))
            return DocumentRevisionPlan(
                document_id="doc-1", base_revision=1, summary="修订计划",
                actions=actions,
            )
        if contract is DocumentRevision:
            return DocumentRevision(
                document_id="doc-1", base_revision=1, summary="已按计划修订",
                blocks=[DocumentRevisionBlock(
                    block_id="block-1", base_version=1, content="修订后正文",
                    summary="完成修订", spec_fragment=SpecFragment(),
                )],
            )
        issue = DocumentReviewIssue(
            severity="warning" if "requirement-registry" in prompt else "blocker",
            block_ids=["block-1"],
            description="需求重复" if "requirement-registry" in prompt else "范围边界不清",
            suggestion="合并需求" if "requirement-registry" in prompt else "明确边界",
        )
        return DocumentReview(
            document_id="doc-1", base_revision=1,
            summary="注册表审核" if "requirement-registry" in prompt else "质量审核",
            issues=[issue],
        )


@pytest.mark.asyncio
async def test_block_generation_does_not_append_an_automatic_quality_review():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner()

    result = await provider.generate_block({
        "block_id": "block-1", "base_version": 1, "content": "", "title": "核心数据模型",
    })

    assert result.content == "合格正文"
    assert [call[2] for call in provider.runner.calls] == [
        ("create-prd", "deliver-acceptance-criteria", "drawio-skill"),
    ]
    assert "$create-prd" in provider.runner.calls[0][0]
    assert len(provider.runner.calls) == 1


@pytest.mark.asyncio
async def test_document_review_runs_two_skill_specialists_and_merges_findings():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner()

    review = await provider.review_document({
        "document_id": "doc-1", "base_revision": 1, "blocks": [],
    })

    assert len(provider.runner.calls) == 2
    assert {call[2] for call in provider.runner.calls} == {
        ("requirement-registry-skill", "deliver-acceptance-criteria"),
        ("prd-critique", "prd-consistency-skill", "traceability-skill"),
    }
    assert [issue.severity for issue in review.issues] == ["blocker", "warning"]


@pytest.mark.asyncio
async def test_automatic_revision_uses_a_bounded_skill_plan_then_one_write_pass():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner()
    context = {
        "document_id": "doc-1", "base_revision": 1, "title": "PRD",
        "review": {"document_id": "doc-1", "base_revision": 1, "summary": "问题",
                   "issues": [{"severity": "warning", "block_ids": ["block-1"],
                               "description": "需求重复", "suggestion": "合并需求"}]},
        "blocks": [{"id": "block-1", "version": 1, "title": "需求", "content": "原文"}],
        "document_map": [{"id": "block-1", "title": "需求", "summary": "摘要"}],
    }

    revision = await provider.revise_document(context)

    assert revision.blocks[0].content == "修订后正文"
    assert len(provider.runner.calls) == 2
    assert provider.runner.calls[0][1] is DocumentRevisionPlan
    assert provider.runner.calls[1][1] is DocumentRevision
    assert "原文" not in provider.runner.calls[0][0]
    assert "原文" in provider.runner.calls[1][0]
    assert "revision_plan" in provider.runner.calls[1][0]


@pytest.mark.asyncio
async def test_automatic_revision_repairs_only_missing_planned_blocks():
    class PartialRunner(RecordingRunner):
        async def run(self, prompt, contract, cwd, **kwargs):
            if contract is DocumentRevisionPlan:
                self.calls.append((prompt, contract, kwargs.get("bundled_skills", ())))
                return DocumentRevisionPlan(document_id="doc-1", base_revision=1, summary="计划", actions=[
                    DocumentRevisionAction(block_id=block_id, base_version=1, issue_numbers=[1],
                                           instruction="统一两处说明", preserve=[], requirement_ids=[])
                    for block_id in ("block-1", "block-2")
                ])
            if contract is DocumentRevision:
                self.calls.append((prompt, contract, kwargs.get("bundled_skills", ())))
                payload = json.loads(prompt.split("<business_data>\n", 1)[1].split("\n</business_data>", 1)[0])
                block_id = payload["revision_plan"]["actions"][0]["block_id"]
                return DocumentRevision(document_id="doc-1", base_revision=1, summary="已修订", blocks=[
                    DocumentRevisionBlock(block_id=block_id, base_version=1, content=f"{block_id} 已修订",
                                          summary="完成", spec_fragment=SpecFragment())
                ])
            return await super().run(prompt, contract, cwd, **kwargs)

    provider = object.__new__(CodexProvider)
    provider.runner = PartialRunner()
    context = {
        "document_id": "doc-1", "base_revision": 1, "title": "PRD",
        "review": {"document_id": "doc-1", "base_revision": 1, "summary": "两处不一致", "issues": [
            {"severity": "warning", "block_ids": ["block-1", "block-2"],
             "description": "说明不一致", "suggestion": "统一说明"},
        ]},
        "blocks": [{"id": block_id, "version": 1, "title": block_id, "content": "原文"}
                   for block_id in ("block-1", "block-2")],
        "document_map": [{"id": block_id, "title": block_id, "summary": "原文"}
                         for block_id in ("block-1", "block-2")],
    }
    revision = await provider.revise_document(context)
    assert [block.block_id for block in revision.blocks] == ["block-1", "block-2"]
    assert len(provider.runner.calls) == 3
    repair_payload = json.loads(
        provider.runner.calls[2][0].split("<business_data>\n", 1)[1].split("\n</business_data>", 1)[0]
    )
    assert [block["id"] for block in repair_payload["blocks"]] == ["block-2"]
    assert [action["block_id"] for action in repair_payload["revision_plan"]["actions"]] == ["block-2"]


def test_revision_groups_keep_shared_issues_and_dependencies_together():
    actions = [DocumentRevisionAction(
        block_id=block_id, base_version=1, issue_numbers=numbers,
        instruction="修订", preserve=[], requirement_ids=requirements,
    ) for block_id, numbers, requirements in (
        ("A", [1], []), ("B", [1], []), ("C", [2], ["FR-001"]),
        ("D", [3], ["FR-001"]), ("E", [4], []),
    )]
    document_map = [{"id": block_id, "parent_id": None, "dependencies": []}
                    for block_id in ("A", "B", "C", "D", "E")]
    groups = group_independent_revision_actions(actions, document_map)
    assert [[action.block_id for action in group] for group in groups] == [["A", "B"], ["C", "D"], ["E"]]
    document_map[-1]["parent_id"] = "D"
    groups = group_independent_revision_actions(actions, document_map)
    assert [[action.block_id for action in group] for group in groups] == [["A", "B"], ["C", "D", "E"]]


@pytest.mark.asyncio
async def test_independent_revision_groups_run_in_parallel_with_a_two_agent_limit():
    class ParallelRunner:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.calls = []

        async def run(self, prompt, contract, _cwd, *, bundled_skills=(), **_kwargs):
            self.calls.append((contract, bundled_skills))
            if contract is DocumentRevisionPlan:
                return DocumentRevisionPlan(document_id="doc-1", base_revision=1, summary="计划", actions=[
                    DocumentRevisionAction(block_id=f"block-{number}", base_version=1,
                                           issue_numbers=[number], instruction="修订", preserve=[], requirement_ids=[])
                    for number in (1, 2, 3)
                ])
            payload = json.loads(prompt.split("<business_data>\n", 1)[1].split("\n</business_data>", 1)[0])
            assert len(payload["blocks"]) == len(payload["revision_plan"]["actions"]) == 1
            assert len(payload["review"]["issues"]) == len(payload["review_issue_numbers"]) == 1
            assert payload["review_issue_numbers"] == payload["revision_plan"]["actions"][0]["issue_numbers"]
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.03)
            finally:
                self.active -= 1
            block_id = payload["blocks"][0]["id"]
            return DocumentRevision(document_id="doc-1", base_revision=1, summary=block_id, blocks=[
                DocumentRevisionBlock(block_id=block_id, base_version=1, content=f"{block_id} 已修订",
                                      summary="完成", spec_fragment=SpecFragment()),
            ])

    provider = object.__new__(CodexProvider)
    provider.runner = ParallelRunner()
    context = {
        "document_id": "doc-1", "base_revision": 1, "title": "PRD",
        "review": {"document_id": "doc-1", "base_revision": 1, "summary": "三个独立问题", "issues": [
            {"severity": "warning", "block_ids": [f"block-{number}"],
             "description": f"问题 {number}", "suggestion": "修订"}
            for number in (1, 2, 3)
        ]},
        "blocks": [{"id": f"block-{number}", "version": 1, "title": str(number), "content": "原文"}
                   for number in (1, 2, 3)],
        "document_map": [{"id": f"block-{number}", "parent_id": None, "dependencies": [], "summary": "原文"}
                         for number in (1, 2, 3)],
    }
    revision = await provider.revise_document(context)
    assert provider.runner.peak == 2
    assert [block.block_id for block in revision.blocks] == ["block-1", "block-2", "block-3"]
    assert len(provider.runner.calls) == 4  # one plan, three isolated write passes


@pytest.mark.asyncio
async def test_duplicate_block_actions_are_merged_before_the_write_pass():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner(duplicate_plan=True)
    context = {
        "document_id": "doc-1", "base_revision": 1, "title": "PRD",
        "review": {"document_id": "doc-1", "base_revision": 1, "summary": "问题",
                   "issues": [{"severity": "warning", "block_ids": ["block-1"],
                               "description": "需求重复", "suggestion": "合并需求"}]},
        "blocks": [{"id": "block-1", "version": 1, "title": "需求", "content": "原文"}],
        "document_map": [{"id": "block-1", "title": "需求", "summary": "摘要"}],
    }

    revision = await provider.revise_document(context)
    payload = json.loads(
        provider.runner.calls[1][0].split("<business_data>\n", 1)[1].split("\n</business_data>", 1)[0]
    )
    action = payload["revision_plan"]["actions"][0]

    assert revision.blocks[0].block_id == "block-1"
    assert len(payload["revision_plan"]["actions"]) == 1
    assert "统一需求并补全验收标准" in action["instruction"]
    assert "补充异常路径验收" in action["instruction"]
    assert action["preserve"] == ["FR-001", "现有范围"]


def test_revision_plan_repairs_minor_skill_scope_and_metadata_drift():
    plan = DocumentRevisionPlan(
        document_id="copied-wrong-id", base_revision=99, summary="修订计划",
        actions=[
            DocumentRevisionAction(
                block_id="block-2", base_version=99, issue_numbers=[99],
                instruction="同步验收映射", preserve=[], requirement_ids=["FR-001", "FR-1"],
            ),
            DocumentRevisionAction(
                block_id="block-3", base_version=1, issue_numbers=[1],
                instruction="无关修改", preserve=[], requirement_ids=[],
            ),
        ],
    )

    normalized = normalize_revision_plan(
        plan, document_id="doc-1", base_revision=3,
        known_versions={"block-1": 1, "block-2": 2, "block-3": 1},
        review_issues=[{
            "severity": "warning", "block_ids": ["block-1"],
            "description": "FR-001 的验收映射不完整", "suggestion": "同步验收映射",
        }],
        document_map=[
            {"id": "block-1", "parent_id": None, "dependencies": []},
            {"id": "block-2", "parent_id": None, "dependencies": ["block-1"]},
            {"id": "block-3", "parent_id": None, "dependencies": []},
        ],
    )

    assert normalized.document_id == "doc-1"
    assert normalized.base_revision == 3
    assert [action.block_id for action in normalized.actions] == ["block-2"]
    assert normalized.actions[0].base_version == 2
    assert normalized.actions[0].issue_numbers == [1]
    assert normalized.actions[0].requirement_ids == ["FR-001"]


def test_revision_result_keeps_only_approved_blocks_and_normalizes_versions():
    plan = DocumentRevisionPlan(
        document_id="doc-1", base_revision=3, summary="计划",
        actions=[DocumentRevisionAction(
            block_id="block-1", base_version=2, issue_numbers=[1],
            instruction="修订", preserve=[], requirement_ids=[],
        )],
    )
    revision = DocumentRevision(
        document_id="wrong", base_revision=99, summary="完成",
        blocks=[
            DocumentRevisionBlock(
                block_id="block-2", base_version=1, content="越界", summary="越界", spec_fragment=SpecFragment(),
            ),
            DocumentRevisionBlock(
                block_id="block-1", base_version=99, content="允许", summary="允许", spec_fragment=SpecFragment(),
            ),
        ],
    )

    normalized = normalize_revision_result(revision, plan, {"block-1": 2, "block-2": 1})

    assert normalized.document_id == "doc-1"
    assert normalized.base_revision == 3
    assert [(block.block_id, block.base_version, block.content) for block in normalized.blocks] == [
        ("block-1", 2, "允许"),
    ]


@pytest.mark.asyncio
async def test_automatic_revision_verification_uses_one_targeted_skill_call():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner()

    await provider.review_document({
        "document_id": "doc-1", "base_revision": 1, "blocks": [],
        "auto_resolution_verification": True,
    })

    assert len(provider.runner.calls) == 1
    assert provider.runner.calls[0][2] == (
        "requirement-registry-skill", "prd-consistency-skill", "prd-critique",
        "traceability-skill", "deliver-acceptance-criteria",
    )


def test_revision_context_keeps_target_prose_and_compacts_other_blocks():
    review = DocumentReview(
        document_id="doc-1", base_revision=1, summary="问题",
        issues=[DocumentReviewIssue(
            severity="warning", block_ids=["block-1"],
            description="需求重复", suggestion="合并需求",
        )],
    )
    candidates = {
        "block-1": {"id": "block-1", "title": "需求", "parent_id": None, "order": 1,
                    "version": 1, "content": "目标完整正文", "dependencies": [], "summary": "目标摘要"},
        "block-2": {"id": "block-2", "title": "背景", "parent_id": None, "order": 2,
                    "version": 1, "content": "不应发送的非目标完整正文", "dependencies": [], "summary": "背景摘要"},
    }

    compact = compact_document_context(
        {"document_id": "doc-1", "base_revision": 1, "title": "PRD"},
        candidates, review, ["block-1"],
    )

    assert [block["id"] for block in compact["blocks"]] == ["block-1"]
    assert "不应发送的非目标完整正文" not in str(compact)
    assert compact["document_map"][1]["summary"] == "背景摘要"
