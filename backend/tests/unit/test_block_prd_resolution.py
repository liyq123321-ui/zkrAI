import pytest

from app.ai.provider import DocumentReview, DocumentReviewIssue, DocumentRevision, DocumentRevisionBlock, SpecFragment
from app.services.ai_service import AIService
from app.services.block_service import BlockError


def context():
    blocks = [
        {"id": block_id, "title": block_id, "parent_id": None, "order": index,
         "version": 1, "content": f"{block_id} 原文", "dependencies": [], "summary": f"{block_id} 摘要"}
        for index, block_id in enumerate(("A", "B"))
    ]
    return {
        "document_id": "doc", "base_revision": 3, "title": "PRD", "background": "", "global_rules": "",
        "blocks": blocks,
        "review": DocumentReview(document_id="doc", base_revision=3, summary="两处不一致", issues=[
            DocumentReviewIssue(severity="warning", block_ids=["A", "B"],
                                description="两处规则冲突", suggestion="统一规则"),
        ]).model_dump(mode="json"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("remaining_severity", "expected_rounds"), [("warning", 1), ("blocker", 2)])
async def test_revision_verifies_issue_chapters_and_only_retries_blockers(remaining_severity, expected_rounds):
    class Provider:
        def __init__(self):
            self.rounds = 0
            self.verifications = []

        async def revise_document(self, payload):
            self.rounds += 1
            block_id = "A" if self.rounds == 1 else "B"
            assert block_id in {block["id"] for block in payload["blocks"]}
            return DocumentRevision(document_id="doc", base_revision=3, summary="已修订", blocks=[
                DocumentRevisionBlock(block_id=block_id, base_version=1, content=f"{block_id} 新规则",
                                      summary="新规则", spec_fragment=SpecFragment()),
            ])

        async def review_document(self, payload):
            self.verifications.append(payload)
            issues = [DocumentReviewIssue(severity=remaining_severity, block_ids=["B"],
                                          description="B 仍冲突", suggestion="修订 B")] if self.rounds == 1 else []
            return DocumentReview(document_id="doc", base_revision=3, summary="复审", issues=issues)

    provider = Provider()
    service = AIService(None, provider)
    service.update_progress = lambda *_args, **_kwargs: None
    revision, verification = await service.resolve_document("run", {**context(), "verify_after_revision": True})

    assert provider.rounds == expected_rounds
    assert [block["id"] for block in provider.verifications[0]["blocks"]] == ["A", "B"]
    assert {block.block_id for block in revision.blocks} == ({"A", "B"} if expected_rounds == 2 else {"A"})
    assert len(verification.issues) == (0 if expected_rounds == 2 else 1)


@pytest.mark.asyncio
async def test_revision_defaults_to_one_pass_without_review():
    class Provider:
        async def revise_document(self, payload):
            return DocumentRevision(document_id="doc", base_revision=3, summary="已修订", blocks=[
                DocumentRevisionBlock(block_id="A", base_version=1, content="新规则", summary="已改", spec_fragment=SpecFragment()),
            ])

        async def review_document(self, _payload):
            raise AssertionError("默认修订不应自动复审")

    service = AIService(None, Provider())
    service.update_progress = lambda *_args, **_kwargs: None
    revision, verification = await service.resolve_document("run", context())
    assert verification is None
    assert revision.blocks[0].content == "新规则"


@pytest.mark.asyncio
async def test_noop_revision_keeps_original_review_without_wasting_verification():
    class Provider:
        async def revise_document(self, payload):
            block = payload["blocks"][0]
            return DocumentRevision(document_id="doc", base_revision=3, summary="未修改", blocks=[
                DocumentRevisionBlock(block_id=block["id"], base_version=1, content=block["content"],
                                      summary="原文", spec_fragment=SpecFragment()),
            ])

        async def review_document(self, _payload):
            raise AssertionError("没有修改时不应重复调用复审")

    service = AIService(None, Provider())
    service.update_progress = lambda *_args, **_kwargs: None
    with pytest.raises(BlockError, match="未产生正文改动"):
        await service.resolve_document("run", context())
