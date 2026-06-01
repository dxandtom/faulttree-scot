# Node Kinds —— v4.0 完整参考

每个 plan 节点都带一个 `kind` 字段。worker 用 `kind` 来 dispatch。
v4.0 合法 kind 共 6 个，对应 4 个 routine_view 标签：

| `kind` | dispatch | routine_view |
|--------|---------|--------------|
| `script` | 走本地 runner（command / python / builtin），in-process | `#tool` |
| `agent_skill` | prompt-only SKILL.md；默认由 worker API agent 执行，未配置 API 时 fallback 到 `_scot/skill` | `#skill` |
| `for_each` | 顺序对一份 list 跑同一个 template 节点 | `#tool` |
| `llm_text` | 默认由 worker API agent 调模型，未配置 API 时 fallback 到 `_scot/llm` | `#context` |
| `ask_user` | 发 `_scot/input` ACP 请求让 harness 问用户 | `#user` |
| `noop` | 占位 / 屏障 / join 点 | （不渲染） |

老 `kind=skill` / `kind=tool` / `kind=prompt_skill` 在 v4.0 已彻底删除，
plan_schema 在 parse 阶段直接拒绝。

---

## `script` —— 本地 runner / `#tool`

```json
{
  "id": "n1",
  "kind": "script",
  "name": "alarm-query",
  "purpose": "查询目标设备的当前告警",
  "inputs": { "device_id": "$memory.device_id" },
  "outputs": { "alarms": "$memory.alarms" },
  "transition": { "next_on_success": "n2", "next_on_fail": "n_fail" },
  "policy": { "timeout_ms": 5000, "max_retry": 1, "idempotent": true }
}
```

执行：`result = await invoker.invoke("alarm-query", {"device_id": ...})`。
catalog entry 必须有本地 runner（command / python / builtin）。如果 invoker
返回 `waiting_skill`（即 catalog 其实是 prompt-only），worker 报 schema error
并要求 planner 改成 `kind=agent_skill`。

routine_view 中 `script` 展示为 `#tool`，对人类来说就是"调用一个工具/脚本"。

---

## `agent_skill` —— prompt-only SKILL.md / `#skill`

```json
{
  "id": "n3",
  "kind": "agent_skill",
  "name": "summarize-runbook",
  "purpose": "用 SKILL.md 描述的方法总结 runbook",
  "inputs": { "runbook": "$memory.runbook_text" },
  "outputs": { "summary": "$memory.summary" },
  "transition": { "next_on_success": "n4" },
  "model_hint": "deep"
}
```

执行：worker 默认调用配置好的 SCOT worker API agent，附 `skill_md_path` /
`input_schema` / `output_schema` 等元数据，让轻量模型按 SKILL.md 指令执行。
如果没有配置 worker API，则 fallback 为 `_scot/skill` 请求，外层 harness
执行 prompt-only skill 后把结构化 JSON 作为 response 返回，worker 写入 outputs。

catalog entry 必须是 prompt-only（没有本地 runner）。如果 invoker 报这其实
是本地可跑的，worker 报 schema error 并要求 planner 改成 `kind=script`。

routine_view 中 `agent_skill` 展示为 `#skill`。

---

## `for_each` —— 顺序迭代 / `#tool`

```json
{
  "id": "n_query_all_pods",
  "kind": "for_each",
  "purpose": "对每个 pod 查 TPS 指标",
  "for_each": { "items": "$memory.pod_list", "as": "pod" },
  "template": {
    "kind": "script", "name": "query-pod-metrics",
    "inputs": { "pod_name": "$input.pod", "pod_index": "$input.pod_index" }
  },
  "outputs": { "iterations": "$memory.all_pod_metrics" },
  "transition": { "next_on_success": "n_summarize" }
}
```

执行：worker 解析 `for_each.items`（必须 ref 到 list），逐项把 item 绑定
进 `$input.<as_var>` 和 `$input.<as_var>_index`，按 template dispatch。
聚合输出：`{iterations: [...], count: N, failed_indices: [...]}`。

