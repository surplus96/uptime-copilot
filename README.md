# Uptime Copilot

An OpenAI-based RAG + multi-agent backend, paired with a Streamlit frontend. Built around a
manufacturing equipment maintenance domain example, backed by the Azure Predictive
Maintenance dataset.

## Folder Structure

```
uptime-copilot/
├── archive/               Azure PdM raw CSVs (download required - see "Data Setup" below)
├── backend/                FastAPI backend
│   ├── main.py               Entry point (uvicorn main:app)
│   ├── core/                  Harness (input/output validation) + system prompt
│   ├── rag/                    RAG pipeline (hybrid search + Multi-Query) + docs/
│   │                             (`pump_manual.py` is a static lookup dict the agent reads
│   │                             directly — not retrieved via RAG; see Architecture Principles)
│   ├── agent/                  LangGraph multi-agent graph (routing + HITL + event scanner)
│   ├── data/                    PdM data ingestion/query layer + event store (SQLite)
│   ├── store/                    Generated state (pdm_telemetry.db, checkpoints.db,
│   │                             chroma_db/) — gitignored, separate from source so a
│   │                             Docker volume can be mounted here without shadowing code
│   ├── tests/                    pytest regression suite — see "Running the tests" below
│   ├── notify.py                 Slack alerting — optional, see Environment Variables
│   ├── cmms_client.py             CMMS work-order push — optional, see PHASE_7_PLAN.md
│   └── Dockerfile
├── frontend/                Streamlit chat UI
│   └── Dockerfile
└── docker-compose.yml       See "Running with Docker Compose" below
```

## Prerequisites

- Python ≥3.10 (developed and tested on 3.12; the codebase uses `X | None` union syntax throughout)
- Docker + Docker Compose, if you'd rather skip the manual venv setup below (see
  "Running with Docker Compose")

## Running with Docker Compose (recommended)

The dataset still has to be downloaded manually either way — it can't be redistributed
inside the image — so "Data Setup" below applies regardless of which path you take.

```bash
# 1) Data Setup (see below) — download the dataset into archive/ first

# 2) Configure
cp backend/.env.example backend/.env
# fill in backend/.env (OPENAI_API_KEY is required; everything else optional —
# see Environment Variables below)

# 3) Build and start both services
docker compose up -d --build
```

- Frontend: http://localhost:8501 — Backend: http://localhost:8000 (`/docs` for the
  interactive API)
- **First startup takes a few minutes**: the backend downloads the same ~470MB embedding
  model mentioned below, inside the container this time. `docker compose logs -f backend`
  to watch progress; it reports "starting" (not yet healthy) until that finishes, and the
  frontend container waits for it automatically — no action needed, just wait.
- **One-time data ingestion still has to be run once, inside the container**, the first
  time you bring the stack up (the image intentionally ships with no data baked in — see
  `backend/.dockerignore` — so a fresh `docker compose up` starts from an empty DB):
  ```bash
  docker compose exec backend python data/pdm_dataloader.py
  ```
- State (`backend/store/`: the SQLite DBs and the RAG index) lives in a named Docker
  volume, not in the image — it survives `docker compose down` / `up`. Only
  `docker compose down -v` wipes it (you'd need to re-run the ingestion step above
  afterward).
- Both services bind to `127.0.0.1` only, same as the manual setup below — nothing is
  exposed on your LAN.
- `docker compose down` to stop.

## Running Without Docker

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# fill in .env (OPENAI_API_KEY is required; everything else is optional - see
# Environment Variables below)
```

### Data Setup (one-time, after `pip install`, before the first run)

1. Download the [Microsoft Azure Predictive Maintenance dataset](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance)
   (PdM_machines.csv, PdM_errors.csv, PdM_maint.csv, PdM_failures.csv, PdM_telemetry.csv —
   PdM_telemetry.csv alone is ~870k rows, so ingestion takes a moment).
2. Place the files under `uptime-copilot/archive/`.
3. Run the one-time SQLite ingestion (from `backend/`, with the venv above active):
   ```bash
   python data/pdm_dataloader.py
   ```

### First run

```bash
uvicorn main:app --reload --port 8000
```

The very first startup also downloads the `intfloat/multilingual-e5-small` embedding
model (~470MB) from Hugging Face and builds the RAG index — allow a few minutes and
network access; it looks hung but isn't. Subsequent restarts are fast.

```bash
# Frontend (separate terminal)
cd frontend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Running the Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ --ignore=tests/test_rag_dedup.py   # fast path - skips the embedding-model test
pytest tests/                                     # full suite, downloads/loads the embedding model
```

## Environment Variables (`backend/.env`)

| Variable | Required | Effect if unset |
|---|---|---|
| `OPENAI_API_KEY` | **Yes** | Backend refuses to start |
| `OPENAI_MODEL` | No | Defaults to `gpt-5.6-luna` |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | No | LangSmith tracing disabled |
| `SLACK_WEBHOOK_URL` | No | Slack alerts on 긴급/주의 detections are silently skipped (`backend/notify.py`) |
| `CMMS_MCP_URL` + `CMMS_MCP_TOKEN` | No | CMMS work-order push on approval is silently skipped (`backend/cmms_client.py`); needs a running Atlas-MCP + Atlas CMMS instance if you do set these — see `PHASE_7_PLAN.md` |
| `ALLOWED_HOSTS` | No | Extra comma-separated hostnames allowed past `TrustedHostMiddleware` (`backend/main.py`), in addition to the always-allowed `localhost`/`127.0.0.1`. `docker-compose.yml` sets this to `backend` for you (its `environment:` block always wins over whatever you put in `backend/.env` for this key) — only needed manually if you put the backend behind another hostname or reverse proxy. |
| `BACKEND_URL` (frontend, not `backend/.env`) | No | Where the Streamlit app looks for the backend. Defaults to `http://localhost:8000`; `docker-compose.yml` sets it to `http://backend:8000` for you. |

