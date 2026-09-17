# Claude Code agent team

Nine review-and-diagnosis subagents, packaged for local installation.

Extracted from [`surplus96/Langgraph-MCP-Agent`](https://github.com/surplus96/Langgraph-MCP-Agent)
at `.claude/agents/`. The agent definitions in `agents/` are byte-identical
copies of the ones that repository uses — verify with `MANIFEST.txt`.

Korean: see [README_KOR.md](README_KOR.md).

## What is in here

```
agents/            The 9 agent definitions + their routing README
install.ps1        Installer (Windows PowerShell)
install.sh         Installer (Linux / macOS)
snippets/          A block to paste into a project's CLAUDE.md
MANIFEST.txt       sha256 of every agent file
```

## The nine

| Agent | Kind | Answers |
|---|---|---|
| `code-quality-reviewer` | auditor | Is this maintainable? |
| `security-reviewer` | auditor | Can this be attacked? |
| `pipeline-optimizer` | auditor | Is the LLM runtime current and token-efficient? |
| `docs-reviewer` | auditor | Do the docs match the code? |
| `interface-reviewer` | auditor | Does the app tell the user the truth? |
| `debugger` | diagnostician | Why does this fail? |
| `build-doctor` | diagnostician | Why does this not build, install or run in CI? |
| `performance-profiler` | diagnostician | Where does the time go? |
| `test-engineer` | verifier | Would these tests fail if the code were wrong? |

Full routing rules, the history that produced each agent, and the list of roles
deliberately *not* created are in [`agents/README.md`](agents/README.md). Read
that before adding a tenth.

## Install

Two scopes. They are not exclusive — a project copy shadows the user copy.

| Scope | Target | Use when |
|---|---|---|
| **user** | `~/.claude/agents/` (`%USERPROFILE%\.claude\agents\` on Windows) | You want the team available in every project on this machine. |
| **project** | `<project>/.claude/agents/` | You want the team committed alongside one repository, so collaborators and CI sessions get it too. |

### Windows (PowerShell)

```powershell
# every project on this machine
.\install.ps1 -Scope user

# one repository
.\install.ps1 -Scope project -Path D:\cty_ai\some-project

# see what would change, write nothing
.\install.ps1 -Scope user -DryRun
```

PowerShell may refuse to run an unsigned script. Either unblock this one file:

```powershell
Unblock-File .\install.ps1
```

or run it for this session only:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Scope user
```

### Linux / macOS

```bash
chmod +x install.sh
./install.sh --scope user
./install.sh --scope project --path ~/code/some-project
./install.sh --scope user --dry-run
```

### By hand

Copy `agents/*.md` into `.claude/agents/`. That is the whole mechanism — Claude
Code reads every `.md` in that directory and registers the `name` in its
frontmatter. No restart of anything is required beyond starting a new session.

Both installers refuse to overwrite an existing file unless you pass `-Force`
(`--force`), and print the name of every file they skipped.

## Verify the install

Start a Claude Code session in the target project and ask it to list its
subagent types, or just name one:

```
use the security-reviewer agent to audit this repository
```

If the agent is not found, check that the file landed in `.claude/agents/`
(not `.claude/`) and that its frontmatter still has `name:` as the first key.

## Porting to a project with a different stack

The agent definitions are mostly stack-neutral, but three carry references to
the project they came from. They still work elsewhere; they will just spend a
few sentences on things your project does not have.

| File | Line | Reference |
|---|---|---|
| `code-quality-reviewer.md` | frontmatter, item 4 | Python / Streamlit `session_state` |
| `pipeline-optimizer.md` | frontmatter, items 3 and 7 | LangGraph wiring, Streamlit rerun |
| `security-reviewer.md` | item 5 | Streamlit CORS/XSRF, MCP trust boundary |

`agents/README.md` also names LangGraph once, in the routing table.

If your project is not an LLM application at all, `pipeline-optimizer` is the
one with nothing to do — drop it rather than rewriting it. The rest transfer
unchanged.

## The rule that makes them worth installing

From the source repository's `CLAUDE.md`:

> **A green suite is not evidence. A test counts only if breaking the code
> breaks the test.**

and

> Reviewing your own work in the same context that produced it is how the
> caching no-op, the surviving mutations, and the half-landed timeout fix all
> got through the first time.

Every one of the nine exists because a specific defect got past self-review.
`agents/README.md` names which defect produced which agent. Installing them and
not spawning them reproduces the original problem.

## Licence

Same as the source repository: MIT.
