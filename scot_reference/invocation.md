# SCOT Skill Invocation Cheatsheet

`scot-generate` is a one-shot CLI. `scot-execute` owns a short-lived
ScotWorker state machine (ACP JSON-RPC over stdio, optionally wrapped by MCP).
This document is the single source of truth — the SKILL.md files only
summarize and link back here.

## Step 1 — Read the install manifest

`install.sh` writes a manifest at `<skills_home>/.scot-env.json`. **Read it**
instead of guessing paths or activating venvs. For Claude Code the default is
`~/.claude/skills`; for OpenClaw the default is `~/.openclaw/skills`.

```bash
cat <skills_home>/.scot-env.json
# {
#   "platform": "darwin" | "linux" | "windows",
#   "harness": "claude_code" | "openclaw",
#   "harness_name": "Claude Code" | "OpenClaw",
#   "python":  "<venv python absolute path>",
#   "skills_home": "<absolute skills root>",
#   "registry": "<absolute scot_registry.json>",
#   "runners": {
#     "scot_generate": "<.../run_generate.py>",
#     "scot_execute":  "<.../run_execute.py>",
#     "scot_mcp_server": "<.../scot-mcp-server/scripts/server.py>"
#   },
#   "execute_transport": {
#     "primary": "mcp_tools" | "acp",
#     "supported": [...]
#   }
# }
```

## Step 2 — scot-generate (one-shot CLI)

```bash
<manifest.python> <manifest.runners.scot_generate> --input request.json
```

Emits a planning brief + envelope; the host writes the plan JSON. See
`scot-generate/SKILL.md` for the payload contract.

## Step 3 — scot-execute (harness-selected transport)

`scot-execute` has one execution core: a short-lived ScotWorker. Code-like
nodes (`script`, `noop`, `for_each`, and SKILL.md entries with `scripts:`
frontmatter) run locally. Model-like nodes (`llm_text` and prompt-only
`agent_skill`) default to the configured worker API agent. ACP/MCP is the
fallback channel when no API is configured, and the always-external channel for
`ask_user`.

| Harness | Primary transport | Entry |
|---------|-------------------|-------|
| Claude Code | MCP tools | `<manifest.runners.scot_mcp_server>` registered as MCP server |
| OpenClaw | ACP | `<manifest.runners.scot_execute>` |
| ACP-native custom harness | ACP client | drive `<manifest.runners.scot_execute>` directly |

Historical file-reply helpers are archived and are not part of the active
release surface. Use MCP tools for Claude Code and ACP for direct harnesses.

### ScotWorker

`run_execute.py` spawns one short-lived ScotWorker that speaks JSON-RPC 2.0
over stdio. It accepts only `--registry` and `--keep-alive`
— **no `--input` / `--output` / `--plan` flags**, the plan rides in
`session/prompt._meta.scot.scot_plan`.

```bash
<manifest.python> <manifest.runners.scot_execute> [--registry scot_registry.json]
```

Drive it as an ACP client:

1. `initialize` (handshake; reads `agentCapabilities.executionMode` and
   `_meta.scot.clientMethods`)
2. `session/new` → get `sessionId`
3. `session/prompt` with
   `params._meta.scot = {"scot_plan": ..., "runtime_context": ...}`
4. While the prompt runs, respond to pending requests:
   - `_scot/llm` → `{"result": {"text": "...", "tokens_used": N}}`
     only when no worker API is configured
   - `_scot/skill` → `{"result": {"data": {...}}}`
     only when no worker API is configured
   - `_scot/input` → `{"result": {"value": ...}}`
5. The final `session/prompt` response carries
   `result._meta.scot.result` with `status` + `final_answer` /
   `repair_request` / `error`.
6. (optional) `session/close`. The worker exits automatically after
   the prompt response unless launched with `--keep-alive`.

Full protocol contract: `scot-execute/references/acp_protocol.md`.

### Worker API configuration

Configure a lightweight worker model when you want ScotWorker to run
model-like nodes without outer-harness round-trips:

```json
// ~/.config/scot-skills/worker.json
{
  "api_key_file": "~/.config/scot-skills/worker.key",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "skill_model": "gpt-4o-mini",
  "auth_header": "authorization"
}
```

Equivalent env vars: `SCOT_WORKER_API_KEY`, `SCOT_WORKER_BASE_URL`,
`SCOT_WORKER_MODEL`, `SCOT_WORKER_SKILL_MODEL`, `SCOT_WORKER_AUTH_HEADER`,
`SCOT_WORKER_MAX_TOKEN_FIELD`. Set `SCOT_WORKER_CONFIG` to point at a
non-default JSON config file.

Key lookup order: API-key environment variables first, then `api_key_file` /
`SCOT_WORKER_API_KEY_FILE`, then `~/.config/scot-skills/worker.key`, then
`~/.config/scot-skills/xiaomi.key`. Compatibility aliases include
`SCOT_API_KEY`, `XIAOMI_API_KEY`, `MIMO_API_KEY`, `OPENAI_API_KEY`, plus the
matching `*_BASE_URL`, `*_MODEL`, and `*_AUTH_HEADER` variables.

Set `SCOT_WORKER_API=0` to force ACP/MCP fallback for model-like nodes.

### Claude Code MCP tools

When `execute_transport.primary == "mcp_tools"`, use the `scot` MCP server
instead of spawning `run_execute.py` yourself:

- `scot_execute_run_plan`
- `scot_execute_reply`
- `scot_execute_status`
- `scot_execute_cancel`

The MCP server wraps the same ScotWorker internally. With worker API
configured, `scot_execute_run_plan` usually returns the final result directly
unless the plan asks for user input. Without worker API, pending
LLM/skill/input requests are answered with `scot_execute_reply`; MCP sampling
is not required.

### Common platform layouts

| OS            | Python                                                      | Runner                                                    |
|---------------|-------------------------------------------------------------|-----------------------------------------------------------|
| macOS / Linux | `<skills_home>/.scot-venv/bin/python`                    | `<skills_home>/scot-adapters/<profile>/run_*.py`     |
| Windows       | `<skills_home>/.scot-venv/Scripts/python.exe`            | same                                                      |

### Smoke test (no ACP client required)

Feed an `initialize` frame on stdin and check the response line:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | \
  <manifest.python> <manifest.runners.scot_execute>
```

Expected: a single JSON line containing
`"agentInfo":{"name":"scot-worker","version":"4.0"}`.

## Don't

- ❌ Passing the plan as argv or stdin to raw `run_execute.py` — that
  batch-style invocation was removed in v4.0; ScotWorker receives plans
  through `session/prompt._meta.scot`
- ❌ `python ...adapters/claude_code/run_execute.py` with system Python —
  system Python lacks `pydantic` and the other venv dependencies; you'll
  get `ModuleNotFoundError`. Use the manifest's `<manifest.python>`.
- ❌ Activating the venv in the user's shell — manifest paths are absolute,
  no activation needed
