# 执行结果结构定义

> 本文件定义 cs-scot-execute 产出的执行结果文件结构，是评估反射技能的输入契约。

## 执行产物目录结构

```
<result-dir>/                           # 例如：output/ldp-lsp-result/result-20260810T034800Z-9a2c55/
├── run.json                           # 【必须】运行状态与元数据
├── rca_input.json                     # 【必须】RCA输入（逐条件事实）
├── rca.json                           # 【必须】RCA最终结论
└── _compression.txt                   # （可选）压缩统计
```

---

## run.json

**用途**：记录执行状态，包括是否成功、缺哪些数据、哪些步骤失败了。

### 必须字段

```json
{
  "run_id": "run-20260810T034800Z-9a2c55",  // 【必须】运行唯一标识
  "created_at": "2026-08-10T03:48:00Z",    // 【必须】创建时间（ISO8601）
  "execution_status": {                     // 【必须】各步骤执行状态
    "config_analyzer": { "ok": true, "exitcode": 0, "error": null },
    "diag_analyzer":   { "ok": true, "exitcode": 0, "error": null },
    "log_analyzer":    { "ok": true, "exitcode": 0, "error": null },
    "rca":             { "ok": true, "exitcode": 0, "error": null }
  },
  "missing_inputs": []                    // 缺的数据源列表
}
```

### execution_status 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| ok | boolean | 步骤是否成功执行 |
| exitcode | integer | 脚本退出码（ok=true时为0） |
| error | string \| null | 错误信息（ok=false时才有值） |
| skipped | string | 跳过原因（被跳过时才有此字段） |

### missing_inputs 字段说明

- 类型：`string[]`
- 说明：本次运行缺失的数据源key列表
- 示例：`["chr", "rb"]`

---

## rca_input.json

**用途**：RCA步骤的输入，包含逐条件的机械判断结果（事实表）。

### 结构

```json
{
  "fault_tree_md": "...",               // 故障树markdown全文
  "indicator_results": {                 // 各指标解析结果
    "kpi": {
      "source": "kpi",
      "summary": { "total": 100, "ok": 95 },
      "conditions": [
        {
          "node_id": "n6",
          "predicate_satisfied": true,
          "matched": 80,
          "total": 100,
          "evidence": [...]
        }
      ]
    }
  },
  "missing_indicators": {                // （可选）缺失的指标
    "chr": "数据源 'chr' 未通过 --data 提供"
  }
}
```

### conditions[] 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| node_id | string | 节点ID，必须与tree_index一致 |
| predicate_satisfied | boolean | 条件判断结果 |
| matched | integer | 匹配次数 |
| total | integer | 总检查次数 |
| evidence | array | 证据列表 |

---

## rca.json

**用途**：RCA最终结论，四档裁决。

### 结构

```json
{
  "rca_result": [                         // 【必须】确证根因
    {
      "Root_cause": "接口DOWN",
      "Fault_tree_path": ["n1", "n3", "n6"],
      "support": "高",
      "evidence_path": [
        {
          "node": "接口DOWN",
          "node_id": "n6",
          "condition": "LogContains(OperStatus=DOWN)",
          "actual": "matched=80/total=100",
          "how": "日志中80处出现OperStatus=DOWN",
          "judgment": "成立"
        }
      ]
    }
  ],
  "probable_causes": [                    // 疑似根因
    {
      "Root_cause": "...",
      "Fault_tree_path": ["..."],
      "confidence": "high|medium|low",
      "blocked_at": "缺失话统数据",
      "caveats": "...",
      "support": "中",
      "evidence_path": [...]
    }
  ],
  "undetermined": [                       // 待确认
    {
      "Fault_tree_path": ["..."],
      "blocked_at": "缺失配置数据",
      "reason": "缺哪个量",
      "observed": ["..."]
    }
  ],
  "tree_issues": {                        // 树结构问题
    "uncovered_condition_nodes": ["n5"],
    "orphan_node_ids": []
  }
}
```

### rca_result vs probable_causes 区别

| 字段 | 含义 | 特征 |
|------|------|------|
| rca_result | 确证根因 | 有完整证据链，可直接用于评估对账 |
| probable_causes | 疑似根因 | 证据链不完整（blocked_at），置信度medium/low |

### tree_issues 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| uncovered_condition_nodes | string[] | 有判据但无事实的节点（可能数据不足） |
| orphan_node_ids | string[] | RCA产出了事实但树索引中没有的节点（绑定问题） |

---

## 与评估的关联

### 用于 ENV_FAILURE（类0）检测
- 检查 `execution_status` 中是否有 `ok: false`
- 或 `run.json` 不存在

### 用于 INPUT_GAP（类1）检测
- 检查 `missing_inputs` 是否非空

### 用于 CRITERIA_GAP（类4）检测
- 比对 `rca_input.json` 中各节点的 `predicate_satisfied` 与 `expected_facts`

### 用于 VERDICT_GAP（类5）检测
- 比对 `rca.json` 中的裁决结果与 `expected_root_causes`
- 检查 `tree_issues.orphan_node_ids` 是否非空
