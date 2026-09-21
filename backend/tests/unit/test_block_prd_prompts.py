import json

import pytest

from app.ai.codex_provider import (
    CodexProvider,
    authoring_skills,
    build_operation_instructions,
    normalize_revision_plan,
    normalize_revision_result,
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


def test_block_generation_uses_native_docx_authoring_rules():
    instructions = build_operation_instructions("generate", {})

    assert "The final publishing surface is DOCX" in instructions
    assert "native Microsoft Word DOCX" in instructions
    assert "visible `#`, `##`, `**`" in instructions
    assert "DOCX image relationship" in instructions
    assert "internal interchange format" in instructions


def test_document_revision_uses_docx_rules_but_read_only_review_does_not():
    revision = build_operation_instructions("document_revision", {})
    review = build_operation_instructions("document_review", {})

    assert "<DOCX_AUTHORING_RULES>" in revision
    assert "<DOCX_AUTHORING_RULES>" not in review
    assert "do not return replacement content" in review


def test_diagram_skill_is_routed_only_when_the_block_contains_visual_evidence():
    assert "drawio-skill" not in authoring_skills({"title": "项目背景"})
    assert "drawio-skill" in authoring_skills({"title": "核心数据模型与实体关系"})


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
async def test_block_generation_runs_separate_authoring_and_quality_skill_passes():
    provider = object.__new__(CodexProvider)
    provider.runner = RecordingRunner()

    result = await provider.generate_block({
        "block_id": "block-1", "base_version": 1, "content": "", "title": "核心数据模型",
    })

    assert result.content == "合格正文"
    assert [call[2] for call in provider.runner.calls] == [
        ("create-prd", "deliver-acceptance-criteria", "drawio-skill"),
        ("requirement-registry-skill", "prd-consistency-skill", "prd-critique", "traceability-skill"),
    ]
    assert "$create-prd" in provider.runner.calls[0][0]
    assert "$traceability-skill" in provider.runner.calls[1][0]


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
