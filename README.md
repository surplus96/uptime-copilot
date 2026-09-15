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
│   ├── agent/                  LangGraph multi-agent graph (routing + HITL)
│   ├── data/                    PdM data ingestion/query layer
│   └── practice/                Standalone learning scripts per curriculum phase (reference only)
└── frontend/                Streamlit chat UI
```

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
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # fill in the values (OPENAI_API_KEY is required)
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Key API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/session` | Create a new chat session |
| POST | `/chat` | Chat (synchronous) — goes through harness validation |
| POST | `/chat/stream` | Chat (streaming) |
| DELETE | `/session/{session_id}` | Delete a session |
| POST | `/rag/query` | RAG-based document Q&A |
| POST | `/agent/query` | Multi-agent query — returns `pending_approval` on urgent findings |
| POST | `/agent/resume` | Resume the graph after an HITL approval/rejection decision |

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
  diagnosis / maintenance-schedule / general-inquiry branches; when a diagnosis is
  classified as "urgent" (an actual failure record exists), it runs three parallel
  perspective evaluations (safety / production / maintenance) and then pauses for
  Human-in-the-Loop (HITL) approval.
- **Data**: All path constants are resolved relative to the file's own location
  (`Path(__file__).parent`) rather than the current working directory, so they remain
  correct regardless of where the code is run from or moved to.
- **Observability**: Integrated with LangSmith; an anonymizer built from the same PII
  regex patterns masks sensitive data before traces are sent out.

## Related Documents

- `SESSION_SUMMARY.md` — summary of the most recent work (real-data migration, folder
  reorganization)
- `backend/practice/` — standalone scripts recording how each Phase 5–6 concept (agent
  loop, function calling, routing/parallelization/evaluator-optimizer, LangGraph, MCP)
  was verified