v4.0 顺序执行。template 不能嵌套 `kind=for_each`。

routine_view 中 `for_each` 同样展示为 `#tool`。

---

## `llm_text` —— 纯 LLM 推理 / `#context`

```json
{
  "id": "n4",
  "kind": "llm_text",
  "name": "generate-summary",
  "purpose": "生成最终诊断摘要",
  "inputs": { "analysis": "$memory.root_cause" },
  "outputs": { "summary": "$output.final_answer" },
  "transition": { "end": true },
  "prompt_template": "Based on the following analysis:\n{{ analysis }}\n\nWrite a concise diagnostic summary.",
  "model_hint": "fast",
  "response_schema": {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"]
  }
}
```

执行：worker 用 Jinja2 渲染 `prompt_template`，默认交给配置好的 SCOT worker
API agent 调模型，结果写入 outputs。`outputs` / `response_schema` 会作为输出
契约传给 worker API agent；若没有配置 worker API，则 fallback 为 `_scot/llm`
请求交给外层 harness。若回复缺少声明 key，worker 会按 `policy.max_retry`
在本节点局部重试，仍失败则以 schema 错误进入 repair。

**模板里支持 `$ref` 语法** —— 跟 `inputs` 完全一样的写法：

```jinja
{{ $memory.user_request }}
{{ $step.collect.records[0].value }}
{{ $input.alert_time }}
{{ $output.final_answer }}
```

不需要在 `inputs` 里把每个引用单独声明一遍。Jinja2 的 `{{ ... }}`
块外面如果出现裸 `$memory.X` 字符串（例如 routine_view 摘要里），
不会被替换 —— 只有 `{{ ... }}` 块内的 ref 会被解析。

**LLM 返结构化 JSON 时自动解析** —— 模型返 `{"has_drop": true,
"trend": "down"}` 这种 JSON 字面量字符串时，worker 自动 `json.loads`
后写入 outputs key，下游的 `$memory.X.has_drop == true` 之类条件可以
直接访问嵌套字段；解析失败回退到原字符串。`data["text"]` 始终保留原
字符串供 final_answer 用。**故意不解析**裸标量（`"42"`、`"true"`、
`'"some string"'`）—— 那些没有可索引字段，按原文本存储更直观。

所有需要模型分析、分类、总结、解释、输出文案的步骤都应该使用这个 kind，
**不要**用 `agent_skill` —— 后者是"用一份 SKILL.md 描述的工作流"，对应
catalog 里的某个具名能力，不是任意的 LLM 提示。

---

## `ask_user` —— 询问用户 / `#user`

```json
{
  "id": "n_ask",
  "kind": "ask_user",
  "name": "get-device-id",
  "purpose": "请用户提供设备 ID",
  "inputs": {},
  "outputs": { "device_id": "$memory.device_id" },
  "transition": { "next_on_success": "n2" },
  "prompt_template": "请提供需要诊断的设备 ID。"
}
```

执行：worker 发 `_scot/input` 请求，harness 收集用户答案后返回
`{"value": ...}`。答案通过节点的 `outputs` 映射写入 `$memory`；多字段输入支持结构化 dict 或
`服务名称:CCE 压测服务;资源标识:...` 这种带标签的单段文本。

`prompt_template` 同样支持 `$ref` 直接引用（`{{ $memory.X }}` /
`{{ $input.Y }}` …），跟 `llm_text` 一样。

`ask_user.outputs` 必须写 `$memory.<key>`，不要写 `$output.<key>` —— 否则
下游 `$input.<key>` 读不到。

---

## `noop` —— 占位 / 屏障 / 汇合点

```json
{
  "id": "n_join",
  "kind": "noop",
  "name": "join-point",
  "purpose": "等待并行分支汇合",
  "inputs": {},
  "outputs": {},
  "transition": { "next_on_success": "n5" }
}
```

执行：直通。`routine_renderer` 默认不渲染 `noop` 节点 —— 它只是结构性，
对人类用户没有可读价值。

`noop` 也是 `parallel_after` fan-out 节点和 `join_at` 汇合点的常用 kind。
