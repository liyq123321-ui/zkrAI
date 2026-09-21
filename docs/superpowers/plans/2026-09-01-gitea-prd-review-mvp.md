# Gitea PRD Review MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 firstFlight 现有 Spec 审核门禁上增加 Gitea PRD 文件、行内批注、回复、resolve、按批注生成下一版以及可查询后台任务闭环。

**Architecture:** GiteaClient 隔离官方 HTTP、源行验证、分页与能力预检；PrdReviewService 负责根 WorkItem、Project、SpecVersion 与 Gitea 投影绑定；PmRewriteService 通过内部 `publish_review` 两阶段命令生成不可变 SpecVersion 并运行既有自动审核；ReviewPublishCoordinator 负责持久快照、actor、Gitea 文件和签名回复回执。Gitea 是 PRD 文件与批注审计源，SQLite 保存 `versions/comments_index/tasks` 投影并保留既有工作流权威状态。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、SQLite、Pydantic v2、httpx、pytest、pytest-asyncio。

## Global Constraints

- 基线分支从 `codex/project-spec-agent-spec-final-fixes@b57b837` 派生；不得重写或删除现有 Project、WorkItem、Spec、CommandService、审核或 `/chat` 行为。
- 不做前端、Webhook、SSE、WebSocket、消息队列、批注监听或子 Agent 执行。
- 只有 `POST /prd/{wi}/reviews/publish` 触发一次 PM Agent；所有测试使用 FakeAgent 与 fake httpx transport，真实 Gitea/LLM/公网调用数为 0。
- `{wi}` 只接受根 WorkItem ID；写操作要求 actor 是 final approver、project manager 或 root owner。
- Gitea token、响应正文、命令行、stdout/stderr 和内部异常不得进入公开错误。
- 新旧 Markdown 永不覆盖；`versions.content_hash` 是规范化 Markdown SHA-256，`versions.spec_content_hash` 是结构化 `SpecVersion.content_hash`，两条校验链分别一致。
- 本 MVP 只支持单进程/单 worker；命令准备 ownership fence 和 FastAPI BackgroundTasks 不声称跨进程协调。
- 每项生产代码必须先有正确失败的自动化测试，再写最小实现。
- `.DS_Store`、`.superpowers/`、`tests/helpers/__init__.py` 和任何凭据不进入提交。

---

## File Map

- `app/schemas/prd_review.py`：九个端点的严格请求/响应 DTO 与纯文本验证。
- `app/services/gitea.py`：Gitea HTTP、能力预检、官方 Branch/文件/PR、源行验证、comment thread/reply/resolve。
- `app/services/prd_review.py`：根 WorkItem 解析、责任校验、version/comment 投影、PRD 查询与评论 CRUD。
- `app/services/pm_agent.py`：批注快照、rewrite 输出验证、两阶段 Spec 修订 handler、task/publish 协调器。
- `app/api/prd_review.py`：REST 路由、BackgroundTasks 调度和稳定错误映射。
- `app/database/models.py`：`PrdVersion`、`CommentIndex`、`ReviewTask` 三张增量表。
- `app/domain/types.py`：`PUBLISH_REVIEW`、批注 action 与 PM rewrite 输出类型。
- `app/services/spec_service.py`：抽出初始/文本修订/批注修订共用的自动审核准备与物化逻辑。
- `main.py`：构造 Gitea/PRD review 依赖并注册路由。

---

### Task 1: Gitea 配置与三张增量表

