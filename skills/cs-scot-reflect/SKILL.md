---
name: cs-scot-reflect
description: 评估反思skill。对generate→execute的执行结果与期望进行比对，区分机械性错误与模型性错误，输出反馈供generate下一轮改进。当用户说"评估结果"、"对账"、"检查诊断是否正确"、"反思"、"运行评估"时触发。本skill独立于执行链，只读取cases/目录下的expected.json（不传generate/execute），确保防泄题。
---

# cs-scot-reflect —— 评估反思

**本文件已加载，不要再次加载或调用本技能；"加载"≠"完成"，真正产出评估报告才算完成。**

评估反思独立运行于 generate → execute 流程之后，对执行结果与期望进行比对，区分**机械性错误**与**模型性错误**，输出反馈供 generate 下一轮改进。

## 核心流程

```
Step 0: 读取输入
        ↓
Step 1: 对账层（纯机械）
        ↓
Step 2: 分类层（纯机械，按0→5查表）
        ↓
Step 3: 反馈层
        ↓
Step 4: 输出评估报告
```

## 用法

### 主入口（推荐）

```bash
python "<本目录>/scripts/eval_run.py" \
  --case <case_id> \
  --result-dir <result-dir> \
  --skill-dir <skill-dir> \
  [--cases-dir cases] \
  [--out <output-dir>] \
  [--prev-evaluation <上一轮-evaluation_report.json-path>]
```

### 批量评估

```bash
python "<本目录>/scripts/eval_run.py" \
  --case-dir <cases-parent-dir> \
  --result-dir <result-base-dir> \
  --skill-dir <skill-dir> \
  [--out <evaluation-base-dir>] \
  [--prev-evaluation <上一轮-evaluation_report.json-path>]
```

## 输入说明

### 0. 路径约定（无硬编码）

| 参数 | 说明 | 示例 |
|------|------|------|
| `--case` | 用例ID | `ldp-lsp-001` |
| `--cases-dir` | 用例根目录 | `cases/`（默认） |
| `--result-dir` | 执行结果目录 | `scot技能/ldp-lsp/assets/` |
| `--skill-dir` | 技能包目录 | `output/ldp-lsp-result/result-20260810/` |
| `--out` | 评估输出目录 | `output/<scenario>-eval/`（默认） |
| `--prev-evaluation` | 上一轮 evaluation_report.json（可选） | 用于追踪 loop 轮次 |

实际路径：
- `expected.json` = `<cases-dir>/<case_id>/expected.json`
- `result-dir` = 用户指定的执行结果目录（包含run.json/rca_input.json/rca.json）
- `tree_index` = `<skill-dir>/assets/tree/*.tree_index.json`（glob匹配）
- `prev-evaluation` = 上一轮 evaluation_report.json（可选，用于 round 继承）

### 1. expected.json（用例库）

目录形态：`cases/<case_id>/expected.json`

```json
{
  "case_id": "ldp-lsp-001",
  "scenario": "LDP LSP未建立",
  "negative": false,
  "expected_root_causes": [
    { "node_id": "n6", "node_name": "接口DOWN", "note": "log里约80行OperStatus=DOWN" }
  ],
  "expected_facts": {
    "n6": { "predicate_satisfied": true, "evidence_source": "log" }
  }
}
```

### 2. 执行产物（来自result-*/目录）

| 文件 | 必须 | 作用 |
|------|------|------|
| `run.json` | 是 | execution_status + missing_inputs |
| `rca_input.json` | 是 | 逐节点predicate_satisfied |
| `rca.json` | 是 | 四档结论 |

### 3. tree_index.json（来自技能包）

路径：`<skill-dir>/assets/tree/*.tree_index.json`

### 4. evaluation_report.json（来自上一轮评估，非必须）

用于 loop 连续性追踪。读取逻辑：
- 不传 `--prev-evaluation` → `round=1, feedback_iteration=0`
- 传入 → `round = 上轮.round + 1`，feedback_iteration 继承并在触发 feedback 时+1
- PASS / PASS_WITH_GAPS 时 feedback_iteration 清零

## 错误分类（6类，按序命中即停）

| # | 类型 | 检测条件 | 处理方 |
|---|------|---------|--------|
| 0 | ENV_FAILURE（执行失败） | `execution_status.*.ok == false` 或 run.json不存在 | 人工修环境 |
| 1 | INPUT_GAP（缺数据） | `missing_inputs`非空 **且** 有期望根因 MISSED | 使用方补数据 |
| 2 | BINDING_GAP（没绑上） | 静态查tree_index：节点有判据但无绑定 | generate补绑定 |
| 3 | KNOWLEDGE_GAP（不认识） | tree_index不存在该节点 | tester增节点 |
| 4 | CRITERIA_GAP（判不对） | predicate vs expected_facts不相等 | 人拍板→调优表 |
| 5 | VERDICT_GAP（裁不对） | 裁决≠期望 或 orphan非空 | 人裁决→调规则 |

