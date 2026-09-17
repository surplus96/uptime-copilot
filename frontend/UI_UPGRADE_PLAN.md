# Frontend UI Upgrade Plan (re-audited 2026-09-17)

This plan originally described a single-mode Phase 1 chat prototype that wired up only
`/session` + `/chat/stream`. That version no longer exists — `streamlit_app.py` has since
been rebuilt into three modes (설비 에이전트 / 매뉴얼 검색 / 이상감지 이벤트) covering RAG,
the full multi-agent + HITL flow, and the event scanner. Re-checked line-by-line against
the current file; almost everything below the "Done" line has since shipped. Only the
genuinely still-open items remain listed.

## Done

- Sidebar mode switcher (`st.sidebar.radio`) across all three modes — RAG, agent+HITL, and
  event scanner are all reachable from the UI.
- Full HITL approval flow: `pending_approval` renders as a bordered, warning-styled
  `st.container` (not a normal chat bubble), with 승인/반려 buttons calling `/agent/resume`;
  `thread_id` is tracked explicitly in `st.session_state` and reused only while an approval
  is pending.
- Structured error bodies (`harness_rejected`, `llm_api_error`) are parsed into Korean
  labels instead of raw exception text, including FastAPI's list-typed 422 `detail`.
- Empty-state guidance: a "무엇을 도와드릴까요?" card with three clickable example questions
  when the agent conversation is empty.
- Title/branding: `st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️")`,
  page title "Uptime Copilot" throughout.
- RAG mode wired to `/rag/query`, including a collapsed `st.expander` for the retrieved
  source context.
- Event scanner mode: scan-result feedback ("N건 발견"/"이상 없음"), distinct empty states
  for "never scanned" vs. "scanned and clean", severity badge legend, and confirmation
  messages (with counts) on 완료 처리 / 삭제.
- Theme: `.streamlit/config.toml` sets `primaryColor`, `backgroundColor`,
  `secondaryBackgroundColor`, `textColor`, and `font` (Hanwha Orange, Pantone 1585 C —
  re-verify against an official brand guide if this ships externally).
- Discarding a pending HITL approval now requires an explicit confirmation checkbox
  before "새 진단 시작" is enabled.
- Approval/rejection outcome (승인됨/반려됨) is now surfaced directly above the work-order
  card via `st.success`/`st.warning`, not hidden inside a collapsed expander.

## Still Open

1. **Mode switcher**: `st.radio` → `st.tabs` (or a segmented control). Each mode's hint
   text/example questions could live on its own tab instead of a sidebar radio list.
2. **Per-mode chat avatars**: `st.chat_message(role, avatar=...)` is not used anywhere —
   all messages render with Streamlit's default avatar regardless of mode.
3. **Side-by-side perspective columns**: the three parallel safety/production/maintenance
   evaluations (`agent_service.py`'s `perspectives` list) are only visible inside the
   pending-approval message's collapsed "원본 메시지 보기" expander, as a single text block —
   never rendered as distinct cards. `st.columns(3)` would make the three viewpoints
   scannable at a glance instead of requiring the user to open the expander and parse
   `[안전]`/`[생산]`/`[정비]` prefixes out of running text.
4. **Per-message mode badge**: no visual indicator on an assistant message showing which
   mode (agent / RAG / general) produced it — only relevant if modes are ever merged into
   one conversation view; not needed while each mode has its own separate panel.
5. **`layout="wide"`** was never added to `st.set_page_config` — low priority, worth
   revisiting if the work-order cards or event list feel cramped.

## Suggested Order

Items 1–2 are cheap and independent — do them together. Item 3 is the most valuable
remaining one (the perspectives are real diagnostic content currently hidden behind an
extra click) and should be scoped on its own since it touches how `agent_service.py`'s
`interrupt()` payload is shaped, not just the render call. Items 4–5 are cosmetic;
pick up opportunistically.
