# 执行结果结构定义

> 本文件定义 cs-scot-execute 产出的执行结果文件结构，是评估反思技能的输入格式约定。

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

### 字段

```json
{
  "run_id": "run-20260810T034800Z-9a2c55",  // 【必须】运行唯一标识
  "created_at": "2026-08-10T03:48:00Z",    // 【必须】创建时间（ISO8601）
  "plan_id": "plan_ldp-lsp",               // 【必须】计划标识，即 plan.json 的 plan_id
  "product_dir": "scot技能/ldp-lsp/assets",  // 【必须】本次运行的产物目录
  "inputs": {                              // 【必须】各输入数据的指纹（路径、大小、sha1）；批量判卷按它把用例对到结果
    "config": { "...": "..." }
  },
  "execution_status": {                     // 【必须】各步骤执行状态，每步跑完立即写入
    "config_analyzer": { "ok": true, "exitcode": 0, "error": null },
    "diag_analyzer":   { "ok": true, "exitcode": 0, "error": null },
    "log_analyzer":    { "ok": true, "exitcode": 0, "error": null },
    "rca":             { "ok": true, "exitcode": 0, "error": null }
  },
  "missing_inputs": ["chr"],               // 只在有缺口时出现：缺的数据源列表
  "missing_kind": { "chr": "not_provided" } // 只在有缺口时出现：缺口性质，见下
}
```

### execution_status 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| ok | boolean | 步骤是否成功执行 |
| exitcode | integer | 脚本退出码（ok=true时为0） |
| error | string \| null | 错误信息（ok=false时才有值） |
| skipped | string | 跳过原因（被跳过时才有此字段） |

### missing_inputs 与 missing_kind 字段说明

- `missing_inputs`：`string[]`，本次运行缺失的数据源 key 列表，示例 `["chr", "rb"]`；没有缺口时整个字段不出现。
- `missing_kind`：`{源名: 性质}`，与 `missing_inputs` 同时出现。`collection_absent` 表示带 `--data raw=` 运行时该类数据在采集里本来就不存在，不是漏传，评估归因据此不判 INPUT_GAP；`not_provided` 表示没有提供。

---

## rca_input.json

**用途**：RCA步骤的输入，包含逐条件的机械判断结果（事实表）。

**有两种形态，评估读取端两种都要认**：

### 形态 A：局部子树形态（真实场景的常态）

树大时执行链会做局部子树压缩，事实挂在剪枝节点上：

```json
{
  "fault_tree": {
    "scenario": "…",
    "root": "n1",
    "outline": { "n1": "…", "n2": "…" },   // 全树 id→粗状态
    "pruned_nodes": {                        // node_id 是字典的 key
      "n6": {
        "name": "接口DOWN", "parent": "n3", "is_leaf": true,
        "facts": [
          // fact 条目两种写法都存在：
          { "id": "…", "indicator": "log",              // 瘦身后的扁平结构（字段在顶层）
            "status": "ok", "predicate_satisfied": true, "matched": 80, "total": 100 },
          { "id": "…", "indicator": "log",
            "fact": { "status": "ok", "predicate_satisfied": true } }   // 未瘦身的嵌套结构
        ]
      }
    }
  },
  "missing_indicators": { "chr": "数据源 'chr' 未通过 --data 提供" }
}
```

节点 predicate 判定：该节点任一 fact 的 `predicate_satisfied == true` 即成立（多事实取"或"）。

### 形态 B：完整形态（没有 tree_index，或树很小、剪枝不划算时才出现）

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

### conditions[] 字段说明（形态 B）

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
      "Fault_tree_path": ["MPLS问题", "LDP会话down", "接口DOWN"],  // 注意：是节点【名字】不是 id
      "node_id": "n6",                    // 候选自身的 node_id（对账用这个）
      "support": "高",
      "evidence_path": [                  // 根→叶逐节点，最后一个=候选自身；每个都带 node_id
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

### 用于 BINDING_GAP（类2）检测
- 检查 `rca.json` 的 `tree_issues.orphan_node_ids` 是否非空（孤儿绑定）
- 漏判的期望根因自身有判据却没有任何绑定（静态查 tree_index 与各分析脚本）

### 用于 CRITERIA_GAP（类4）检测
- 比对 `rca_input.json` 中各节点的 `predicate_satisfied` 与 `expected_facts`

### 用于 VERDICT_GAP（类5）检测
- 比对 `rca.json` 中的裁决结果与 `expected_root_causes`（顶层 `node_id` 是候选自身的编号，对账用它）
