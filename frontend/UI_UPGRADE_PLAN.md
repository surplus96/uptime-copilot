# Frontend UI Upgrade Plan

Planning document for the next work session on `frontend/streamlit_app.py`. Covers both
functional coverage gaps (the UI only exposes 2 of 8 backend endpoints) and a visual
design pass, since the two should be built together rather than sequentially.

## Current State Assessment

`streamlit_app.py` is still the original Phase 1 chat prototype. It wires up only
`POST /session` and `POST /chat/stream`. None of the backend capabilities built since
then are reachable from the UI:

| Backend capability | Wired into UI? |
|---|---|
| `/session`, `/chat/stream` | Yes (Phase 1 baseline) |
| `DELETE /session/{id}` | No — no "new chat" control |
| `/rag/query` | No — no RAG mode |
| `/agent/query`, `/agent/resume` | **No — the biggest gap.** The multi-agent + HITL approval flow, the session's main deliverable, has no UI path at all. |

Other completeness/polish issues:
- No mode switcher — the app is hard-wired to plain chat.
- Structured error bodies (`harness_rejected`, `llm_api_error`) are shown as raw
  exception text instead of a readable message.
- Page title/branding still reads "AI Chatbot (OpenAI backend)" — doesn't reflect the
  "Uptime Copilot" name or the equipment-maintenance domain.
- No visible `session_id` / `thread_id`, which makes debugging the agent's HITL flow
  (tied to `thread_id`) harder than it needs to be.
- Default Streamlit look throughout: no page icon, no chat avatars, no empty-state
  guidance, default blue theme (already partially addressed — see "Done" below).

## Done Already

- `frontend/.streamlit/config.toml` created with `primaryColor = "#F96D17"`
  (Hanwha Orange, Pantone 1585 C). Only `primaryColor` was set; the rest of the theme
  is still Streamlit's default and is in scope for the design pass below.

## Functional Plan

**P0 — Core feature coverage**
1. Sidebar mode switcher: `General Chat` / `Document Search (RAG)` / `Equipment Agent`.
2. Wire up RAG mode via `/rag/query`. Note: the current response only returns `answer`,
   not the retrieved `context` — decide whether to surface source context in the UI,
   which would require extending the backend response schema first.
3. Wire up Agent mode via `/agent/query` + `/agent/resume`, including the **HITL approval
   UI** — this is the hardest part. A `pending_approval` response needs a distinct UI
   state (not a normal chat bubble) with Approve/Reject controls that call
   `/agent/resume`. Track the pending `thread_id` explicitly in `st.session_state`,
   since Streamlit reruns the whole script on every interaction.

**P1 — Reliability / quality**
4. Parse structured error bodies (`error`, `reason`/`detail`) into a readable message
   instead of the raw exception string.
5. "New chat" button — resets the session and calls `DELETE /session/{id}`.
6. Update title/copy to "Uptime Copilot" and the equipment-maintenance domain.

**P2 — Nice to have**
7. Manage `thread_id` for Agent mode explicitly (distinct concept from chat's
   `session_id`).
8. Expandable source-context panel for RAG mode.
9. Small badge on each assistant message showing which mode produced it.

## Design Plan

**D0 — Skeleton (do first, cheap and immediately visible)**
1. `st.set_page_config(page_title="Uptime Copilot", page_icon="🛡️", layout="wide")`.
2. Extend `.streamlit/config.toml` beyond `primaryColor` — background/secondary
   background/text colors and font, ideally consistent with the palette already used in
   the build-log artifact (steel blue + copper accent) for a unified brand feel across
   docs and app.
3. Empty-state screen: when there are no messages yet, show a short "what this agent can
   do" card plus a few example-question buttons that submit directly.

**D1 — Per-mode visual distinction (build alongside P0)**
4. Replace the mode `st.radio` with `st.tabs` or a segmented control; each tab carries
   its own hint text / example questions.
5. Distinct `st.chat_message(role, avatar=...)` per mode (e.g., 🤖 general, 📄 RAG,
   🏭 agent) so the response source is visible at a glance.
6. Small mode badge next to each assistant message (ties to P2 item 9).

**D2 — Dedicated HITL approval styling (build together with P0 item 3)**
7. Render `pending_approval` as a bordered `st.container` with warning styling —
   visually distinct from a chat bubble, since it represents a decision point, not a
   conversational turn.
8. Render the three parallel perspectives (safety / production / maintenance) side by
   side with `st.columns(3)` instead of as a single block of text.

**D3 — Loading / feedback states**
9. Agent mode responses are not streamed and can take several seconds (parallel
   perspective calls on urgent cases) — use `st.spinner(...)` with domain-specific copy
   (e.g., "Checking maintenance history...") rather than a generic loading message.
10. Wrap RAG source context in `st.expander("📄 View source documents")`, collapsed by
    default.

## Suggested Execution Order

| Step | Work |
|---|---|
| 1 | D0 (skeleton) — fast, immediately visible |
| 2 | P0 (RAG + Agent wiring) together with D1 (per-mode visuals) — build function and style together, not in separate passes |
| 3 | P0 item 3 (HITL) together with D2 (approval card styling) — the most challenging piece of the session |
| 4 | P1 (error handling, new chat) together with D3 (loading states) |
| 5 | Remaining P2 / D items if time allows |

**Guiding principle**: don't build all functionality first and style it later — build each
piece of functionality with its styling in the same pass, especially for the HITL flow,
where the interaction pattern itself demands a visual treatment different from a normal
chat turn.
