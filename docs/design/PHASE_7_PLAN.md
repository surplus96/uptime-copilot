# Phase 7 — CMMS/Alert Integration via Microsoft's Predictive-Maintenance Reference Architecture

> **Not required to run this project.** Everything below is an optional integration
> (Slack alerts + CMMS work-order push). With `SLACK_WEBHOOK_URL` / `CMMS_MCP_URL` /
> `CMMS_MCP_TOKEN` unset, both paths no-op and the app is fully functional. See
> `README.md` to just run it.

(Replaces the previous Phase 7 definition of "Clean Architecture, TDD, SDD methodology,"
which was never started and is superseded by this plan — see decision trail in
`SESSION_SUMMARY.md`'s 2026-09-17 entries and this session's MCP-scope conversation.)

## Why this replaces the old Phase 7

The old Phase 7 was a generic engineering-methodology upgrade with no concrete trigger.
This one has a concrete trigger and a concrete external validation: Microsoft's own
Fabric Real-Time Intelligence predictive-maintenance reference architecture states the
target pattern explicitly —

> "A key challenge is ensuring alerts go to a CMMS work order rather than an email inbox,
> with sensor data traveling with the work order so technicians understand the context
> before touching equipment."

Our `agent_service.py`'s `work_order_node` already satisfies the second half of that
sentence — `component_evidence` (actual error/failure/telemetry evidence) is assembled
deterministically into the `[매뉴얼 근거]` field, not restated/hallucinated by an LLM. What
we're missing is the first half: the work order's destination is currently a Streamlit
screen, not a CMMS. Phase 7 closes that gap, plus adds the simpler notification channel
alongside it.

## Explicit non-goals (carried over from this session's critical evaluation — do not re-litigate without new evidence)

- **No SCADA/IoT live telemetry integration.** That's MCP re-check item 3
  (`SESSION_SUMMARY.md` "재검토 사항"); the dataset stays static/simulator-driven.
- **No custom-built fake external system to "practice" MCP against.** Rejected this
  session: it recreates the exact situation that made the original `mcp` /
  `langchain-mcp-adapters` dependencies dead weight (no real system on the other end).
- **No MCP for the notification channel (Stage 1 below).** A deterministic "if 긴급:
  notify" rule is a direct-API-call case, not an agentic-tool-use case — confirmed by
  2026 MCP-vs-API guidance (see `sources` at bottom).
- **Simulator auto-ticking is a separate, smaller backlog item** (adjacent to the existing
  "`/scan` 자동 스케줄러" item), not part of this phase.

## Stage 0 — Precondition (blocking on Stage 2, not Stage 1)

Confirm whether there is an actual target CMMS/ERP vendor for this project, or whether
Stage 2 is exploratory/portfolio work with no real deployment target yet. This gate exists
specifically to avoid repeating the dead-dependency mistake — Stage 2 must not expand
scope (real vendor contract, production credentials, etc.) without a real answer here.

If no real vendor is ever named: Stage 2 stays a self-contained demo against a trial
account, explicitly not extended further, and this file's Stage 2 section is the record
of why it stopped there.

**Resolved 2026-09-17**: no real vendor named. Stage 2 proceeds as a demo-only prototype
(target picked and revised same day — see Stage 2 step 1).

## Stage 1 — Slack/Email alert channel (direct webhook, no MCP) — ✅ Done (2026-09-17)

Supplementary, fast "heads-up" channel — **not** a replacement for Stage 2. Per the
Microsoft quote above, an email/chat ping is explicitly the pattern being upgraded away
from, not the end state; this stage exists because it's cheap and independent, not because
it satisfies the reference architecture.

- New module `backend/notify.py`: a thin function calling a Slack Incoming Webhook URL
  (or SMTP for email), no client library/session lifecycle to manage.
- Trigger points (two, both already know exactly when to fire — no LLM decision needed):
  - `agent_service.scan_all_machines()`, right after `event_store.save_event(...)` for a
    newly-surfaced 긴급/주의 event.
  - `finalize_node()`, on 긴급 **승인** outcome only (반려는 아무 것도 전송하지 않음 —
    확인된 의도된 동작, `docs-reviewer` 지적으로 이 문구를 정정, 2026-09-18).
