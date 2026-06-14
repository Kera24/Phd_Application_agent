# ScholarReach

A **LangGraph-based, human-in-the-loop PhD outreach agent** for Raj Kumar Sah.
It researches supervisors, identifies a real research gap, scores fit, renders a
tailored one-page research summary PDF, drafts a personalised email, and tracks
the full pipeline — but it is an **assistant, not an auto-mailer**. Nothing is
ever sent without explicit per-email approval through a LangGraph interrupt.

Two modes:

- **Reactive** — paste a LinkedIn post / URL for an advertised, fully funded
  position; the agent parses it, gates on funding, researches the supervisor,
  scores fit, and produces a quality-gated Gmail draft for approval.
- **Proactive** — prospect labs/professors in funded-by-default countries; each
  selected seed becomes a speculative opportunity run through the same graph
  with the speculative scoring rubric and the speculative email template.

## Architecture

```
Streamlit dashboard  ──HTTP──▶  FastAPI backend  ──invoke/resume──▶  LangGraph
     (app.py)                    (api/main.py)                      (agent/)
                                      │                                 │
                                      ▼                                 ▼
                                SQLAlchemy DB                    Checkpointer
                          (Supabase Postgres / SQLite)        (Postgres / SQLite)
```

- `agent/graph.py` — main graph + per-opportunity subgraph (fan-out via the
  `Send` API so one bad opportunity never blocks the batch).
- `agent/nodes.py` — node implementations; Funding Gate and Quality Review are
  **conditional edges decided in Python**, not prompts. Human Approval uses
  `interrupt()` + the checkpointer, resumed with `Command(resume=...)`.
- `agent/tools.py` — LangChain `@tool` functions (search, paper APIs, PDF,
  Gmail, timezone, scheduler, dedupe). `gmail_send_tool` is hard-gated.
- `agent/state.py` — `OutreachState` / `OppState` with merge reducers for
  parallel fan-out.
- `modules/` — domain logic (parser, professor research + citation
  verification, scoring rubrics, email generation, quality gate, summary PDF,
  scheduler, timezones, Gmail client).
- `db/models.py` — opportunities, professors, publications (verbatim API
  records + source URLs), research_gaps, emails, approvals, scheduled_emails,
  pipeline_events (append-only), assets, followups (Phase-3 schema only).

Graph diagrams (generated from the compiled graph): `docs/graph_main.mmd` and
`docs/graph_subgraph.mmd`. Regenerate with `python -m scripts.export_diagram`.

## Safety model (enforced in code, not prompts)

| Rule | Where enforced |
|---|---|
| Fully funded only | Funding Gate conditional edge (`nodes.funding_route`); non-funded → `archived_not_funded`, `unknown` → human review |
| Never send without approval | Scheduler reachable **only** from the approval interrupt (`tests/test_graph.py::test_safety_scheduler_only_reachable_via_approval`); `gmail_send_tool` + `gmail_client.send` additionally require `approved_send_mode` |
| No LinkedIn scraping | LinkedIn enters only as manually pasted text/URLs |
| No fabricated citations | `prof_research.verify_titles` exact-title match vs stored `publications`; fail → retry once → `needs_review` |
| Claims have source URLs | `publications.source_url` stored verbatim from the paper APIs |
| One professor, one email | `professors.email` unique constraint + dedupe checks before draft and again before send |
| No mass-mailing | Daily cap (`daily_send_cap`, default 10) + mandatory quality gate |
| Low confidence → `needs_review` | Routed explicitly in the graph; never silent |
| Robots/rate limits | `http_cache.get` (robots-aware, cached, rate-limited) |

## Setup

```powershell
cd scholarreach
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
```

### Environment variables

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # LLM parsing/research/scoring/drafting
$env:TAVILY_API_KEY    = "tvly-..."     # web search (discovery/prospecting)
$env:DATABASE_URL      = "postgresql://...supabase.co:5432/postgres"  # optional
```

- Without `ANTHROPIC_API_KEY` the app still runs using deterministic fallbacks
  (heuristic parser, keyword scoring, template-fill drafts).
- Without `DATABASE_URL` it falls back to local SQLite (`data/scholarreach.db`)
  — same schema. LangGraph checkpoints follow the same choice (Postgres saver
  when `DATABASE_URL` is Postgres and `langgraph-checkpoint-postgres` is
  installed, else `data/checkpoints.sqlite`).

### Supabase

Apply `migrations/001_init.sql` in the Supabase SQL editor (or
`psql $DATABASE_URL -f migrations/001_init.sql`), then set `DATABASE_URL`.
Regenerate the migration from the models with `python -m scripts.export_schema`.

### Gmail OAuth

1. In Google Cloud Console create an OAuth **Desktop app** client and download
   the client secret JSON to `data/gmail_credentials.json`.
2. First draft creation (or **Settings → Authorise Gmail**) opens the consent
   flow; the token is cached at `data/gmail_token.json`.
3. Scopes are minimal: `gmail.compose` for drafts; `gmail.send` is only used
   when send mode is on, and `gmail_client.send` refuses unless the email is
   `approved` **and** `approved_send_mode: true`.

### WeasyPrint on Windows

PDF generation needs the GTK runtime
(https://github.com/tschoonj/GTK-for-Windows-Runtime-Installer) or WSL. If it
is missing, the summary-PDF step is skipped with a logged warning and the rest
of the pipeline still works.

## Run

```powershell
# 1. Backend (graph invocation, approval queue, settings, OAuth)
uvicorn api.main:app --port 8000

