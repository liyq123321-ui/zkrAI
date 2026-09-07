# PRD draw.io ER Diagrams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Generate draw.io ER diagrams only when PRD evidence warrants them, render them inline with pan/zoom, keep Diff text-only, and round-trip external draw.io edits into immutable reviewed PRD revisions.

**Architecture:** A pinned drawio-skill is copied into the otherwise isolated Codex runtime only for PRD generation and rewrite nodes. Draw.io XML lives in SpecVersion.content; stable Markdown anchor links keep Gitea review and Diff compact, while the PRD API supplies XML to a locally bundled viewer. External editor saves reuse the durable review-task table, are validated, materialized as n+1, automatically reviewed, and published through the existing Gitea PRD branch.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy, defusedxml, Codex CLI skills, React 19, TypeScript 5.8, react-markdown, Vitest, Testing Library, official draw.io viewer/embed protocol.

## Global Constraints

- Start from main commit 417e702b3aa6ec26fa1236a50f8a6b9a0ffdf914, verified against origin/main on 2026-09-06.
- Pin Agents365-ai/drawio-skill to 65f5fa0505f43d8af104d00c6087cb02c8c0e2f3 (version 3.2.1, MIT).
- Pin the local viewer to draw.io core 31.4.2 and retain its Apache-2.0 license and source metadata.
- Keep the Agent runtime --ignore-user-config, --skip-git-repo-check, ephemeral, read-only, and neutral except for the explicitly allowed PRD skill.
- Draw.io XML never appears in rendered Markdown or the PRD Diff API.
- Inline diagrams are read-only; editing happens only in the official external draw.io editor.
- External saves create immutable n+1 revisions, run automatic review, and never auto-approve or auto-decompose.
- Historical Specs without er_diagrams remain readable as an empty list.
- Do not add database columns; durable edit snapshots reuse ReviewTask.comment_snapshot JSON with an explicit discriminator.
- Per the user's instruction, do not commit during individual tasks. End each task with git diff --check. After user review, create branch ergraph and make the requested commit.

---

### Task 1: Pin and selectively load drawio-skill

