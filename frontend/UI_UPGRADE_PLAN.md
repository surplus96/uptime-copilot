# Frontend UI Upgrade Plan (re-audited 2026-09-17, updated same day after items 1-3 shipped)

This plan originally described a single-mode Phase 1 chat prototype that wired up only
`/session` + `/chat/stream`. That version no longer exists — `streamlit_app.py` has since
been rebuilt into three modes (설비 에이전트 / 매뉴얼 검색 / 이상감지 이벤트) covering RAG,
the full multi-agent + HITL flow, and the event scanner. Re-checked line-by-line against
the current file; almost everything below the "Done" line has since shipped. Only the
genuinely still-open items remain listed.

## Done

- **Mode switcher: `st.radio` → `st.tabs`** (2026-09-17) — top-level mode switching moved
  from a sidebar radio (a widget semantically meant for settings/filters, not page
  navigation) to `st.tabs(["설비 에이전트", "매뉴얼 검색", "이상감지 이벤트"])`. Purely
  mechanical change (`if/elif/else` → `with tab1/tab2/tab3:`, same indentation level, no
  body changes needed). Noted trade-off: unlike the radio version (only the selected
  branch executed per rerun), all three tab bodies now execute on every rerun regardless
  of which tab is visible — the 이상감지 이벤트 tab's unconditional `GET /events` call now
  fires on every interaction anywhere in the app, not just when that tab is open. Left
  as-is (SQLite read, negligible local cost); revisit with `@st.cache_data` if it ever
  matters.
- **Per-mode (role-based) chat avatars** (2026-09-17) — `CHAT_AVATARS = {"user": "🧑‍🔧",
  "assistant": "🛡️"}` (assistant icon matches the app's own `page_icon`), applied to both
  `st.chat_message()` call sites in 설비 에이전트 mode. 매뉴얼 검색 mode has no chat UI at
  all (plain `st.markdown` Q&A), so this item only applies to one mode in practice.
- **Side-by-side perspective columns** (2026-09-17) — the safety/production/maintenance
  HITL opinions render as three `st.columns(3)` cards (🛡️ 안전 관점 / 🏭 생산 관점 /
  🛠️ 정비 관점) above the raw-text "원본 메시지 보기" expander (kept as a fallback, matching
  the file's existing "structured view + raw fallback" convention). Required a backend
  change: `agent_service.approval_node()`'s `interrupt()` payload and `start_agent()`'s
  `pending_approval` response now carry a `perspectives` field, which the earlier `message`
  string alone didn't expose structurally. Fixed-roster rendering (not a bare `zip` over
  the raw list) so a missing perspective shows "의견을 가져오지 못했습니다." instead of
  silently vanishing; a heading distinguishes this as unverified LLM opinion vs. the
  deterministically-assembled work order above it. Caught and fixed along the way: a
  `NameError` in `start_agent()`'s non-interrupt return path (a stray edit had it reference
  an out-of-scope `payload` variable and return the wrong `status`), which had been
  silently breaking every non-approval agent response (일반 문의 / 정비 일정 / 진단 without
  approval) until caught by `interface-reviewer` + direct review.
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
- **`layout="wide"`** added to `st.set_page_config` (2026-09-17) — more headroom for the
  work-order cards, the 3-column perspective row, and the event list.

## Still Open

None. All items shipped or explicitly decided against (see below).

## Decided against (not a gap)

- **Per-message mode badge**: no visual indicator on an assistant message showing which
  mode (agent / RAG / general) produced it. Re-evaluated after moving to `st.tabs` — still
  not needed, since each mode remains a fully separate panel/tab rather than a merged
  conversation view. Revisit only if modes are ever merged into one timeline.

## Suggested Order

All non-cosmetic items have shipped (2026-09-17). Only `layout="wide"` remains, and it's
a one-line, low-stakes change — pick it up opportunistically whenever the layout is being
touched for something else anyway, no need to scope it separately.
