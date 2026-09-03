import base64
import json

import httpx
import pytest

from app.config import Settings
from app.services.gitea import (
    CommentLineNotInDiff,
    GiteaClient,
    GiteaError,
    GiteaThread,
    diff_position_for_new_line,
    commentable_lines_from_patch,
)


def test_commentable_lines_from_patch_returns_only_context_and_additions():
    patch = "@@ -1,3 +1,4 @@\n heading\n-old\n+new\n keep\n+last\n"

    assert commentable_lines_from_patch(patch) == [
        (1, "context", "heading"),
        (2, "addition", "new"),
        (3, "context", "keep"),
        (4, "addition", "last"),
    ]


def test_commentable_lines_from_patch_preserves_source_numbers_across_hunks():
    patch = "@@ -1 +1 @@\n first\n@@ -10 +12,2 @@\n twelfth\n+thirteenth\n"

    assert commentable_lines_from_patch(patch) == [
        (1, "context", "first"),
        (12, "context", "twelfth"),
        (13, "addition", "thirteenth"),
    ]


def test_diff_position_for_new_line_maps_context_and_added_lines():
    """Changing a hunk body offset must not alter source-line anchors."""
    patch = "@@ -1,2 +1,3 @@\n old\n+new\n keep\n"

    assert diff_position_for_new_line(patch, 1) == 1
    assert diff_position_for_new_line(patch, 2) == 2
    assert diff_position_for_new_line(patch, 3) == 3


def test_diff_position_for_new_line_returns_source_line_for_each_hunk():
    """Gitea's new_position is the requested new-file line, including later hunks."""
    patch = "@@ -1 +1 @@\n first\n@@ -10 +10,2 @@\n tenth\n+eleventh\n"

    assert diff_position_for_new_line(patch, 1) == 1
    assert diff_position_for_new_line(patch, 10) == 10
    assert diff_position_for_new_line(patch, 11) == 11


@pytest.mark.parametrize("line", [0, -1, 2, 9])
def test_diff_position_for_new_line_rejects_non_positive_deleted_and_unrepresented_lines(line):
    """Returning a position for a missing target line would create a misplaced review."""
    patch = "@@ -1,2 +1 @@\n-old\n keep\n"

    with pytest.raises(CommentLineNotInDiff):
        diff_position_for_new_line(patch, line)


def test_diff_position_for_new_line_ignores_no_newline_marker():
    """The marker is metadata, never a reviewable diff line."""
    patch = "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n"

    assert diff_position_for_new_line(patch, 1) == 1