**Files:**
- Create: backend/skills/drawio-skill/**
- Create: backend/skills/drawio-skill-source.json
- Modify: backend/app/agents/codex.py
- Modify: backend/tests/helpers/scripted_codex.py
- Test: backend/tests/unit/test_agent_gateway.py

**Interfaces:**
- Consumes: Settings.codex_home and the node name passed to CodexAgentGateway._run_node.
- Produces: a keyword-only bundled_skills tuple on CodexStructuredRunner.run; only PRD generation/rewrite receive the skill.

- [x] **Step 1: Write failing isolation tests**

~~~python
def test_prd_runner_receives_only_pinned_drawio_skill(tmp_path, monkeypatch):
    runner, env_log = configured_fake_runner(tmp_path, monkeypatch)
    asyncio.run(runner.run(
        "Generate PRD.", ProjectSpecPayload, tmp_path,
        bundled_skills=("drawio-skill",),
    ))
    runtime = json.loads(env_log.read_text(encoding="utf-8"))
    assert runtime["skill_names"] == ["drawio-skill"]
    assert runtime["config_exists"] is False
    assert runtime["agents_exists"] is False
~~~

Add one gateway test proving reviewer/decomposition nodes receive no skill, and one tamper test expecting AgentExecutionError with "bundled skill integrity check failed" before subprocess creation.

- [x] **Step 2: Run tests and verify RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_agent_gateway.py -k 'skill or ephemeral_codex_home' -q
~~~

Expected: FAIL because bundled_skills and skill discovery logging do not exist.

- [x] **Step 3: Vendor immutable skill provenance**

Copy the complete upstream skills/drawio-skill tree at the pinned commit and include the upstream MIT license. Add source metadata with name, version, source URL, commit, source Git tree ID, license, and one deterministic SHA-256 computed from sorted relative paths plus file bytes.

- [x] **Step 4: Implement verified selective copying**

~~~python
BUNDLED_SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills"
PRD_DRAWIO_NODES = frozenset({"pm_generate_spec", "pm_rewrite_prd"})

def install_bundled_skills(runtime_home: Path, names: tuple[str, ...]) -> None:
    destination = runtime_home / "skills"
    for name in names:
        if name != "drawio-skill":
            raise AgentExecutionError(f"bundled skill is not allowed: {name}")
        source = BUNDLED_SKILL_ROOT / name
        verify_bundled_skill(source, BUNDLED_SKILL_ROOT / f"{name}-source.json")
        destination.mkdir(exist_ok=True)
        shutil.copytree(source, destination / name)
~~~

Call this after auth.json is copied. Pass ("drawio-skill",) only for PRD_DRAWIO_NODES. Never copy config, AGENTS, memories, plugins, or .system skills.

- [x] **Step 5: Update fake logging and verify GREEN**

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_agent_gateway.py -q
git diff --check
~~~

Expected: PASS and the skill allowlist is exact.

---

### Task 2: Add ER contracts, XML validation, and Markdown anchors

**Files:**
- Create: backend/app/services/drawio_diagrams.py
- Modify: backend/app/domain/types.py
- Modify: backend/app/services/spec_service.py
- Modify: backend/app/agents/output_validation.py
- Modify: backend/requirements.txt
- Modify: backend/tests/helpers/factories.py
- Test: backend/tests/unit/test_drawio_diagrams.py
- Test: backend/tests/unit/test_contracts.py
- Test: backend/tests/integration/test_spec_service.py

**Interfaces:**
- Produces: ErDiagram, ErDiagramSection, validate_drawio_xml, normalize_drawio_xml, diagram_anchor.
- Consumes: ProjectSpecPayload, _render_markdown, structured-output validation.

- [x] **Step 1: Write failing contract/XML tests**

~~~python
def test_legacy_spec_defaults_er_diagrams_to_empty(valid_spec_dict):
    valid_spec_dict.pop("er_diagrams", None)
    assert ProjectSpecPayload.model_validate(valid_spec_dict).er_diagrams == []

def test_valid_erd_is_accepted():
    stats = validate_drawio_xml(MINIMAL_ERD_XML)
    assert (stats.entity_count, stats.relationship_count) == (2, 1)

@pytest.mark.parametrize("xml", [DOCTYPE_XML, SCRIPT_XML, EXTERNAL_IMAGE_XML, DANGLING_EDGE_XML])
def test_unsafe_or_invalid_erd_is_rejected(xml):
    with pytest.raises(InvalidDrawioDiagram):
        validate_drawio_xml(xml)
~~~

Also cover duplicate IDs, compressed diagrams, over 4 pages, over 150 entities, over 300 edges, XML over 1 MiB, HTML labels, executable links, and fewer than two entities.

- [x] **Step 2: Run tests and verify RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_drawio_diagrams.py tests/unit/test_contracts.py -q
~~~

Expected: import failure because the contracts and validator do not exist.

- [x] **Step 3: Implement strict diagram types**

~~~python
class ErDiagramSection(StrEnum):
    FUNCTIONAL_REQUIREMENTS = "functional_requirements"
    SYSTEM_BOUNDARIES = "system_boundaries"
    CORE_OBJECTS = "core_objects"
    MAIN_FLOWS = "main_flows"

class ErDiagram(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagram_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    title: str = Field(min_length=1, max_length=160)
    after_section: ErDiagramSection
    drawio_xml: str = Field(min_length=1, max_length=1_048_576)
~~~

Add er_diagrams: list[ErDiagram] = Field(default_factory=list, max_length=8) to ProjectSpecPayload. Validators call validate_drawio_xml and reject duplicate diagram IDs.

- [x] **Step 4: Implement safe parsing and anchors**

Use defusedxml.ElementTree.fromstring. Reject dangerous text before parsing.

~~~python
@dataclass(frozen=True, slots=True)
class DrawioDiagramStats:
    page_count: int
    entity_count: int
    relationship_count: int

def diagram_anchor(diagram: ErDiagram) -> str:
    normalized = normalize_drawio_xml(diagram.drawio_xml)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"firstflight-er-{diagram.diagram_id}-{digest}"
~~~

Allow only standard vertex/edge mxCell ER structures. Permit the skill's html=1 style flag but require label values to remain plain text. Reject actual label HTML, javascript:, remote images, link/event attributes, duplicate IDs, and missing edge endpoints.

- [x] **Step 5: Render link-only Markdown**

~~~python
for diagram in diagrams_by_section.get(key, ()):
    lines.extend(("", f"[ER 图：{diagram.title}](#{diagram_anchor(diagram)})"))
~~~

Keep current section ordering. Assert Markdown contains no mxGraphModel or mxCell.

- [x] **Step 6: Add output consistency checks and verify GREEN**

Reject duplicate titles and obvious entity-label conflicts with core_objects; do not infer absent fields or cardinality.

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_drawio_diagrams.py tests/unit/test_contracts.py tests/integration/test_spec_service.py -q
git diff --check
~~~

Expected: PASS, including legacy no-diagram fixtures.

---

### Task 3: Return version-correct diagrams from the PRD API

**Files:**
- Modify: backend/app/schemas/prd_review.py
- Modify: backend/app/services/prd_review.py
- Modify: backend/tests/integration/test_review_publish_pipeline.py
- Modify: backend/tests/integration/test_prd_review_api.py
- Modify: frontend/aios-main/src/api/dto.ts

**Interfaces:**
- Produces: PrdErDiagramRead and PrdDocumentRead.er_diagrams plus matching TypeScript DTO.
- Consumes: diagram_anchor, PrdVersion.spec_version_id, and SpecVersion.content_hash.

- [x] **Step 1: Write failing current/history tests**

~~~python
latest = client.get(f"/prd/{wi}").json()
historical = client.get(f"/prd/{wi}/v/1").json()
assert latest["er_diagrams"][0]["anchor"].endswith(v2_hash)
assert historical["er_diagrams"][0]["anchor"].endswith(v1_hash)
assert latest["er_diagrams"][0]["drawio_xml"] == V2_ERD_XML
assert "mxGraphModel" not in latest["content"]
~~~

Add corrupt content/hash expecting 409 PRD_CONTENT_CONFLICT, and legacy content expecting er_diagrams: [].

- [x] **Step 2: Run and verify RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/integration/test_prd_review_api.py -k diagram -q
~~~

Expected: FAIL because PrdDocumentRead has no diagrams.

- [x] **Step 3: Add transport contracts and preflight mapping**

~~~python
class PrdErDiagramRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagram_id: str
    title: str
    after_section: ErDiagramSection
    anchor: str
    drawio_xml: str

class PrdDocumentRead(BaseModel):
    er_diagrams: list[PrdErDiagramRead] = Field(default_factory=list)
~~~

In _preflight_binding, validate ProjectSpecPayload from the bound Spec, then map the exact version's diagrams and anchors after existing Markdown/structured hash checks.

- [x] **Step 4: Add the frontend DTO**

~~~ts
export interface PrdErDiagramDto {
  diagram_id: string;
  title: string;
  after_section: 'functional_requirements' | 'system_boundaries' | 'core_objects' | 'main_flows';
  anchor: string;
  drawio_xml: string;
}
~~~

Add er_diagrams to PrdDocumentDto and use document.er_diagrams ?? [] for compatibility.

- [x] **Step 5: Verify GREEN**

~~~bash
cd backend
.venv/bin/python -m pytest tests/integration/test_prd_review_api.py tests/integration/test_review_publish_pipeline.py -q
cd ../frontend/aios-main
npm test -- src/api/client.test.ts
git diff --check
~~~

Expected: current and historical versions return their own diagrams.

---

### Task 4: Require drawio-skill behavior in PRD generation/rewrite

**Files:**
- Modify: backend/prompts/nodes/pm_generate_spec.txt
- Modify: backend/prompts/nodes/pm_rewrite_prd.txt
- Modify: backend/prompts/nodes/reviewer_spec.txt
- Modify: backend/app/agents/codex.py
- Test: backend/tests/unit/test_agent_gateway.py
- Test: backend/tests/unit/test_output_repair.py
- Test: backend/tests/integration/test_pm_rewrite_command.py

**Interfaces:**
- Consumes: $drawio-skill, ProjectSpecPayload.er_diagrams, validate_node_output repair loop.
- Produces: explicit generation/rewrite rules and semantic-review consistency checks.

- [x] **Step 1: Write failing skill-selection and repair tests**

~~~python
assert generation_call.bundled_skills == ("drawio-skill",)
assert rewrite_call.bundled_skills == ("drawio-skill",)
assert reviewer_call.bundled_skills == ()
~~~

Add a repair test returning invalid XML on attempt one and valid XML on attempt two.

- [x] **Step 2: Run and verify RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_agent_gateway.py tests/unit/test_output_repair.py -k drawio -q
~~~

Expected: FAIL because node execution does not yet select the skill or validate diagrams.

- [x] **Step 3: Update generation/rewrite/reviewer instructions**

Generation must read $drawio-skill and required ER/XML references, return [] unless evidence identifies at least two persistent entities and a relationship, return uncompressed self-contained XML, and never write files/open GUI/fetch network. Rewrite preserves stable IDs/XML for unaffected relationships, updates affected diagrams after comments, removes obsolete diagrams, and never guesses fields/cardinality. Reviewer compares diagram labels/edges with core_objects, requirements, and flows but ignores layout-only differences.

- [x] **Step 4: Verify GREEN**

~~~bash
cd backend
.venv/bin/python -m pytest tests/unit/test_agent_gateway.py tests/unit/test_output_repair.py tests/integration/test_pm_rewrite_command.py -q
git diff --check
~~~

Expected: PASS and invalid diagrams use the existing three-attempt repair loop.

---

### Task 5: Persist external saves as durable reviewed revisions

**Files:**
- Create: backend/app/services/diagram_revision.py
- Modify: backend/app/schemas/prd_review.py
- Modify: backend/app/api/prd_review.py
- Modify: backend/app/services/pm_agent.py
- Modify: backend/app/services/spec_service.py
- Modify: backend/main.py
- Test: backend/tests/integration/test_diagram_revision.py
- Test: backend/tests/integration/test_prd_review_api.py
- Test: backend/tests/unit/test_database_schema.py

**Interfaces:**
- Produces: DiagramRevisionRequest, ReviewTaskAccepted, ReviewPublishCoordinator.create_or_resume_diagram_revision.
- Consumes: ReviewTask, reviewer authorization, external-base checks, Gitea publication, SpecService automatic review, PrdVersion projection.

- [x] **Step 1: Write failing API/durability tests**

~~~python
response = client.post(
    f"/prd/{wi}/diagrams/order-model/revisions",
    json={
        "actor_id": "owner-1",
        "base_version": 1,
        "base_commit_sha": base_sha,
        "drawio_xml": UPDATED_ERD_XML,
        "change_summary": "调整订单与支付关系",
    },
)
assert response.status_code == 202
task = run_background_task(response.json()["task_id"])
assert task.status == "done"
assert task.new_version == 2
assert load_spec(2).parent_version_id == load_spec(1).id
assert load_spec(1).content["er_diagrams"][0]["drawio_xml"] == ORIGINAL_ERD_XML
~~~

Also test identical normalized XML no-change, invalid XML 422 INVALID_DRAWIO_DIAGRAM, stale base 409, unauthorized actor 403, retry idempotency, and interrupted/partially published recovery.

- [x] **Step 2: Run and verify RED**

~~~bash
cd backend
.venv/bin/python -m pytest tests/integration/test_diagram_revision.py tests/integration/test_prd_review_api.py -k diagram_revision -q
~~~

Expected: FAIL with endpoint not found.

- [x] **Step 3: Add request/accepted contracts**

~~~python
class DiagramRevisionRequest(_ActorRequest):
    base_version: int = Field(gt=0)
    base_commit_sha: str = Field(min_length=1, max_length=128)
    drawio_xml: str = Field(min_length=1, max_length=1_048_576)
    change_summary: str = Field(min_length=1, max_length=8000)

class ReviewTaskAccepted(BaseModel):
    task_id: str
    base_version: int
    no_change: bool = False
~~~

Normalize text and map InvalidDrawioDiagram to HTTP 422.

- [x] **Step 4: Freeze edits without a schema migration**

~~~python
snapshot = [{
    "snapshot_type": "DRAWIO_REVISION",
    "diagram_id": diagram_id,
    "drawio_xml": normalize_drawio_xml(request.drawio_xml),
    "change_summary": request.change_summary,
}]
~~~

Store its canonical hash in comment_snapshot_hash. Keep comments/findings/decisions/replies empty. Add _is_diagram_revision_task and exclude these tasks from ordinary comment-revision retry selection.

- [x] **Step 5: Materialize and automatically review idempotently**

Clone the parent payload with only the target diagram replaced. Create a durable AgentCall operation diagram_edit whose request contains task/base/hash evidence and whose response contains the exact updated payload. Create one SpecVersion with generation_source HUMAN_DRAWIO_EDIT, parent ID, new content hash, link-only Markdown, and AUTO_REVIEW. Record ER_DIAGRAM_REVISION_CREATED with old/new hashes only. Allow the semantic-review preparation path to use diagram_edit as a deterministic generation receipt, then call _run_automatic_review.

- [x] **Step 6: Reuse Gitea publication stages**

~~~python
if self._is_diagram_revision_task(task_id):
    version_id = await self._diagram_revisions.materialize_and_review(task_id)
    self._record_revised_version(task_id, version_id)
    await self._publish_file(task_id)
    self._project_version(task_id)
else:
    await self._run_comment_revision(task_id)
~~~

Diagram tasks skip publish_replies. Retain current base/branch checks, idempotent file publication, projection, completion, safe errors, and interrupted-task behavior.

- [x] **Step 7: Add endpoint and application wiring**

~~~python
@router.post(
    "/prd/{wi}/diagrams/{diagram_id}/revisions",
    response_model=ReviewTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revise_diagram(
    wi: str,
    diagram_id: str,
    request_body: DiagramRevisionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ReviewTaskAccepted:
    actor_id = actor_resolver.resolve(request, request_body.actor_id)
    task, no_change = await coordinator.create_or_resume_diagram_revision(
        wi, diagram_id, actor_id, request_body
    )
    if not no_change:
        background_tasks.add_task(coordinator.run, task.id)
    return ReviewTaskAccepted(
        task_id=task.id, base_version=task.base_version, no_change=no_change
    )
~~~

Use the existing coordinator so GET /tasks and lifespan interruption remain centralized.

- [x] **Step 8: Verify GREEN**

~~~bash
cd backend
.venv/bin/python -m pytest tests/integration/test_diagram_revision.py tests/integration/test_prd_review_api.py tests/integration/test_review_publish_pipeline.py tests/unit/test_database_schema.py -q
git diff --check
~~~

Expected: PASS with no database column added.

---

### Task 6: Render the local read-only diagram component

**Files:**
- Create: frontend/aios-main/public/drawio/31.4.2/viewer-static.min.js
- Create: frontend/aios-main/public/drawio/31.4.2/LICENSE
- Create: frontend/aios-main/public/drawio/31.4.2/SOURCE.json
- Create: frontend/aios-main/src/api/DrawioErDiagram.tsx
- Create: frontend/aios-main/src/api/drawioViewer.ts
- Modify: frontend/aios-main/src/api/PrdDocumentViews.tsx
- Modify: frontend/aios-main/src/api/PrdReviewPanel.tsx
- Test: frontend/aios-main/src/api/DrawioErDiagram.test.tsx
- Test: frontend/aios-main/src/api/PrdDocumentViews.test.tsx

**Interfaces:**
- Produces: DrawioErDiagram and singleton loadDrawioViewer.
- Consumes: PrdErDiagramDto, Markdown anchors, local viewer-static.min.js.

- [x] **Step 1: Write failing component/document tests**

~~~tsx
render(<PrdDocumentViews
  content={markdownWithDiagram}
  diagrams={[diagram]}
  version={1}
  commitSha="base-sha"
  patch={markdownPatch}
  commentable={null}
  ready={false}
  selectedLine={null}
  onSelectLine={() => undefined}
/>);
await user.click(screen.getByRole('button', { name: '正文' }));
expect(screen.getByRole('region', { name: 'ER 图：订单模型' })).toBeInTheDocument();
expect(screen.getByRole('button', { name: '放大' })).toBeInTheDocument();
expect(screen.queryByText(/mxGraphModel/)).not.toBeInTheDocument();

await user.click(screen.getByRole('button', { name: 'Diff' }));
expect(screen.queryByRole('region', { name: 'ER 图：订单模型' })).not.toBeInTheDocument();
expect(screen.getByText(/ER 图：订单模型/)).toBeInTheDocument();
~~~

Also test ordinary links, missing-diagram fallback, script failure, download filename, keyboard-accessible controls, and multiple diagrams.

- [x] **Step 2: Run and verify RED**

~~~bash
cd frontend/aios-main
npm test -- src/api/DrawioErDiagram.test.tsx src/api/PrdDocumentViews.test.tsx
~~~

Expected: FAIL because the component and diagrams prop do not exist.

- [x] **Step 3: Vendor viewer provenance**

Copy viewer-static.min.js from draw.io core 31.4.2, retain Apache-2.0 LICENSE, and record source, version, and the computed file SHA-256 in SOURCE.json.

- [x] **Step 4: Implement loader and read-only shell**

loadDrawioViewer reuses one Promise, loads only the local asset, checks window.GraphViewer, and rejects after onerror or a bounded timeout. The component config is:

~~~tsx
const config = {
  highlight: '#22d3ee',
  nav: true,
  resize: true,
  toolbar: 'zoom layers lightbox',
  xml: diagram.drawio_xml,
};
~~~

Call GraphViewer.createViewerForElement. Add labelled controls for zoom, fit, reset, fullscreen, drawio download, and external editing. No XML links/scripts may execute.

- [x] **Step 5: Replace recognized Markdown anchors only in正文**

Build Map(anchor -> diagram). The custom link renderer returns DrawioErDiagram only for a matching firstflight anchor; all ordinary links retain target=_blank and rel=noreferrer. Diff continues to use diffRows plain text and never receives XML.

- [x] **Step 6: Verify GREEN**

~~~bash
cd frontend/aios-main
npm test -- src/api/DrawioErDiagram.test.tsx src/api/PrdDocumentViews.test.tsx src/api/workspace.test.tsx
npm run lint
git diff --check
~~~

Expected: PASS with no TypeScript errors.

---

### Task 7: Round-trip external draw.io edits and preserve drafts

**Files:**
- Create: frontend/aios-main/src/api/drawioEmbed.ts
- Create: frontend/aios-main/src/api/drawioEmbed.test.ts
- Modify: frontend/aios-main/src/api/DrawioErDiagram.tsx
- Modify: frontend/aios-main/src/api/prd.ts
- Modify: frontend/aios-main/src/api/dto.ts
- Modify: frontend/aios-main/src/api/PrdReviewPanel.tsx
- Test: frontend/aios-main/src/api/DrawioErDiagram.test.tsx
- Test: frontend/aios-main/src/api/workspace.test.tsx

**Interfaces:**
- Produces: openDrawioEditorSession, publishDiagramRevision, draft storage keyed by wi/diagram/base version.
- Consumes: https://embed.diagrams.net JSON protocol, base version/commit, Task 5 endpoint.

- [x] **Step 1: Write failing protocol/security tests**

~~~ts
const session = openDrawioEditorSession({ xml, expectedOrigin, openWindow });
dispatchMessage({ origin: 'https://evil.example', source: popup, data: { event: 'save', xml: attack } });
expect(onSave).not.toHaveBeenCalled();

dispatchMessage({ origin: expectedOrigin, source: popup, data: { event: 'init' } });
expect(popup.postMessage).toHaveBeenCalledWith(
  expect.objectContaining({ action: 'load', xml }),
  expectedOrigin,
);
~~~

Also test malformed JSON, wrong window, disposal, popup blocking, close without save, conflict, task success, and localStorage recovery.

- [x] **Step 2: Run and verify RED**

~~~bash
cd frontend/aios-main
npm test -- src/api/drawioEmbed.test.ts src/api/DrawioErDiagram.test.tsx
~~~

Expected: FAIL because the helper does not exist.

- [x] **Step 3: Implement origin-bound embed protocol**

~~~ts
export const DRAWIO_EMBED_ORIGIN = 'https://embed.diagrams.net';
export const DRAWIO_EMBED_URL =
  DRAWIO_EMBED_ORIGIN + '/?embed=1&proto=json&spin=1&libraries=0';
~~~

Accept messages only for the exact origin, popup source, active session, and init/save/exit event. On init send action load with XML and autosave 0. On save, persist a draft before callback; send exit only after backend acceptance.

- [x] **Step 4: Add API and draft contracts**

~~~ts
export const publishDiagramRevision = (
  wi: string,
  diagramId: string,
  input: {
    base_version: number;
    base_commit_sha: string;
    drawio_xml: string;
    change_summary: string;
  },
) => apiClient.request<ReviewTaskAcceptedDto>(
  '/prd/' + wi + '/diagrams/' + diagramId + '/revisions',
  { method: 'POST', body: input },
);
~~~

Draft fields are wi, diagramId, baseVersion, baseCommitSha, xml, savedAt. Ask for a non-empty change summary before submission.

- [x] **Step 5: Wire polling and recovery**

Pass wi/version/commit from PrdReviewPanel. On 409 retain the exact draft and explain the stale base. On accepted task reuse the current banner/polling. Clear the draft and reload正文/Diff only when task becomes done. Provide retry and download-draft actions after failure.

- [x] **Step 6: Verify GREEN**

~~~bash
cd frontend/aios-main
npm test -- src/api/drawioEmbed.test.ts src/api/DrawioErDiagram.test.tsx src/api/workspace.test.tsx
npm run lint
git diff --check
~~~

Expected: hostile messages cannot save; failures retain drafts; successful tasks refresh the PRD.

---

### Task 8: Documentation, full verification, and user review build

**Files:**
- Modify: README.md
- Modify: backend/readme.md
- Modify: CHANGELOG.md
- Modify: docs/openapi.json
- Modify: the approved design only if implementation exposes a required correction

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: documented behavior, regenerated API snapshot, full test evidence, and a local review build.

- [x] **Step 1: Add failing OpenAPI assertions**

~~~python
assert "/prd/{wi}/diagrams/{diagram_id}/revisions" in schema["paths"]
assert "er_diagrams" in schema["components"]["schemas"]["PrdDocumentRead"]["properties"]
~~~

- [x] **Step 2: Regenerate OpenAPI and update docs**

Document pinned sources/update procedure, ER generation criteria, pan/zoom, external save creating n+1, link-only Diff, draft recovery, and stale-version behavior. Regenerate docs/openapi.json through the repository's existing generator, not by hand.

- [x] **Step 3: Run all backend verification**

~~~bash
cd backend
.venv/bin/python -m pytest -q
~~~

Expected: zero failures.

Actual on 2026-09-06: 726 passed; the same two pre-existing clarification-flow
failures reproduced on the clean main baseline remain in
tests/integration/test_sessions_api.py.

- [x] **Step 4: Run all frontend verification**

~~~bash
cd frontend/aios-main
npm test
npm run lint
npm run build
~~~

Expected: tests PASS, TypeScript has no errors, and Vite build succeeds.

- [x] **Step 5: Browser verification**

Start the existing backend/frontend development commands and inspect: no-diagram PRD; one diagram with pan/zoom/reset/fullscreen/download; Diff with name/link only; external init/save; saved n+1 automatic-review flow; and stale edit draft recovery. Record exact local URLs for user review.

- [x] **Step 6: Final dirty-tree review**

~~~bash
git diff --check
git status --short
git diff --stat
~~~

Expected: only intended files, no whitespace errors, and no commit.

- [x] **Step 7: Stop for user approval**

Report verification and local review URL. Remind the user a Git-saveable version is ready. Do not create ergraph or commit until explicit approval. After approval:

~~~bash
git switch -c ergraph
git add backend frontend README.md CHANGELOG.md docs/openapi.json docs/superpowers/specs/2026-09-06-prd-drawio-er-diagrams-design.md docs/superpowers/plans/2026-09-06-prd-drawio-er-diagrams.md
git commit -m "feat: add drawio ER diagrams to PRDs"
~~~

Git branch names cannot start with slash, so the valid interpretation of /ergraph is ergraph.
