# SCOT Plan JSON Schema (v4.0)

本文件与 `scot-execute/references/plan_schema.md` 保持镜像，让两个 skill
能各自自包含。两份文件必须遵循同一份 schema（`tests/test_docs_mirror.py`
校验字节对齐）。

v4.0 plan format 要点：
- **dispatch kind**：`script`（本地 runner）/ `agent_skill`（prompt-only
  SKILL.md，默认由 worker API agent 执行，未配置 API 时走 `_scot/skill`）/
  `llm_text`（默认由 worker API agent 调模型，未配置 API 时走 `_scot/llm`）/
  `ask_user`（ACP/MCP `_scot/input`）/ `noop` / `for_each`。
  老 `skill` / `tool` / `prompt_skill` aliases 在 parse 阶段就拒绝。
- **并发**：`transition.parallel_after` + `join_at` fan-out；
  `policy.parallel_fail` 决定失败语义。
- **失败处理**：`policy.idempotent`（自动重试 gate）+
  `policy.compensation_node`（Saga 补偿）。
- **运行时**：SCOT worker 一次跑到底。LLM 和 prompt-only skill 节点默认由
  worker API agent 处理；没有配置 worker API 时才走 ACP/MCP `_scot/*`
  fallback。没有跨进程续跑。

---

## 1. 顶层结构

```json
{
  "version": "4.0",
  "plan_id": "plan_<uuid>",
  "meta": {
    "goal": "用户目标的一句话描述",
    "success_criteria": ["完成告警查询", "输出诊断结论"],
    "created_by": "scot-generate",
    "created_at": "ISO8601",
    "budget": {
      "max_llm_calls": 20,
      "max_total_tokens": 50000,
      "max_wall_time_ms": 120000,
      "max_plan_steps": 20
    }
  },
  "entry": "n1",
  "nodes": [],
  "memory_init": {
    "user_request": "$input.user_request"
  }
}
```

`script` / `agent_skill` / `for_each.template` 节点的 `outputs` 左侧是
实际返回 JSON 的顶层字段名，右侧才是 `$memory` / `$output` 写入位置。
若返回 `{"data": ...}`，应写 `{"data": "$memory.metrics_data"}`。

| 字段 | 必填 | 用途 |
|-----|------|------|
| `version` | 是 | 当前 schema 版本 `"4.0"`（同主版本 `4.x` 都能加载；字段必填，缺失拒绝） |
| `plan_id` | 否 | 不传时自动生成 |
| `meta` | 是 | 人类可读目标 + 成功判据 + 预算 |
| `entry` | 是 | 起始节点 id（必须在 `nodes` 中存在） |
| `nodes` | 是 | NodeSpec 数组 |
| `memory_init` | 否 | 工作记忆初始值；支持引用表达式 |

---

## 2. NodeSpec

```json
{
  "id": "string —— 唯一标识",
  "kind": "script | agent_skill | llm_text | ask_user | noop | for_each",
  "name": "string",
  "purpose": "string —— 给人看的描述",
  "inputs": { "param_name": "$memory.x 或字面量" },
  "outputs": { "result_field": "$memory.target" },
  "transition": {
    "next_on_success": "string | null",
    "next_on_fail":    "string | null",
    "conditions": [{"expr": "...", "goto": "node_id"}],
    "parallel_after": ["branch1_id", "branch2_id"],   // v4.0+
    "join_at": "join_node_id",                          // v4.0+, 配合 parallel_after
    "end": false
  },
  "policy": {
    "timeout_ms": 5000, "max_retry": 1, "on_fail": "abort",
    "parallel_fail": "fail_fast",                       // v4.0, 或 "best_effort"
    "idempotent": false,                                // v4.0, 默认 false
    "compensation_node": null                            // v4.0, Saga 补偿
  },
  "for_each": {"items": "$memory.host_list", "as": "host"},  // 仅 kind=for_each
  "template": {"kind": "script", "name": "..."},             // 仅 kind=for_each
  "prompt_template": "Jinja2，llm_text / ask_user 使用；支持 {{ $memory.x }} 等 $ref 直引用",
  "model_hint": "fast | deep",
  "response_schema": {"type": "object", "properties": {"summary": {"type": "string"}}} // 可选，llm_text 输出契约
}
```

`kind` 是**唯一**的 dispatch 字段。routine_view 标签是收敛的：
`script` / `for_each` → `#tool`；`agent_skill` → `#skill`；
`llm_text` / `noop` → `#context`；`ask_user` → `#user`。每种 kind 的
详细示例见 `node_kinds.md`。

### parallel_after / join_at

声明一组并发分支：当前节点是 barrier（不真正派发），spawn 每个 branch
并发跑，等所有分支沿 `next_on_success` 走到 `join_at` 后再继续。约束：

- `parallel_after` 不能和 `next_on_success` 在同节点共存；
- 每个分支必须**唯一汇合**到 `join_at`；
- 分支之间写入的 `$memory.<key>` 必须互斥（没有合并策略）；
- 分支内不能再嵌套 `parallel_after`（v4.0 再考虑）。

`policy.parallel_fail`：`fail_fast`（任一分支失败立即取消其他）或
`best_effort`（等所有跑完再决定），失败统一进 `NEEDS_REPAIR`。

---

## 3. 引用表达式的命名空间

| 前缀 | 含义 | 读 | 写 |
|------|------|----|-----|
| `$input` | 执行启动时传入的参数 | 可 | 否 |
| `$memory` | 工作记忆存储 | 可 | 可 |
| `$output` | 最终输出缓冲 | 否 | 可 |
| `$step.<id>` | 前序某节点的输出数据 | 可 | 否 |

路径语法：`$memory.alarms[0].severity`。路径或索引不存在 → `null`。

---

## 4. 条件表达式文法

```
condition := ref_expr comparator value
           | ref_expr "exists"
           | ref_expr "empty"
           | "not" condition
           | condition "and" condition
           | condition "or" condition
           | "(" condition ")"

comparator := "==" | "!=" | ">" | ">=" | "<" | "<=" | "in" | "contains"
```

条件在 `next_on_success` **之前**求值 —— 第一个匹配的条件胜出。