@pytest.mark.asyncio
async def test_list_comment_threads_reads_official_review_comment_paths_and_groups_by_review(gitea_settings):
    """Reading an aggregate comment endpoint would not preserve Gitea review ownership."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/reviews"):
            return httpx.Response(
                200,
                json=[
                    {"id": 100, "comments_count": 2},
                    {"id": 200, "comments_count": 1},
                    {"id": 300, "comments_count": 0},
                ],
            )
        if request.url.path.endswith("/reviews/100/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 12,
                        "body": "Updated in v2.",
                        "user": {"login": "agent"},
                        "path": "docs/prd/root/v1.md",
                        "position": 2,
                        "resolver": None,
                        "pull_request_review_id": 100,
                        "created_at": "2026-09-01T09:01:00Z",
                    },
                    {
                        "id": 10,
                        "body": "Clarify the owner.",
                        "user": {"login": "reviewer"},
                        "path": "docs/prd/root/v1.md",
                        "position": 2,
                        "resolver": {"login": "reviewer"},
                        "pull_request_review_id": 100,
                        "created_at": "2026-09-01T09:00:00Z",
                    },
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 20,
                    "body": "State the fallback.",
                    "user": {"login": "reviewer"},
                    "path": "docs/prd/root/v1.md",
                    "position": 3,
                    "resolver": None,
                    "pull_request_review_id": 200,
                    "created_at": "2026-09-01T09:02:00Z",
                }
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    threads = await client.list_comment_threads(7)

    assert len(threads) == 2
    assert isinstance(threads[0], GiteaThread)
    assert threads[0].comment.id == 10
    assert threads[0].comment.line == 2
    assert threads[0].comment.resolved is True
    assert threads[0].comment.user == "reviewer"
    assert [(reply.id, reply.body) for reply in threads[0].replies] == [
        (12, "Updated in v2.")
    ]
    assert threads[1].comment.id == 20
    assert threads[1].comment.resolved is False
    assert [request.url.path for request in seen] == [
        "/api/v1/repos/product/requirements/pulls/7/reviews",
        "/api/v1/repos/product/requirements/pulls/7/reviews/100/comments",
        "/api/v1/repos/product/requirements/pulls/7/reviews/200/comments",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_list_comment_threads_rejects_duplicate_comment_id_across_reviews(gitea_settings):
    """A comment ID reused across reviews makes the PR snapshot ambiguous."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/reviews"):
            return httpx.Response(
                200,
                json=[
                    {"id": 100, "comments_count": 1},
                    {"id": 200, "comments_count": 1},
                ],
            )
        if request.url.path.endswith("/reviews/100/comments"):
            review_id = 100
        elif request.url.path.endswith("/reviews/200/comments"):
            review_id = 200
        else:
            raise AssertionError(
                f"Unexpected request: {request.method} {request.url}"
            )
        anchor = 10 if review_id == 100 else 20
        return httpx.Response(
            200,
            json=[
                {
                    "id": 501,
                    "body": f"Review {review_id} comment.",
                    "user": {"id": 41, "login": "reviewer"},
                    "path": "docs/prd/root/v1.md",
                    "position": anchor,
                    "resolver": None,
                    "pull_request_review_id": review_id,
                    "created_at": f"2026-09-01T09:{anchor:02d}:00Z",
                }
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.list_comment_threads(7)

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    await client.aclose()


@pytest.mark.asyncio
async def test_list_comment_threads_paginates_reviews_and_keeps_independent_roots(gitea_settings):
    """One review can contain independent conversations at different path/line anchors."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/reviews"):
            page = request.url.params["page"]
            if page == "1":
                return httpx.Response(
                    200,
                    headers={"X-HasMore": "true"},
                    json=[{"id": 100, "comments_count": 3}],
                )
            return httpx.Response(
                200,
                headers={"X-HasMore": "false"},
                json=[{"id": 200, "comments_count": 0}],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "body": "Root A",
                    "user": {"id": 41, "login": "reviewer"},
                    "path": "a.md",
                    "position": 10,
                    "resolver": None,
                    "pull_request_review_id": 100,
                    "created_at": "2026-09-01T09:00:00Z",
                },
                {
                    "id": 12,
                    "body": "Root B",
                    "user": {"id": 42, "login": "second-reviewer"},
                    "path": "b.md",
                    "position": 20,
                    "resolver": None,
                    "pull_request_review_id": 100,
                    "created_at": "2026-09-01T09:01:00Z",
                },
                {
                    "id": 11,
                    "body": "Reply A",
                    "user": {"id": 99, "login": "agent"},
                    "path": "a.md",
                    "position": 10,
                    "resolver": None,
                    "pull_request_review_id": 100,
                    "created_at": "2026-09-01T09:02:00Z",
                },
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    threads = await client.list_comment_threads(7)

    assert [(thread.comment.id, [reply.id for reply in thread.replies]) for thread in threads] == [
        (10, [11]),
        (12, []),
    ]
    assert threads[0].comment.user_id == 41
    assert threads[0].replies[0].user_id == 99
    review_requests = [request for request in seen if request.url.path.endswith("/reviews")]
    assert [request.url.params["page"] for request in review_requests] == ["1", "2"]
    assert all(request.url.params["limit"] == "50" for request in review_requests)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "comment",
    [
        {
            "id": 10,
            "body": "Missing position.",
            "user": {"login": "reviewer"},
            "path": "docs/prd/root/v1.md",
            "resolver": None,
            "pull_request_review_id": 100,
            "created_at": "2026-09-01T09:00:00Z",
        },
        {
            "id": 10,
            "body": "Wrong review.",
            "user": {"login": "reviewer"},
            "path": "docs/prd/root/v1.md",
            "position": 2,
            "resolver": None,
            "pull_request_review_id": 101,
            "created_at": "2026-09-01T09:00:00Z",
        },
    ],
)
async def test_list_comment_threads_rejects_malformed_or_mismatched_official_records(
    gitea_settings, comment
):
    """Accepting partial or cross-review data could attach a reply to the wrong thread."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=[{"id": 100, "comments_count": 1}])
        return httpx.Response(200, json=[comment])

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.list_comment_threads(7)

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    await client.aclose()


@pytest.mark.asyncio
async def test_list_comment_threads_rejects_empty_declared_review_but_ignores_zero_comment_review(gitea_settings):
    """A nonempty review without returned comments is not an authoritative thread snapshot."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=[{"id": 100, "comments_count": 0}, {"id": 200, "comments_count": 1}])
        return httpx.Response(200, json=[])

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.list_comment_threads(7)

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    assert seen == [
        "/api/v1/repos/product/requirements/pulls/7/reviews",
        "/api/v1/repos/product/requirements/pulls/7/reviews/200/comments",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_create_comment_maps_target_line_before_using_comment_review_endpoint(gitea_settings):
    """Passing the source line as a diff position would anchor comments incorrectly."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "docs/prd/root/v1.md",
                        "patch": "@@ -1,2 +1,3 @@\n old\n+new\n keep\n",
                    }
                ],
            )
        return httpx.Response(200, json={"id": 4, "state": "COMMENT"})

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    await client.create_comment(7, "docs/prd/root/v1.md", 2, "Add an owner.", "commit-sha")

    assert [request.method for request in seen] == ["GET", "POST"]
    assert seen[0].url.path == "/api/v1/repos/product/requirements/pulls/7/files"
    assert seen[1].url.path == "/api/v1/repos/product/requirements/pulls/7/reviews"
    assert json.loads(seen[1].content) == {
        "body": "Add an owner.",
        "commit_id": "commit-sha",
        "event": "COMMENT",
        "comments": [
            {
                "body": "Add an owner.",
                "path": "docs/prd/root/v1.md",
                "new_position": 2,
            }
        ],
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_create_comment_paginates_files_and_uses_requested_later_hunk_source_line(gitea_settings):
    """A target file on a later page/hunk still uses its requested source line."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET" and request.url.params["page"] == "1":
            return httpx.Response(
                200,
                headers={"X-HasMore": "true"},
                json=[{"filename": "other.md", "patch": "@@ -1 +1 @@\n old\n"}],
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"X-HasMore": "false"},
                json=[
                    {
                        "filename": "docs/prd/root/v1.md",
                        "patch": "@@ -1 +1 @@\n first\n@@ -10 +10,2 @@\n tenth\n+eleventh\n",
                    }
                ],
            )
        return httpx.Response(200, json={"id": 4, "state": "COMMENT"})

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    await client.create_comment(7, "docs/prd/root/v1.md", 11, "Later hunk.", "commit-sha")

    assert [request.url.params["page"] for request in seen[:2]] == ["1", "2"]
    assert json.loads(seen[-1].content)["comments"][0]["new_position"] == 11
    await client.aclose()


@pytest.mark.asyncio
async def test_reply_and_resolve_use_official_comment_endpoints(gitea_settings):
    """A reply or resolution sent to an issue endpoint would lose thread semantics."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/replies"):
            return httpx.Response(
                201,
                json={
                    "id": 91,
                    "body": "Updated in v2.",
                    "user": {"id": 99, "login": "firstflight"},
                    "created_at": "2026-09-01T09:02:00Z",
                },
            )
        return httpx.Response(200, json={})

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    reply = await client.reply_comment(7, 10, "Updated in v2.")
    await client.set_comment_resolved(10, True)
    await client.set_comment_resolved(10, False)

    assert [(request.method, request.url.path) for request in seen] == [
        ("POST", "/api/v1/repos/product/requirements/pulls/7/comments/10/replies"),
        ("POST", "/api/v1/repos/product/requirements/pulls/comments/10/resolve"),
        ("POST", "/api/v1/repos/product/requirements/pulls/comments/10/unresolve"),
    ]
    assert json.loads(seen[0].content) == {"body": "Updated in v2."}
    assert (reply.id, reply.user_id, reply.user) == (91, 99, "firstflight")
    await client.aclose()


