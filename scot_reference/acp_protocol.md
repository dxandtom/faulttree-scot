# scot-execute ACP 协议（v4.0）

scot-execute 通过 ACP（Agent Client Protocol，JSON-RPC 2.0 over stdio）跟外
层 harness 通信。通用 harness 可以直接 spawn
`scot-execute/scripts/acp_agent.py`；adapter 也可以提供自己的
`run_execute.py` shim。该进程是**短生命周期 SCOT worker**，由
`session/prompt` 接收 SCOT plan，跑完后退出。Worker 默认会尝试加载 SCOT
worker API 配置；配置存在时，`llm_text` 和 prompt-only `agent_skill` 在
worker 进程内由 API-backed worker agent 完成。没有配置 API 时，这两类节点
才通过 `_scot/llm` / `_scot/skill` 反过来调外层 ACP 客户端。`ask_user`
始终通过 `_scot/input` 交给外层。

## 进程模型

```
┌──────────────────┐  JSON-RPC over stdio  ┌──────────────────────┐
│  ACP client      │ ◀────────────────────▶ │  scot-execute worker │
│  (harness)       │                        │  (acp_agent.py)      │
└──────────────────┘                        └──────────────────────┘
        │
        ├─ initialize                  → 协议握手
        ├─ session/new                 → 拿到 sessionId
        ├─ session/prompt              → 提交 SCOT plan，进入主循环
        │     ↓
        │   worker emits notifications: session/update (STARTED, STEP_DONE)
        │   worker may emit client requests: _scot/llm / _scot/skill / _scot/input
        │     ↓
        │   client replies → worker continues
        ├─ session/close
        └─ exit
```

- `acp_agent.py` 是通用 SCOT worker 入口，参数 `--registry` 可重复；
  adapter 的 `run_execute.py` 可以作为薄 shim 把同样的参数透传过去。
- `scot-shared/api_native_harness.py` 是随 skill 安装发布的参考 client，
  适合 CI、自研网关、OpenClaw 这类能直接调用模型 API 的 harness 复用。
- Worker 跑完 plan 后**立即 `os._exit(0)`** —— SCOT worker 是短生命周期，
  一次 `session/prompt` 就是一次完整执行；持续服务模式需要 `--keep-alive`
  调试 flag。

## 握手与会话

### `initialize`

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "initialize",
  "params": {"clientInfo": {"name": "<client>"}}
}
```

Worker 响应：

```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "protocolVersion": 1,
    "agentInfo": {"name": "scot-worker", "version": "4.0"},
    "agentCapabilities": {
      "loadSession": false,
      "sessionCapabilities": {"close": true},
      "_meta": {
        "scot": {
          "executePlan": true,
          "workerAgent": "api-when-configured",
          "clientMethods": ["_scot/llm", "_scot/skill", "_scot/input"]
        }
      },
      "executionMode": "api_worker | outer_harness"
    }
  }
}
```

`agentCapabilities.executionMode` 会显示当前 worker 是否已经加载 API worker
agent。`_meta.scot.clientMethods` 是 fallback 契约公告：`outer_harness` 模式下
客户端必须能响应 `_scot/llm`、`_scot/skill`、`_scot/input`；`api_worker`
模式下通常只会收到 `_scot/input`，除非 worker API 被显式关闭或未来策略要求
外层接管。

### `session/new`

```json
{"jsonrpc": "2.0", "id": 2, "method": "session/new",
 "params": {"cwd": "/optional/working/dir"}}