### 决策树

```
Step 0: 读取产物
  读取：expected.json + run.json + rca_input.json + rca.json + tree_index.json
  可选：evaluation_report.json（传入则继承 round/feedback_iteration）
        ↓
Step 1: 对账（逐期望根因）
  ├─ 期望在rca_result → HIT_CONFIRMED
  ├─ 期望只在probable_causes → HIT_DEGRADED
  ├─ 期望不在任何档 → MISSED
  └─ 阴性用例+有rca_result → FALSE_CONFIRM
        ↓
Step 2: 按0→5顺序查错（仅在有 MISSED 或 FALSE_CONFIRM 时触发）
  ├─ 0: execution_status.*.ok == false？ → 类0 ENV_FAILURE
  ├─ 1: missing_inputs非空 + 有MISSED？ → 类1 INPUT_GAP
  ├─ 2: 有判据但无绑定（静态查tree_index）？ → 类2 BINDING_GAP
  ├─ 3: tree_index不存在该节点？ → 类3 KNOWLEDGE_GAP
  ├─ 4: predicate vs expected_facts不相等？ → 类4 CRITERIA_GAP
  ├─ 5: 裁决≠期望 或 orphan非空？ → 类5 VERDICT_GAP
        ↓
Step 3: 综合裁定 verdict
  ├─ 所有期望根因均 HIT_CONFIRMED/HIT_DEGRADED：
  │    * 有 missing_inputs → verdict=PASS_WITH_GAPS
  │    * 无 missing_inputs → verdict=PASS
  ├─ 有期望根因 MISSED 或 FALSE_CONFIRM：
  │    * 有类0~5错误触发 → verdict=FAIL
  │    * 无类0~5错误触发 → verdict=PARTIAL_PASS
```

### 处理规则

| 情况 | 处理 |
|------|------|
| 类0 / 类1 | 必须中断，人工修复 |
| 类2 / 类3 / 类4 / 类5 | 自动回流generate，可继续loop |
| 同节点同类连改2轮不过 | 熔断，标记人深查 |

## 输出结构

```
output/<scenario>-eval/
└── <case_id>-v<round>-<时间戳>/
    ├── evaluation_report.json    # 完整评估报告（唯一对外出口）
    ├── failure_signatures.json   # 失败签名列表（内部用）
    └── eval_run.json             # 本次评估元数据（内部用）
```

> 目录名带 `v<round>` 版本号，外部可直观识别是第几轮评估

### evaluation_report.json（唯一对外输出）

```json
{
  "success": true,

  "meta": {
    "case_id": "ldp-lsp-001",
    "scenario": "LDP LSP未建立",
    "round": 1,
    "feedback_iteration": 0,
    "evaluation_time": "2026-08-10T00:00:00Z",
    "source_result_dir": "output/ldp-lsp-result/result-20260810/",
    "skill_dir": "scot技能/ldp-lsp/",
    "verdict": "PASS"
  },

  "reconciliation": {
    "total_expected": 1,
    "hit_confirmed": 1,
    "hit_degraded": 0,
    "missed": 0,
    "false_confirm": 0,
    "details": [
      {
        "node_id": "n6",
        "node_name": "接口DOWN",
        "expected_result": "confirmed",
        "actual_result": "confirmed",
        "recon": "HIT_CONFIRMED",
        "evidence_source": "log"
      }
    ]
  },

  "data_gaps": null,

  "failure_signatures": [],

  "verdict_detail": {
    "verdict": "PASS",
    "error_class": null,
    "reason": "所有期望根因已确证；判断依据：n6节点predicate_satisfied=true；实际数值：OperStatus=DOWN事件命中；实际判断：cross_analyzer验证通过；结论：根因成立"
  },

  "feedback_to_generate": null,

  "checks": {
    "env_failure": { "passed": true, "detail": null },
    "binding_gap": { "passed": true, "detail": null },
    "knowledge_gap": { "passed": true, "detail": null },
    "criteria_gap": { "passed": true, "detail": null },
    "verdict_gap": { "passed": true, "detail": null }
  }
}
```

### failure_signatures.json（内部便利文件）

```json
[
  {
    "expected_node": "n4",
    "recon_result": "MISSED",
    "error_class": "BINDING_GAP",
    "reason": "节点n4有判据但在tree_index里无任何绑定",
    "evidence": ["tree_index.json#n4", "expected.json#expected_root_causes[1]"],
    "priority": "high"
  }
]
```

### feedback_to_generate.json（仅在需要反馈时生成）

