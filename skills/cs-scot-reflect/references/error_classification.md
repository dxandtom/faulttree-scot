# 错误分类详细说明

> 本文件定义 6 类错误的检测逻辑、判断条件、处理方式。

## 决策树总览

```
Step 0: 读取输入
        ↓
Step 1: 对账（逐期望根因 vs 实际结果）
        ↓
Step 2: 按0→5顺序查错，命中即停
        ↓
Step 3: 输出分类结果
```

---

## 类0：ENV_FAILURE（执行失败）

**定义**：执行层问题（环境崩溃、脚本崩溃），与模型质量无关。

**Verdict**: `run_invalid`

### 检测条件（满足任一即命中）

1. `run.json` 不存在
2. `execution_status` 中任意步骤的 `ok == false`

### 证据字段

```json
{
  "class": "ENV_FAILURE",
  "verdict": "run_invalid",
  "failed_steps": ["config_analyzer", "diag_analyzer"],
  "error_details": ["script exit with code 1", "..."]
}
```

### 处理方式

- **必须中断**
- 人工修复环境/脚本后重跑 execute
- 不回流 generate

---

## 类1：INPUT_GAP（缺数据）

**定义**：使用方没有提供必要的数据源，导致部分步骤被跳过。

**Verdict**: `needs_data`

### 检测条件

- `missing_inputs` 非空数组

### 证据字段

```json
{
  "class": "INPUT_GAP",
  "verdict": "needs_data",
  "missing_inputs": ["chr", "rb"]
}
```

### 处理方式

- **必须中断**
- 使用方补数据后重跑 execute
- 不回流 generate

---

## 类2：BINDING_GAP（没绑上）

**定义**：静态检查 tree_index 发现节点有判据但没有任何绑定。

**Verdict**: `needs_binding`

### 检测条件

静态查 `tree_index.json`：
1. 节点存在
2. 节点有 `condition`（判据）
3. 节点在所有 analyzer 脚本中都没有任何绑定

### 检测逻辑（伪代码）

```
for each node in tree_index:
    if node.has_condition and not node.has_any_binding:
        → BINDING_GAP(node)
```

### 证据字段

```json
{
  "class": "BINDING_GAP",
  "verdict": "needs_binding",
  "node_id": "n4",
  "node_name": "BFD邻居关系",
  "condition": "BFD会话DOWN",
  "reason": "节点n4有判据但在tree_index里无任何绑定"
}
```

### 处理方式

- **可继续 loop**
- 自动回流 generate 补绑定
- 熔断：同节点同类连改 2 轮不过 → 标记人深查

---

## 类3：KNOWLEDGE_GAP（不认识）

**定义**：`expected.json` 中提到的节点在 `tree_index.json` 中不存在。

**Verdict**: `needs_knowledge`

### 检测条件

1. `expected_root_causes` 中某 `node_id` 在 `tree_index.json` 中不存在

### 证据字段

```json
{
  "class": "KNOWLEDGE_GAP",
  "verdict": "needs_knowledge",
  "node_id": "n999",
  "reason": "tree_index中不存在该节点"
}
```

### 处理方式

- **可继续 loop**
- 回流 tester 增节点 → migrate 更新树
- 熔断：同节点同类连改 2 轮不过 → 标记人深查

---

## 类4：CRITERIA_GAP（判不对）

**定义**：节点的 predicate_satisfied 判断与 expected_facts 不一致。

**Verdict**: `needs_criteria_review`

### 检测条件

- `expected_facts` 中存在该节点
- `rca_input.json` 中该节点的 `predicate_satisfied` ≠ `expected_facts[node_id].predicate_satisfied`

### 证据字段

```json
{
  "class": "CRITERIA_GAP",
  "verdict": "needs_criteria_review",
  "node_id": "n6",
  "expected_predicate": true,
  "actual_predicate": false,
  "reason": "predicate_satisfied与期望不符"
}
```

### 处理方式

- **可继续 loop**
- 人拍板 → 调优表修正
- 熔断：同节点同类连改 2 轮不过 → 标记人深查

---

## 类5：VERDICT_GAP（裁不对）

**定义**：RCA 裁决结论与期望不符，或存在孤立节点。

**Verdict**: `needs_adjudication`

### 检测条件（满足任一即命中）

1. **裁决不符期望**：
   - 期望根因不在 `rca_result` 中（且不在 `probable_causes` 中）
   - 阴性用例却有 `rca_result` 非空（假阳）

2. **孤立节点**：
   - `tree_issues.orphan_node_ids` 非空数组

### 证据字段

```json
{
  "class": "VERDICT_GAP",
  "verdict": "needs_adjudication",
  "expected_node": "n6",
  "expected_in": "rca_result",
  "actual_in": "none",
  "orphan_nodes": [],
  "reason": "裁决结论与期望不符"
}
```

### 处理方式

- **可继续 loop**
- 人裁决 → 调规则
- 熔断：同节点同类连改 2 轮不过 → 标记人深查

---

## 对账结果类型

对账层（Step 1）输出以下结果类型：

| 类型 | 含义 | 用于 |
|------|------|------|
| HIT_CONFIRMED | 期望根因在 rca_result 中 | 阳性用例命中 |
| HIT_DEGRADED | 期望根因只在 probable_causes 中 | 降级命中 |
| MISSED | 期望根因不在任何档 | 漏判 |
| FALSE_CONFIRM | 阴性用例却有 rca_result | 假阳 |

---

## 熔断机制

### 触发条件

同节点同类错误连续 2 轮未修复

### 处理

- 停止自动 loop
- 标记"需人工深查"
- 记录熔断历史

### 记录结构

```json
{
  "fused": true,
  "node_id": "n4",
  "error_class": "BINDING_GAP",
  "fuse_reason": "同节点同类连改2轮不过",
  "rounds": ["round1_id", "round2_id"]
}
```

---

## 处理规则汇总

| 类 | 类型 | 处理方 | 可继续loop | 熔断 |
|----|------|--------|------------|------|
| 0 | ENV_FAILURE | 人工修环境 | 否 | - |
| 1 | INPUT_GAP | 使用方补数据 | 否 | - |
| 2 | BINDING_GAP | generate补绑定 | 是 | 是 |
| 3 | KNOWLEDGE_GAP | tester增节点 | 是 | 是 |
| 4 | CRITERIA_GAP | 人拍板→调优表 | 是 | 是 |
| 5 | VERDICT_GAP | 人裁决→调规则 | 是 | 是 |