```

Worker 响应：`{"result": {"sessionId": "scot_<12hex>"}}`。

### `session/prompt`（核心）

SCOT plan 通过 `params._meta.scot` 传递（**不**复用 `params.prompt`，那是
人类对话的位置）：

```json
{
  "jsonrpc": "2.0", "id": 7,
  "method": "session/prompt",
  "params": {
    "sessionId": "scot_abc123def456",
    "_meta": {
      "scot": {
        "scot_plan": { "version": "4.0", "plan_id": "p1", "entry": "...", "nodes": [...] },
        "runtime_context": { /* 任意 dict，传给 worker */ },
        "registry": ["/path/to/scot_registry.json"],
        "dry_run": false
      }
    }
  }
}
```

**唯一支持的 key 是 `_meta.scot`**。早期草案中的备用 alias key 和
`params.prompt` JSON fallback 已经在 v4.0 发布前彻底删除 —— 未识别
的输入直接 `-32602` 失败，避免 "natural language 被当 plan 解析"
这种坑。

`session/prompt` 的 `params.prompt` 字段如果存在会被忽略（ACP 约定
那是给人类对话用的位置）。

## Notifications（worker → client）

执行过程中 Worker 通过 ACP 的 `session/update` 把进度推给客户端：

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "scot_abc123def456",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {"type": "text", "text": "[scot] STEP_DONE node_id=n3 status=success"},
      "_meta": {"scot": {"event": {"event": "STEP_DONE", "node_id": "n3", "status": "success", "outputs": {...}}}}
    }
  }
}
```

- `content.text` 是**人类可读的一行摘要**，SCOT-unaware 客户端在 chat
  feed 里看到这一行就够；
- `_meta.scot.event` 是**结构化事件**，SCOT-aware 客户端 pattern-match
  这个字段重建 plan 进度。

事件类型（在 `_meta.scot.event.event`）：

| event | 触发时机 | 关键字段 |
|-------|----------|----------|
| `STARTED`   | worker 启动后第一条 | `plan_id`, `run_id`, `shared_api_version` |
| `STEP_DONE` | 每个节点结束 | `node_id`, `status`, `outputs` |
| `RESULT`    | plan 终态（最终在 `session/prompt` response 里，不是 notification） | `status`, `final_answer`, `working_memory`, `trace`, `repair_request`, `budget_usage`, `error` |
| `ERROR`     | worker 自身崩溃 | `where`, `message`, `traceback` |

`budget_usage` 是**事后记账**（LLM 次数 / token / 节点 / 墙钟），不做
强约束，由客户端自行跟 `plan.meta.budget` 对比。

## 客户端请求（worker → client）

这些是 ACP 扩展方法。没有配置 worker API 时，客户端必须实现 `_scot/llm` 和
`_scot/skill`；无论是否配置 worker API，客户端都应实现 `_scot/input`。

### `_scot/llm`

```json
{
  "jsonrpc": "2.0", "id": 11,
  "method": "_scot/llm",
  "params": {
    "sessionId": "scot_abc123def456",
    "node_id": "ask_n1",
    "node_name": "summarize",
    "system": "<system prompt>",
    "user": "<user prompt>",
    "model_hint": "fast",
    "expected_format": "text",
    "outputs_spec": {"answer": "$memory.answer"},
    "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}}}
  }
}
```

客户端响应：`{"result": {"text": "<completion>", "tokens_used": 320}}`。
`tokens_used` 可选；非法值（None / 字符串 / 负数）会被 `safe_nonneg_int`
归零，不阻断运行。

`outputs_spec` / `response_schema` 是给 harness 的输出契约提示，也是 worker
本地校验/局部重试的依据。多 key `outputs_spec` 要求模型返回可解析 JSON 对象
并覆盖声明 key；不满足时该 `llm_text` 节点会按 `policy.max_retry` 局部重问，
仍不满足则以 `error_kind=schema` 失败，进入 reflection / repair。

失败：`{"result": {"status": "failed", "error": "...", "error_kind": "..."}}`
也接受；触发 `BridgeReplyError` → 节点失败 → reflection 处理。

### `_scot/skill`

```json
{
  "method": "_scot/skill",
  "params": {
    "sessionId": "...",
    "node_id": "...",
    "node_name": "...",
    "skill_name": "<catalog skill name>",
    "purpose": "<short description>",
    "inputs": {...},
    "expected_keys": ["..."],
    "skill_md_path": "<absolute path or null>",
    "input_schema": {...},
    "output_schema": {...},
    "expected_format": "json"
  }
}
```

