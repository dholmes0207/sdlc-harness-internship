# Skill Routing

Two static tables for picking the RIGHT skill: by DOMAIN (decision trees, domain →
skill) and by WORKFLOW (intent phrase → start command). Pick ONE skill per distinct
user intent.

---

## Domain routing — by technical domain

When a user's task involves a specific domain, use these decision trees to pick the RIGHT skill based on user intent.

### Frontend / UI

```
User wants to...
├── Replicate a mockup, screenshot, or video    → /hs:frontend-design
├── Build React/TS components with best practices → /hs:frontend-development
├── Style with Tailwind CSS + shadcn/ui          → /hs:ui-styling
├── Choose colors, fonts, layout, design system  → /hs:ui-ux
├── Audit existing UI for accessibility/UX       → /hs:web-design-guidelines
├── Apply React performance patterns             → /hs:react-best-practices
├── Build with Stitch (AI design generation)     → /hs:stitch
├── Create 3D / WebGL / Three.js experience      → /hs:threejs
├── Write GLSL shaders / procedural graphics     → /hs:shader
└── Build programmatic video with Remotion       → /hs:remotion
```

### Codebase Understanding

```
User wants to...
├── Quick file search, locate specific code     → /hs:scout
├── Onboard a new repo / dump codebase for LLM  → /hs:repomix
├── Semantic go-to-definition, find-usages      → /hs:gkg
└── Build a queryable knowledge graph from code → /hs:graphify
```

### Backend / API

```
User wants to...
├── Build REST/GraphQL API (NestJS, FastAPI, Django) → /hs:backend-development
├── Add authentication (OAuth, JWT, passkeys)        → /hs:better-auth
└── Integrate payments (Stripe, Polar, SePay)        → /hs:payment-integration
```

### Database

```
User wants to...
├── Design schemas, write SQL/NoSQL queries     → /hs:databases
├── Optimize indexes, migrations, replication   → /hs:databases
└── Add auth with database-backed sessions      → /hs:better-auth
```

### Infrastructure / Deployment

```
User wants to...
├── Deploy to Vercel, Netlify, Railway, Fly.io   → /hs:deploy
└── Docker, Kubernetes, CI/CD pipelines, GitOps   → /hs:devops
```

### Security

```
User wants to...
├── STRIDE/OWASP audit, scan for secrets/vulnerabilities → /hs:security-scan
└── OSINT / CTI / threat-intel investigation            → /hs:cti-expert
```

### AI / LLM

```
User wants to...
├── Optimize context, agent architecture, memory → /hs:context-engineering
├── Generate llms.txt, LLM-friendly docs         → /hs:llms
├── Build AI agents with Google ADK              → /hs:google-adk-python
├── Generate/analyze images, audio, video with AI → /hs:ai-multimodal
└── Verified multi-source technical research      → /hs:research
```

### MCP (Model Context Protocol)

```
User wants to...
├── Build a new MCP server                       → /hs:mcp-builder
├── Convert existing code into CLI/MCP server    → /hs:agentize
└── Discover and execute MCP tools               → /hs:use-mcp
```

### Testing / Browser

```
User wants to...
├── Run test suites, coverage reports, TDD          → /hs:test
├── Test strategy + Playwright/Vitest/k6 runner     → /hs:web-testing
└── Drive a live browser                            → /hs:agent-browser
```

### Media

```
User wants to...
├── Process video/audio (FFmpeg), images (ImageMagick) → /hs:media-processing
└── Generate AI images (Imagen, Nano Banana)           → /hs:ai-artist
```

### Documentation

```
User wants to...
├── Update project docs (codebase-summary, PDR)   → /hs:docs
├── Search library/framework docs (context7)      → /hs:docs-seeker
├── Discover skills by capability / "is there a skill" → /hs:find-skills
├── Build docs site with Mintlify                 → /hs:mintlify
├── Inline doc diagrams (Mermaid v11)             → /hs:mermaidjs
├── Publish-grade SVG/PNG diagrams (architecture) → /hs:tech-graph
├── Read long-form docs / RFCs / specs in browser → /hs:markdown-novel-viewer
├── Generate session hand-off / EOD summary       → /hs:watzup
└── Sprint retrospective from git history         → /hs:retro
```

