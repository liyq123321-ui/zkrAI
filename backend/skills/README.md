# Project Agent Skills

PRD creation, diagram routing, visual review, prototype approval and Spec
decomposition follow [`docs/prd-spec-skill-workflow.md`](../../docs/prd-spec-skill-workflow.md).

The 20 directories in this folder are the project's bundled Skill catalog. Each
Skill has a matching `<name>-source.json` manifest. `app.agents.codex` checks the
canonical SHA-256 tree digest before copying a selected Skill into the isolated
Codex runtime. Editing any Skill requires updating that manifest; a digest only
pins the local snapshot and does not prove that a third-party source is safe.

The runtime allowlist includes all 20 Skills. A Skill is copied **only when its
call site requests it** through `bundled_skills`. This avoids loading unrelated
instructions and large assets on every Agent call. Current automatic routes are:

| Agent work | Bundled Skill |
| --- | --- |
| Structured PRD generation and rewrite | `drawio-skill` |
| Spec decomposition, revision, task planning and review | `planning-and-task-breakdown` |
| Block PRD authoring and review | The Skills selected in `app.ai.codex_provider` |
| Frozen PRD presentation planning | `ppt-generation` for the structured slide plan |
| Editable PPTX creation and validation | `officecli-skill` via its checksum-verified local CLI |

Other allowed Skills can be selected by a project call site when its task actually
needs them. The generic guidance in a Skill never overrides an approved Spec,
the SDLC selection and execution rules, or an explicit human decision.

Some bundled Skills have additional execution requirements:

- `chart-visualization/scripts/generate.js` sends chart data to an external
  service. Do not use it for private PRD data without explicit authorization.
- `mermaid-skill` uses mermaid.ink or a CDN for rendering. Markdown Mermaid
  source can be generated locally; remote rendering needs authorization.
- `officecli-skill` includes a local Windows executable. Loading its Skill does
  not verify or execute that binary. Use it only from an explicitly selected
  document workflow and validate the executable before deployment.
- `ppt-generation` references environment-specific image and composition tools.
  Loading its instructions does not install those tools.

The 12 third-party Skill directories added locally and their manifests must be
tracked in the deployment checkout for another host to load them. The
`ooxml-edit-skill` manifest records a local curated snapshot without claiming an
upstream commit.
