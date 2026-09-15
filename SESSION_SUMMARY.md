# Uptime Copilot — Session Summary (2026-09-14 ~ 09-15)

## Overview

Starting from a Gemini-based RAG chatbot (`project_edu`), this session progressed through
porting to OpenAI, hardening production-grade guardrails, adding LangSmith observability,
Phase 4 Advanced RAG, Phase 5 agent fundamentals, and Phase 6 multi-agent orchestration
(LangGraph + MCP + HITL). Finally, the synthetic mock data was replaced with a real
open-source manufacturing dataset (Azure Predictive Maintenance).

## Key Achievements Today — Real Data Migration & Structural Cleanup

1. **Azure PdM Dataset Integration**: Loaded real data for 100 machines (errors, maintenance,
   failures, telemetry) via SQLite + pandas, and rewrote `agent_service.py`'s diagnosis /
   scheduling / severity-determination logic entirely on top of real data.
2. **Improved HITL Severity Determination**: Replaced arbitrary labeling with a check against
   `PdM_failures.csv` (actual component-replacement records) — upgrading the trigger to a
   more trustworthy, evidence-based signal.
3. **Two New Features Added**: Z-score-based anomaly pre-warning (`detect_anomaly`) and
   per-model vulnerable-component statistics (`get_component_failure_stats`, normalized
   per machine).
4. **Project Restructuring**: Renamed `fastapi/` → `backend/` and `streamlit/` → `frontend/`.
   Reorganized `backend/` into functional subfolders — `core/` (harness + prompts), `rag/`,
   `agent/`, `data/`, and `practice/` (Phase-by-phase learning scripts). Converted all path
   constants from CWD-relative to file-location-relative (`Path(__file__).parent`) so they
   stay correct regardless of where the code is run from or moved to.
5. **End-to-End Verification**: Confirmed the full HITL cycle — `/agent/query` (both the
   no-approval-needed and approval-required branches) followed by `/agent/resume` — works
   correctly against a live HTTP server.

## Key Defects Found & Fixed

- `check_recent_failure` was comparing dataset timestamps (2014–2016) against the real
  wall-clock "now" (2026) instead of the dataset's own latest timestamp — this silently
  made the "recent failure" check always return false regardless of the threshold.
- The actual loader script was saved as `pdm_dataloader.py` (no underscore) rather than the
  originally suggested `pdm_data_loader.py`, which nearly caused it to be skipped during
  the folder reorganization.
- Component failure statistics are misleading without per-machine normalization — confirmed
  with real numbers that model1 (fewer machines) actually has a higher per-machine failure
  rate for comp3 than model3 (more machines, higher raw count).

## Remaining Open Items

- `detect_anomaly()` and `get_component_failure_stats()` are not yet wired into the agent
  graph as nodes.
- Several scripts under `backend/practice/` no longer run after `manufacturing_data.py` was
  deleted (left intentionally as historical records of each Phase's exercise).
- Phase 7 (Clean Architecture, TDD, SDD methodology) has not been started yet.
