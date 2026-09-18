# Uptime Copilot — Session Summary (updated 2026-09-18)

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
  sites found); `langgraph-checkpoint-sqlite` added. (`mcp` was later re-added for Phase 7
  Stage 2 — see below — once a real endpoint existed to call; `langchain-mcp-adapters`
  stayed removed, deliberately not used even then, see `PHASE_7_PLAN.md`.)
- **`README.md` corrected**: setup commands were Windows-only despite the actual dev
  machine being macOS; 5 endpoints (`/scan`, `/events*`, `/simulate/tick`) were
  undocumented; a `backend/practice/` folder was referenced that never existed in this
  repo's history.

## 2026-09-17 (계속) — Event-Scan Re-Suppression Bug, HITL Perspective Cards, UI Upgrade Items 1-3

Continued the same day, driven by the user's own manual runtime testing of the 이상감지
이벤트 tab (완료 처리 → 삭제 → rescan) surfacing a real bug, then moving on to the highest-
value remaining `UI_UPGRADE_PLAN.md` item.

- **Event re-surfacing suppression bug found and fixed** (`debugger` agent, reproduced
  against a live DB copy, read-only). `scan_all_machines()`'s "don't re-flag a completed
  event unless there's genuinely new evidence" rule was comparing `evidence_at` (a mix of
  fixed historical failure timestamps and continuously-advancing anomaly timestamps)
  against `completed_at` (always the dataset's current wall clock at completion time) —
  two incompatible units. Effect was backwards from intent: 긴급 (fixed evidence) was
  muted essentially forever once completed, since any later "now" is always `>=` a past
  failure timestamp; 주의 (evidence anchored to "latest telemetry, whenever anomalous")
  could resurface on the very next tick with zero new evidence. Fixed by persisting the
  actual `evidence_at` on both `detected_events` and `completed_events`
  (`backend/data/event_store.py`: schema migration + `save_event`/`complete_events`
  updated) and comparing evidence-to-evidence in `agent_service.scan_all_machines()`
  instead of evidence-to-wall-clock. Existing 8 already-completed rows were backfilled
  (recomputed via `_diagnose_machine`, since the dataset clock hadn't moved) rather than
  reset, so the fix didn't silently erase prior 완료 처리 acknowledgments. Verified live:
  ticked the simulator forward via `POST /simulate/tick` (the only way to advance it — see
  below), confirmed a real event appears, gets completed, and correctly does *not*
  reappear on a rescan with no new evidence.
- **Found in passing**: `POST /simulate/tick` (`backend/main.py:214`) has no corresponding
  UI control anywhere in `streamlit_app.py` — the only way to advance the "실시간" event
  scanner's simulated clock is to call the endpoint directly (curl or the FastAPI `/docs`
  Swagger page). Not fixed; noted here since it's easy to mistake for the bug above during
  testing (스캔해도 아무 일도 안 생김 because nothing advanced the clock, not because
  detection is broken).
- **HITL perspective cards** (`UI_UPGRADE_PLAN.md` item 3, highest remaining value) shipped
  — see that file's "Done" section for the full description (backend `perspectives` field
  plumbing, fixed-roster card rendering, the `NameError` regression this surfaced and its
  fix in `agent_service.start_agent()`, all verified live across 긴급/주의/일반 paths).
- **UI upgrade items 1, 2, and 5** (`st.tabs`, per-role chat avatars, `layout="wide"`)
  shipped the same way — see `UI_UPGRADE_PLAN.md`, now fully closed out.
- Workflow note: starting this session, the user types all code changes themselves (to
  stay hands-on with the implementation) — Claude's role shifted to explaining exact
  file/line/diff and reviewing what gets typed, rather than editing directly.

## Remaining Open Items