@pytest.fixture
def gitea_settings(tmp_path):
    return Settings(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
        gitea_url="https://gitea.example",
        gitea_token="secret",
        gitea_owner="product",
        gitea_repo="requirements",
        gitea_base_branch="main",
        gitea_timeout_seconds=17,
    )


@pytest.mark.asyncio
async def test_get_file_sends_token_decodes_content_and_encodes_ref(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "path": "docs/prd/root/v 1.md",
                "content": base64.b64encode(b"# PRD\n").decode(),
                "sha": "file-sha",
            },
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.get_file("docs/prd/root/v 1.md", "prd-review/root")

    assert result.path == "docs/prd/root/v 1.md"
    assert result.content == "# PRD\n"
    assert result.sha == "file-sha"
    assert seen[0].headers["Authorization"] == "token secret"
    assert seen[0].url.path == "/api/v1/repos/product/requirements/contents/docs/prd/root/v 1.md"
    assert seen[0].url.params == httpx.QueryParams({"ref": "prd-review/root"})
    assert seen[0].extensions["timeout"]["connect"] == 17
    assert seen[0].extensions["timeout"]["read"] == 17
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_branch_returns_existing_branch_without_creating_it(gitea_settings):
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"name": "prd-review/root", "commit": {"id": "review-head"}})

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    result = await client.ensure_branch("prd-review/root")

    assert result.sha == "review-head"
    assert methods == ["GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_branch_creates_from_base_when_branch_is_absent(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET" and request.url.path.endswith("/branches/prd-review/root"):
            return httpx.Response(404)
        return httpx.Response(
            201, json={"name": "prd-review/root", "commit": {"id": "new-head"}}
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    result = await client.ensure_branch("prd-review/root")

    assert result.sha == "new-head"
    assert [(request.method, request.url.path) for request in seen] == [
        ("GET", "/api/v1/repos/product/requirements/branches/prd-review/root"),
        ("POST", "/api/v1/repos/product/requirements/branches"),
    ]
    assert json.loads(seen[-1].content) == {
        "new_branch_name": "prd-review/root",
        "old_ref_name": "main",
    }
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("sha", [None, "old-file-sha"])
async def test_put_file_uses_contents_api_with_optional_existing_sha(gitea_settings, sha):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"commit": {"sha": "commit-sha"}})

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    result = await client.put_file(
        "docs/prd/root/v1.md", "# Next\n", "prd-review/root", "publish PRD", sha=sha
    )

    payload = json.loads(seen[0].content)
    assert result.sha == "commit-sha"
    assert seen[0].method == "PUT"
    assert payload == {
        "branch": "prd-review/root",
        "content": base64.b64encode(b"# Next\n").decode(),
        "message": "publish PRD",
        **({"sha": "old-file-sha"} if sha else {}),
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_reuses_open_exact_head_and_base(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"number": 4, "head": {"ref": "other"}, "base": {"ref": "main"}},
                {"number": 7, "head": {"ref": "prd-review/root"}, "base": {"ref": "main"}},
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.ensure_review_pr("root", "prd-review/root")

    assert result.number == 7
    assert result.head == "prd-review/root"
    assert result.base == "main"
    assert [request.method for request in seen] == ["GET"]
    assert seen[0].url.params == httpx.QueryParams(
        {"state": "open", "base_branch": "main", "page": "1", "limit": "50"}
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_creates_when_no_exact_open_pr_exists(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            201,
            json={"number": 8, "head": {"ref": "prd-review/root"}, "base": {"ref": "main"}},
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.ensure_review_pr("root", "prd-review/root")

    assert result.number == 8
    assert json.loads(seen[-1].content) == {
        "title": "PRD review: root",
        "head": "prd-review/root",
        "base": "main",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_paginates_using_supported_base_branch_filter(gitea_settings):
    """Exact head matching is local because Gitea list-pulls has no head filter."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                headers={"X-HasMore": "true"},
                json=[{"number": 4, "head": {"ref": "other"}, "base": {"ref": "main"}}],
            )
        return httpx.Response(
            200,
            headers={"X-HasMore": "false"},
            json=[
                {"number": 7, "head": {"ref": "prd-review/root"}, "base": {"ref": "main"}}
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.ensure_review_pr("root", "prd-review/root")

    assert result.number == 7
    assert [request.url.params["page"] for request in seen] == ["1", "2"]
    assert all("head" not in request.url.params and "base" not in request.url.params for request in seen)
    assert all(request.url.params["base_branch"] == "main" for request in seen)
    await client.aclose()


@pytest.mark.asyncio
async def test_check_capabilities_rejects_missing_configuration_without_request(tmp_path):
    settings = Settings(
        database_url="sqlite://",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    client = GiteaClient(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.check_capabilities()

    assert captured.value.code == "GITEA_NOT_CONFIGURED"
    assert calls == 0
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (403, "GITEA_FORBIDDEN", False),
        (429, "GITEA_RATE_LIMITED", True),
        (503, "GITEA_UNAVAILABLE", True),
        (404, "GITEA_INCOMPATIBLE", False),
    ],
)
async def test_check_capabilities_classifies_safe_public_errors(
    gitea_settings, status_code, expected_code, retryable
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="token secret and internal response body")

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.check_capabilities()

    error = captured.value
    assert error.code == expected_code
    assert error.retryable is retryable
    assert "secret" not in str(error)
    assert "internal response body" not in str(error)
    await client.aclose()


@pytest.mark.asyncio
async def test_check_capabilities_is_cached_and_validates_identity_repo_and_base_branch(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "1.27.0"})
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 99, "login": "firstflight"})
        if request.url.path.endswith("/branches/main"):
            return httpx.Response(200, json={"name": "main", "commit": {"id": "base-head"}})
        return httpx.Response(
            200,
            json={"full_name": "product/requirements", "permissions": {"pull": True, "push": True}},
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    identity = await client.check_capabilities()
    cached = await client.check_capabilities()

    assert (identity.id, identity.login) == (99, "firstflight")
    assert cached == identity
    assert [request.url.path for request in seen] == [
        "/api/v1/version",
        "/api/v1/user",
        "/api/v1/repos/product/requirements",
        "/api/v1/repos/product/requirements/branches/main",
    ]

    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1.26.9", "not-a-version"])
async def test_check_capabilities_requires_gitea_1_27_or_newer(gitea_settings, version):
    client = GiteaClient(
        gitea_settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"version": version})
        ),
    )

    with pytest.raises(GiteaError) as captured:
        await client.check_capabilities()

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_file_classifies_resource_404_as_not_found(gitea_settings):
    client = GiteaClient(
        gitea_settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )

    with pytest.raises(GiteaError) as captured:
        await client.get_file("missing.md", "main")

    assert captured.value.code == "GITEA_NOT_FOUND"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_file_rejects_malformed_base64_content(gitea_settings):
    client = GiteaClient(
        gitea_settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"path": "docs/prd/root/v1.md", "content": "@@@@", "sha": "file-sha"},
            )
        ),
    )

    with pytest.raises(GiteaError) as captured:
        await client.get_file("docs/prd/root/v1.md", "prd-review/root")

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_file_keeps_gitea_line_wrapped_base64_valid(gitea_settings):
    client = GiteaClient(
        gitea_settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "path": "docs/prd/root/v1.md",
                    "content": "IyBQUkQK\n",
                    "sha": "file-sha",
                },
            )
        ),
    )

    result = await client.get_file("docs/prd/root/v1.md", "prd-review/root")

    assert result.content == "# PRD\n"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_file_escapes_reserved_path_and_query_characters_on_the_wire(gitea_settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"path": "docs/prd/a?b#c% d.md", "content": "Iw==", "sha": "file-sha"},
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    await client.get_file("docs/prd/a?b#c% d.md", "prd-review/root?draft")

    assert seen[0].url.raw_path == (
        b"/api/v1/repos/product/requirements/contents/docs/prd/a%3Fb%23c%25%20d.md"
        b"?ref=prd-review%2Froot%3Fdraft"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_branch_reuses_authoritative_branch_after_create_conflict(gitea_settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(409)
        if len(requests) == 1:
            return httpx.Response(404)
        return httpx.Response(
            200, json={"name": "prd-review/root", "commit": {"id": "raced-head"}}
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.ensure_branch("prd-review/root")

    assert result.sha == "raced-head"
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_rejects_malformed_record_before_creating(gitea_settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"number": 7, "head": {"ref": "prd-review/root"}},
                {"number": 8, "head": {"ref": "prd-review/root"}, "base": {"ref": "main"}},
            ],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.ensure_review_pr("root", "prd-review/root")

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    assert [request.method for request in requests] == ["GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_rejects_non_object_record_before_creating(gitea_settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=["not a pull request"])

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.ensure_review_pr("root", "prd-review/root")

    assert captured.value.code == "GITEA_INCOMPATIBLE"
    assert [request.method for request in requests] == ["GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_reuses_authoritative_pr_after_create_conflict(gitea_settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(409)
        if len(requests) == 1:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[{"number": 9, "head": {"ref": "prd-review/root"}, "base": {"ref": "main"}}],
        )

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))
    result = await client.ensure_review_pr("root", "prd-review/root")

    assert result.number == 9
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ensure_review_pr_fails_safely_when_create_conflict_has_no_exact_pr(gitea_settings):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(409)
        return httpx.Response(200, json=[])

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.ensure_review_pr("root", "prd-review/root")

    assert captured.value.code == "GITEA_UNAVAILABLE"
    assert captured.value.retryable is True
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "expected_code"),
    [
        (
            lambda request: httpx.ReadTimeout(
                "https://gitea.example/version?token=secret Authorization: token secret", request=request
            ),
            "GITEA_TIMEOUT",
        ),
        (
            lambda request: httpx.ConnectError(
                "transport secret query=token%3Dsecret Authorization: token secret", request=request
            ),
            "GITEA_UNAVAILABLE",
        ),
    ],
)
async def test_transport_failures_return_only_safe_public_error(
    gitea_settings, error_factory, expected_code
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_factory(request)

    client = GiteaClient(gitea_settings, transport=httpx.MockTransport(handler))

    with pytest.raises(GiteaError) as captured:
        await client.check_capabilities()

    assert captured.value.code == expected_code
    assert "secret" not in str(captured.value)
    assert "token" not in str(captured.value)
    assert "Authorization" not in str(captured.value)
    assert "transport" not in str(captured.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_official_files_without_patch_use_raw_diff_and_keep_target_source_lines(gitea_settings):
    """Gitea files only contain statistics; a different file's hunks must not leak in."""
    seen = []
    raw_diff = (
        'diff --git a/other.md b/other.md\n--- a/other.md\n+++ b/other.md\n'
        '@@ -0,0 +100 @@\n+unrelated\n'
        'diff --git a/docs/prd/root/v1.md b/docs/prd/root/v1.md\n'
        '--- a/docs/prd/root/v1.md\n+++ b/docs/prd/root/v1.md\n'
        '@@ -1 +1 @@\n title\n@@ -10 +12,2 @@\n keep\n+new requirement\n'
        'diff --git a/last.md b/last.md\n--- a/last.md\n+++ b/last.md\n'
        '@@ -0,0 +200 @@\n+also unrelated\n'
    )

    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/files'):
            return httpx.Response(200, json=[{'filename':'docs/prd/root/v1.md', 'status':'modified', 'additions':1,'deletions':0,'changes':1}])
        if request.url.path.endswith('/7.diff'):
            return httpx.Response(200, text=raw_diff)
        if request.method == 'POST' and request.url.path.endswith('/reviews'):
            return httpx.Response(200, json={'id':4,'state':'COMMENT'})
        raise AssertionError(str(request.url))

    async with GiteaClient(gitea_settings, transport=httpx.MockTransport(handler)) as client:
        assert await client.commentable_lines(7, 'docs/prd/root/v1.md') == [
            (1, 'context', 'title'), (12, 'context', 'keep'), (13, 'addition', 'new requirement')
        ]
        await client.create_comment(7, 'docs/prd/root/v1.md', 13, 'Clarify this.', 'commit-sha')
        assert json.loads(seen[-1].content)['comments'] == [{'body':'Clarify this.','path':'docs/prd/root/v1.md','new_position':13}]
        posts_before = sum(request.method == 'POST' for request in seen)
        with pytest.raises(CommentLineNotInDiff):
            await client.create_comment(7, 'docs/prd/root/v1.md', 100, 'Wrong file.', 'commit-sha')
        assert sum(request.method == 'POST' for request in seen) == posts_before


@pytest.mark.asyncio
@pytest.mark.parametrize('raw_diff', [
    'diff --git a/other.md b/other.md\n--- a/other.md\n+++ b/other.md\n@@ -0,0 +1 @@\n+other\n',
    ('diff --git a/docs/prd/root/v1.md b/docs/prd/root/v1.md\n--- /dev/null\n+++ b/docs/prd/root/v1.md\n@@ -0,0 +1 @@\n+duplicate\n') * 2,
])
async def test_raw_diff_refuses_missing_or_ambiguous_target_file(gitea_settings, raw_diff):
    def handler(request):
        if request.url.path.endswith('/files'):
            return httpx.Response(200, json=[{'filename':'docs/prd/root/v1.md','status':'added','additions':1,'deletions':0,'changes':1}])
        return httpx.Response(200, text=raw_diff)
    async with GiteaClient(gitea_settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CommentLineNotInDiff):
            await client.commentable_lines(7, 'docs/prd/root/v1.md')
