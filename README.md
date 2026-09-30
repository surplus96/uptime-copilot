# Uptime Copilot

[![English](https://img.shields.io/badge/English-current-0c7c8c?style=for-the-badge)](README.md)
[![한국어](https://img.shields.io/badge/한국어-switch-555555?style=for-the-badge)](README_KOR.md)

[![Backend checks](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml/badge.svg)](https://github.com/surplus96/uptime-copilot/actions/workflows/backend-checks.yml)

**Uptime Copilot — Predictive Maintenance & MRO Copilot**

An OpenAI-based RAG + multi-agent backend, paired with a Streamlit frontend. Built around a
manufacturing equipment maintenance domain example, backed by the Azure Predictive
Maintenance dataset. Extended with an MRO (parts/inventory/procurement) layer and closed-network
security operations (offline mode, audit log) — see the MRO Extension section below.

> 📄 **[Portfolio case study →](docs/PORTFOLIO.md)** — architecture diagrams, core logic, and key engineering decisions.

> **Independent project inspired by Hanwha's public materials on TOMMS/HUMS** (predictive-maintenance
> program names referenced for comparison in [mro-copilot-upgrade-plan.md](mro-copilot-upgrade-plan.md) §0-1) —
> not affiliated with, endorsed by, or built for Hanwha. All part numbers, stock levels, lead times,
> costs, and end-of-life dates in this repo are synthetic (see the MRO Extension section below); the
> replacement history behind the demand forecast comes from the public Azure PdM dataset, which is
> itself simulated data.

## MRO Extension (parts, demand forecast, inventory risk, offline mode, audit log)

Built on top of the predictive-maintenance core above: a parts master, a demand forecast,
and an inventory-risk dashboard, tied into the existing diagnosis pipeline so a real
equipment failure also surfaces whether the part it needs is actually in stock. Plan and
decision log: [mro-copilot-upgrade-plan.md](mro-copilot-upgrade-plan.md),
[docs/decisions.md](docs/decisions.md).

- **Parts master (`backend/data/parts_master.py`)** — 5 synthetic parts (comp1–4 + one
  end-of-life alternate for comp4), auto-seeded on backend startup. **All part numbers,
  stock levels, lead times and the end-of-life date are synthetic** — see
  [docs/design/parts_assumptions.md](docs/design/parts_assumptions.md) for the generation
  rules and why comp4 is deliberately scarce (it's the demo scenario for the procurement
  recommendation below).
- **Demand forecast (`backend/data/demand_forecast.py`)** — expected 30/90-day replacement
  counts per part, split into a preventive-maintenance term (scheduled, model-independent)
  and a failure-replacement term. The failure term uses the *actual* alarm state for today
  (conditional probability from the validation period) and a population base rate for
  future days whose alarm state isn't known yet — mixing these up the wrong way produced a
  real, measured bug during development (see `docs/decisions.md`, 2026-09-28). Backtested
  against the Nov–Dec 2015 replacement counts of the Azure PdM dataset using only a 2015-11-01
  snapshot for the alarm state; rates are fitted on Jan–Oct 2015 and no Nov–Dec data is used
  for fitting: -5.0% to +3.5% error. **This validates the base rates, not the alarm term** —
  the alarm term adds only 0–2 units of ~120, and a forecast without it scores the same
  within noise (roughly -6% to +3%).
- **Inventory-risk tab** — `GET /parts/inventory_risk`, shown in the Streamlit app's fourth
  tab. Numbers are rounded for display and explicitly labeled as estimates that don't
  account for stock already on order.
- **Priority stays separate from parts availability** — a missing part doesn't make a
  diagnosis more urgent by itself (urgency still comes only from the safety/production/
  maintenance perspectives and the risk model, as in the core pipeline). A shortage or
  imminent end-of-life shows as its own `[조달 긴급도]` line on the work order instead of
  inflating the priority level.
- **Offline mode (`backend/core/offline_guard.py`)** — a destination-based allowlist
  (`localhost` / `127.0.0.1` / `host.docker.internal`), not a per-module switch: anything inside
  it (Ollama, a self-hosted CMMS) keeps working with `OFFLINE=1`. What it does, each part
  checked by running it rather than only by reading it:
  - **Refuses to start** when `OFFLINE` has an unrecognised value (a typo like `y` used to run
    silently online — accepted: `1/true/yes/on`, `0/false/no/off` or empty), when `LLM_PROVIDER`
    is `openai` or unknown, when `OLLAMA_BASE_URL`/`CMMS_MCP_URL` point outside the allowlist or
    are ambiguous URLs (userinfo, backslashes, control characters, non-http schemes), or when a
    proxy variable (`HTTP(S)_PROXY`/`ALL_PROXY`, any letter case) would send allowlisted hosts through
    a proxy — set `NO_PROXY` to include them (an empty lowercase `no_proxy` overrides it). Only
    environment variables are checked, not OS-level proxy settings.
  - **Turns things off before anything else is imported**: `HF_HUB_OFFLINE=1` (so the embedding
    model must already be in the cache — an `OFFLINE=1` first run on an empty cache fails at
    startup instead of hanging) and all LangChain/LangSmith tracing variables. Slack is skipped;
    CMMS pushes are refused at call time unless the host is in the allowlist.
  - **Checked automatically in CI**: `backend/tests/test_offline_guard.py` (default CI path;
    `-m offline_e2e` selects it alone) blocks real sockets with `pytest-socket` except to the
    allowlist and drives diagnose → approve with a *stubbed LLM and stubbed diagnosis*; Slack is
    skipped by the guard and CMMS is unconfigured in that scenario. The startup refusals, the
    call-time CMMS check and the URL parsing are covered by unit tests, not by the socket test.
  - **Not proven**: `pytest-socket` only sees TCP `connect()`, so proxy routing, DNS lookups
    and HTTP redirects are outside it (proxies are refused at startup instead). The socket test
    does not import `main.py`, so startup and RAG initialisation are not exercised in CI — they
    were verified separately with sockets blocked. The frontend container used to look up its
    public IP on every start; `browser.serverAddress` removes that (verified by emulating the
    container's Streamlit config, not in a live container).
- **Audit log (`backend/data/audit_log.py`)** — records every LLM-using step (routing, the three
  perspective assessments, schedule/general answers, RAG answers, the local-model warm-up —
  failures included), the parts-availability lookup, approvals/rejections, and Slack/CMMS pushes
  with their real outcome (sent / failed / not configured / blocked by offline mode), each with a
  timestamp, thread ID, event type, target, result and provider. **Not audited**: the diagnosis
  data lookups (maintenance history, anomaly detection, risk model) and the background scan's
  parts lookups; a retried call, and a RAG answer together with its faithfulness judge, each show
  as one row. Free text is PII-masked before it is stored (national ID, card, mobile,
  international and landline numbers, e-mail); names and addresses can't be detected by pattern
  and are not masked. Queryable via `GET /audit_log` (`event_type`, `thread_id`, `since`,
  `until`, `limit` 1–1000) and the Streamlit app's fifth tab. Filtering by thread ID reconstructs
  one scenario — routing → parts check → the three perspective assessments → approval → pushes.

## Folder Structure

```
uptime-copilot/
├── archive/               Azure PdM raw CSVs (download required - see "Data Setup" below)
├── backend/                FastAPI backend
│   ├── main.py               Entry point (uvicorn main:app)
│   ├── core/                  Harness (input/output validation), LLM provider adapter, offline guard
│   ├── rag/                    RAG pipeline (hybrid BM25+dense search) + docs/
│   │                             (`pump_manual.py` is a static lookup dict the agent reads
│   │                             directly — not retrieved via RAG; see Architecture Principles)
│   ├── agent/                  LangGraph multi-agent graph (routing + HITL + event scanner)
│   ├── ml/                      Failure-risk model: feature pipeline, training, prediction
│   ├── data/                    PdM data ingestion/query layer + event store, parts master,
│   │                             demand forecast, audit log (SQLite)
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
├── .github/workflows/       CI: ruff + mypy + pytest on pushes to main and on every PR
└── docker-compose.yml       See "Running with Docker Compose" below
```

## Prerequisites

- Python 3.12 (what CI and the Docker image use; dependencies are unpinned, and the current resolution pulls numpy 2.x / scipy releases that require ≥3.12 — older Python versions have not been tested)
- Docker + Docker Compose, if you'd rather skip the manual venv setup below (see
  "Running with Docker Compose")

## Running with Docker Compose (recommended)

The dataset still has to be downloaded manually either way — it can't be redistributed
inside the image — so "Data Setup" below applies regardless of which path you take.

```bash
# 1) Data Setup (see below) — download the dataset into archive/ first

# 2) Configure
cp backend/.env.example backend/.env
# fill in backend/.env (an OpenAI key is NOT needed for the Docker path — compose defaults to a
# local Ollama; it is only required if you switch to LLM_PROVIDER=openai. See Environment
# Variables below)

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
- **The failure-risk model needs the same one-time treatment** — `backend/store/` is a
  named volume, not baked into the image, so a fresh container has no trained model either.
  Without this, `_diagnose_machine()` silently falls back to the (much weaker) Z-score
  threshold and logs `위험도 모델 파일이 없어 Z-score만으로 판정합니다` — that log line is
  expected until you run this once, not a sign anything is broken. The **inventory-risk tab
  needs it too**: the demand forecast loads the trained models (and the raw CSVs), so without
  them `GET /parts/inventory_risk` answers `503` with the missing file and these commands:
  ```bash
  docker compose exec backend python -m ml.build_features
  docker compose exec backend python -m ml.train
  ```
- State (`backend/store/`: the SQLite DBs and the RAG index) lives in a named Docker
  volume, not in the image — it survives `docker compose down` / `up`. Only
  `docker compose down -v` wipes it (you'd need to re-run the ingestion step above
  afterward — and re-download the embedding model, since `down -v` also drops the
  `hf_cache` volume mentioned above).
- Both services bind to `127.0.0.1` only, same as the manual setup below — this stack exposes
  nothing on your LAN. (Starting Ollama with `OLLAMA_HOST=0.0.0.0`, as suggested below, is the
  exception — see the warning there.)
- `docker compose down` to stop.
- **`docker-compose.yml` defaults the backend to `LLM_PROVIDER=ollama`** (the plain code
  default, e.g. when running the backend directly without Docker, is `openai` — this override
  lives in `docker-compose.yml`'s `environment:` block, which always wins over
  `backend/.env`; see Evaluation Baseline below for what that trade-off actually costs). For
  the container to reach it, start Ollama on the host with `OLLAMA_HOST=0.0.0.0 ollama serve`
  (its default, loopback-only, isn't reachable from inside the container) — `docker-compose.yml`
  already points `OLLAMA_BASE_URL` at `host.docker.internal` for you (and, for Linux Docker
  Engine, maps that name to the host gateway via `extra_hosts`; not verified on a Linux host).
  **Security note:** binding Ollama to `0.0.0.0` exposes its unauthenticated API on every network
  interface of the host — anyone on your LAN can run inference or make it pull models from the
  internet, which defeats an "offline" deployment. Restrict it with a firewall (allow only the
  Docker bridge / loopback), or bind it to a specific interface. Whether Docker Desktop can
  reach an Ollama bound only to loopback has not been verified here. Pull the model once
  with `ollama pull qwen3:8b`. Set `LLM_PROVIDER: openai` in `docker-compose.yml` instead if
  you'd rather the containers use the cloud model.
- **Recommended**: run Ollama on the host with `OLLAMA_KEEP_ALIVE=30m ollama serve` so the
  model doesn't get evicted from memory after a short idle period. With the default (usually
  5 minutes), the first request after a lull pays a cold reload from disk on top of normal
  inference latency (the backend sends one warm-up call on startup, but a long idle gap after
  that will still evict it again).

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
4. Build the failure-risk model's features and train it (also one-time; without this,
   `_diagnose_machine()` falls back to the weaker Z-score threshold and logs a warning):
   ```bash
   python -m ml.build_features
   python -m ml.train
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
| Router (category) | 90.0% | 2026-09-27 |
| Machine-ID extraction | 100.0% | 2026-09-27 |
| Re-ask on missing ID | 100.0% | 2026-09-27 |

(Cloud model `gpt-5.6-luna`, one run over the 40 golden questions. Earlier runs on 2026-09-23/25
did not record which provider produced them and are not cited.)

Each run writes a full per-case breakdown to `backend/tests/eval/results/<timestamp>.json`
(gitignored — regenerate rather than version-control). Expected severity is intentionally
*not* pinned in the golden set: the background simulator changes real machine state every
tick, so severity ground truth is computed live at eval time (`_diagnose_machine()`) rather
than baked into the file.

### Local (Ollama) vs cloud

The same golden set, run against a fully local `LLM_PROVIDER=ollama` (Qwen3-8B, Apple
Silicon, no GPU acceleration beyond Metal) instead of the cloud model:

| Metric | Cloud (gpt-5.6-luna, 1 run) | Local (Ollama Qwen3-8B, 5 runs) |
|---|---|---|
| Router accuracy (40-question golden set) | 90.0% | 87.5–92.5% (mean 91.0%) |
| Router accuracy (10-question holdout, never used to tune the safety net) | 100.0% | 90–100% (mean 96.0%) |
| Machine-ID extraction | 100.0% | 100.0% (all 5 runs) |
| Re-ask on missing ID | 100.0% | 100.0% (all 5 runs) |
| Dangerous misroute (named machine → ungrounded node) | 0 | 0 (across all 10 golden+holdout runs) |
| Mean latency / call | ~1.2s | ~13s (routing+extraction merged into one call; occasional outliers up to ~40s under memory pressure, see below) |

*Methodology note*: an earlier version of this table reported a single "77.5% → 90.0%"
local run and compared it to a single stale cloud run that hadn't gone through the current
safety net — an external review caught this as an apples-to-oranges comparison tuned on
its own failure cases. The numbers above are from a clean rerun: cloud measured once with
current code, local measured 5 times, both split into the 40 questions used to build the
safety net and a separate 10-question holdout that was never looked at while building it.
Eval result files from 2026-09-27 on record which provider/model produced them
(`llm_provider`/`llm_model` in `tests/eval/results/*.json`). Those files are **gitignored, so they
exist only on the machine that ran them** — to re-check a number, re-run `pytest -m eval` (needs an
OpenAI key or a running Ollama). Latency figures (~1.2 s / ~13 s / ~40 s) have no stored artifact
at all.

Extraction and re-ask held up identically locally from the start — those are narrow,
closed-form tasks. Routing initially trailed the cloud model by double digits and every
call ran roughly 10x slower as two separate calls (routing, then a follow-up extraction
call). Both gaps were narrowed on the code side, not by using a bigger model: router+
extraction were merged into a single structured-output call (removing a full round trip
for every diagnosis/schedule request), and a deterministic post-classification check now
catches the specific phrasings the local model kept misrouting into the ungrounded
free-chat node — machine-number word order ("설비 46번" vs "46번 설비") and symptom-only
reports with no machine number at all ("설비가 이상해요"). A golden-set regression test
(`tests/eval/test_golden.py`) specifically fails if any query naming a real machine ID
lands in the ungrounded node, separately from the overall accuracy number, since that
combination is what actually produces a false "everything's fine" answer about a real
failure (see `docs/decisions.md`, 2026-09-25 and 2026-09-27).

A spot check of the safety/production/maintenance perspective assessments (outside the
golden set, which only covers routing/extraction) had also surfaced a contradiction the
local model produced that the cloud model didn't: `risk_level: "중간"` (medium) together
with `requires_shutdown: true`. This is now structurally impossible rather than just
logged — `requires_shutdown` is no longer an LLM-set field; it's derived in code from
`risk_level`/`recommended_window`, so the model has no way to set it inconsistently.

Bottom line: the adapter (`LLM_PROVIDER=ollama`) now repeatedly measures in the same
accuracy range as the cloud model (mean 91.0% vs. 90.0%), and the safety-critical metric
(dangerous misroutes) reproduced at zero across all 10 runs. Latency gap roughly halved
through call-merging alone — remaining costs are the still-real ~10x per-call latency
(mitigated by warm-up + `OLLAMA_KEEP_ALIVE`, not eliminated) and this machine's memory
headroom (16GB unified memory already runs close to its limit with Docker Desktop and
the app's own containers running, which is the likely cause of the occasional latency
outlier above) — worth it for a closed environment, no longer a straightforward
accuracy/latency trade for going fully local.

## Environment Variables (`backend/.env`)

| Variable | Required | Effect if unset |
|---|---|---|
| `LLM_PROVIDER` | No | Code default is `openai`; set to `ollama` to run entirely against a local Ollama server instead (`backend/core/llm_provider.py`) — no API key or internet needed, at a real accuracy/latency cost (see Evaluation Baseline below). **`docker-compose.yml` overrides this to `ollama`** for the containerized path regardless of what's in `.env` — see "Running with Docker Compose" above. |
| `OFFLINE` | No | `1`/`true`/`yes`/`on` enables the destination-based offline guard (`backend/core/offline_guard.py`, see MRO Extension above); `0`/`false`/`no`/`off`/empty disables it; any other value refuses to start. Enabled: LangSmith tracing off, Slack skipped, `HF_HUB_OFFLINE=1` forced (the embedding model must already be cached), CMMS pushes refused outside the allowlist. Refuses to start with `LLM_PROVIDER=openai` or an unknown provider, with `OLLAMA_BASE_URL`/`CMMS_MCP_URL` outside the allowlist, or with proxy variables set unless `NO_PROXY` covers `localhost,127.0.0.1,host.docker.internal`. |
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
| GET | `/agent/status/{thread_id}` | Recover a request after a client timeout without re-running it: `pending_approval` (with the work order), `done`, `running` (the graph is still executing — normal for ~3 minutes on a local model), `failed` (a node raised; only the error type is returned), or `not_found` |
| POST | `/scan` | Sweep all 100 machines (no LLM), saving 긴급/주의 findings to the event store |
| GET | `/events` | List pending detected events |
| POST | `/events/complete` | Mark events completed — archived, and only re-surfaces on genuinely new evidence |
| POST | `/events/delete` | Discard events with no record kept (they can reappear on the next scan) |
| POST | `/simulator/start` | Start the automated background degradation simulator (resumes from its current state) |
| POST | `/simulator/stop` | Stop the background loop |
| GET | `/simulator/status` | Running state, simulated clock, degrading-machine list, `has_stale_events`, and `last_error`/`consecutive_failures` if a background tick has been failing |
| POST | `/simulator/inject` | Force a specific machine into a strong degradation, for demos (`{"machine_id": 12}`, optional `"signal"`: one of `volt`/`rotate`/`pressure`/`vibration`, random if omitted) |
| POST | `/simulator/reset` | Wipe simulator state/data **and** the detected/completed event tables — call this before a fresh run; nothing is cleared automatically. See `docs/design/SIMULATOR_PLAN.md` |
| GET | `/parts/inventory_risk` | 30/90-day demand vs. on-hand stock per part, with a coverability flag per horizon (see MRO Extension above) |
| GET | `/audit_log` | Query the audit log — filters `event_type`, `thread_id`, `since`, `until`; `limit` 1–1000 (default 100). LLM-using steps, the parts lookup, approvals/rejections, external pushes, offline-blocked calls |

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
- **RAG**: Hybrid (BM25 + Dense) retrieval, with automatic Faithfulness scoring. Multi-Query
  rewriting and a Self-RAG retrieval-necessity check were both removed 2026-09-23 — this
  corpus is small enough that they added no measurable value (see `docs/decisions.md`).
- **Agent**: Built on LangGraph's `StateGraph`. Routes each question into
  diagnosis / maintenance-schedule / general-inquiry branches. Diagnoses use a
  three-tier severity model: 일반(normal) / 주의(caution — a failure-risk-model alarm, no
  confirmed failure; a Z-score telemetry anomaly is used only as the fallback when no trained
  model exists) / 긴급(urgent — an actual logged failure record).
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