- Config: one new env var (`SLACK_WEBHOOK_URL` or `SMTP_*`), no new service.
- Explicitly deferred to later (not this stage): letting the conversational agent itself
  decide, mid-chat, to compose and send an ad hoc Slack message. That *would* be a genuine
  MCP use case (Slack's official hosted MCP server, GA since Feb 2026) — revisit only if
  that specific interactive need shows up.

**Verified live (2026-09-17)**: Slack channel chosen (Incoming Webhook, not email).
Both trigger points confirmed against a real Slack workspace — `scan_all_machines()`
posted 3 real detections (설비 #5 주의, #34/#84 긴급) in the exact `[severity] 설비 #id
이상 감지\n{diagnosis}` format; approving 설비 #84's 긴급 work order posted the
`[긴급 승인] 설비 #84 작업지시서 승인됨\n{work_order}` message with the full parsed
`[부품]/[증상]/[매뉴얼 근거]/[조치사항]/[긴급도]` blocks intact. `requests` added to
`backend/requirements.txt` (was only a transitive dependency before, now pinned
explicitly). **Failure-swallowing behavior tested live 2026-09-18**: a truly unreachable
host correctly timed out at 5s and logged `[알림 실패]` — fine as originally written. But
a webhook Slack itself rejects (bad path, still a real HTTP response) was **silently
swallowed with no log at all**: `requests.post()` doesn't raise on a non-2xx status
without `.raise_for_status()`, so the `except requests.exceptions.RequestException`
never triggered. Fixed same day: added `response.raise_for_status()` before the
implicit success return, re-verified both the now-logged rejection case and that the
real, valid webhook still succeeds with no exception.

Side finding while testing: the 이상감지 이벤트 list's "마지막 스캔" timestamp is real
wall-clock (`datetime.now()`, e.g. `2026-09-17T16:09:40`) while the diagnosis/work-order
content's dates are the simulated dataset clock (e.g. `2016-01-01 08:00:00`) — both
correct by design (`event_store.py`'s `_dataset_now()` vs `save_event`'s `detected_at`,
same wall-clock/dataset-clock separation this project adopted after the
`check_recent_failure` bug). Not documented anywhere in the UI, so it reads as a bug on
first encounter. Follow-up: ✅ shipped — see `frontend/streamlit_app.py`'s 이상감지 이벤트
tab caption (added 2026-09-17, confirmed still present by `docs-reviewer` 2026-09-18).

## Stage 2 — CMMS work-order push (thin adapter onto an existing MCP gateway)

This is the stage that actually implements the Microsoft reference pattern
("prediction → CMMS work order, evidence attached").

1. **Pick a concrete target for prototyping**.
   - ~~Original pick: Limble CMMS (`https://mcp.limblecmms.com/mcp`).~~ **Reverted
     2026-09-17**: Limble's MCP feature is gated to Premium+/Enterprise plans, which in
     practice means a sales demo call to get access — not workable for an individual
     prototyping this on their own, and not worth that relationship for a demo-only
     exercise (Stage 0 already decided this stays demo-only).
   - **New target: Atlas CMMS**, self-hosted (`github.com/Grashjs/cmms`, GPL v3, Docker /
     Docker Compose install). Removes the vendor-account problem entirely — there's no
     company to request a demo from, the "account" is the Docker container you run
     yourself. Still a genuinely real, functioning CMMS (API-first REST backend, real
     work-order storage), not a fake stand-in — it just satisfies "existing real system"
     without requiring a business relationship.
   - A community-built MCP server for it already exists ("Atlas MCP" by GabrielGB1999,
     `github.com/GabrielGB1999/Atlas-MCP`, `main` = `fabced6`) — signs into Atlas's REST
     API once with a service-account email/password, holds the JWT in memory, refreshes
     automatically.
   - **`security-reviewer` audit of Atlas-MCP, completed 2026-09-17 (static source read,
     nothing installed/run/committed)**: **conditional GO** for a throwaway demo. Notable
     context: 6 of 7 commits are authored *and* committed by `Claude <noreply@anthropic.com>`
     — this is agent-written code with one human commit (the LICENSE), no independent
     human review. Code quality itself checked out fine (zod validation on every tool
     input, timing-safe bearer comparison, error bodies never reach the MCP client, no
     secrets in git history across all commits/branches, no outbound calls beyond the
     configured Atlas host, dependency versions past known CVE lines) — but:
     - **HIGH**: ships insecure-by-default — `MCP_AUTH_TOKEN` unset means no auth check at
       all, and `docker-compose.yml` binds the port to `0.0.0.0` (all interfaces). Running
       it with the example `.env` as-is exposes an unauthenticated work-order
       create/update/assign/status-change surface to the whole LAN.
     - **MEDIUM x3**: no Origin/Host check on the HTTP transport (DNS-rebinding exposure,
       same fix as above); `API_BASE_URL` defaults to plaintext `http://` and the password
       is re-sent on every token refresh, not just once; failed-request logging writes full
       request/response bodies (not credentials, but our generated work-order text would
       land in container logs).
     - **MEDIUM (design-relevant, not a code defect)**: CMMS-authored text flows verbatim
       into what the agent reads back — a prompt-injection surface into a tool-using agent
       that also has write access. Our own design already mitigates this by construction
       (CMMS push only ever fires after human approval in `finalize_node`, never on
       agent-initiated judgment) — just don't let that invariant erode later.
     - **Required before running it** (all of these, not optional): set a real
       `MCP_AUTH_TOKEN`; change the compose port mapping to `127.0.0.1:3000:3000`; use a
       dedicated throwaway Atlas service account whose password is never reused anywhere
       else; keep the MCP container and Atlas API on the same host (or put TLS in front of
       Atlas); `npm ci` rather than `npm install` if run outside Docker; **pin to commit
       `fabced6` rather than tracking `main`** (or vendor a fork — only ~2,100 lines, four
       direct deps, cheap to own outright and removes the supply-chain question).
     - No credential rotation or takedown needed — nothing has been run yet with this
       server, and no live secret was ever found in its history.
   - **Hardening applied 2026-09-17** (`/Users/surplus96/projects/atlas-mcp`, forked from
     `fabced6`, `hardened` branch, commit `424163a`): `MCP_AUTH_TOKEN` now required at
     startup (fail-fast, matching the existing `requireEnv` pattern for
     `API_EMAIL`/`API_PASSWORD`) instead of silently allowing an unauthenticated
     endpoint; `docker-compose.yml` port binds to `127.0.0.1` only; added a Host-header
     allowlist gate on `/mcp` (DNS-rebinding defense, configurable via `ALLOWED_HOSTS`);
     error logging now records field names/shape (`shapeOf()`) instead of full
     request/response bodies; container runs as `USER node`, not root. `npm run lint`
     and `npm test` both pass unchanged (16/16, integration suite still skips without
     live creds, as upstream documented). Live smoke-tested on a scratch port: no token
     → 401, spoofed `Host` header → 421, valid token+host → 200 through to real tool
     dispatch. Remaining upstream `npm install` (vs `npm ci`) is only in the README's
     bare-metal dev instructions, not in the Dockerfile we actually run — left alone.
   - If a real vendor is ever named later (Stage 0 reopened): check that vendor's own MCP
     server first, then Makini (`makini.io`) as an aggregator gateway, before reaching for
     a custom REST integration.
   - **Connected live 2026-09-17.** Two environment gotchas hit and fixed along the way
     (both in `/Users/surplus96/projects/atlas-mcp`, `hardened` branch):
     - Host-port collision: Atlas-MCP's own default port is also 3000, colliding with
       Atlas CMMS's nginx on host 3000. Fixed in `docker-compose.yml` (commit `9417fe1`):
       host-side remapped to **3100**, container-internal stays 3000.
     - Since the host port changed, real requests' `Host` header became `localhost:3100`,
       which didn't match the hardening's allowlist (built from `MCP_PORT`) — fixed by
       actually wiring `ALLOWED_HOSTS` through `docker-compose.yml`'s `environment:` block
       (commit `9348657`; Compose does not auto-forward `.env` into the container, only
       vars explicitly listed there).
     - `API_BASE_URL` must be `http://host.docker.internal:3000/api`, **not**
       `http://localhost:3000/api` — from inside the Atlas-MCP container, `localhost`
       means the container itself (which is also listening on 3000), not the host. This
       produced a confusing same-shaped 404 ("Cannot POST /api/auth/signin") from
       Atlas-MCP's own Express server answering its own signin call — worth remembering,
       the error looked identical whether it came from the real backend or from
       misrouted-to-self traffic.
     - Verified end-to-end: `tools/list` returns 9 tools — writes: `create-work-order`,
       `update-work-order`, `change-work-order-status`, `assign-work-order`; reads:
       `list-work-orders`, `get-work-order`, `list-assets`, `get-asset`,
       `generate-weekly-work-order-report`. `create-work-order` requires only `title` and
       `description`; everything else (`assetId` int, `priority`
       NONE/LOW/MEDIUM/HIGH, `dueDate`, `locationId`, `teamId`, etc.) is optional.
       `list-assets` against the real Atlas instance found our test asset: **"Machine #84"
       → `assetId: 1`** (display id `A000001`, location "test-uptime-copilot").
     - Separately flagged (not fixed, not blocking): the Atlas CMMS account's password was
       twice set to something that looks like a real/personal, guessable pattern (a name
       and what looks like a birthdate) rather than a random throwaway value, despite the
       explicit "dedicated throwaway account, password never reused" condition from the
       security review. Recommended generating one with `openssl rand -base64 18` instead.
2. **Add an MCP client to the graph — ✅ Done (2026-09-18).** Not a new LangGraph node in
   the end (simpler than originally planned): `backend/cmms_client.py` added
   (`push_work_order()`, using the official `mcp` Python SDK's `ClientSession` +
   `streamablehttp_client` directly — verified against the real installed `mcp==1.30.0`
   API before writing the guidance, since two different web sources disagreed on the
   client's shape and only one matched what's actually installed). Called inline from
   `finalize_node()`'s existing `if state.approved:` branch, same location as Stage 1's
   `notify.send_alert(...)` — no separate graph node needed since no agent decision is
   involved (`priority` is unconditionally `"HIGH"`, matching the fact this path only
   ever runs for 긴급+approved). `mcp` added to `backend/requirements.txt`;
   `CMMS_MCP_URL`/`CMMS_MCP_TOKEN` added to `backend/.env.example` (the token must match
   `atlas-mcp/.env`'s `MCP_AUTH_TOKEN` exactly). Note: `push_work_order()` refuses to send
   over plaintext `http://` unless the host is a recognized loopback address
   (`localhost`/`127.0.0.1`/`host.docker.internal` — `_is_loopback_url()`,
   `backend/cmms_client.py`) to avoid leaking `CMMS_MCP_TOKEN` in the clear; pointing
   `CMMS_MCP_URL` at a real LAN/public host over `http://` gets silently blocked
   (`[CMMS push 실패] ... 차단` in the logs) rather than sent — use `https://` for anything
   beyond loopback.
3. **Field mapping — turned out to need none.** `work_order_node` already keeps
   `state.work_order` as one deterministic, evidence-grounded string (see its docstring);
   `push_work_order()` just reuses it verbatim as the `description` argument to
   `create-work-order`, no re-parsing of `[부품]/[증상]/...` needed. `title` is
   synthesized separately (`설비 #{machine_id} 긴급 정비`). Real tool schema discovered
   live via `tools/list` (see Stage 2 step 1 above) confirmed only `title`/`description`
   are required. `MACHINE_ID_TO_ASSET_ID = {84: 1}` is a hardcoded single-entry stand-in
   for a real machine-id → Atlas-assetId mapping — fine for this prototype, a real
   integration needs a proper lookup/mapping table.
4. **Verify — ✅ Done (2026-09-18), twice.** First a standalone script
   (`asyncio.run`-based, same shape as the final code) confirmed the whole path end-to-end
   before handing the pattern over as guidance — created `WO000001`. Then the actual
   `finalize_node` code path was exercised live through the real app: 84번 설비 긴급
   진단 → 3-관점 카드 → 승인 → backend log shows 4 clean `POST .../mcp` calls (200/202,
   no `[CMMS push 실패]`) → Slack got the Stage 1 alert → **Atlas CMMS UI shows a second,
   genuinely new work order, `WO000002`**, created `09/18/26 10:25` (matches the backend
   log's `[승인 재개]` timestamp to the second), `Asset: Machine #84`, `HIGH PRIORITY`,
   full `comp3`+`comp1` evidence blocks intact, `Created By: Taeyoung Choi` (the Atlas
   service account) — confirmed via screenshot, not just log inference.
5. **Failure handling — ✅ Done, tested live 2026-09-18.** `finalize_node`'s call is
   wrapped in `try/except Exception` with a `print(f"[CMMS push 실패] {e}")` fallback.
   Unlike `notify.py`, `cmms_client.push_work_order()` has no internal try/except of its
   own — it relies entirely on the caller. Verified both real failure modes directly
   against `push_work_order()` (not just read the code):
   - **Atlas-MCP actually stopped** (`docker compose stop`): raises an `anyio`
     `ExceptionGroup` (from the `TaskGroup` inside `streamablehttp_client`) — confirmed
     `except Exception` in `finalize_node` catches it fine (`ExceptionGroup` subclasses
     `Exception`, not `BaseException`, when every sub-exception is itself an `Exception`).
     Logged as `[CMMS push 실패] unhandled errors in a TaskGroup (1 sub-exception)` — a
     real if not very legible message; worth wrapping with more context if this gets
     revisited, not urgent for a demo.
   - **Wrong `CMMS_MCP_TOKEN`**: same graceful path; Atlas-MCP's own hardening logged
     "Rejected /mcp request with missing or invalid bearer token" on its side too — the
     421/401 hardening from Stage 2 step 1 is doing its job against our own client, not
     just a hypothetical attacker.
   - **`machine_id` with no `MACHINE_ID_TO_ASSET_ID` entry** (tested with 84 → 15): work
     order created successfully with `"asset": null`, confirmed via `list-work-orders`
     (`WO000004`) — no exception, exactly the intended degrade-gracefully behavior.

**Stage 2 verdict: prototype complete, proven with real data, and its failure paths are
now proven too** (not just asserted) — within its stated scope (demo-only, `assetId`
hardcoded to one test machine, no real vendor). Remaining before this could be anything
beyond a demo: a real `machine_id → assetId` mapping, and Stage 3's decision (real vendor
vs. staying a demo). Not yet tested: resuming a pending approval across a full backend
restart with the CMMS integration in place (Stage 1 alone was verified pre-Stage-2;
deferred since it requires restarting the live dev server rather than an isolated script).

**Phase 7 testing pass closed out 2026-09-18** — Stages 0-2 and their failure paths are
done and verified; the restart/resume case above is the one explicitly deferred item,
not forgotten.

**Gap found and fixed the same day, by a full-project audit pass** (`code-quality-reviewer`
and `pipeline-optimizer`, independently, the latter also reading the actual `atlas-mcp`
server source): the failure tests above only covered *transport*-level failures (server
down, bad token) — none exercised Atlas rejecting the request at the *application* level.
MCP reports that case as `isError: true` inside an otherwise-successful response, not as
an exception, and `cmms_client.py`'s `_create_work_order` discarded `call_tool`'s return
value entirely — so a real CMMS-side rejection would have looked like a successful push.
Fixed: check `result.isError` and raise. **Reproduced the exact failure live** (a bogus
`assetId` → Atlas returns 500 → MCP wraps it as `isError: true`) and confirmed the fix
now raises and would be caught by `finalize_node`'s existing `except Exception`, closing
the one real hole in an otherwise-verified integration.

## Stage 3 — Decision point after Stage 2 prototype

- Real vendor confirmed (Stage 0 resolved with an answer) → swap the Atlas CMMS prototype
  for the actual target's MCP server, or Makini if the target lacks a native one.
- No real vendor ever confirmed → Atlas CMMS prototype stands as-is, explicitly not
  deployed or extended. Update this file to record that outcome rather than silently
  abandoning it.

## What NOT to carry over from the old Phase 7 wording

The old Phase 7 mentioned Clean Architecture / TDD / SDD as general goals with no trigger.
This plan doesn't reintroduce that scope. The one place it's naturally relevant is Stage
2 step 3 (the work-order field-mapping logic) — that's a pure function worth a couple of
unit tests once it exists, the same way the `evidence_at` suppression bug this session
would have been caught earlier by one. Not a phase-wide mandate; just noted where it
actually applies.

---

### Sources (from this session's research, for whoever picks this up later — re-verify, the MCP ecosystem moves fast)

- [Predictive Maintenance Architecture With Real-Time Intelligence — Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/predictive-maintenance)
- [Limble MCP: User Setup Guide](https://help.limblecmms.com/en/articles/12902567-limble-mcp-user-setup-guide)
- [Why Model Context Protocol is the Missing Link for AI in Maintenance — Limble](https://limble.com/blog/model-context-protocol/)
- [Makini | Universal API & MCP Server for Industrial Systems](https://www.makini.io/)
- [When to use — MCP vs API vs Function/Tool call in your AI Agent](https://jamwithai.substack.com/p/when-to-use-mcp-vs-api-vs-functiontool)
- [GitHub - Grashjs/cmms — Atlas CMMS, self-hosted open source](https://github.com/Grashjs/cmms)
- [Free Open-Source CMMS — atlas-cmms.com](https://atlas-cmms.com/)
- [Atlas MCP by GabrielGB1999 | Glama](https://glama.ai/mcp/servers/GabrielGB1999/Atlas-MCP)
- [Atlas | MCP Servers · LobeHub](https://lobehub.com/mcp/atlas-agent-atlas)
