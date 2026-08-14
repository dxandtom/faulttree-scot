# feedback_to_generate.json 结构定义

> 本文件定义评估后反馈给 generate 的数据结构，是评估反射技能的输出契约。

## 用途

`feedback_to_generate.json` 是 reflect 阶段输出给 generate 阶段的反馈文件，用于指导 generate 下一轮改进。

## 目录形态

```
<output-dir>/
└── <case_id>-<时间戳>/
    ├── evaluation_report.json    # 完整评估报告
    ├── failure_signatures.json   # 失败签名列表
    ├── feedback_to_generate.json # 反馈给generate
    └── eval_run.json             # 本次评估元数据
```

---

## feedback_to_generate.json 完整结构

```json
{
  "case_id": "ldp-lsp-001",
  "evaluation_time": "2026-08-10T00:00:00Z",
  "scenario": "LDP LSP未建立",
  "verdict": "FAIL",
  "next_action": "REGENERATE",
  "issues": [
    {
      "error_category": "BINDING_GAP",
      "error_class": 2,
      "node_id": "n4",
      "node_name": "BFD邻居关系",
      "expected": "confirmed",
      "actual": "undetermined",
      "reason": "节点n4有判据但在tree_index里无任何绑定",
      "suggestion": "在diag_analyzer.py中为n4添加binding配置",
      "evidence": [
        "tree_index.json#n4",
        "expected.json#expected_root_causes[1]"
      ]
    }
  ],
  "reconciliation_summary": {
    "total_expected": 2,
    "hit_confirmed": 1,
    "hit_degraded": 0,
    "missed": 1,
    "false_confirm": 0
  },
  "fused_nodes": []
}
```

---

## 字段说明

### 顶层字段

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| case_id | string | 是 | 用例唯一标识 |
| evaluation_time | string | 是 | 评估时间（ISO8601） |
| scenario | string | 是 | 场景名称 |
| verdict | string | 是 | 评估结果：PASS / FAIL |
| next_action | string | 是 | 下一步动作（见下） |
| issues | array | 是 | 问题列表（空数组表示 PASS） |
| reconciliation_summary | object | 是 | 对账汇总 |
| fused_nodes | array | 是 | 熔断节点列表 |

### next_action 取值

| 值 | 含义 | 条件 |
|----|------|------|
| REGENERATE | 回流 generate 改进 | 有类2/3/4/5错误 |
| RETRY_EXECUTE | 重跑 execute | 有类0/1错误（已人工修复） |
| STOP | 停止，人工介入 | 熔断触发或有类0/1错误 |

### issues[] 字段

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| error_category | string | 是 | 错误类别名称（如 BINDING_GAP） |
| error_class | integer | 是 | 错误类别编号（0-5） |
| node_id | string | 是 | 问题节点ID |
| node_name | string | 否 | 节点名称（可读性） |
| expected | string | 是 | 期望状态 |
| actual | string | 是 | 实际状态 |
| reason | string | 是 | 问题原因简述 |
| suggestion | string | 是 | 修复建议 |
| evidence | array | 是 | 证据文件列表 |

### reconciliation_summary 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| total_expected | integer | 期望根因总数 |
| hit_confirmed | integer | 确证命中数 |
| hit_degraded | integer | 降级命中数（probable_causes） |
| missed | integer | 漏判数 |
| false_confirm | integer | 假阳数 |

### fused_nodes[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| node_id | string | 熔断节点ID |
| error_class | string | 熔断的错误类别 |
| fuse_reason | string | 熔断原因 |
| rounds | array | 连续失败的轮次ID列表 |

---

## 使用方式

1. **读取**：`generate` 技能读取 `feedback_to_generate.json` 获取改进方向
2. **历史**：评估输出目录保留历史记录，可追溯改进过程
3. **自动化**：支持自动化脚本读取并触发下一轮 generate

---

## 示例场景

### 场景1：BINDING_GAP 反馈

```json
{
  "case_id": "ldp-lsp-001",
  "verdict": "FAIL",
  "next_action": "REGENERATE",
  "issues": [
    {
      "error_category": "BINDING_GAP",
      "error_class": 2,
      "node_id": "n4",
      "expected": "confirmed",
      "actual": "undetermined",
      "reason": "节点n4有判据但在tree_index里无任何绑定",
      "suggestion": "在diag_analyzer.py中为n4添加binding配置",
      "evidence": ["tree_index.json#n4", "expected.json#expected_root_causes[1]"]
    }
  ]
}
```

### 场景2：多问题反馈

```json
{
  "case_id": "ldp-lsp-001",
  "verdict": "FAIL",
  "next_action": "REGENERATE",
  "issues": [
    {
      "error_category": "BINDING_GAP",
      "error_class": 2,
      "node_id": "n4",
      "expected": "confirmed",
      "actual": "undetermined",
      "reason": "节点n4有判据但无绑定",
      "suggestion": "检查diag_analyzer.py中n4的binding配置",
      "evidence": []
    },
    {
      "error_category": "CRITERIA_GAP",
      "error_class": 4,
      "node_id": "n6",
      "expected": "predicate_satisfied=true",
      "actual": "predicate_satisfied=false",
      "reason": "条件判断与期望不符",
      "suggestion": "检查n6的binding列/阈值配置",
      "evidence": []
    }
  ]
}
```

### 场景3：PASS（无问题）

```json
{
  "case_id": "ldp-lsp-001",
  "verdict": "PASS",
  "next_action": null,
  "issues": [],
  "reconciliation_summary": {
    "total_expected": 1,
    "hit_confirmed": 1,
    "hit_degraded": 0,
    "missed": 0,
    "false_confirm": 0
  },
  "fused_nodes": []
}
```
