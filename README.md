# Uptime Copilot

[![English](https://img.shields.io/badge/English-current-0c7c8c?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/한국어-switch-555555?style=for-the-badge)](README_KOR.md)

[![Backend checks](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml/badge.svg)](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml)

**Uptime Copilot — Predictive Maintenance & MRO Copilot**

A RAG + multi-agent backend (FastAPI/LangGraph) with a Streamlit frontend, built around a
manufacturing maintenance example on the Azure Predictive Maintenance dataset. Extended with
an MRO (parts/inventory/procurement) layer and closed-network security operations (offline
mode, audit log) — see MRO Extension below.

> 📄 **[Portfolio case study →](docs/PORTFOLIO.md)** — architecture, design decisions, engineering war stories.

> **Independent project inspired by Hanwha's public materials on TOMMS/HUMS** — not
> affiliated with, endorsed by, or built for Hanwha. All part numbers, stock levels, lead
> times, costs, and end-of-life dates are synthetic. The replacement history behind the
> demand forecast comes from the public Azure PdM dataset, itself simulated data.

## MRO Extension (parts, demand forecast, inventory risk, offline mode, audit log)

A parts master, demand forecast, and inventory-risk dashboard on top of the core pipeline,
plus offline mode and an audit log for closed-network deployment. Plan and decision log:
[mro-copilot-upgrade-plan.md](mro-copilot-upgrade-plan.md), [docs/decisions.md](docs/decisions.md).

- **Parts master** (`backend/data/parts_master.py`) — 5 synthetic parts (comp1–4 + one
  end-of-life alternate), auto-seeded on startup. All part numbers, stock, lead times, and
  the end-of-life date are synthetic — see [docs/design/parts_assumptions.md](docs/design/parts_assumptions.md).
- **Demand forecast** (`backend/data/demand_forecast.py`) — 30/90-day replacement forecast
  per part: a preventive term (scheduled, model-independent) plus a failure term (today's
  actual alarm state, future days at the population base rate — mixing these up caused a
  real bug, see `docs/decisions.md` 2026-09-28). Backtested against real Nov–Dec 2015
  replacement counts, fitted only on Jan–Oct data: **-5.0% to +3.5% error**. This validates
  the base rates, not the alarm term — the alarm term itself adds only 0–2 of ~120 units.
- **Inventory-risk tab** — `GET /parts/inventory_risk`. Numbers are rounded estimates that
  don't account for stock already on order.
- **Priority stays separate from parts availability** — a missing part doesn't inflate
  urgency; it shows as its own `[조달 긴급도]` line on the work order.
- **Offline mode** (`backend/core/offline_guard.py`) — a destination allowlist (`localhost`
  / `127.0.0.1` / `host.docker.internal`), not a per-module switch. With `OFFLINE=1` the app
  refuses to start on an unrecognised value, `LLM_PROVIDER=openai`, an endpoint outside the
  allowlist, an ambiguous URL, or a proxy that would reroute an allowlisted host (`NO_PROXY`
  must cover them, env vars only — not OS-level proxy settings); it forces
  `HF_HUB_OFFLINE=1` (the embedding model must already be cached — an `OFFLINE=1` first run
  on an empty cache fails at startup instead of hanging) and disables LangSmith tracing
  before anything else imports (holds for the `main.py` entry point only), skips Slack, and
  refuses CMMS pushes outside the allowlist.
  CI (`test_offline_guard.py`) blocks real sockets and drives diagnose → approve with a
  stubbed LLM and stubbed diagnosis. **Not covered by that test**: proxy routing and DNS/
  redirects (proxies are refused at startup instead); startup and RAG initialization
  (verified separately, sockets blocked, not in CI).
- **Audit log** (`backend/data/audit_log.py`) — every LLM-using step (failures included),
  the parts lookup, approvals/rejections, and Slack/CMMS pushes with their real outcome
  (sent / failed / not configured / blocked), each with a timestamp, thread ID, event type,
  and result. **Not audited**: diagnosis data lookups and the scan's parts lookups; a
  retried call, and a RAG answer together with its faithfulness judge, each show as one
  row. Free
  text is PII-masked (national ID, card, phone, email — not names or addresses). Query via
  `GET /audit_log` or the Streamlit app's 5th tab.

## Folder Structure

```
uptime-copilot/
├── archive/               Azure PdM raw CSVs (download required - see "Data Setup" below)
├── backend/                FastAPI backend
│   ├── main.py               Entry point (uvicorn main:app)
│   ├── core/                  Harness (input/output validation), LLM provider adapter, offline guard
│   ├── rag/                    RAG pipeline (hybrid BM25+dense search) + docs/
│   ├── agent/                  LangGraph multi-agent graph (routing + HITL + event scanner)
│   ├── ml/                      Failure-risk model: feature pipeline, training, prediction
│   ├── data/                    PdM data layer + event store, parts master, demand forecast,
│   │                             audit log, degradation simulator (SQLite)
│   ├── store/                    Generated state (gitignored) — Docker volume mounts here
│   ├── tests/                    pytest regression suite — see "Running the Checks" below
│   ├── notify.py                 Slack alerting — optional, see Environment Variables
│   ├── cmms_client.py             CMMS work-order push — optional, see docs/design/PHASE_7_PLAN.md
│   └── Dockerfile
├── frontend/                Streamlit chat UI
│   └── Dockerfile
├── .github/workflows/       CI: ruff + mypy + pytest on pushes to main and on every PR
└── docker-compose.yml       See "Running with Docker Compose" below
```

## Prerequisites

- Python 3.12 (what CI and Docker use; dependencies are unpinned and the current
  resolution needs ≥3.12 — untested on older versions)
- Docker + Docker Compose, if you'd rather skip the manual venv setup below

## Running with Docker Compose (recommended)

The dataset has to be downloaded manually either way (can't be redistributed in the image),
so "Data Setup" below applies regardless of which path you take.

```bash
# 1) Data Setup (see below) — download the dataset into archive/ first

# 2) Configure
cp backend/.env.example backend/.env
# fill in backend/.env — an OpenAI key is NOT needed here (compose defaults to local
# Ollama); see Environment Variables below

# 3) Build and start both services
docker compose up -d --build
```

- Frontend: http://localhost:8501 — Backend: http://localhost:8000 (`/docs` for the API)
- **First cold boot can take ~10 minutes** (healthcheck allows `start_period: 600s`): the
  backend downloads the ~470MB embedding model inside the container. `docker compose logs -f
  backend` to watch; it's cached in the `hf_cache` volume afterward, so later builds are
  healthy in seconds.
- **Runtime data is initialized on first startup** from the mounted `archive/` CSVs.
  The newest source timestamp is shifted to the current Asia/Seoul hour; operational
  records are restricted to that calendar year (2026 for this deployment). Historical
  incidents are reference data; new incident detection starts after the stored anchor.
  Existing databases are backed up before the one-time migration. Training CSVs remain
  unchanged. See [runtime dataset plan](docs/design/RUNTIME_2026_DATASET_PLAN.md).
  To explicitly reload the source using the same stored date mapping:
  ```bash
  docker compose exec backend python -m data.pdm_dataloader
  ```
- **The failure-risk model needs the same one-time step** — without it, diagnosis silently
  falls back to a weaker Z-score threshold, and `/parts/inventory_risk` answers `503`:
  ```bash
  docker compose exec backend python -m ml.build_features
  docker compose exec backend python -m ml.train
  ```
- State (`backend/store/`) lives in a named volume — survives `down`/`up`; only
  `docker compose down -v` wipes it (re-run ingestion and the embedding download afterward).
- Both services bind to `127.0.0.1` only — nothing is exposed on your LAN, except the Ollama
  setup below if you follow it.
- `docker compose down` to stop.
- **Compose defaults the backend to `LLM_PROVIDER=ollama`** (the plain code default is
  `openai`; compose's `environment:` block always wins over `.env` — see Evaluation
  Baseline below for the accuracy/latency trade-off). For the container to reach it, start
  Ollama on the host with `OLLAMA_HOST=0.0.0.0 ollama serve` (its default is loopback-only,
  unreachable from the container) and pull the model once: `ollama pull qwen3:8b`. Compose
  already points `OLLAMA_BASE_URL` at `host.docker.internal` (with `extra_hosts` for Linux
  Docker Engine, unverified on Linux). Set `LLM_PROVIDER: openai` in `docker-compose.yml`
  instead to use the cloud model.
  **Security note:** `OLLAMA_HOST=0.0.0.0` exposes an unauthenticated API on every network
  interface — restrict it with a firewall (Docker bridge / loopback only), since it defeats
  an "offline" deployment otherwise. Whether Docker Desktop can reach an Ollama bound only
  to loopback instead has not been verified here.
- **Recommended**: `OLLAMA_KEEP_ALIVE=30m ollama serve` on the host, so the model isn't
  evicted from memory between requests (the default ~5 min idle timeout adds a disk-reload
  delay to the first request after a lull).

## Running Without Docker

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# fill in .env (OPENAI_API_KEY is required unless LLM_PROVIDER=ollama — see
# Environment Variables below)
```

### Data Setup (one-time, after `pip install`, before the first run)

1. Download the [Microsoft Azure Predictive Maintenance dataset](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance)
   (PdM_machines.csv, PdM_errors.csv, PdM_maint.csv, PdM_failures.csv, PdM_telemetry.csv —
   the last one is ~870k rows, so ingestion takes a moment).
2. Place the files under `uptime-copilot/archive/`.
3. Run the one-time SQLite ingestion (from `backend/`, venv active):
   ```bash
   python -m data.pdm_dataloader
   ```
4. Build and train the failure-risk model (also one-time; without it, diagnosis falls back
   to the weaker Z-score threshold and logs a warning):
   ```bash
   python -m ml.build_features
   python -m ml.train
   ```

### First run

```bash
uvicorn main:app --reload --port 8000
```

The first startup also downloads the `intfloat/multilingual-e5-small` embedding model
(~470MB) and builds the RAG index — allow a few minutes; it looks hung but isn't.
Subsequent restarts are fast. If the download stalls, set `HF_HUB_DISABLE_XET=1` before
starting uvicorn (some networks block the newer "xet" transfer path; the container path
already sets this).

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
ruff check .                   # lint + import order
mypy                           # type checks data/ + cmms_client.py; must stay at 0 errors
pytest tests/ --ignore=tests/test_rag_dedup.py   # fast path - skips the embedding-model test
pytest tests/                                     # full suite, downloads/loads the embedding model
```

CI (`.github/workflows/backend-checks.yml`) runs `ruff check`, `mypy`, and the fast pytest
path on every push and pull request.

## Evaluation Baseline

A 40-case golden set (`backend/tests/eval/golden.jsonl`) checks the router and machine-ID
extraction against real LLM calls. It costs real API tokens, so it's excluded from the
default test run and CI — run it explicitly:

```bash
cd backend
pytest tests/eval/test_golden.py -m eval -v -s
```

| Metric | Accuracy | Measured |
|---|---|---|
| Router (category) | 90.0% | 2026-09-27 |
| Machine-ID extraction | 100.0% | 2026-09-27 |
| Re-ask on missing ID | 100.0% | 2026-09-27 |

(Cloud model `gpt-5.6-luna`, one run. Earlier runs on 2026-09-23/25 didn't record which
provider produced them and aren't cited.) Each run writes a per-case breakdown to
`backend/tests/eval/results/<timestamp>.json` (gitignored — regenerate rather than
version-control).

### Local (Ollama) vs cloud

| Metric | Cloud (1 run) | Local Qwen3-8B (5 runs) |
|---|---|---|
| Router accuracy (40-question golden set) | 90.0% | 87.5–92.5% (mean 91.0%) |
| Router accuracy (10-question holdout, never tuned on) | 100.0% | 90–100% (mean 96.0%) |
| Machine-ID extraction / re-ask on missing ID | 100.0% / 100.0% | 100.0% / 100.0% |
| Dangerous misroute (named machine → ungrounded node) | 0 | 0 (across all 10 runs) |
| Mean latency / call | ~1.2s | ~13s (occasional outliers to ~40s under memory pressure) |

Local now matches cloud on accuracy, with the safety-critical metric (dangerous misroutes)
at zero across all runs; the remaining real cost is latency (~10x), mitigated by
call-merging and a warm-up ping but not eliminated. See `docs/PORTFOLIO.md` §09 for the
full methodology and how the routing gap was closed. Latency figures have no stored
artifact; eval result JSONs are gitignored — re-run `pytest -m eval` to re-check.

## Environment Variables (`backend/.env`)

| Variable | Required | Effect if unset |
|---|---|---|
| `LLM_PROVIDER` | No | Code default `openai`; `ollama` runs entirely against a local Ollama server — no API key/internet needed, at an accuracy/latency cost (see Evaluation Baseline). **`docker-compose.yml` overrides this to `ollama`** regardless of `.env`. |
| `OFFLINE` | No | `1`/`true`/`yes`/`on` enables the offline guard (see MRO Extension); `0`/`false`/`no`/`off`/empty disables it; any other value refuses to start. Enabled (`main.py` entry point only): LangSmith off, Slack skipped, `HF_HUB_OFFLINE=1` forced (embedding model must already be cached), CMMS refused outside the allowlist. Also refuses to start with `LLM_PROVIDER=openai`, an endpoint outside the allowlist, or a proxy env var not excluded via `NO_PROXY` (env vars only, not OS-level proxy settings). |
| `OPENAI_API_KEY` | **Yes, unless `LLM_PROVIDER=ollama`** | Backend refuses to start. Not needed for the Docker path (compose sets `ollama`). |
| `OPENAI_MODEL` | No | Defaults to `gpt-5.6-luna`. Used only with `LLM_PROVIDER=openai` |
| `OLLAMA_BASE_URL` | No | Defaults to `http://localhost:11434/v1`. Used only with `LLM_PROVIDER=ollama` |
| `OLLAMA_MODEL` | No | Defaults to `qwen3:8b`. Pull it first: `ollama pull qwen3:8b` |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | No | LangSmith tracing disabled |
| `SLACK_WEBHOOK_URL` | No | 긴급/주의 alerts are silently skipped (`backend/notify.py`) |
| `CMMS_MCP_URL` + `CMMS_MCP_TOKEN` | No | CMMS work-order push on approval is silently skipped — needs a running Atlas-MCP + Atlas CMMS instance if set, see `docs/design/PHASE_7_PLAN.md` |
| `ALLOWED_HOSTS` | No | Extra hostnames allowed past `TrustedHostMiddleware`, beyond `localhost`/`127.0.0.1`. Compose sets this to `backend` for you. |
| `BACKEND_URL` (frontend, not `backend/.env`) | No | Where Streamlit looks for the backend. Defaults to `http://localhost:8000`; compose sets `http://backend:8000`. |
| `SIM_TICK_SECONDS` | No | Defaults to `60` — real seconds between simulator ticks |
| `SIM_HOURS_PER_TICK` | No | Defaults to `1` — simulated hours advanced per tick |
| `SIM_SEED` | No | Defaults to `42` — RNG seed; `POST /simulator/reset` restarts from it |

> **`backend/.env.example` ships demo-tuned simulator values** (`SIM_TICK_SECONDS=15`,
> `SIM_HOURS_PER_TICK=12`, ~48x the code defaults) so events show up within a minute for a
> live demo. Delete those two lines (or set `60`/`1`) for a slower, real-time-ish pace.

> **Backend in Docker + Atlas-MCP on the host:** `localhost` inside the container means the
> container itself. Use `http://host.docker.internal:PORT/mcp` for `CMMS_MCP_URL`, and add
> `host.docker.internal:PORT` to Atlas-MCP's own `ALLOWED_HOSTS` (its DNS-rebinding check
> otherwise rejects the request with 421).

## Key API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/rag/query` | RAG-based document Q&A |
| POST | `/agent/query` | Multi-agent query — returns `pending_approval` on urgent findings |
| POST | `/agent/resume` | Resume the graph after an HITL approval/rejection decision |
| GET | `/agent/status/{thread_id}` | Recover a request after a client timeout: `pending_approval`, `done`, `running` (normal for ~3 min on a local model), `failed` (error type only), or `not_found` |
| POST | `/scan` | Sweep all 100 machines (no LLM), saving 긴급/주의 findings to the event store |
| GET | `/events` | List pending detected events |
| POST | `/events/complete` | Mark events completed — re-surfaces only on genuinely new evidence |
| POST | `/events/delete` | Discard events with no record kept (can reappear on the next scan) |
| POST | `/simulator/start` | Start the background degradation simulator (resumes current state) |
| POST | `/simulator/stop` | Stop the background loop |
| GET | `/simulator/status` | Running state, simulated clock, degrading machines, error status |
| POST | `/simulator/inject` | Force a machine into strong degradation, for demos (`{"machine_id": 12}`) |
| POST | `/simulator/reset` | Wipe simulator + event state — call before a fresh run |
| GET | `/parts/inventory_risk` | 30/90-day demand vs. stock per part, with a coverability flag |
| GET | `/audit_log` | Query the audit log (`event_type`, `thread_id`, `since`, `until`, `limit` 1–1000) |

Example `/agent/query` request:
```json
{"message": "Machine #12 has an error, what's wrong?", "thread_id": "unique-thread-id"}
```
If the response is `{"status": "pending_approval", "message": "..."}`, send
`{"thread_id": "...", "approved": true|false}` to `/agent/resume` with the same
`thread_id` to continue execution.

## Architecture Principles

- **Harness**: A deterministic layer validating model input/output (`core/harness.py`) —
  regex-based PII checks (computational) plus LLM-as-judge checks (inferential).
- **RAG**: Hybrid (BM25 + Dense) retrieval with automatic faithfulness scoring. Multi-Query
  rewriting and a Self-RAG retrieval-necessity check were removed 2026-09-23 — this corpus
  is small enough that neither added measurable value.
- **Agent**: A LangGraph `StateGraph` routes each question into diagnosis /
  maintenance-schedule / general-inquiry. Diagnoses use a three-tier severity model: 일반
  (normal) / 주의 (caution — a failure-risk-model alarm; Z-score is only the fallback when
  no model is trained) / 긴급 (urgent — a logged failure record, so it is a *post-failure*
  status; the advance warning is 주의). Only 긴급 cases run three
  parallel perspective evaluations and pause for HITL approval; 주의 goes straight to a work
  order. Approval state is checkpointed to SQLite (survives restarts). A separate `/scan`
  runs the same diagnosis logic (no LLM) across all 100 machines; a completed event only
  re-surfaces once genuinely new evidence appears.
- **Automated simulator**: `backend/data/sim_engine.py` and friends drive a background
  degradation model per machine (HEALTHY → DEGRADING → FAULT → failure+repair), calibrated
  against measured statistics from the real dataset (see `docs/design/SIMULATOR_PLAN.md`).
  Precursor errors and the per-signal drift direction follow the pre-failure signature
  measured in the real data; in an offline replay (`python -m ml.sim_alarm_eval --episodes 150
  --seed 2`, run from `backend/` or the backend container with trained models and the loaded
  dataset; scans every 12 simulated hours) the risk score crosses the 주의 threshold before the failure in 83–99% of
  episodes per signal (including the 1–5% of failures that, as in the real data, have no
  precursor error), and always on the right component. Error rates, the 24-hour precursor
  timing, per-component age floors and a maintenance-event process (components serviced
  together, as in the real maintenance log) were all measured from the real data; the
  remaining gap to the ~99% the model scores when real failures are replayed through it (99.2%
  on the held-out Nov–Dec failures) is attributed to the real data's 15-day maintenance calendar
  and finer telemetry structure, which the simulator does not reproduce — partly confirmed by
  substituting real values, not fully closed. This shows the simulator is consistent
  with the model — both come from the same dataset — not that the model alarms in the field.
  Writes to separate `sim_*` tables; fully optional (stopped by default).
- **Data**: Path constants resolve relative to the file's own location, not the working
  directory. Generated state lives under `backend/store/`, separate from source, so a
  Docker volume can mount there without shadowing code.
- **Observability**: LangSmith integration; the same PII regex patterns anonymize traces
  before they're sent.

## Related Documents

- `docs/PORTFOLIO.md` — architecture/design case study (diagrams, key decisions, war stories)
- `docs/design/SIMULATOR_PLAN.md` — simulator design record: calibration data, state machine
- `docs/INTEGRATION_CONTRACT.md` (Korean) — what a real plant integration must supply (inputs, maintenance records, error-code mapping, time basis) and what the model can/cannot claim
- `docs/design/PHASE_7_PLAN.md` — **optional, not required to run this project.** Slack +
  CMMS integration design. With `SLACK_WEBHOOK_URL` / `CMMS_MCP_URL` unset, both no-op.
- `docs/design/UI_UPGRADE_PLAN.md` — frontend upgrade history, fully closed out
- `LICENSE` — MIT