# 2. Dashboard (talks only to the backend; set SCHOLARREACH_API to override URL)
streamlit run app.py
```

Dashboard pages: **Pipeline** (kanban by status), **Opportunities**,
**Add Opportunity** (paste text *or upload an image/PDF*), **Discover**
(proactive web search), **Prospecting**, **Professors**, **Profile** (your
application details), **Documents** (CV/transcript/SOP/etc. uploads), **Apply**
(application fill-plan + local fillers), **Approvals** (the interrupt queue),
**Analytics**, **Settings** (send-mode toggle, daily cap, fit threshold).

## How the approval flow works

1. A run (`POST /runs`) executes the graph: parse → funding gate → professor
   research (with citation verification) → gap → fit scoring → summary PDF →
   email writer → quality gate → Gmail draft.
2. The graph then **pauses** at the Human Approval node via LangGraph
   `interrupt()`; the paused state is persisted by the checkpointer and exposed
   at `GET /approvals`.
3. On the Approvals page Raj can **approve**, **edit-and-approve** (the quality
   gate re-runs on the edit), or **reject** — which resumes the thread via
   `POST /approvals/{thread_id}/resume` with `Command(resume=...)`.
4. Only approved emails — and only when `approved_send_mode: true` in
   `config/config.yaml` — reach the Scheduler, which books a send slot
   Mon–Thu 08:00–09:00 in the professor's local timezone (randomised minute,
   daily cap, pre-send dedupe + approval re-checks). Otherwise the email simply
   stays an approved Gmail draft.

## Application fillers (local, never auto-submit)

Two ways to fill an application form, both run on **your machine** in a visible
browser and **stop for you to review and submit** — nothing is submitted
automatically. Generate context from the **Apply** page (it shows the exact
commands), then run one locally. Setup once:

```bash
pip install -r requirements-local.txt
playwright install chromium
```

- **Option A — simple filler** (`scripts/fill_application.py`): uses the
  server-built fill-plan (profile → form fields) and fills standard HTML inputs.
  No LLM credits needed. Best for plain, static, publicly-reachable forms.

  ```bash
  python scripts/fill_application.py --api <backend> --opportunity <id> --cv cv.pdf
  ```

- **Option B — agentic browser** (`scripts/agent_fill.py`): a *browser harness*
  where **Claude drives the browser** step-by-step, so it adapts to JavaScript /
  multi-step portals the simple filler can't. Needs `ANTHROPIC_API_KEY` with
  credits (uses many calls). The ~80–90% path — CAPTCHA, logins and exotic
  widgets still need you (you handle them in the headed browser).

  ```bash
  python scripts/agent_fill.py --api <backend> --opportunity <id> --cv cv.pdf
  ```

Neither is a universal auto-applier: they accelerate filling and leave you in
control of login, CAPTCHA, anything not in your profile, and the final submit.

## Configuration

- `config/profile.yaml` — Raj's background (ground truth; never embellished).
- `config/config.yaml` — countries, fields, caps, send mode, thresholds, paths.
- `config/email_templates.yaml` — advertised + speculative templates and tone
  rules (120–180 words, subject ≤ 9 words, banned phrases).

## Tests

```powershell
python -m pytest -q
```

Covers timezone resolution (cities, US states, DST boundaries), scheduler
window logic (Mon–Thu 08–09 local, never Fri–Sun), dedupe, state-machine
transitions, quality-gate checks, the fully-funded hard filter, citation
verification, and LangGraph integration tests: the interrupt fires before any
send path, reject/approve flows, send-mode gating, and the structural safety
assertion that the Scheduler's only predecessor is Human Approval. Gmail is
mocked and paper APIs are canned — **no network, no real sends**.

## Build status

Phases 1–2 implemented: full graph + subgraph with checkpointing and
interrupts, FastAPI backend, Streamlit dashboard wired to the approval queue,
dual scoring rubrics, quality gate, summary PDF, Gmail OAuth + drafts,
timezone-gated scheduler, prospecting, append-only audit log, Supabase
migration. Phase 3 (reply detection, follow-up drafts, analytics) is schema
only — no logic, by design.

# Phd_Application_agent
