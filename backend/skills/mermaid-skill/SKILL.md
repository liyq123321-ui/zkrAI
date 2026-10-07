---
name: mermaid-skill
description: Generate Mermaid diagrams (flowchart, sequence, class, state, ER, Gantt, pie, etc.) as SVG, PNG, or interactive HTML. Use when the user wants a diagram from text-based Mermaid syntax, or wants to render, preview, or convert Mermaid diagrams. Prefer this over drawio-skill for quick text-to-diagram needs and when editable draw.io output is not required.
license: MIT
metadata:
  version: "1.0.0"
  source: "https://github.com/mermaid-js/mermaid"
  renderer: "mermaid.ink + CDN HTML"
---

# Mermaid Diagram Skill

Generate diagrams from Mermaid text syntax. Outputs SVG, PNG, or interactive HTML.

## Diagram Types

| Type | Syntax | Use Case |
|------|--------|----------|
| Flowchart | `graph TD/LR/BT/RL` | Process flows, decision trees |
| Sequence | `sequenceDiagram` | Interaction sequences, API flows |
| Class | `classDiagram` | OOP class structures |
| State | `stateDiagram-v2` | State machines, lifecycle |
| ER | `erDiagram` | Entity relationships, data models |
| Gantt | `gantt` | Project timelines |
| Pie | `pie` | Proportional charts |
| Git | `gitGraph` | Git branch history |
| Journey | `journey` | User journey maps |
| Mindmap | `mindmap` | Idea hierarchies |
| Timeline | `timeline` | Event timelines |
| Requirement | `requirementDiagram` | Requirement tracking |
| Architecture | `architecture-beta` | System architecture (beta) |
| Sankey | `sankey-beta` | Flow quantities (beta) |

## How to Use

### Option 1: Generate HTML Preview (recommended)

Create a self-contained HTML file that renders the diagram via Mermaid CDN.
Open the file in any browser to view, zoom, and interact.

```bash
python3 scripts/render_mermaid.py --input diagram.mmd --output diagram.html --format html
```

### Option 2: Generate SVG / PNG via mermaid.ink

Use the mermaid.ink service to render server-side SVG or PNG.
Good for embedding in documents.

```bash
python3 scripts/render_mermaid.py --input diagram.mmd --output diagram.svg --format svg
python3 scripts/render_mermaid.py --input diagram.mmd --output diagram.png --format png
```

### Option 3: Inline Mermaid in Markdown

For README.md or documentation, just write fenced code blocks:

```mermaid
graph TD
    A[Start] --> B{Is it working?}
    B -->|Yes| C[Great!]
    B -->|No| D[Debug]
    D --> B
```

Most modern platforms (GitHub, GitLab, Notion, Obsidian) render these natively.

## Best Practices

1. **Start simple.** Begin with a minimal flowchart, then add detail.
2. **Use meaningful IDs.** `UserDB` not `node1`; makes the source readable.
3. **Keep diagrams focused.** One diagram = one message. Split complex flows into multiple diagrams.
4. **Label directions clearly.** For flowcharts, prefer `LR` (left-to-right) for horizontal flows, `TD` (top-down) for hierarchies.
5. **Add titles.** Use `---` title syntax to give context.

## Examples

### Flowchart

```mermaid
graph LR
    A[User Request] --> B[API Gateway]
    B --> C{Authenticated?}
    C -->|Yes| D[Backend Service]
    C -->|No| E[401 Unauthorized]
    D --> F[Database]
    F --> G[Response]
    G --> H[Client]
```

### Sequence Diagram

```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant API
    participant DB

    User->>Frontend: Click Login
    Frontend->>API: POST /login
    API->>DB: Query user
    DB-->>API: User data
    API->>API: Verify password
    alt Success
        API-->>Frontend: 200 + token
        Frontend-->>User: Show dashboard
    else Failure
        API-->>Frontend: 401
        Frontend-->>User: Error message
    end
```

### ER Diagram

```mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    CUSTOMER {
        string id PK
        string name
        string email
    }
    ORDER ||--|{ ORDER_ITEM : contains
    ORDER {
        string id PK
        string customer_id FK
        date order_date
        decimal total
    }
    ORDER_ITEM }o--|| PRODUCT : references
    ORDER_ITEM {
        string id PK
        string order_id FK
        string product_id FK
        int quantity
        decimal price
    }
    PRODUCT {
        string id PK
        string name
        decimal price
        int stock
    }
```

### Gantt Chart

```mermaid
gantt
    title Project Timeline
    dateFormat  YYYY-MM-DD
    section Phase 1
    Requirements     :a1, 2026-01-01, 14d
    Design           :a2, after a1, 10d
    section Phase 2
    Implementation   :b1, after a2, 20d
    Testing          :b2, after b1, 10d
    section Launch
    Deployment       :c1, after b2, 3d
    Review           :c2, after c1, 4d
```

## Troubleshooting

- **Syntax error?** Check for missing semicolons, mismatched quotes, or unclosed blocks.
- **Diagram too big?** Split into smaller diagrams or use subgraphs for organization.
- **mermaid.ink timeout?** Use HTML format (local CDN render) instead.
- **Not rendering in Markdown?** Verify the platform supports Mermaid (GitHub, GitLab do; some don't).

## References

- [Mermaid Official Docs](https://mermaid.js.org/)
- [Mermaid Live Editor](https://mermaid.live/)
- [mermaid.ink Service](https://mermaid.ink/)
