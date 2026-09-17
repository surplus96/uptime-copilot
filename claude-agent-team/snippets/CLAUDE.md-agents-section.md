Paste the block below into the project's `CLAUDE.md`, after installing the
agents. Without it the agents exist but nothing tells the session to reach for
them — which is the failure mode the agents were created to fix.

The last paragraph is the part that does the work: it makes the session record
which specialist took which piece, so "did you actually use the team?" is
answerable from the summary rather than from trust.

---

## The agent team

`.claude/agents/` defines nine specialists — five auditors, three
diagnosticians, one verifier. [Its README](.claude/agents/README.md) routes by
question type, and lists what was deliberately *not* created and what would
justify adding it.

Use them. Reviewing your own work in the same context that produced it is how
defects get through the first time. Spawn the specialist whose question you
actually have, rather than a general reviewer.

Then record it: end every summary with one short line per piece of work, naming
the agent that took it — or saying plainly that none was needed and why. Both
halves matter: a list that only ever names agents is not evidence of judgement,
it is evidence of ceremony. Keep it to a line each; the reasoning belongs in the
body of the report.

```
- Secret-scanning CI     → security-reviewer. Found three bypasses I had missed.
- Documentation set      → docs-reviewer. Found two live defects.
- v0.3.0 tag             → no agent. One command, and it either works or 403s.
```