客户端响应 `{"result": {"data": {...}}}`；`data` 必须是 dict 且包含
`expected_keys` —— worker 用 `validate_skill_response_data` 严格校验，
shape 不对就当 `error_kind=schema` 失败。

### `_scot/input`

```json
{
  "method": "_scot/input",
  "params": {
    "sessionId": "...",
    "node_id": "...",
    "node_name": "...",
    "prompt": "<ask the human>",
    "outputs_spec": {"answer": "string"}
  }
}
```

客户端响应 `{"result": {"value": {...}}}` 或 `{"result": {"value": "<text>"}}`。

## 取消（`$/cancelRequest`）

JSON-RPC 2.0 标准通知，参数 `{"id": <session/prompt 的 id>}`：

```json
{"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 7}}
```

也接受 `$/cancel` 别名（部分客户端用这个）。`id` 类型 `int | string`
都支持 —— 真实 ACP 客户端常用 uuid-string id。

### 取消语义（必读）

**Cancel = "停止节点推进 + 尽量回滚"**，**不是**"立即 die"：

1. **跳过 reflection**：不 retry、不 fallback、不升级 NEEDS_REPAIR；
2. **仍跑 compensation**：Saga 是"取消 + 尽量回滚"，已记账的
   `policy.compensation_node` 按 LIFO 反向跑一遍，best-effort
   回滚副作用。补偿失败只记 trace、不阻断退出；
3. 然后 worker 返回 `RESULT.status=FAILED` + `error_kind=canceled`，
   `session/prompt` 的 response 正常返回（不是 JSON-RPC error）；
4. 如果运行在并发 fan-out 里，整个 fan-out（含其它分支）也会被
   取消并跑各自的 compensation 后再退出。
5. 想"真正零副作用立即 die"的场景，由 harness SIGTERM 子进程。

### 取消生效边界

取消信号**在外部请求边界生效** —— worker 必须运行到下一次
`bridge.request_llm/skill/input(...)` 调用才会观察到 cancel 标志，随后
立即走 compensation + FAILED。**纯本地节点链**（连续 `script` / `noop`
/ 内建工具）期间取消**不会预占**：worker 跑完当前本地节点才检查。两个
应对方式：(a) 给可能耗时的节点设 `policy.timeout_ms`，超时自动转
transient 失败；(b) 真的需要强制立即停止时，harness SIGTERM agent 子
进程（worker 端 daemon 线程会跟着死）。

## 错误传递

- 客户端给 worker 回 `{"result": {"status": "failed", "error": "...",
  "error_kind": "..."}}` → worker 把对应节点标 fail，走 reflection 流程
  （retry / fallback / needs_repair / cancel）。
- 客户端返 JSON-RPC error（`{"error": {"code": ..., "message": "..."}}`）
  → worker 把它当 `error_kind=harness` 失败。
- Worker 内部异常 → 在 `session/prompt` response 里返
  `RESULT.status=FAILED`；致命崩溃才走 JSON-RPC error。
- Worker 跑出 `NEEDS_REPAIR` → `RESULT` 携带 `repair_request`。
- Worker 跑出 `SUCCESS` → `RESULT` 携带 `final_answer`。

`session/prompt` 的最终响应**始终是 JSON-RPC result**（除非真崩溃），
ExecResult 在 `result._meta.scot.result`。

## 字段保留

客户端 reply **不能**自带这些 worker-owned 字段（会被忽略 / 覆盖）：

- `type` —— ACP 用 JSON-RPC 标准 result，没有自定义 type 标签；
- `reply_to` —— 路由用 JSON-RPC `id`，不要再带；
- `status` —— 顶层 `status` 由 worker 决定，要表达失败用上面的失败 shape。

## 参考实现

- Worker 端：`scot-execute/scripts/acp_agent.py::ScotAcpAgent`
- AcpBridge：`scot-execute/scripts/acp_agent.py::AcpBridge`
- 高层 runner：`scot-execute/scripts/runner.py::scot_execute`
- Worker 内部 bridge 接口：`scot-execute/scripts/worker_bridge.py`
- 测试：`tests/test_acp_execute_agent.py`