### Documents / Office Files

```
User wants to...
├── Create / edit / extract from .docx (Word)         → /hs:document-skills
├── Create / edit / extract from .pdf (forms, tables) → /hs:document-skills
├── Create / edit / extract from .pptx (PowerPoint)   → /hs:document-skills
└── Create / edit / extract from .xlsx (spreadsheets) → /hs:document-skills
```

### Content / Copy

```
User wants to...
├── Write landing page, email, headline copy     → /hs:copywriting
├── Brand identity, logos, banners               → /hs:design
└── Create Excalidraw diagrams                   → /hs:excalidraw
```

### Frameworks

```
User wants to...
├── Next.js App Router, RSC, Turborepo           → /hs:web-frameworks
├── TanStack Start/Form/AI                       → /hs:tanstack
├── React Native, Flutter, SwiftUI               → /hs:mobile-development
└── Shopify apps, Polaris, Liquid templates       → /hs:shopify
```

### Usage Notes

- Pick ONE skill per distinct user intent
- If a task spans two domains (e.g. "build + deploy"), suggest the primary skill and mention the secondary
- Domain skills combine with core workflow: `/hs:plan` → domain skill → `/hs:cook`
- Skills not listed here are either core workflow skills (see Workflow routing below) or utility skills activated on demand (e.g. `/hs:ask`, `/hs:preview`, `/hs:sequential-thinking`)

---

## Workflow routing — by task intent

When orchestrating multi-step tasks, consider these workflow sequences. Skills are listed in typical execution order.

### Core Development Workflow

```
/hs:plan → /hs:cook → /hs:test → /hs:code-review → /hs:ship → /hs:journal
```

| User Intent | Suggested Start |
|-------------|----------------|
| "implement feature X", "build X", "add X" | `/hs:plan` then `/hs:cook` |
| "execute this plan" | `/hs:cook <plan-path>` |
| "quick implementation" | `/hs:cook --fast` |

### Bugfix Workflow

```
/hs:scout → /hs:debug → /hs:fix → /hs:test → /hs:code-review
```

| User Intent | Suggested Start |
|-------------|----------------|
| "X is broken", "error in X", "bug in X" | `/hs:fix` (auto-scouts internally) |
| "CI is failing", "tests broken" | `/hs:fix --auto` |
| "investigate why X happens" | `/hs:scout` then `/hs:debug` |

### Investigation Workflow

```
/hs:scout → /hs:debug → /hs:brainstorm → /hs:plan
```

| User Intent | Suggested Start |
|-------------|----------------|
| "understand how X works" | `/hs:scout` |
| "why is X happening" | `/hs:debug` |
| "explore options for X" | `/hs:brainstorm` then `/hs:plan` |

### Post-Implementation Checklist

After completing implementation work, consider:
- `/hs:code-review` — review changes before merging
- `/hs:ship` — run full shipping pipeline (tests, review, version, PR)
- `/hs:journal` — document decisions and lessons learned

### Advisory & Intake

Before committing to build — reframe, decide, or intake:

| User Intent | Suggested Start |
|-------------|----------------|
| "should I build X?", "second opinion", "sanity-check this idea" | `/hs:advise` (one-question-at-a-time interview → honest verdict) |
| "turn this GitHub issue into a plan", "triage issue #N" | `/hs:issue-to-plan` (audit gate → plan, stops before implement) |
| "reason carefully about X", "this needs rigor, not a fast answer" | `/hs:fable-thinking` (evidence-typed, adversarial reasoning protocol) |

### Setup Skills

Before starting implementation in a shared codebase:
- `/hs:worktree` — create isolated worktree for the feature/fix
- `/hs:scout` — discover relevant files and code patterns
