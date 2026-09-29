# Uptime Copilot

[![English](https://img.shields.io/badge/English-current-0c7c8c?style=for-the-badge)](PORTFOLIO.md)
[![한국어](https://img.shields.io/badge/한국어-switch-555555?style=for-the-badge)](PORTFOLIO_KOR.md)

> A RAG + multi-agent copilot for manufacturing predictive maintenance. It diagnoses
> equipment anomalies from real sensor, error, and maintenance-history data, and routes
> urgent cases through Human-in-the-Loop approval into Slack alerts and CMMS work orders —
> an end-to-end pipeline I designed and built myself. Extended with an MRO layer (parts,
> demand forecast, procurement) and closed-network security operations (offline mode,
> audit log) — §11.

`FastAPI` · `LangGraph` · `Streamlit` · `RAG (Chroma)` · `SQLite` · `Model Context Protocol` · `Docker Compose` · `pytest`

| | | | | |
|---|---|---|---|---|
| **100** monitored machines | **100%** failure event recall on held-out data (synthetic benchmark — see caveats) | **97–100%** detection precision (vs **6–18%** for the existing Z-score threshold) | **99** regression tests (CI-enforced, 107 total) | **0** disallowed network calls under `OFFLINE=1` (proven, not assumed — §11) |

---

## 01 · Overview — What and why

Built on the Azure Predictive Maintenance dataset (100 machines, ~870k telemetry rows, plus
error, maintenance, and failure records), the goal was a copilot that, when equipment
misbehaves, diagnoses it with evidence and — when action is needed — carries the case through
approval into the tools a plant actually uses (Slack, a CMMS).

> **Core design principle: "Code states the facts; the LLM only interprets."**
> Diagnostic evidence (error logs, Z-score anomalies, actual replacement history) and
> standard repair procedures are all fetched deterministically from the DB/code. The LLM is
> involved only where judgment is needed — routing, summarization, multi-perspective
> assessment. Because the LLM never re-words the work-order text, there is structurally no
> room for it to distort facts (hallucinate) in the output.

## 02 · Architecture

A Streamlit frontend calls the FastAPI backend. The backend passes every model input/output
through a Harness (a deterministic validation layer) before delegating to the LangGraph
supervisor and the RAG service. State (telemetry, events, HITL checkpoints) lives in SQLite,
document embeddings in Chroma, and only approved urgent cases propagate — deterministically —
to Slack and Atlas CMMS (via MCP).

```mermaid
flowchart LR
    UI["Streamlit UI"]
    subgraph BE["FastAPI Backend"]
        HN["Harness<br/>input / output validation"]
        SV["LangGraph Supervisor"]
        RS["RAG Service<br/>hybrid + multi-query"]
        SC["Event Scanner<br/>/scan"]
    end
    subgraph DL["Data Layer"]
        DB[("SQLite<br/>telemetry / events / checkpoints")]
        VS[("Chroma<br/>vector index")]
    end
    subgraph EX["External Systems"]
        SL["Slack Webhook"]
        MC["Atlas-MCP<br/>(hardened fork)"]
        CM[("Atlas CMMS")]
    end

    UI <--> HN
    HN <--> SV
    HN <--> RS
    SV --> DB
    SC --> DB
    RS --> VS
    SV -- "approved" --> SL
    SV -- "approved" --> MC
    MC --> CM
```
*Figure 1 — Service boundaries and data flow*

## 03 · Core Logic

### Three-tier severity model

Every diagnosis resolves to one of three levels, and the level changes the path through the graph.

| Level | Basis | What happens next |
|---|---|---|
| 🟢 **일반** (normal) | No anomaly | Answer directly, no further action |
| 🟡 **주의** (caution) | Telemetry Z-score anomaly — not yet a confirmed failure | Generate a work order and finalize immediately (no approval) |
| 🔴 **긴급** (urgent) | An actual failure record exists in `PdM_failures.csv` | Three parallel perspective assessments (safety / production / maintenance) → **HITL approval wait** → on approval, propagate to Slack + CMMS |

### LangGraph multi-agent graph

A single `StateGraph` manages everything from routing to approval. Only urgent cases fan out
to three perspective nodes in parallel (results merged via `operator.add`) and pause on
`interrupt()` to wait for a human decision. That approval state is checkpointed with
SqliteSaver, so it survives server restarts.

```mermaid
flowchart TD
    START(("START")) --> route["route"]
    route -- "diagnosis" --> diagnosis["diagnosis"]
    route -- "schedule" --> schedule["schedule"] --> ENDs(("END"))
    route -- "general" --> general["general"] --> ENDg(("END"))
    diagnosis -- "normal" --> general2["general"] --> ENDg2(("END"))
    diagnosis -- "caution / urgent" --> manual["manual_lookup"]
    manual -- "caution" --> wo["work_order"]
    manual -- "urgent" --> safety["safety"]
    manual -- "urgent" --> production["production"]
    manual -- "urgent" --> maintenance["maintenance"]
    safety --> merge["merge_perspectives"]
    production --> merge
    maintenance --> merge
    merge --> wo
    wo -- "urgent" --> approval[["approval\n(HITL interrupt)"]]
    wo -- "caution" --> finalize["finalize"]
    approval --> finalize
    finalize --> ENDf(("END"))
```
*Figure 2 — Nodes and edges map 1:1 to the real graph definition in `backend/agent/agent_service.py`*

### RAG · Harness

| Component | Details |
|---|---|
| RAG retrieval | Hybrid BM25 (sparse) + dense-embedding search, with Multi-Query rewriting to expand each question from several angles. Embeddings: `intfloat/multilingual-e5-small`; vector store: Chroma. |
| Faithfulness scoring | After generation, an LLM judge automatically scores how faithful the answer is to the retrieved context (guards against RAG's "plausible but unsupported answer" failure mode). |
| Harness | A deterministic validation layer on every model input/output — regex-based PII checks (computational) combined with LLM-judge quality/faithfulness checks (inferential). |

### Event scanner — evidence-based suppression

`/scan` sweeps all 100 machines without any LLM and stores urgent/caution findings in an
event store. Whether a completed event is allowed to resurface is decided by **whether new
"evidence" has appeared, not by wall-clock time** — a completed event returns only if new
error logs have actually accumulated since completion; otherwise it stays quietly suppressed
no matter how many times you scan.

## 04 · Automated Degradation Simulator

The source dataset is frozen at 2016-01-01 — nothing new ever happens in it unless someone
manually advances a clock. I built a background simulator that runs its own per-machine
state machine and keeps producing new, evolving failures on its own, so the full
diagnose → approve → notify pipeline can run itself, unattended, for a live demo.

```mermaid
stateDiagram-v2
    [*] --> HEALTHY
    HEALTHY --> DEGRADING: onset (per-hour probability)
    DEGRADING --> FAULT: precursor error fires
    DEGRADING --> HEALTHY: lead time elapses, no error ever fired
    FAULT --> HEALTHY: lead time elapses — failure + maintenance recorded
```
*Figure 3 — Per-machine lifecycle, driven by `backend/data/sim_engine.py`*

> **Calibrated against measured reality, not guessed.** The first version used made-up
> numbers. I measured the real dataset instead — 761 failures across 100 machines — and
> rebuilt every constant around what actually happens there: the telemetry deviation at
> failure time averages **1.6σ**, not the 6σ a naive model would climb to; the lead time
> from first drift to failure runs **44–52 hours**; the large majority of failures are
> preceded by *some* error in the prior week, but only a minority of errors are ever
> followed by a failure. The simulator reproduces that same statistical texture instead of
> an easy-to-detect caricature of it.

A single `asyncio` background task, wired into FastAPI's lifespan, advances all 100
machines together (1 real minute ≈ 1 simulated hour), writes to dedicated `sim_*` tables —
structurally separate from the original, read-only dataset — and runs an incremental scan
plus batched Slack alert after every tick. A `threading.Lock` serializes each tick against
operator-triggered actions (force-inject, reset) so the two can't race on the same
100-row state table.

## 05 · Failure Risk Prediction — replacing a threshold tuned on inflated demo data

The simulator (§04) makes failures visible on demand for a demo. That's separate from
whether the app's actual detection logic — a 3σ Z-score threshold — catches anything on
data that isn't artificially inflated. I measured it against the real archive distribution
first, rather than assume: per-sample, only ~1.2% of the real drift ever crosses 3σ, against
15% in the demo's inflated version — the threshold wasn't wrong on its face, it was tuned
against a signal the underlying dataset rarely actually produces.

So I built a real detector: four per-component LightGBM classifiers, trained and evaluated
on a strict time split (no random splitting — adjacent 3-hour samples share heavily
overlapping features, so a random split leaks — confirmed in the cross-review below).

| Component | Event recall (model) | Event recall (Z-score baseline) | Precision, per 3h sample (model) | Precision, per 3h sample (baseline) |
|---|---|---|---|---|
| comp1 | 100% | 73.3% | 96.7% | 17.5% |
| comp2 | 100% | 86.7% | 100.0% | 14.3% |
| comp3 | 100% | 100.0% | 99.3% | 6.0% |
| comp4 | 100% | 96.2% | 99.5% | 9.6% |

*Event recall = did the model flag the actual failure at least once in the 24h before it
happened, on held-out Nov–Dec test data never seen during training. Precision = of the
individual 3-hour samples flagged positive, how many were genuinely inside a real
pre-failure window — same test period and same models, counted per-sample instead of
per-event.*

**On event recall the baseline is already decent** — 73–100%, tied outright on comp3. The
real gap is precision: the Z-score baseline's individual alerts are 82–94% false positives,
against 0–3% for the model, at a mean lead time of 19.5–21.0h within the 24h prediction
window. That's the actual claim here — not "the baseline catches nothing," but "the
baseline drowns a real signal in false alarms, and the model doesn't."

**Separately**, I re-ran the demo-vs-reality gap through the simulator itself, not just the
archive data, as a second, independent check (the model was never run against this
particular simulated data, so this isn't a model-vs-baseline result — it's a check on the
Z-score threshold alone). Monte Carlo-simulating 8,760 hours × 100 machines through the
actual simulator code, with the demo's "strong episode" knob (`STRONG_FRACTION`) turned
off:

| Mode | `STRONG_FRACTION` | Z-score detection rate |
|---|---|---|
| Demo (shipped default) | 0.15 | 15.45% |
| De-inflated to real levels | 0 | 0.00% |

Zero isn't a rounding artifact — it's exact by construction (normal drift is clipped below
3σ, strong drift starts above it) — and it lines up with what measuring the real archive
data separately predicted (~1.2%, above).

> **Caveat:** near-perfect scores are unusual for real-world predictive maintenance.
> Cross-review (below) ruled out data leakage; the working explanation is that this
> particular public dataset injects degradation cleanly enough to make it an easier
> benchmark than field data. Don't expect this to transfer to noisy real sensor data
> as-is — full discussion in `docs/model_card.md` §8.

<details>
<summary><strong>WAR STORY · When the results are too good, that's a bug report, not a win</strong></summary>

First training run: recall and precision near 1.00 on every component — a number I don't
trust from a real-world predictive-maintenance model. Rather than write it up, I ran an
ablation (telemetry-only features scored 0.11 PR-AUC, error+maintenance-only scored 0.69 —
neither alone was suspiciously strong) and then sent the exact feature/label windowing code
to an independent model for cross-review before touching anything else. It found no future-data
leak, and specifically confirmed the trickiest spot (761 failures, 743 with a same-timestamp
maintenance record) was already handled correctly by the label boundary. It did catch three
real refinements — an under-filled window at the start of each machine's data and an
unclamped label window at the end — and I re-ran training after fixing them: the metrics
barely moved, confirming those weren't the cause. Root cause: this specific public dataset
is synthetically generated with unusually clean injected drift patterns — a known property
of it, not a bug in this codebase. Logged in full, including the parts I got wrong on the
first pass, in `docs/decisions.md`.
</details>

## 06 · External Integrations — Slack alerts · CMMS work orders (MCP)

Post-approval alerts and work-order creation are not tool calls an agent decides to make on
the fly — they are fixed actions that follow a decision already made. So instead of
`langchain-mcp-adapters` (which hands tool selection to the LLM), I used the MCP Python SDK so
that code calls one predetermined tool (`create-work-order`) directly and deterministically.

> I self-hosted **Atlas CMMS** with Docker Compose and forked and hardened a community MCP
> server (Atlas-MCP): always-on Bearer-token checks, constant-time comparison
> (`timingSafeEqual`) against timing attacks, and a Host-header allowlist
> (`ALLOWED_HOSTS`) against DNS rebinding. I later reused the same pattern to extend the
> uptime-copilot backend's own `TrustedHostMiddleware`.

## 07 · Engineering Notes — Problems I actually hit

A system that looks like it works can, on closer inspection, be quietly returning wrong
values. Six cases where I took it all the way through reproduction, root cause, and fix:

<details>
<summary><strong>BUG · Event suppression — "Scanning shows nothing": two clocks were being mixed</strong></summary>

The code compared the completion time (`completed_at`, real wall-clock time) directly against
the evidence time (`evidence_at`, the dataset's own time axis). Urgent events were suppressed
permanently, while caution events resurfaced with no new evidence. I fixed it by carrying
`evidence_at` through the whole pipeline and comparing evidence to evidence only.
</details>

<details>
<summary><strong>BUG · MCP failure handling — why a rejected CMMS call looked like success</strong></summary>

MCP reports tool-execution failures not as exceptions but as an **`isError` field inside a
successful response**. `_create_work_order()` simply discarded the return value of
`call_tool()`, so even when Atlas rejected the call with a 500 the code exited normally. Two
independent review agents (code quality and pipeline) each flagged it; I reproduced it by
calling with a non-existent assetId, then fixed it.
</details>

<details>
<summary><strong>INFRA · Container networking — two defenses against the same attack blocked each other</strong></summary>

For the containerized backend to reach a CMMS running on the host it needed
`host.docker.internal`, and that address was rejected by **both** uptime-copilot's own DNS
rebinding defense and Atlas-MCP's Host-header allowlist. Instead of bypassing either, I
registered the exact host in both defenses.
</details>

<details>
<summary><strong>UX · Measure, don't guess — "line breaks aren't preserved"</strong></summary>

While fixing Slack/CMMS notification readability, I hypothesized that the CMMS screen doesn't
render newlines. I tested even the U+2028 (LINE SEPARATOR) trick by rendering it in headless
Chrome and comparing screenshots, confirmed that text alone cannot solve it, and only then
settled on a separator-based alternative — without modifying another open-source project's
frontend.
</details>

<details>
<summary><strong>BUG · A function vanished mid-edit, and nothing caught it for 70 minutes</strong></summary>

A function the whole simulator background loop depended on was silently deleted while
hand-editing an unrelated part of the same file. Every tick kept "succeeding" from the
outside — `/simulator/status` reported `running: true` the entire time — because the
resulting exception landed in a bare `except Exception: logger.exception(...)` that just
logged and moved on, with no surfaced count of consecutive failures. It only became visible
as a live 500 in the browser, 70 minutes later. I wrote a test that AST-scans every call
site of that module and asserts the referenced name still exists on it, then added mypy to
CI — the exact class of bug a type checker catches in under a second and a test suite alone
can miss.
</details>

<details>
<summary><strong>RACE · Two writers, one table, no lock — a forced demo event could silently vanish</strong></summary>

The background tick and the operator-triggered "force a failure now" endpoint both do a
full read-modify-write of the same 100-row simulator state table, from separate threads.
Whichever finished last silently overwrote the other's write — an operator forcing a
failure for a demo could watch it get discarded by a tick landing at the same moment. A
security review surfaced it by reasoning about the two code paths, not by reproducing it
live; I added a `threading.Lock` around both paths and confirmed the fix by deliberately
racing an inject against a tick.
</details>

## 08 · Quality & Process — Testing and review

| Item | Details |
|---|---|
| Regression tests | For each bug found I add a pytest test, then **revert to the pre-fix code and confirm the test actually goes red** before restoring the fix — a fixed routine that proves the tests aren't just decorative. 99 run automatically in CI (`pytest tests/ --ignore=tests/test_rag_dedup.py`); 107 total. The gap is two opt-in suites excluded by default via pytest markers: a 40-case real-LLM golden-set eval (costs API tokens) and a 3-case `pytest-socket` offline-network proof (§11) that CI can't run since it has no cached embedding model to survive it — plus 2 tests needing the live embedding model, skipped only in CI's fast path via `--ignore`. |
| Static analysis + CI | ruff and mypy run in GitHub Actions on every push/PR, scoped to the modules under active development — adopted specifically because the "vanished function" bug above is exactly what a type checker catches instantly and a test suite might not. |
| Review-agent process | During development, used nine single-lane review subagents (security / code quality / interface / pipeline & model operations / docs / debugging / build & packaging / performance / test validity) instead of one general reviewer — reviewing code in the same context that wrote it lets defects straight through. That tooling was development-only and isn't part of the shipped repo. |
| Cross-review checkpoints | For decisions where getting it wrong is expensive — the failure-risk model's suspiciously perfect first-pass metrics (§05), the priority-rule design that decides shutdown recommendations — I sent the exact code/data to an independent model for review before shipping, rather than self-certify. Both are logged in full in `docs/decisions.md`, including what the reviews actually found. |
| Observability | LangSmith tracing, with traces anonymized using the same PII regexes before being sent. |

## 09 · LLM Provider Flexibility

The app runs against OpenAI by default, but `LLM_PROVIDER=ollama` switches every LLM call —
routing, extraction, perspective assessments, the RAG judge — to a fully local model via
Ollama's OpenAI-compatible endpoint, no code changes needed. I measured what that switch
actually costs rather than assume it's free, and caught my own first measurement being
methodologically unfair before publishing it: an earlier pass compared a single local run
against a single stale cloud run that hadn't gone through the current safety net — an
external review flagged it as apples-to-oranges, tuned on its own failure cases. The
numbers below are from a clean rerun: cloud measured once with current code, local measured
5 times, both split into the 40 questions used to build the safety net and a separate
10-question holdout never looked at while building it.

| Metric | Cloud (1 run) | Local Qwen3-8B (5 runs) |
|---|---|---|
| Router accuracy (40-question golden set) | 90.0% | 87.5–92.5% (mean 91.0%) |
| Router accuracy (10-question holdout) | 100.0% | 90–100% (mean 96.0%) |
| Machine-ID extraction / re-ask on missing ID | 100.0% / 100.0% | 100.0% / 100.0% (all 5 runs) |
| Dangerous misroute (named machine → ungrounded node) | 0 | 0 (across all 10 golden+holdout runs) |
| Mean latency / call | ~1.2s | ~13s |

Extraction and re-ask held up identically from the start — narrow, closed-form tasks.
Routing initially trailed the cloud model by double digits and ran ~10x slower as two
separate calls; both gaps were narrowed on the code side, not by using a bigger model —
merging routing+extraction into one structured-output call, and a deterministic
post-classification check for the specific phrasings the local model kept misrouting into
the ungrounded free-chat node. A spot check outside the golden set also caught the local
model producing an internally contradictory structured output (`risk_level: 중간` alongside
`requires_shutdown: true`) that didn't appear on the cloud model — fixed structurally by
deriving `requires_shutdown` in code instead of letting the model set it. Bottom line: local
now matches cloud on accuracy (91.0% vs 90.0%) with the safety-critical metric at zero
misroutes across all runs; the remaining real cost is latency, mitigated by call-merging and
a warm-up ping but not eliminated. Worth it for a closed environment; not a drop-in free
upgrade.

## 10 · Stack

`FastAPI` `LangGraph` `LangChain` `OpenAI API` `Ollama` `LightGBM` `scikit-learn`
`Streamlit` `SQLite` `Chroma`
`HuggingFace sentence-transformers` `rank_bm25` `Model Context Protocol SDK`
`LangSmith` `Docker / Docker Compose` `pytest / pytest-asyncio` `pytest-socket` `asyncio`
`ruff` `mypy` `GitHub Actions`

## 11 · MRO Extension — Parts, Inventory, and Security Operations

The predictive-maintenance core above answers "is this machine failing?" A real MRO
(Maintenance, Repair, Operations) workflow also needs "do we have the part, and can we say
so without pretending this is real inventory data?" — plus the two things a closed-network
deployment actually needs before it can run there at all: proof that nothing leaks out, and
a record of everything that happened. All numbers below are synthetic-data demos, disclosed
as such in the UI and in `docs/design/parts_assumptions.md` — see §0-1 of
[mro-copilot-upgrade-plan.md](../mro-copilot-upgrade-plan.md) for why this is scoped as a
separate extension rather than folded into the core numbers above.

| Component | What it does |
|---|---|
| Parts master + demand forecast | 5 synthetic parts, 30/90-day replacement forecast split into a preventive term and a failure term (see below), backtested against real Nov–Dec 2015 replacement counts: -5.0% to +3.5% error |
| Inventory-risk dashboard | `GET /parts/inventory_risk`, flags shortfalls and distinguishes a genuine risk (lead time exceeds the horizon) from a normal reorder signal (it doesn't) |
| Procurement urgency, kept separate from priority | A missing part doesn't inflate how urgent a diagnosis is — it's its own line on the work order, decided by the same deterministic function the background scanner uses |
| Offline mode | Destination-based allowlist (not a per-module switch); proven with `pytest-socket` against the real `notify.py`/`cmms_client.py` code paths, not mocks |
| Audit log | Every LLM call, tool call, approval/rejection, external push, and offline-blocked call, correlated by thread ID |

<details>
<summary><strong>WAR STORY · A statistics bug that only showed up as the wrong kind of wrong, twice</strong></summary>

The first version of the demand formula applied a single alarm-conditioned failure rate
across the entire 30/90-day forecast window. It looked reasonable and it was wrong in a way
that's easy to miss: an external review found the model's precision is high enough that the
"no alarm today" failure rate is effectively zero in the validation data — apply that flat
rate across the whole window and you're claiming a machine with no alarm today stays safe
for the next 90 days, which isn't a claim the model actually supports (it only knows about
the next 24h). Fixing it by swapping to the correct conditional rate seemed obvious — and
produced a new, opposite bug: 22–32% underprediction, caught immediately by the backtest I'd
already built. The rate wasn't wrong, its *scope* was: today (known alarm state) needs the
conditional rate, every day after (unknown future alarm state) needs the population rate,
and conflating the two either direction breaks it. Splitting day-1 from day-2-onward fixed
both errors at once. Logged in full, including the failed first attempt, in
`docs/decisions.md`.
</details>

<details>
<summary><strong>BUG · A synthetic scarcity demo that only worked by coincidence</strong></summary>

One part was deliberately seeded with low stock to demo a procurement-shortage scenario.
Widening a different, unrelated part's random-number range shifted the shared RNG's draw
sequence and silently flipped the demo part back to "not actually short" — the shortage had
never been guaranteed by design, just a lucky roll under one specific seed. Fixed by
narrowing that part's own range so the condition holds regardless of what else draws from
the same generator first.
</details>

<details>
<summary><strong>UX · Fixing a document doesn't fix the product — a distinction the user had to point out to me</strong></summary>

An audit of the plan document turned up a real product gap (two parts showing a 90-day
shortfall in a tone visually identical to a genuinely urgent one, when their lead time meant
they weren't actually at risk) and I corrected the *documentation* to accurately describe it
as unresolved — then reported it back as if that were the fix. It wasn't; the on-screen
inventory tab still read the same way to an actual user. Caught by a direct question — "is
that also resolved?" — that separated "the record is now accurate" from "the thing it
describes is now different." Fixed by branching the UI on the coverability flag the backend
already computed, so a genuine risk still warns and a normal reorder point reads as one.
</details>

<details>
<summary><strong>INFRA · Two pytest-socket functions with overlapping names, opposite designs</strong></summary>

`disable_socket()` and `socket_allow_hosts()` look composable — call both, get "block
everything except this allowlist." They aren't: `disable_socket()` replaces `socket.socket`
itself and unconditionally blocks `getaddrinfo`, before `socket_allow_hosts()`'s
connect-level allowlist check ever runs. Reading the plugin's own source
(`inspect.getsource`, not the docs) showed the actual supported combination is the
`allow_hosts` marker, which calls `socket_allow_hosts()` alone. Switched to that and the
allowlist test started passing for the right reason instead of failing for a different one.
</details>

<details>
<summary><strong>GAP · The most safety-relevant log lines were the ones with no thread ID</strong></summary>

After wiring six audit-log call sites, a live end-to-end run — filtered to one thread ID —
was missing the three safety/production/maintenance risk assessments entirely. The shared
assessment function had never taken a `thread_id` parameter, because at the time I wrote
that connection point it didn't seem load-bearing enough to plumb through. Querying one real
scenario end to end, rather than trusting that six connected call sites meant six usable log
lines, is what actually surfaced it.
</details>

---

<sub>Uptime Copilot — Predictive Maintenance & MRO Copilot · Case study</sub>
