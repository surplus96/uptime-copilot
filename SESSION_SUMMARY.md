# Uptime Copilot — Session Summary (updated 2026-09-17)

## Overview

Starting from a Gemini-based RAG chatbot (`project_edu`), the project progressed through
porting to OpenAI, hardening production-grade guardrails, adding LangSmith observability,
Phase 4 Advanced RAG, Phase 5 agent fundamentals, and Phase 6 multi-agent orchestration
(LangGraph + MCP + HITL). The synthetic mock data was then replaced with the real
Azure Predictive Maintenance dataset, followed by a real-time event-scanner feature and,
most recently, an environment migration + full audit-and-fix pass.

## 2026-09-14 ~ 09-15 — Real Data Migration & Structural Cleanup

1. **Azure PdM Dataset Integration**: Loaded real data for 100 machines (errors, maintenance,
   failures, telemetry) via SQLite + pandas, and rewrote `agent_service.py`'s diagnosis /
   scheduling / severity-determination logic entirely on top of real data.
2. **HITL Severity Determination**: Severity is grounded in the dataset's own distinction
   between `errors` (ongoing warnings) and `failures` (actual component replacements),
   rather than an arbitrary label.
3. **Two Features Added**: Z-score-based anomaly pre-warning (`detect_anomaly`) and
   per-model vulnerable-component statistics (`get_component_failure_stats`, normalized
   per machine).
4. **Project Restructuring**: Renamed `fastapi/` → `backend/` and `streamlit/` → `frontend/`.
   Reorganized `backend/` into `core/`, `rag/`, `agent/`, `data/`. Converted all path
   constants to `Path(__file__).parent`-relative so they stay correct regardless of where
   the code runs from.
5. Fixed `check_recent_failure` comparing dataset timestamps (2014–2016) against wall-clock
   "now" instead of the dataset's own latest timestamp — this had silently made the
   "recent failure" check always return false.

## 2026-09-16 — Real-Time Event Scanner

1. **`event_simulator.py` / `event_store.py` added**: a runtime simulator that advances the
   dataset's own timeline forward (not real wall-clock time) to synthesize new telemetry
   ticks and occasional new errors/failures, plus a `detected_events` /
   `completed_events` SQLite-backed event store.
2. **`/scan` sweep**: `agent_service.scan_all_machines()` runs the same diagnosis logic as
   chat (no LLM) across all 100 machines and stores 긴급/주의 findings.
3. **Two design decisions resolved** (left open at the end of the 09-16 session, closed
   09-17): the scanner uses a shorter `within_days=1` window for `check_recent_failure`
   while chat diagnosis keeps `within_days=30` (`_diagnose_machine(machine_id, within_days=...)`
   parameter); and a completed event only re-surfaces once genuinely new evidence
   (`evidence_at`) postdates its `completed_at` — both timestamps anchored to the
   dataset/simulator's own clock, not the real wall clock, to avoid repeating the same
   class of bug as item 5 above.
4. Work-order generation had repeated hallucination (part/symptom confusion) across
   several fix attempts; root-caused by making `manual_lookup_node`/`work_order_node`
   assemble the `[부품]/[증상]/[매뉴얼 근거]/[조치사항]/[긴급도]` fields **deterministically
   from already-verified data**, with no LLM restating facts.
5. Frontend rebuilt from a single chat prototype into three modes: 설비 에이전트 (agent +
   HITL), 매뉴얼 검색 (RAG), 이상감지 이벤트 (scanner).

## 2026-09-17 — Environment Migration + 5-Agent Audit + Fix Pass

Resumed on a new (macOS) machine from `claude-history.txt`. Rebuilt the environment
(`backend/.venv`, `frontend/.venv`, re-downloaded the Azure PdM CSVs, rebuilt
`pdm_telemetry.db`), installed a 9-agent review-and-diagnosis team
(`.claude/agents/`, see `CLAUDE.md`), and ran the 5 auditor agents
(code-quality / security / pipeline / docs / interface) against the whole repo. Fixes
applied from that audit:

- **`MAX_INPUT_LENGHT` → `MAX_INPUT_LENGTH`** typo in `core/harness.py` — independently
  flagged by 4 of 5 agents; the input-length rejection path was silently raising
  `NameError` (uncaught 500) instead of the intended 400 response.
- **HITL checkpointing moved off `InMemorySaver` onto `SqliteSaver`**
  (`backend/data/checkpoints.db`) — pending approvals no longer vanish on
  `uvicorn --reload` or a restart.
- **Honest HITL outcome text**: approving/rejecting no longer claims
  "현장 책임자에게 즉시 보고되었습니다" (nothing in the codebase ever sent that report) —
  now states the decision was recorded and that reporting is a separate manual step.
- **Removed the entire unused `/chat`, `/chat/stream`, `/session` subsystem** from
  `main.py` (confirmed unreferenced by the frontend) along with its schemas,
  `chat_sessions` state, and `SessionNotFoundError`.
- **Fixed `backend/rag/generate_docs.py`**, which imported a module-level `_errors`
  DataFrame that no longer exists in `pdm_operations.py`; it now queries the `errors`
  table directly, matching the rest of the data layer's SQLite-query style.
- **Duplicate `AgentQueryRequest` class definition** removed from `main.py`.
- **Model migration**: `gpt-4o-mini` (legacy tier) → `gpt-5.6-luna` (current-generation,
  lowest-cost tier) across all 9 call sites (8 in `agent_service.py`'s nodes +
  `harness.py`'s `JUDGE_MODEL`), consolidated into one `OPENAI_MODEL`-driven constant
  per file (`agent_service.MODEL`, `harness.JUDGE_MODEL`, `main.DEFAULT_MODEL`) instead
  of 9 separate hardcoded literals — changing `.env`'s `OPENAI_MODEL` now updates the
  whole app with no code change. (Caught and fixed a real bug in the process: `harness.py`
  is imported before `main.py`'s own `load_dotenv()` call runs, so it needed its own
  `load_dotenv()` call to actually see `.env` values instead of silently falling back to
  the hardcoded default.)
- **Dead dependencies removed** (`mcp`, `langchain-mcp-adapters`, `ragas` — zero import
  sites found); `langgraph-checkpoint-sqlite` added.
- **`README.md` corrected**: setup commands were Windows-only despite the actual dev
  machine being macOS; 5 endpoints (`/scan`, `/events*`, `/simulate/tick`) were
  undocumented; a `backend/practice/` folder was referenced that never existed in this
  repo's history.

## Remaining Open Items

- **`gpt-5.6-luna` swap is untested in a live multi-turn session** — structural
  verification (imports, graph compiles, env-var wiring) is done; actual response
  quality/behavior on real diagnosis traffic hasn't been exercised yet.
- **No auth on any backend route** — acceptable today since `uvicorn` binds to
  `127.0.0.1` only; must be closed before any deployment beyond localhost (`--host
  0.0.0.0`, Docker, reverse proxy), especially `/events/delete` and `/agent/resume`.
- **No lint/type-checker/test config, zero test files** anywhere in the repo.
- **Dependencies remain unpinned** (no lockfile) — the dead-dependency cleanup didn't
  address reproducibility.
- **`/scan` is still a manual button** — no scheduler (e.g. APScheduler) triggers it
  automatically; discussed, not decided.
- **Phase 7 (Clean Architecture, TDD, SDD methodology) not started.**
- **`frontend/UI_UPGRADE_PLAN.md` genuinely-remaining items** (rewritten 09-17 to match
  reality — most of its original scope already shipped): mode `st.radio` → `st.tabs`,
  per-mode chat avatars, side-by-side `st.columns(3)` for the safety/production/
  maintenance perspectives (currently only visible inside a collapsed raw-text
  expander), and a per-message mode badge.
- **No CONTRIBUTING.md, LICENSE, or CHANGELOG.md.**
