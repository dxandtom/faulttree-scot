# 错误分类详细说明

> 本文件定义 6 类错误的检测逻辑、判断条件、处理方式。

## 决策树总览

```
Step 0: 读取输入
        ↓
Step 1: 对账（逐期望根因 vs 实际结果）
        ↓
Step 2: 按0→5顺序查错，命中即停（仅在有 MISSED 或 FALSE_CONFIRM 时触发；全命中不查错）
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

**定义**：使用方没有提供必要的数据源，导致部分步骤被跳过，**且因此有期望根因漏判**。

**Verdict**: `needs_data`

### 检测条件（两条同时满足）

- `missing_inputs` 非空数组
- 有期望根因 MISSED（缺数据但全命中不算错——verdict 层按 PASS_WITH_GAPS 处理；
  真实跑批缺一两个数据源是常态，只查非空会把一切用例在类1短路）

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

## 类2：BINDING_GAP（绑定层毛病）

**定义**：绑定层的一切不一致——孤儿绑定（产出事实但树上无此节点），或节点有判据却没有
任何绑定**且该节点与本用例的漏判相关**。

**Verdict**: `needs_binding`

### 检测条件（两条任一命中）

**条件A·孤儿绑定**：`rca.json` 的 `tree_issues.orphan_node_ids` 非空——执行产出了某节点的
事实但树上没有这个节点（典型：删节点没清绑定）。绑定/树不一致，修法=清残留绑定。

**条件B·缺绑定**，静态查 `tree_index.json`：
1. 节点存在
2. 节点有 `condition`（判据）
3. 节点在所有 analyzer 脚本中都没有任何绑定
4. **相关性**：该节点是某个漏判（MISSED）期望根因**自身**，或位于其**祖先链**上

> 为什么要第 4 条：执行链有一条合规调优通道——中间节点判断方法不成熟时，人工清空该节点
> 绑定，RCA 按"默认放行、报告注明此处未做判断"处理。全树扫到未绑定就报会把这条通道
> 当错误无限追杀。与漏判无关的未绑定节点只进报告的信息栏（checks.binding_gap.detail），
> 不定级、不进 feedback。

### 检测逻辑（伪代码）

```
for each missed_expected_node:
    for node in {missed_expected_node} ∪ ancestors(missed_expected_node):
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
- 熔断：同节点同类连改 3 轮不过 → 标记人深查

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
- 熔断：同节点同类连改 3 轮不过 → 标记人深查

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
- 熔断：同节点同类连改 3 轮不过 → 标记人深查

---

## 类5：VERDICT_GAP（裁不对）

**定义**：RCA 裁决结论与期望不符——纯裁决层错误：0~4 全排除、每个节点的事实都对，
结论还归错档，才轮到它（孤儿绑定归类2）。

**Verdict**: `needs_adjudication`

### 检测条件

**裁决不符期望**：
   - 期望根因不在 `rca_result` 中（且不在 `probable_causes` 中）
   - 阴性用例却有 `rca_result` 非空（假阳）

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
- 熔断：同节点同类连改 3 轮不过 → 标记人深查

---

## 对账结果类型

对账层（Step 1）输出以下结果类型（与确证候选**自身** node_id 比对，路径祖先单列）：

| 类型 | 含义 | 用于 |
|------|------|------|
| HIT_CONFIRMED | 期望根因=某条确证根因的候选自身 | 阳性用例精确命中 |
| HIT_ANCESTOR | 期望根因是某条确证根因的路径祖先（诊断更深、落在期望子树内） | 算通过；单独计数并出"期望粒度偏粗"case_quality 提示，建议把用例期望精化到实际确证节点（粗粒度期望无法区分同一子树内的兄弟叶子） |
| HIT_DEGRADED | 期望根因在 probable_causes（候选自身或其路径祖先） | 降级命中 |
| MISSED | 期望根因不在任何档 | 漏判 |
| FALSE_CONFIRM | 阴性用例却有 rca_result | 假阳 |
| TRUE_NEGATIVE | 阴性用例且 rca_result 为空 | 真阴 |

> 反向不对称是有意的：期望比实际**深**（诊断只到祖先）不算命中——那是诊断没挖到底，
> 照常 HIT_DEGRADED / MISSED。

---

## 熔断机制

### 触发条件

**同 case 同节点同类**错误连续 3 轮未修复（按 case_id + node_id + error_class 跨轮计数；自动迭代 3 轮不过才转人工）

### 处理

- 停止自动 loop
- 标记"需人工深查"（feedback 的 suggestion 带【熔断警告】）
- 记录熔断历史——固定存 `--out` 目录的 `fuse_history.jsonl`（跨轮稳定；存临时目录会因每轮换目录而失效）

### 记录结构

```json
{
  "fused": true,
  "node_id": "n4",
  "error_class": "BINDING_GAP",
  "fuse_reason": "同节点同类连改3轮不过",
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