```json
{
  "meta": {
    "case_id": "ldp-lsp-001",
    "round": 2,
    "feedback_iteration": 1,
    "evaluation_time": "2026-08-10T00:00:00Z",
    "source_result_dir": "output/ldp-lsp-result/result-20260810/",
    "scenario": "LDP LSP未建立"
  },

  "issues": [
    {
      "error_class": "BINDING_GAP",
      "node_id": "n4",
      "node_name": "LDP会话down",
      "expected_result": "confirmed",
      "actual_result": "undetermined",
      "reason": "tree_index中n4无任何binding配置；判断依据：静态查tree_index发现n4节点有判据但binding字段为空；实际数值：binding=null；判断过程：扫描tree_index所有节点，n4是唯一有判据无绑定的节点；结论：BINDING_GAP",
      "suggestion": {
        "action": "ADD_BINDING",
        "target_script": "diag_analyzer.py",
        "target_node": "n4",
        "detail": "在scripts/diag_analyzer.py中为n4节点添加binding配置",
        "template": "参考n57的binding写法"
      },
      "priority": "high",
      "previous_attempts": 0
    }
  ],

  "next_action": "REGENERATE",

  "summary": "第2轮评估：n4节点binding缺失，需generate补齐后重新生成技能包"
}
```

## 防泄题红线

1. **文件通道**：expected.json只住cases/，不传generate、不拷进assets/、不随result-*携带
2. **上下文通道**：读过expected/失败签名的会话不得执行手工RCA诊断与回归，必须派独立会话

## 典型用法示例

### 评估单个用例（第一轮）

```bash
python scripts/eval_run.py \
  --case ldp-lsp-001 \
  --result-dir output/ldp-lsp-result/result-20260810T034800Z-9a2c55 \
  --skill-dir scot技能/ldp-lsp-is-not-established \
  --out output/ldp-lsp-eval
```

### 评估单个用例（第二轮，接上轮）

```bash
python scripts/eval_run.py \
  --case ldp-lsp-001 \
  --result-dir output/ldp-lsp-result/result-20260811T120000Z-8b3d21 \
  --skill-dir scot技能/ldp-lsp-is-not-established \
  --out output/ldp-lsp-eval \
  --prev-evaluation output/ldp-lsp-eval/ldp-lsp-001-v1-20260810T034800Z/evaluation_report.json
```

### 批量评估（同一场景多轮执行）

```bash
python scripts/eval_run.py \
  --case-dir cases \
  --result-dir output/ldp-lsp-result \
  --skill-dir scot技能/ldp-lsp-is-not-established \
  --out output/ldp-lsp-eval
```

### 用户一句话启动

```
/cs-scot-reflect

请帮我评估执行结果（第二轮，接上轮评估）。
- 用例：cases/ldp-lsp-001/
- 执行结果：output/ldp-lsp-result/result-20260811T120000Z-8b3d21/
- 技能包：scot技能/ldp-lsp-is-not-established/
- 上一轮评估：output/ldp-lsp-eval/ldp-lsp-001-v1-20260810T034800Z/evaluation_report.json
```

## 与上下游的配合

```
第一轮：
  generate → execute ──→ reflect ──→ evaluation_report.json（round=1）
                                                        │
第二轮（接上轮）：                                      ↓
  generate ←── reflect（读取上轮 evaluation_report.json）
     │                    │
     └── 新技能包          └── evaluation_report.json（round=2）
```

- **上游generate**：生成技能包（plan.json、analyzer、tree_index）
- **中游execute**：执行诊断，产生result-*/目录
- **下游reflect（本skill）**：读取产物与expected.json对账，输出feedback_to_generate
- **轮次追踪**：evaluation_report.json 作为非必须输入，实现 loop 连续性

### verdict 体系（只做评级，不复用错误类型名）

> verdict 只回答一个问题：「这次跑完要不要管？」

| verdict | 含义 | 是否触发 feedback | 说明 |
|---------|------|-------------------|------|
| `PASS` | 全部命中、完美跑完 | 否 | 无需任何干预 |
| `PASS_WITH_GAPS` | 全部命中、有数据缺口 | 否 | 结论可靠，数据完整后可进一步验证 |
| `PARTIAL_PASS` | 部分命中 | 是 | 有期望根因MISSED，需反馈给generate改进 |
| `FAIL` | 需要改进 | 是 | 存在类0~5错误，需反馈给generate修复 |

## 注意事项

1. **纯机械判断**：0→5分类查表完成，模型不参与分类判断
2. **留档到文件**：每步输出都落盘，避免中途失败丢失进度
3. **无硬编码路径**：所有路径通过参数传入
4. **通用设计**：与具体指标类型无关，可适配任意generate→execute产物
5. **并发安全**：每轮评估创建独立目录，互不覆盖
6. **verdict 只做评级**：verdict 永不复用错误分类（0→5）的名字
7. **轮次追踪**：通过 evaluation_report.json 传递 round/feedback_iteration
