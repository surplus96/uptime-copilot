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
│   └── data/                    PdM data ingestion/query layer + event store (SQLite)
└── frontend/                Streamlit chat UI
```

## Prerequisites

- Python ≥3.10 (developed and tested on 3.12; the codebase uses `X | None` union syntax throughout)

## Data Setup

1. Download the Azure Predictive Maintenance dataset (PdM_machines.csv, PdM_errors.csv,
   PdM_maint.csv, PdM_failures.csv, PdM_telemetry.csv).
2. Place the files under `uptime-copilot/archive/`.
3. Run the one-time SQLite ingestion:
   ```bash
   cd backend/data
   python pdm_dataloader.py
   ```

## Running the Project

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# fill in .env (OPENAI_API_KEY is required)
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Key API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/rag/query` | RAG-based document Q&A |
| POST | `/agent/query` | Multi-agent query — returns `pending_approval` on urgent findings |
| POST | `/agent/resume` | Resume the graph after an HITL approval/rejection decision |
| POST | `/simulate/tick` | Advance the runtime telemetry simulator by N hours |
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
  (`backend/data/checkpoints.db`), not held only in memory, so it survives
  `--reload`/restarts. A separate `/scan` sweep runs the same diagnosis logic
  (no LLM) across all 100 machines and stores 긴급/주의 findings in an event store;
  a completed event only re-surfaces on a later scan once genuinely new evidence
  (postdating the completion time) appears — see `backend/data/event_store.py`.
- **Data**: All path constants are resolved relative to the file's own location
  (`Path(__file__).parent`) rather than the current working directory, so they remain
  correct regardless of where the code is run from or moved to.
- **Observability**: Integrated with LangSmith; an anonymizer built from the same PII
  regex patterns masks sensitive data before traces are sent out.

## Related Documents

- `SESSION_SUMMARY.md` — summary of past work sessions (may lag behind the latest
  changes on a fast-iterating branch; verify against the code for anything load-bearing)
- `frontend/UI_UPGRADE_PLAN.md` — remaining frontend polish items (most of the original
  plan has already shipped; only the still-open items are listed)
- `.claude/agents/README.md` — the nine review/diagnosis subagents installed in this repo
  (code quality, security, pipeline, docs, interface, debugger, build-doctor, performance,
  test-engineer) and when to reach for each