> **Running the backend in Docker with an Atlas-MCP instance on the host:** `localhost`
> inside the `backend` container means the container itself, not your host machine, so
> `CMMS_MCP_URL=http://localhost:PORT/mcp` will fail to connect. Use
> `http://host.docker.internal:PORT/mcp` instead, and add `host.docker.internal:PORT` to
> Atlas-MCP's own `ALLOWED_HOSTS` (its DNS-rebinding Host-header check will otherwise
> reject the request with 421). `backend/cmms_client.py`'s own loopback check already
> allowlists `host.docker.internal` for this reason.

## Key API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/rag/query` | RAG-based document Q&A |
| POST | `/agent/query` | Multi-agent query — returns `pending_approval` on urgent findings |
| POST | `/agent/resume` | Resume the graph after an HITL approval/rejection decision |
| POST | `/simulate/tick` | Advance the runtime telemetry simulator by N hours (no UI button for this — curl or the `/docs` Swagger page only; without ticking, rescanning returns the same results every time) |
| POST | `/scan` | Sweep all 100 machines (no LLM), saving 긴급/주의 findings to the event store |
| GET | `/events` | List pending detected events |
| POST | `/events/complete` | Mark events completed — archived, and only re-surfaces on genuinely new evidence |
| POST | `/events/delete` | Discard events with no record kept (they can reappear on the next scan) |

Example `/agent/query` request:
```json
{"message": "Machine #12 has an error, what's wrong?", "thread_id": "unique-thread-id"}
```
If the response is `{"status": "pending_approval", "message": "..."}`, send
`{"thread_id": "...", "approved": true|false}` to `/agent/resume` with the same
`thread_id` to continue execution.

## Architecture Principles

- **Harness**: A deterministic layer that validates model input/output before and after
  each call (`core/harness.py`) — combining regex-based PII checks (computational) with
  LLM-as-judge checks (inferential).
- **RAG**: Multi-Query rewriting + hybrid (BM25 + Dense) retrieval, with automatic
  Faithfulness scoring.
- **Agent**: Built on LangGraph's `StateGraph`. Routes each question into
  diagnosis / maintenance-schedule / general-inquiry branches. Diagnoses use a
  three-tier severity model: 일반(normal) / 주의(caution — a Z-score telemetry
  anomaly, no confirmed failure) / 긴급(urgent — an actual logged failure record).
  Only 긴급 cases run three parallel perspective evaluations (safety / production /
  maintenance) and pause for Human-in-the-Loop (HITL) approval; 주의 cases skip
  straight to a work order. HITL approval state is checkpointed to SQLite
  (`backend/store/checkpoints.db`), not held only in memory, so it survives
  `--reload`/restarts. A separate `/scan` sweep runs the same diagnosis logic
  (no LLM) across all 100 machines and stores 긴급/주의 findings in an event store;
  a completed event only re-surfaces on a later scan once genuinely new evidence
  (postdating the completion time) appears — see `backend/data/event_store.py`.
- **Data**: All path constants are resolved relative to the file's own location
  (`Path(__file__).parent`) rather than the current working directory, so they remain
  correct regardless of where the code is run from or moved to. Generated/mutable
  state (`pdm_telemetry.db`, `checkpoints.db`, the RAG Chroma index) lives under
  `backend/store/`, kept separate from source code specifically so a Docker named
  volume can be mounted there without ever shadowing application code.
- **Observability**: Integrated with LangSmith; an anonymizer built from the same PII
  regex patterns masks sensitive data before traces are sent out.

## Related Documents

- `docs/PORTFOLIO.md` — architecture/design case study written for a portfolio audience
  (diagrams, key engineering decisions, debugging war-stories)
- `SESSION_SUMMARY.md` — running engineering log of past work sessions (a narrative log,
  not a reference — may lag behind the latest changes; verify against the code for
  anything load-bearing)
- `PHASE_7_PLAN.md` — **optional integration, not required to run this project.** Design/
  status record for Slack alerting + CMMS work-order push. With `SLACK_WEBHOOK_URL` /
  `CMMS_MCP_URL` / `CMMS_MCP_TOKEN` left unset, both features no-op and the app is fully
  functional without anything described in this file.
- `frontend/UI_UPGRADE_PLAN.md` — record of the frontend upgrade pass; fully closed out,
  kept as history rather than an active backlog
- `.claude/agents/README.md` — the nine review/diagnosis subagents installed in this repo
  (code quality, security, pipeline, docs, interface, debugger, build-doctor, performance,
  test-engineer) and when to reach for each
- `LICENSE` — MIT