**Files:**
- Modify: `app/config.py:6-23`
- Modify: `app/database/models.py:179-335`
- Create: `.env.example`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_database_schema.py`

**Interfaces:**
- Produces: `Settings.gitea_url/token/owner/repo/base_branch/review_branch_prefix/timeout_seconds`。
- Produces: `PrdVersion`, `CommentIndex`, `ReviewTask` SQLAlchemy models。

- [ ] **Step 1: Write failing configuration tests**

```python
def test_settings_load_optional_gitea_configuration(monkeypatch):
    monkeypatch.setenv("GITEA_URL", "https://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "secret")
    monkeypatch.setenv("GITEA_OWNER", "product")
    settings = Settings.from_env()
    assert settings.gitea_url == "https://gitea.example"
    assert settings.gitea_repo == "docs"
    assert settings.gitea_timeout_seconds == 20
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv/bin/pytest tests/unit/test_config.py -q`  
Expected: FAIL because `Settings` has no Gitea fields.

- [ ] **Step 3: Add immutable Settings fields and `.env.example`**

```python
gitea_url: str | None = None
gitea_token: str | None = None
gitea_owner: str | None = None
gitea_repo: str = "docs"
gitea_base_branch: str = "main"
gitea_review_branch_prefix: str = "prd-review/"
gitea_timeout_seconds: int = 20
```

Ensure existing tests that directly construct `Settings` remain source-compatible through defaults. `.env.example` contains names only and uses `<bridge-service-token>`, never a real token.

- [ ] **Step 4: Write failing schema tests for the three tables**

```python
def test_prd_review_tables_have_integrity_constraints(engine):
    names = set(inspect(engine).get_table_names())
    assert {"versions", "comments_index", "tasks"} <= names
    assert _unique_sets(engine, "versions") >= {
        frozenset({"wi", "version"}), frozenset({"spec_version_id"})
    }
```

Also assert task snapshot columns and comment primary key exist; call `init_database` twice against a temporary SQLite file to prove incremental initialization is idempotent.

- [ ] **Step 5: Run the database tests and verify RED**

Run: `.venv/bin/pytest tests/unit/test_database_schema.py -q`  
Expected: FAIL because the three tables do not exist.

- [ ] **Step 6: Add minimal SQLAlchemy models**

Use the corrected columns from design §5, including `versions.spec_content_hash`, `tasks.initiator_actor_id`, `tasks.comment_snapshot` and `tasks.reply_receipts`. Store JSON at the ORM layer; persist UTC timestamps. Add `(wi, base_version, comment_snapshot_hash)` uniqueness and an idempotent SQLite migration that recomputes Markdown hash, preserves the legacy structured hash, backfills the historical final approver, and adds command owner columns.

- [ ] **Step 7: Verify GREEN and regression**

Run: `.venv/bin/pytest tests/unit/test_config.py tests/unit/test_database_schema.py -q`  
Expected: PASS with no new project warning.

- [ ] **Step 8: Commit**

```bash
git add .env.example app/config.py app/database/models.py tests/unit/test_config.py tests/unit/test_database_schema.py
git commit -m "feat: add prd review persistence"
```

---

### Task 2: 严格 API 与 PM rewrite 合同

**Files:**
- Create: `app/schemas/prd_review.py`
- Modify: `app/domain/types.py:27-215`
- Modify: `app/agents/gateway.py`
- Modify: `app/agents/codex.py:12-196`
- Create: `prompts/nodes/pm_rewrite_prd.txt`
- Modify: `tests/helpers/fake_agent.py`
- Create: `tests/unit/test_prd_review_contracts.py`
- Modify: `tests/unit/test_agent_gateway.py`

**Interfaces:**
- Produces: `RewriteAction`, `CommentResponse`, `PrdRewriteOutput`。
- Produces: `AgentGateway.rewrite_prd(payload: dict[str, object]) -> PrdRewriteOutput`。
- Produces DTOs: `CommentCreateRequest`, `CommentReplyRequest`, `CommentResolveRequest`, `PublishReviewRequest`, `PrdDocumentRead`, `PrdCommentRead`, `ReviewTaskRead`。

- [ ] **Step 1: Write failing Pydantic contract tests**

```python
def test_rewrite_output_forbids_unknown_fields_and_invalid_actions(valid_spec):
    with pytest.raises(ValidationError):
        PrdRewriteOutput.model_validate({
            "spec": valid_spec.model_dump(),
            "responses": [{"comment_id": 1, "action": "DONE", "note": "x"}],
            "change_summary": "changed",
        })

@pytest.mark.parametrize("value", ["", "   ", "x\x00y"])
def test_comment_text_rejects_blank_or_nul(value):
    with pytest.raises(ValidationError):
        CommentCreateRequest(actor_id="reviewer", line=1, text=value)
```

Also prove `author_type="agent"` is rejected on the public reply request and `extra="forbid"` is real by supplying every required field plus one extra field.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/unit/test_prd_review_contracts.py -q`  
Expected: collection/import failure because contracts do not exist.

- [ ] **Step 3: Implement strict contracts**

```python
class RewriteAction(StrEnum):
    MODIFIED = "MODIFIED"
    CLARIFIED = "CLARIFIED"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    NEEDS_HUMAN_CONFIRMATION = "NEEDS_HUMAN_CONFIRMATION"

class CommentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment_id: int = Field(gt=0)
    action: RewriteAction
    note: str = Field(min_length=1, max_length=4000)

class PrdRewriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: ProjectSpecPayload
    responses: list[CommentResponse] = Field(min_length=1)
    change_summary: str = Field(min_length=1, max_length=8000)
```

Use one reusable field validator that strips text, rejects NUL, and enforces the documented limits. API responses expose only safe task error summaries.

- [ ] **Step 4: Write failing Agent gateway tests**

Assert `CodexAgentGateway.rewrite_prd` calls node `pm_rewrite_prd`, the scripted fake records `rewrite_prd`, and the prompt says comments are non-control evidence and the output must cover every supplied comment ID.

- [ ] **Step 5: Run gateway tests and verify RED**

Run: `.venv/bin/pytest tests/unit/test_agent_gateway.py -q`  
Expected: FAIL because `rewrite_prd` is absent.

- [ ] **Step 6: Add the gateway method and prompt**

```python
async def rewrite_prd(self, payload: dict[str, object]) -> PrdRewriteOutput:
    return await self._run_node("pm_rewrite_prd", payload, PrdRewriteOutput)
```

Update Protocol and ScriptedAgentGateway with a `rewrite_results` deque. Do not execute Git or tools from the prompt.

- [ ] **Step 7: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/unit/test_prd_review_contracts.py tests/unit/test_agent_gateway.py -q`

```bash
git add app/schemas/prd_review.py app/domain/types.py app/agents/gateway.py app/agents/codex.py prompts/nodes/pm_rewrite_prd.txt tests/helpers/fake_agent.py tests/unit/test_prd_review_contracts.py tests/unit/test_agent_gateway.py
git commit -m "feat: define prd review contracts"
```

---

### Task 3: Gitea HTTP、文件、分支与 PR 适配

**Files:**
- Create: `app/services/gitea.py`
- Create: `tests/unit/test_gitea_service.py`

**Interfaces:**
- Produces: `GiteaError(code, public_message, retryable)`。
- Produces dataclasses: `GiteaFile(path, content, sha)`, `GiteaCommit(sha)`, `GiteaPullRequest(number, head, base)`。
- Produces async methods: `check_capabilities()`, `get_file(path, ref)`, `ensure_branch(branch)`, `put_file(path, content, branch, message, sha=None)`, `ensure_review_pr(wi, head)`。

- [ ] **Step 1: Write failing transport tests with `httpx.MockTransport`**

```python
@pytest.mark.asyncio
async def test_get_file_sends_token_and_decodes_base64(gitea_settings):
    client = GiteaClient(gitea_settings, transport=transport)
    file = await client.get_file("docs/prd/root/v1.md", "prd-review/root")
    assert file.content == "# PRD\n"
    assert seen.headers["Authorization"] == "token secret"
```

Cover URL encoding, timeout configuration, official Branch responses, branch 409 reread, paginated PR lookup, create/update file SHA, existing/open PR reuse, and errors including resource `GITEA_NOT_FOUND` distinct from capability `GITEA_INCOMPATIBLE`.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/unit/test_gitea_service.py -q`  
Expected: import failure because `GiteaClient` does not exist.

- [ ] **Step 3: Implement one request boundary**

```python
async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = await self._client.request(method, path, **kwargs)
    except httpx.TimeoutException as error:
        raise GiteaError("GITEA_TIMEOUT", "Gitea is temporarily unavailable.", True) from error
    if response.status_code >= 400:
        raise self._classified_error(response.status_code)
    return response
```

Never include response text, headers, request URL query, or token in exception strings. `put_file` uses Gitea Contents API so Git commit creation is server-side and deterministic; no local clone or GitPython dependency is added.

- [ ] **Step 4: Implement capability, file, branch and PR methods**

`check_capabilities` is lazy/cached, requires Gitea >=1.27.0, and non-mutatingly validates `/version`, `/user`, repo pull/push permissions and the base branch; it does not run during app startup. Branch lookup is `GET /branches/{branch}` and creation is `POST /branches` with `{new_branch_name, old_ref_name}`, returning `commit.id`. PR list uses supported `base_branch`, paginates, and matches exact head/base locally.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/unit/test_gitea_service.py -q`

```bash
git add app/services/gitea.py tests/unit/test_gitea_service.py
git commit -m "feat: add gitea repository client"
```

---

### Task 4: Diff 行号映射与 review comment threads

**Files:**
- Modify: `app/services/gitea.py`
- Modify: `tests/unit/test_gitea_service.py`

**Interfaces:**
- Produces: `diff_position_for_new_line(patch: str, line: int) -> int`。
- Produces: `GiteaComment`, `GiteaReply`, `GiteaThread` dataclasses。
- Produces methods: `list_comment_threads(pr_number)`, `create_comment(pr_number, path, line, body, commit_sha)`, `reply_comment(pr_number, comment_id, body)`, `set_comment_resolved(comment_id, resolved)`。

- [ ] **Step 1: Write failing pure mapping tests**

```python
PATCH = """@@ -1,2 +1,3 @@\n old\n+new\n keep\n"""
def test_maps_new_file_line_to_diff_position():
    assert diff_position_for_new_line(PATCH, 2) == 2

def test_rejects_deleted_or_unrepresented_line():
    with pytest.raises(CommentLineNotInDiff):
        diff_position_for_new_line(PATCH, 9)
```

Cover multiple hunks, context lines, additions, deletions, `\ No newline`, line 0 and negative lines.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/unit/test_gitea_service.py -q`  
Expected: FAIL because mapper is missing.

- [ ] **Step 3: Implement the minimal unified-diff parser**

Track new-file lines only to validate that the requested source line is represented by a context/addition row. Return that requested source line unchanged for Gitea `new_position`; never return a hunk-local body offset. Raise `CommentLineNotInDiff` instead of guessing.

- [ ] **Step 4: Write failing official-path comment tests**

Assert:

```text
POST /repos/{owner}/{repo}/pulls/{index}/reviews
POST /repos/{owner}/{repo}/pulls/{index}/comments/{id}/replies
POST /repos/{owner}/{repo}/pulls/comments/{id}/resolve
POST /repos/{owner}/{repo}/pulls/comments/{id}/unresolve
```

Creation body must contain `event="COMMENT"`, `commit_id`, `path`, and validated source-line `new_position`. Paginate reviews and changed files. Gitea review comments expose no `in_reply_to_id`; assemble each review into independent conversations keyed by actual `(path, position)`, with the earliest item as root and later items as replies. Reject duplicate IDs, missing anchors, and cross-review records as ambiguous/incompatible.

- [ ] **Step 5: Implement comment methods and verify GREEN**

Run: `.venv/bin/pytest tests/unit/test_gitea_service.py -q`

- [ ] **Step 6: Commit**

```bash
git add app/services/gitea.py tests/unit/test_gitea_service.py
git commit -m "feat: add gitea review comments"
```

---

### Task 5: PRD version binding、查询与评论投影服务

**Files:**
- Create: `app/services/prd_review.py`
- Create: `tests/integration/test_prd_review_service.py`

**Interfaces:**
- Produces: `PrdReviewService(session_factory, gitea)`。
- Produces async methods: `latest(wi)`, `version(wi, number)`, `versions(wi)`, `comments(wi)`, `create_comment(wi, request)`, `reply(wi, comment_id, request)`, `resolve(wi, comment_id, request)`。
- Produces: `ensure_current_binding(wi) -> PrdVersion` and `assert_reviewer(project, actor_id)`。

- [ ] **Step 1: Write failing root binding tests**

Create a real Project, root WorkItem and HUMAN_REVIEW SpecVersion in isolated SQLite. Assert non-root IDs return `PrdNotFound`; unauthorized actor raises `PrdForbidden`; the first bind creates `prd-review/{wi}`, writes `docs/prd/{wi}/v1.md`, opens one PR and inserts one `versions` row.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/integration/test_prd_review_service.py -q`  
Expected: import failure because service is absent.

- [ ] **Step 3: Implement root/project/spec resolution and content integrity**

```python
def _root_context(self, db: Session, wi: str) -> tuple[WorkItem, Project, SpecVersion]:
    item = db.get(WorkItem, wi)
    if item is None or item.kind != WorkItemKind.ROOT.value or item.parent_id is not None:
        raise PrdNotFound("root WorkItem not found")
    project = db.get(Project, item.project_id)
    version = db.get(SpecVersion, project.current_spec_version_id)
    return item, project, version
```

Use SHA-256 of normalized UTF-8 Markdown. Existing Gitea file with a different hash raises `PRD_CONTENT_CONFLICT`; it is never overwritten.

- [ ] **Step 4: Write failing query/comment projection tests**

Assert `GET`-level service methods read Gitea content, comments refresh `comments_index`, create uses exact current version path and commit, reply/resolve require the Gitea comment to belong to the same wi/PR, and public `author_type="agent"` cannot be supplied.

- [ ] **Step 5: Implement minimal CRUD methods**

All writes call `assert_reviewer`. `comments()` returns Gitea thread data and upserts top-level comments without deleting historical index rows. Reads of an approved/decomposed project work; writes fail with `PRD_REVIEW_CLOSED`.

- [ ] **Step 6: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/integration/test_prd_review_service.py -q`

```bash
git add app/services/prd_review.py tests/integration/test_prd_review_service.py
git commit -m "feat: bridge prd review state"
```

---

### Task 6: PRD 读取与评论 REST 端点

**Files:**
- Create: `app/api/prd_review.py`
- Modify: `main.py:12-54`
- Create: `tests/integration/test_prd_review_api.py`

**Interfaces:**
- Consumes: Task 2 DTOs and Task 5 PrdReviewService CRUD methods。
- Produces: first seven `/prd` endpoints; publish/task endpoints are added in Task 10。
- Extends: `create_app(..., gitea_client: GiteaClient | None = None)` for deterministic injection。

- [ ] **Step 1: Write failing HTTP contract tests**

Use `TestClient` with fake Gitea. Cover latest/specific/list versions, list/create/reply/resolve comments, response models, 404 cross-project comment, 403 actor, 422 invalid text/line, and safe 503 Gitea errors.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/integration/test_prd_review_api.py -q`  
Expected: 404 because routes are not registered.

- [ ] **Step 3: Implement router and stable error mapping**

```python
def build_router(service: PrdReviewService) -> APIRouter:
    router = APIRouter(tags=["prd-review"])
    @router.get("/prd/{wi}", response_model=PrdDocumentRead)
    async def latest_prd(wi: str):
        return await service.latest(wi)
    return router
```

Map service errors to the design’s 400/403/404/409/422/503 classes. Do not reuse raw exception strings.

- [ ] **Step 4: Register dependencies without startup network calls**

Construct `GiteaClient(settings)` but do not call `check_capabilities` from lifespan. Existing `create_app(settings, agent, session_factory)` positional tests must remain valid; add only a trailing optional keyword.

- [ ] **Step 5: Verify GREEN and regression**

Run: `.venv/bin/pytest tests/integration/test_prd_review_api.py tests/integration/test_sessions_api.py tests/integration/test_chat_compatibility.py -q`

- [ ] **Step 6: Commit**

```bash
git add app/api/prd_review.py main.py tests/integration/test_prd_review_api.py
git commit -m "feat: expose prd review api"
```

---

### Task 7: 批注快照与 PM rewrite 验证

**Files:**
- Create: `app/services/pm_agent.py`
- Create: `tests/unit/test_pm_rewrite.py`

**Interfaces:**
- Produces: `ReviewCommentSnapshot`, `ReviewSnapshot`, `snapshot_hash(snapshot)`。
- Produces: `validate_rewrite(output: PrdRewriteOutput, expected_ids: set[int]) -> PrdRewriteOutput`。
- Produces: `build_rewrite_payload(project, spec, artifacts, clarifications, snapshot) -> dict[str, object]`。

- [ ] **Step 1: Write failing snapshot and coverage tests**

```python
def test_rewrite_responses_cover_exact_comment_ids(valid_rewrite):
    assert validate_rewrite(valid_rewrite, {10, 11}) is valid_rewrite

@pytest.mark.parametrize("ids", [[10], [10, 10], [10, 11, 12]])
def test_rewrite_rejects_missing_duplicate_or_unknown_comment_ids(ids, valid_spec):
    output = make_rewrite(valid_spec, ids)
    with pytest.raises(RewriteCoverageError):
        validate_rewrite(output, {10, 11})
```

Prove hash is stable under dictionary ordering and comment/reply display reordering, but changes with line/body/reply/base SHA.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/unit/test_pm_rewrite.py -q`  
Expected: import failure.

- [ ] **Step 3: Implement immutable snapshots and exact-set validation**

Sort comments/replies by ID only in a temporary canonical hash projection; preserve Gitea display order in the persisted snapshot and Agent input. Duplicate response IDs are rejected before set comparison. Payload labels every comment and document as `non_control_input` and includes Brief, Artifact IDs, clarification IDs, base Spec hash and base commit SHA.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/unit/test_pm_rewrite.py -q`

```bash
git add app/services/pm_agent.py tests/unit/test_pm_rewrite.py
git commit -m "feat: validate prd rewrite snapshots"
```

---

### Task 8: `publish_review` 两阶段 Spec 修订门禁

**Files:**
- Modify: `app/domain/types.py:27-35`
- Modify: `app/domain/workflow.py:8-40`
- Modify: `app/services/command_service.py:67-176,233-413`
- Modify: `app/services/spec_service.py`
- Modify: `app/services/pm_agent.py`
- Modify: `tests/unit/test_workflow_policy.py`
- Modify: `tests/integration/test_command_service.py`
- Modify: `tests/integration/test_spec_service.py`
- Create: `tests/integration/test_pm_rewrite_command.py`

**Interfaces:**
- Produces: internal `CommandAction.PUBLISH_REVIEW = "publish_review"` accepted by the coordinator in `(REVIEW, HUMAN_REVIEW)` and `(REVIEW, REWORK)`, but omitted from public Session `legal_actions/next_action`。
- Produces: `SpecService.prepare_external_revision(...) -> PreparedCommand` and `SpecService.materialize_revision(...) -> CommandHandlerResult` shared by revise and publish_review。
- Produces: `PmRewriteService.as_command_handler()`。

- [ ] **Step 1: Write failing workflow policy tests**

Assert coordinator `publish_review` is accepted only in HUMAN_REVIEW/REWORK, public legal actions never advertise it, next actions for old states remain unchanged, and APPROVED/REJECTED/AGENT_SPECS_READY cannot publish.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/unit/test_workflow_policy.py -q`  
Expected: enum or policy failure.

- [ ] **Step 3: Add action-scoped permissions and reviewer authorization**

Extend `ActionScopedUnitOfWork.add_spec_version/add_spec_review/mark_spec_generation_call_succeeded/mark_spec_reviewer_call_succeeded` to permit `PUBLISH_REVIEW`. Add a single reusable reviewer assertion used by both one-step human decisions and publish_review preparation. Do not add publish_review to `_HUMAN`, because it is Agent-backed and must use two-phase command attempts.

- [ ] **Step 4: Write failing shared Spec revision tests**

Prove existing `revise` still creates exactly one immutable version and identical RULE/AGENT receipts. Add a new test where a prevalidated `PrdRewriteOutput` creates revision 2 with parent revision 1, generation source `GITEA_REVIEW_COMMENTS`, and moves to HUMAN_REVIEW or REWORK based on the same review merge policy.

- [ ] **Step 5: Extract the smallest shared revision seam**

The shared seam accepts a generated `ProjectSpecPayload`, parent snapshot, generation AgentCall ID, change summary and generation source; it performs current rule review, Reviewer Agent call preparation and existing materialization. Existing create/revise public behavior and error codes must not change.

- [ ] **Step 6: Implement PmRewriteService handler**

`prepare` loads `task_id`, verifies task/project/base/persisted snapshot/exact initiator, records one command-bound `rewrite_prd` AgentCall, validates exact coverage, then delegates automatic review. Every successful publication creates immutable n+1 even when structured content is unchanged; `NEEDS_HUMAN_CONFIRMATION` forces NEED_CLARIFICATION/REWORK rather than an automatic pass. `materialize` stores the new version on the task in the same transaction.

- [ ] **Step 7: Verify command idempotency and CAS**

Tests must cover replaying the same task command, stale state before Agent call, concurrent command attempt winner, unauthorized actor, Agent failure evidence, semantic Reviewer failure reuse, and no second rewrite call after materialization receipt.

Run: `.venv/bin/pytest tests/unit/test_workflow_policy.py tests/integration/test_command_service.py tests/integration/test_spec_service.py tests/integration/test_pm_rewrite_command.py -q`

- [ ] **Step 8: Commit**

```bash
git add app/domain/types.py app/domain/workflow.py app/services/command_service.py app/services/spec_service.py app/services/pm_agent.py tests/unit/test_workflow_policy.py tests/integration/test_command_service.py tests/integration/test_spec_service.py tests/integration/test_pm_rewrite_command.py
git commit -m "feat: gate comment driven spec revisions"
```

---

### Task 9: Task 创建、后台执行与幂等外部副作用

> **Final review correction:** retry of an accepted task consumes its persisted
> `comment_snapshot`; it does not reread live Gitea comments or reject a later
> edit/reordering as a change to already accepted evidence. `initiator_actor_id`
> is persisted on creation, must be the same principal on resume, and is
> re-authorized before any Agent/Gitea effect and used for command/audit
> attribution. Validate root topology before actor authorization.

**Files:**
- Modify: `app/services/pm_agent.py`
- Modify: `app/services/prd_review.py`
- Create: `tests/integration/test_review_publish_pipeline.py`

**Interfaces:**
- Produces: `ReviewPublishCoordinator.create_or_resume(wi, actor_id) -> ReviewTask`。
- Produces: `ReviewPublishCoordinator.run(task_id) -> None`。
- Produces: `ReviewPublishCoordinator.task(task_id) -> ReviewTaskRead` and `mark_interrupted_tasks()`。

- [ ] **Step 1: Write failing task snapshot tests**

Assert no unresolved comments returns `NO_UNRESOLVED_COMMENTS` and makes zero Agent calls; task freezes IDs/content/replies/base SHA; a comment added after 202 is absent; same snapshot returns the same task; an error task is reset to pending with the same ID and command ID.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/integration/test_review_publish_pipeline.py -q`  
Expected: coordinator import failure.

- [ ] **Step 3: Implement task creation transaction**

Call Gitea before opening the SQLite write transaction, then revalidate current version/base SHA during the transaction. Insert or load by `(wi, base_version, snapshot_hash)`. Status changes are compare-and-set (`pending -> processing`, `processing -> done|error`).

- [ ] **Step 4: Write failing full and partial-side-effect tests**

Cover:

- command creates v2, Gitea writes v2, `versions` inserted, every comment gets one truthful reply, task done;
- stale base fails before Agent;
- file failure leaves task error and recorded local v2;
- retry replays command receipt, writes the same v2 once and does not call Agent again;
- second reply failure preserves first reply and retry skips it only after validating its persisted reply ID, service user ID, exact deterministic body and signed `firstflight-receipt:v1` marker;
- `NEEDS_HUMAN_CONFIRMATION` reply does not claim “已完成”。

- [ ] **Step 5: Implement idempotent run stages**

Reply bodies include a token-HMAC signed marker plus visible action/note/version/SHA. Persist the returned reply ID, service user ID and exact body. On retry, accept only a live reply matching all receipt fields and signature; a human can copy marker-looking text without being filtered. `put_file` is idempotent by path/content hash.

- [ ] **Step 6: Implement safe task errors and interrupted startup recovery**

Store internal detail in `ReviewTask.error`; `ReviewTaskRead.error` is produced from stable `error_code`. `mark_interrupted_tasks` converts only pending/processing to error code `PROCESS_INTERRUPTED`; it performs no external call.

- [ ] **Step 6a: Correct generic command PREPARING recovery**

Add durable `prepare_owner_id/prepare_owner_started_at` plus a single-process
active-owner fence. Crash-matrix tests must prove: a PREPARING attempt with no
bound AgentCall safely reruns; a validated `RESULT_READY` is reused by the
idempotent handler; `PENDING/AMBIGUOUS` remains `COMMAND_IN_DOUBT`; a live owner
cannot be stolen. Clear the owner on PREPARED/FAILED/REJECTED and release it on
coroutine cancellation. Document that multi-worker deployment is unsupported.

- [ ] **Step 7: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/integration/test_review_publish_pipeline.py -q`

```bash
git add app/services/pm_agent.py app/services/prd_review.py tests/integration/test_review_publish_pipeline.py
git commit -m "feat: publish reviewed prd revisions"
```

---

### Task 10: Publish 与 task HTTP 契约

**Files:**
- Modify: `app/api/prd_review.py`
- Modify: `main.py:20-54`
- Modify: `tests/integration/test_prd_review_api.py`
- Create: `tests/e2e/test_gitea_prd_review.py`

**Interfaces:**
- Produces: `POST /prd/{wi}/reviews/publish -> 202 PublishReviewAccepted`。
- Produces: `GET /tasks/{task_id} -> ReviewTaskRead`。

- [ ] **Step 1: Write failing 202 and task polling tests**

```python
response = client.post(f"/prd/{root_id}/reviews/publish", json={"actor_id": "approver-1"})
assert response.status_code == 202
assert response.json() == {
    "task_id": response.json()["task_id"],
    "base_version": 1,
    "comment_count": 2,
}
assert client.get(f"/tasks/{response.json()['task_id']}").json()["status"] == "done"
```

Use FastAPI BackgroundTasks through TestClient; also assert unknown task 404, no comments 400, unauthorized 403 and internal error redaction.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest tests/integration/test_prd_review_api.py -q`  
Expected: publish/task routes 404.

- [ ] **Step 3: Add routes and dependency wiring**

The publish handler first calls `create_or_resume`, schedules `coordinator.run(task.id)`, then returns 202. `GET /tasks` is read-only. Lifespan calls `mark_interrupted_tasks` after database initialization and before yield; it performs zero Gitea/Agent calls.

- [ ] **Step 4: Write the complete E2E test**

With real services, isolated SQLite, ScriptedAgentGateway and MockTransport: create project → create v1 → reach HUMAN_REVIEW → bind v1 → add two comments → publish → v2 Gitea file → two Agent replies → task done → current local Spec is v2 in HUMAN_REVIEW. Assert `child_process_calls == 0`.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/integration/test_prd_review_api.py tests/e2e/test_gitea_prd_review.py -q`

```bash
git add app/api/prd_review.py main.py tests/integration/test_prd_review_api.py tests/e2e/test_gitea_prd_review.py
git commit -m "feat: complete gitea prd review api"
```

---

### Task 11: 回归、接口文档与发布前验证

**Files:**
- Modify: `readme.md`
- Modify: `docs/superpowers/specs/2026-09-01-gitea-prd-review-mvp-design.md` only if implementation exposed an already-reviewed factual mismatch
- Test: all test files

**Interfaces:**
- Produces: startup/config instructions, nine-endpoint contract, task/error behavior and no-real-network verification record in README。

- [ ] **Step 1: Add README contract and startup instructions**

Document environment variables, Gitea capability requirement, curl examples for comment/publish/task, source-line-to-diff limitation, task interruption behavior, and the fact that approval/WorkItem conversion still uses existing Session commands. Do not document real tokens or internal errors.

- [ ] **Step 2: Run focused new-feature suite**

Run:

```bash
.venv/bin/pytest \
  tests/unit/test_prd_review_contracts.py \
  tests/unit/test_gitea_service.py \
  tests/unit/test_pm_rewrite.py \
  tests/integration/test_prd_review_service.py \
  tests/integration/test_prd_review_api.py \
  tests/integration/test_pm_rewrite_command.py \
  tests/integration/test_review_publish_pipeline.py \
  tests/e2e/test_gitea_prd_review.py -q
```

Expected: all pass; no real Gitea, Codex subprocess or network calls.

- [ ] **Step 3: Run complete regression**

Run: `PYTHONPYCACHEPREFIX=/tmp/firstflight-pycache .venv/bin/pytest -q -p no:cacheprovider`  
Expected: the complete current suite passes. The known third-party Starlette TestClient deprecation warning may remain; no new firstFlight warning is allowed.

- [ ] **Step 4: Run static syntax and diff checks**

```bash
PYTHONPYCACHEPREFIX=/tmp/firstflight-pycache .venv/bin/python -m compileall -q app main.py tests
git diff --check
git status --short
```

Expected: exit 0; only intended files plus pre-existing untracked `.DS_Store` and `tests/helpers/__init__.py` appear.

- [ ] **Step 5: Commit documentation**

```bash
git add readme.md
git commit -m "docs: document gitea prd review api"
```

- [ ] **Step 6: Request independent code review**

Use `requesting-code-review` against the design, this plan and the complete branch diff. Fix every Critical/Important finding with a new RED/GREEN test before claiming completion.

- [ ] **Step 7: Final verification and branch handoff**

Re-run Steps 2–4 after review fixes. Record exact test counts and commit SHA. Use `finishing-a-development-branch`; do not merge or push unless the user selects that option or has already explicitly authorized it.

