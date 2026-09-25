# Uptime Copilot

[![English](https://img.shields.io/badge/English-current-0c7c8c?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/한국어-switch-555555?style=for-the-badge)](README_KOR.md)

[![Backend checks](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml/badge.svg)](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml)

An OpenAI-based RAG + multi-agent backend, paired with a Streamlit frontend. Built around a
manufacturing equipment maintenance domain example, backed by the Azure Predictive
Maintenance dataset.

> 📄 **[Portfolio case study →](docs/PORTFOLIO.md)** — architecture diagrams, core logic, and key engineering decisions.

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
│   │                             (`sim_*.py`: automated runtime degradation simulator —
│   │                             see "Architecture Principles" and `docs/design/SIMULATOR_PLAN.md`)
│   ├── store/                    Generated state (pdm_telemetry.db, checkpoints.db,
│   │                             chroma_db/) — gitignored, separate from source so a
│   │                             Docker volume can be mounted here without shadowing code
│   ├── tests/                    pytest regression suite — see "Running the Checks" below
│   ├── notify.py                 Slack alerting — optional, see Environment Variables
│   ├── cmms_client.py             CMMS work-order push — optional, see docs/design/PHASE_7_PLAN.md
│   ├── pyproject.toml             ruff / mypy / pytest config — see "Running the Checks"
│   └── Dockerfile
├── frontend/                Streamlit chat UI
│   └── Dockerfile
├── .github/workflows/       CI: ruff + mypy + pytest on every push/PR
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
- **First startup can take up to ~10 minutes on a genuinely cold boot** (the healthcheck
  allows up to `start_period: 600s` for this): the backend downloads the same ~470MB
  embedding model mentioned below, inside the container this time. `docker compose logs -f
  backend` to watch progress; it reports "starting" (not yet healthy) until that finishes,
  and the frontend container waits for it automatically — no action needed, just wait.
  The model is cached in a named volume (`hf_cache`) afterward, so every subsequent
  `--build` is healthy again in seconds, not minutes.
- **One-time data ingestion still has to be run once, inside the container**, the first
  time you bring the stack up (the image intentionally ships with no data baked in — see
  `backend/.dockerignore` — so a fresh `docker compose up` starts from an empty DB):
  ```bash
  docker compose exec backend python data/pdm_dataloader.py
  ```
- State (`backend/store/`: the SQLite DBs and the RAG index) lives in a named Docker
  volume, not in the image — it survives `docker compose down` / `up`. Only
  `docker compose down -v` wipes it (you'd need to re-run the ingestion step above
  afterward — and re-download the embedding model, since `down -v` also drops the
  `hf_cache` volume mentioned above).
- Both services bind to `127.0.0.1` only, same as the manual setup below — nothing is
  exposed on your LAN.
- `docker compose down` to stop.
- **`docker-compose.yml` defaults the backend to `LLM_PROVIDER=ollama`** (the plain code
  default, e.g. when running the backend directly without Docker, is `openai` — this override
  lives in `docker-compose.yml`'s `environment:` block, which always wins over
  `backend/.env`; see Evaluation Baseline below for what that trade-off actually costs). For
  the container to reach it, start Ollama on the host with `OLLAMA_HOST=0.0.0.0 ollama serve`
  (its default, loopback-only, isn't reachable from inside the container) — `docker-compose.yml`
  already points `OLLAMA_BASE_URL` at `host.docker.internal` for you. Pull the model once
  with `ollama pull qwen3:8b`. Set `LLM_PROVIDER: openai` in `docker-compose.yml` instead if
  you'd rather the containers use the cloud model.

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
network access; it looks hung but isn't. Subsequent restarts are fast. If the download
stalls for minutes with no progress, set `HF_HUB_DISABLE_XET=1` in your shell before
starting uvicorn — the newer "xet" transfer path is blocked outright in some network
environments; `docker-compose.yml` already sets this for the container path.

```bash
# Frontend (separate terminal)
cd frontend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Running the Checks

```bash
cd backend
source .venv/bin/activate
pip install ruff mypy          # not in requirements.txt (dev-only)
ruff check .                   # lint + import order (config: pyproject.toml)
mypy                           # type checks data/ + cmms_client.py; must stay at 0 errors
pytest tests/ --ignore=tests/test_rag_dedup.py   # fast path - skips the embedding-model test
pytest tests/                                     # full suite, downloads/loads the embedding model
```

CI (`.github/workflows/backend-checks.yml`) runs `ruff check`, `mypy`, and the fast pytest
path on every push and pull request.

## Evaluation Baseline

A 40-case golden set (`backend/tests/eval/golden.jsonl`) checks the agent's router and
machine-ID extraction against real LLM calls. It's excluded from the default test run and
CI (costs real API tokens) — run it explicitly with:

```bash
cd backend
pytest tests/eval/test_golden.py -m eval -v -s
```

| Metric | Accuracy | Measured |
|---|---|---|
| Router (category) | 90.0% | 2026-09-23 |
| Machine-ID extraction | 100.0% | 2026-09-23 |
| Re-ask on missing ID | 100.0% | 2026-09-23 |

Each run writes a full per-case breakdown to `backend/tests/eval/results/<timestamp>.json`
(gitignored — regenerate rather than version-control). Expected severity is intentionally
*not* pinned in the golden set: the background simulator changes real machine state every
tick, so severity ground truth is computed live at eval time (`_diagnose_machine()`) rather
than baked into the file.

### Local (Ollama) vs cloud

The same golden set, run against a fully local `LLM_PROVIDER=ollama` (Qwen3-8B, Apple
Silicon, no GPU acceleration beyond Metal) instead of the cloud model:

| Metric | Cloud (gpt-5.6-luna) | Local (Ollama Qwen3-8B) |
|---|---|---|
| Router accuracy | 90.0% | 77.5% |
| Machine-ID extraction | 100.0% | 100.0% |
| Re-ask on missing ID | 100.0% | 100.0% |
| Mean latency / call | ~1.2s | 13.6s (routing) / 10.8s (extraction) |

Extraction and re-ask hold up identically locally — those are narrow, closed-form tasks.
Routing drops 12.5 points and every call is an order of magnitude slower. A spot check of
the safety/production/maintenance perspective assessments (not part of the golden set,
which only covers routing/extraction) surfaced a contradiction the local model produced
that the cloud model didn't in the same testing: `risk_level: "중간"` (medium) together
with `requires_shutdown: true` — internally inconsistent output that CP-U2's cross-review
(`docs/decisions.md`) had already flagged as a risk worth watching for. Bottom line: the
adapter (`LLM_PROVIDER=ollama`) works end-to-end today, but going fully local trades
measurable accuracy and an order of magnitude of latency for no API cost and full
air-gapped operation — worth it for a closed environment, not a drop-in free upgrade.

## Environment Variables (`backend/.env`)

| Variable | Required | Effect if unset |
|---|---|---|
| `LLM_PROVIDER` | No | Code default is `openai`; set to `ollama` to run entirely against a local Ollama server instead (`backend/core/llm_provider.py`) — no API key or internet needed, at a real accuracy/latency cost (see Evaluation Baseline below). **`docker-compose.yml` overrides this to `ollama`** for the containerized path regardless of what's in `.env` — see "Running with Docker Compose" above. |
| `OPENAI_API_KEY` | **Yes, unless `LLM_PROVIDER=ollama`** | Backend refuses to start. Not needed for the Docker path by default, since compose sets `LLM_PROVIDER=ollama`. |
| `OPENAI_MODEL` | No | Defaults to `gpt-5.6-luna`. Only used when `LLM_PROVIDER=openai` |
| `OLLAMA_BASE_URL` | No | Defaults to `http://localhost:11434/v1`. Only used when `LLM_PROVIDER=ollama` |
| `OLLAMA_MODEL` | No | Defaults to `qwen3:8b`. Only used when `LLM_PROVIDER=ollama` — pull it first with `ollama pull qwen3:8b` |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | No | LangSmith tracing disabled |
| `SLACK_WEBHOOK_URL` | No | Slack alerts on 긴급/주의 detections are silently skipped (`backend/notify.py`) |
| `CMMS_MCP_URL` + `CMMS_MCP_TOKEN` | No | CMMS work-order push on approval is silently skipped (`backend/cmms_client.py`); needs a running Atlas-MCP + Atlas CMMS instance if you do set these — see `docs/design/PHASE_7_PLAN.md` |
| `ALLOWED_HOSTS` | No | Extra comma-separated hostnames allowed past `TrustedHostMiddleware` (`backend/main.py`), in addition to the always-allowed `localhost`/`127.0.0.1`. `docker-compose.yml` sets this to `backend` for you (its `environment:` block always wins over whatever you put in `backend/.env` for this key) — only needed manually if you put the backend behind another hostname or reverse proxy. |
| `BACKEND_URL` (frontend, not `backend/.env`) | No | Where the Streamlit app looks for the backend. Defaults to `http://localhost:8000`; `docker-compose.yml` sets it to `http://backend:8000` for you. |
| `SIM_TICK_SECONDS` | No | Defaults to `60` — how often (real seconds) the background degradation simulator advances, when running (see below) |
| `SIM_HOURS_PER_TICK` | No | Defaults to `1` — simulated hours advanced per tick |
| `SIM_SEED` | No | Defaults to `42` — RNG seed for the simulator; a fresh `POST /simulator/reset` starts a new run from this seed |

> **`backend/.env.example` ships demo-tuned simulator values** (`SIM_TICK_SECONDS=15`,
> `SIM_HOURS_PER_TICK=12`), not the code defaults (`60`/`1`) shown above — this makes
> events show up in well under a minute for a live demo, at ~48x the code's default
> pace. Delete those two lines (or set them to `60`/`1`) for the slower, real-time-ish rate.

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
| POST | `/scan` | Sweep all 100 machines (no LLM), saving 긴급/주의 findings to the event store |
| GET | `/events` | List pending detected events |
| POST | `/events/complete` | Mark events completed — archived, and only re-surfaces on genuinely new evidence |
| POST | `/events/delete` | Discard events with no record kept (they can reappear on the next scan) |
| POST | `/simulator/start` | Start the automated background degradation simulator (resumes from its current state) |
| POST | `/simulator/stop` | Stop the background loop |
| GET | `/simulator/status` | Running state, simulated clock, degrading-machine list, `has_stale_events`, and `last_error`/`consecutive_failures` if a background tick has been failing |
| POST | `/simulator/inject` | Force a specific machine into a strong degradation, for demos (`{"machine_id": 12}`, optional `"signal"`: one of `volt`/`rotate`/`pressure`/`vibration`, random if omitted) |
| POST | `/simulator/reset` | Wipe simulator state/data **and** the detected/completed event tables — call this before a fresh run; nothing is cleared automatically. See `docs/design/SIMULATOR_PLAN.md` |

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
- **Automated simulator**: `backend/data/sim_engine.py`/`sim_store.py`/`sim_query.py`/`sim_loop.py`
  drive a background degradation model (state machine per machine: HEALTHY → DEGRADING →
  FAULT → failure+repair), calibrated against measured statistics from the real dataset
  (drift magnitude, lead time, error co-occurrence — see `docs/design/SIMULATOR_PLAN.md`). It writes to
  separate `sim_*` tables (never touching the original read-only data), an
  `asyncio` background task ticks it forward every `SIM_TICK_SECONDS`, and each tick
  triggers an incremental scan + a batched Slack alert for genuinely new detections. Fully
  optional — the app works identically with it stopped (the default on startup).
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
- `docs/design/SIMULATOR_PLAN.md` — design record for the automated degradation simulator: measured
  calibration data, state machine, background loop, and the `/simulator/*` API
- `docs/design/PHASE_7_PLAN.md` — **optional integration, not required to run this project.** Design/
  status record for Slack alerting + CMMS work-order push. With `SLACK_WEBHOOK_URL` /
  `CMMS_MCP_URL` / `CMMS_MCP_TOKEN` left unset, both features no-op and the app is fully
  functional without anything described in this file.
- `docs/design/UI_UPGRADE_PLAN.md` — record of the frontend upgrade pass; fully closed out,
  kept as history rather than an active backlog
- `LICENSE` — MIT