- **`gpt-5.6-luna` now exercised live** (2026-09-17 continued session) across 일반 문의 /
  정비 일정 / 진단(주의, no-approval) / 진단(긴급, full HITL approval + `/agent/resume`)
  paths, all 200 OK. The interrupt→resume pair is this app's only same-`thread_id`
  multi-turn case by design (`streamlit_app.py:193` deliberately mints a fresh `thread_id`
  per question so prior diagnoses don't leak into a new one) and it's covered by the
  above. Considered fully exercised for now; adversarial/edge-case inputs still untried.
- **No auth on any backend route** — acceptable today since `uvicorn` binds to
  `127.0.0.1` only; must be closed before any deployment beyond localhost (`--host
  0.0.0.0`, Docker, reverse proxy), especially `/events/delete` and `/agent/resume`.
- **No lint/type-checker/test config, zero test files** anywhere in the repo.
- **Dependencies remain unpinned** (no lockfile) — the dead-dependency cleanup didn't
  address reproducibility.
- **`/scan` is still a manual button** — no scheduler (e.g. APScheduler) triggers it
  automatically; discussed, not decided.
- **Phase 7 redefined (2026-09-17)** — no longer "Clean Architecture, TDD, SDD
  methodology" (had no concrete trigger, never started). Now: CMMS work-order push +
  Slack/email alerts, adopting Microsoft's own predictive-maintenance reference
  architecture pattern ("prediction → CMMS work order with evidence attached, not an
  email inbox"). Full staged plan in `PHASE_7_PLAN.md`.
  - Stage 0 resolved: no real CMMS vendor named, Stage 2 proceeds as a self-hosted **Atlas
    CMMS** demo (`/Users/surplus96/projects/atlas-cmms`) — Limble was the original pick
    but reverted (its MCP support needs a Premium+/Enterprise sales demo, not workable
    for an individual prototype).
  - **Stage 1 done and verified live**: `backend/notify.py` (Slack Incoming Webhook,
    direct call, no MCP) wired into both `scan_all_machines()` and `finalize_node()`'s
    긴급 승인 path. Real Slack messages confirmed for both triggers. Surfaced a UX gap
    while testing (not a bug): event list mixes wall-clock `detected_at` with
    dataset-simulated-clock evidence dates with no on-screen explanation — see
    `PHASE_7_PLAN.md` Stage 1 for detail and the planned caption fix.
  - **Stage 2 done and verified live with real data (2026-09-18)**: forked/hardened a
    community Atlas-MCP server (`/Users/surplus96/projects/atlas-mcp`, `security-reviewer`
    audited — found and fixed a HIGH: unauthenticated-by-default + LAN-exposed), connected
    it to Atlas CMMS, and added `backend/cmms_client.py` to push approved 긴급 work orders
    into it via the official `mcp` Python SDK. End-to-end proof: approving a 긴급
    diagnosis for 설비 #84 created a real work order (`WO000002`) visible in Atlas's own
    UI with the full evidence text intact, timestamp-matched to the backend log. Full
    detail, including the environment gotchas hit (host-port collision with Atlas's own
    nginx, a Docker-`localhost`-means-the-container-itself trap, `.env` vars Compose
    doesn't auto-forward), in `PHASE_7_PLAN.md` Stage 2.
- **`frontend/UI_UPGRADE_PLAN.md`**: fully closed out (see that file — items 1-3 and 5
  shipped 09-17, per-message mode badge decided against).
- **No `/simulate/tick` UI control** — only reachable via direct API call (curl/Swagger),
  found while testing the event-scan fix above.
- **No CONTRIBUTING.md, LICENSE, or CHANGELOG.md.**

## 재검토 사항 — MCP 도입 (2026-09-17 판단, 09-18 갱신)

MCP는 현재 **Stage 2(CMMS work-order push)에서만** 사용 중이다(`backend/cmms_client.py`,
`mcp` Python SDK 직접 호출) — 대화형 에이전트가 스스로 도구를 골라 쓰는 agentic 용도로는
아직 미사용. 과거 `mcp`/`langchain-mcp-adapters`를 zero-import로 제거했던 것도 여전히
유효한 원칙(붙일 외부 시스템이 없으면 다시 죽은 의존성이 될 뿐)이고, `langchain-mcp-adapters`
쪽은 지금도 의도적으로 안 쓴다.

1. ~~실제 CMMS/ERP에 작업지시서를 밀어넣어야 할 때~~ → **`PHASE_7_PLAN.md`로 승격**
   (Stage 2). 다만 결론은 "MCP를 우리가 새로 만든다"가 아니라 "이미 존재하는
   CMMS 벤더 자체 MCP 서버(Limble 등) 또는 Makini 같은 통합 게이트웨이에 얇게 붙는다".
2. ~~긴급 스캔 결과를 Slack/이메일 등으로 알려야 할 때~~ → **`PHASE_7_PLAN.md`로 승격**
   (Stage 1). 단, 이건 결정론적 알림이라 MCP 불필요 — direct webhook으로 처리.
3. 정적 Azure PdM 데이터셋이 아니라 실제 SCADA/IoT 라이브 텔레메트리에 붙어야 할 때 —
   **아직 재검토 대상으로 남음.** 가짜 외부 시스템을 만들어 이걸 미리 연습하는 것은
   이번 세션에서 검토 후 기각(이전 MCP 죽은 의존성 실수를 반복할 위험).

`/scan` 자동 스케줄러(+ 시뮬레이터 자동 틱)는 Phase 7과 별개의 작은 백로그 항목으로 남는다.

## 2026-09-18 (계속) — "어필용 실구현" 스코프로 재우선순위화 후 인터페이스 버그 정리

전체 5-에이전트 감사(위 참고)에서 나온 항목들을 "서비스화 아님, 데모/어필 실구현" 기준으로
재분류: 인증·lockfile·CI·자동화·SCADA·Stage 3·재시작 복원력 등은 서비스화 범주로 명시적으로
보류, 실제로 눈에 띄는 인터페이스 버그부터 정리.

`frontend/streamlit_app.py` 수정 및 라이브 스모크테스트(임시 포트로 실제 기동, 200 OK 확인):
- **`st.stop()` 6곳 → 플래그 기반 처리로 전환.** `st.tabs` 구조에서 `st.stop()`은 스크립트
  전체를 멈춰서, 앞선 탭(설비 에이전트/매뉴얼 검색)의 요청 실패가 뒤 탭(이상감지 이벤트)까지
  빈 화면으로 만들던 문제. 각 실패를 해당 섹션 안에서만 처리하도록 변경.
- **매뉴얼 검색 결과가 다른 위젯 클릭 한 번에 사라지던 문제** — `session_state`에 결과 저장,
  재실행에도 유지되게 수정.
- **사이드바 "새 진단 시작" 컨트롤이 탭 무관하게 항상 노출**되던 것 — 소속 라벨 추가.
- **승인 대기 블록에 assistant 아바타 누락** — `with` 튜플로 `st.chat_message` 감싸서 해결
  (재들여쓰기 없이).
- **`layout="wide"` 이후 승인/반려 버튼이 화면 절반 크기**였던 것 — 컬럼 비율 조정으로
  실수 클릭 위험 완화.
