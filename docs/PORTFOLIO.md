# Uptime Copilot

[![English](https://img.shields.io/badge/English-current-0c7c8c?style=for-the-badge)](PORTFOLIO.md)
[![한국어](https://img.shields.io/badge/한국어-switch-555555?style=for-the-badge)](PORTFOLIO_KOR.md)

> A RAG + multi-agent copilot for manufacturing predictive maintenance. It diagnoses
> equipment anomalies from real sensor, error, and maintenance-history data, and routes
> urgent cases through Human-in-the-Loop approval into Slack alerts and CMMS work orders —
> an end-to-end pipeline I designed and built myself.

`FastAPI` · `LangGraph` · `Streamlit` · `RAG (Chroma)` · `SQLite` · `Model Context Protocol` · `Docker Compose` · `pytest`

| | | | |
|---|---|---|---|
| **100** monitored machines | **3-tier** severity model | **51** regression tests | **9** dedicated review agents |

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

## 05 · External Integrations — Slack alerts · CMMS work orders (MCP)

Post-approval alerts and work-order creation are not tool calls an agent decides to make on
the fly — they are fixed actions that follow a decision already made. So instead of
`langchain-mcp-adapters` (which hands tool selection to the LLM), I used the MCP Python SDK so
that code calls one predetermined tool (`create-work-order`) directly and deterministically.

> I self-hosted **Atlas CMMS** with Docker Compose and forked and hardened a community MCP
> server (Atlas-MCP): always-on Bearer-token checks, constant-time comparison
> (`timingSafeEqual`) against timing attacks, and a Host-header allowlist
> (`ALLOWED_HOSTS`) against DNS rebinding. I later reused the same pattern to extend the
> uptime-copilot backend's own `TrustedHostMiddleware`.

## 06 · Engineering Notes — Problems I actually hit

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

## 07 · Quality & Process — Testing and review

| Item | Details |
|---|---|
| Regression tests | For each bug found I add a pytest test, then **revert to the pre-fix code and confirm the test actually goes red** before restoring the fix — a fixed routine that proves the tests aren't just decorative. 51 tests total. |
| Static analysis + CI | ruff and mypy run in GitHub Actions on every push/PR, scoped to the modules under active development — adopted specifically because the "vanished function" bug above is exactly what a type checker catches instantly and a test suite might not. |
| Dedicated review agents | Nine subagents, each reviewing from one lane only: security / code quality / interface / pipeline & model operations / docs / debugging / build & packaging / performance / test validity — designed on the premise that reviewing code in the same context that wrote it lets defects straight through. |
| Observability | LangSmith tracing, with traces anonymized using the same PII regexes before being sent. |

## 08 · Stack

`FastAPI` `LangGraph` `LangChain` `OpenAI API` `Streamlit` `SQLite` `Chroma`
`HuggingFace sentence-transformers` `rank_bm25` `Model Context Protocol SDK`
`LangSmith` `Docker / Docker Compose` `pytest / pytest-asyncio` `asyncio`
`ruff` `mypy` `GitHub Actions`

---

<sub>Uptime Copilot — Predictive Maintenance Copilot · Case study</sub>
