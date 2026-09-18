# 输出文件的完整例子与口径

本文给出评估反思每个输出文件的完整例子。业务取数看第 2 节，平台展示看第 3 节。

## 1. evaluation_report.json（单用例，唯一对外出口）

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
    "hit_ancestor": 0,
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
  "case_quality": [],
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
    "input_gap": { "passed": true, "detail": null },
    "binding_gap": { "passed": true, "detail": null },
    "knowledge_gap": { "passed": true, "detail": null },
    "criteria_gap": { "passed": true, "detail": null },
    "verdict_gap": { "passed": true, "detail": null }
  }
}
```

`checks` 六个键与六类错误一一对应，`passed` 三态：`true` 是评了且通过；`false` 是命中（整场唯一，链条停在这里，detail 附机械原料）；`null` 是没评到（前面某类已命中，按序评估终止）。三态按分类结果的链条机械推导。

## 2. quality_score_batch.json（整批，评估输出根目录）

两级两个文件：每个用例目录里的 `quality_score.json` 是单用例的，评估输出根目录的 `quality_score_batch.json` 是整批的，文件名不同，解析时不会混。一键回归（regress.py，落在 `<工作目录>/eval/`）与批量判卷（eval_run.py，落在 `--out` 目录）写的是同一格式，由 `scripts/batch_summary.py` 统一计算。

```json
{
  "level": "batch",
  "total_score": 46.2,
  "pass_rate": 0.1111,
  "total_cases": 9,
  "passed": 1,
  "verdicts": {"PASS": 0, "PASS_WITH_GAPS": 1, "PARTIAL_PASS": 0, "FAIL": 8, "EVAL_ERROR": 0, "NOT_EVALUATED": 0},
  "dimensions": {
    "env_failure":   {"value": 0.8889, "score": 89, "passed_cases": 8, "dimension_name": {"default": "执行环境", "zh_CN": "执行环境", "en_US": "Execution Environment"}},
    "input_gap":     {"value": 0.7778, "score": 78, "passed_cases": 7, "dimension_name": {"default": "数据完整性", "zh_CN": "数据完整性", "en_US": "Input Completeness"}},
    "binding_gap":   {"value": 0.4444, "score": 44, "passed_cases": 4, "dimension_name": {"default": "绑定正确性", "zh_CN": "绑定正确性", "en_US": "Binding Integrity"}},
    "knowledge_gap": {"value": 0.3333, "score": 33, "passed_cases": 3, "dimension_name": {"default": "树节点认知", "zh_CN": "树节点认知", "en_US": "Tree Knowledge"}},
    "criteria_gap":  {"value": 0.2222, "score": 22, "passed_cases": 2, "dimension_name": {"default": "判据正确性", "zh_CN": "判据正确性", "en_US": "Criteria Correctness"}},
    "verdict_gap":   {"value": 0.1111, "score": 11, "passed_cases": 1, "dimension_name": {"default": "裁决正确性", "zh_CN": "裁决正确性", "en_US": "Verdict Correctness"}}
  },
  "meta": {"evaluation_time": "2026-08-10T00:00:00Z", "value_type": "RATIO"},
  "cases": [
    {"case_id": "n6-接口DOWN", "verdict": "PASS_WITH_GAPS", "quality_score": 100,
     "dimensions": {"env_failure": true, "input_gap": true, "binding_gap": true, "knowledge_gap": true, "criteria_gap": true, "verdict_gap": true},
     "report_path": "n6-接口DOWN-v1-20260810T000000Z/evaluation_report.json"},
    {"case_id": "n9-LDP会话down", "verdict": "FAIL", "quality_score": 33,
     "dimensions": {"env_failure": true, "input_gap": true, "binding_gap": false, "knowledge_gap": null, "criteria_gap": null, "verdict_gap": null},
     "report_path": "n9-LDP会话down-v1-20260810T000000Z/evaluation_report.json"}
  ]
}
```

口径：

- 准确率取 `pass_rate`：分子是 verdict 为 PASS 或 PASS_WITH_GAPS 的用例数（期望根因全命中，缺部分数据不扣通过），分母是全部用例，执行失败、判卷异常、待裁决都算不通过。
- 多用例总得分取 `total_score`：单用例的 total_score 是"六关过了几关"（0 到 100），整批是它们的算术平均，没评出分的用例按 0。挂在第一关的用例把平均拉低是有意的。
- 整批六维：各维等于通过该关的用例数除以用例总数，没评到的关按不通过。因此 total_score 也等于六个维度分的平均；平台可以用同一套六维界面展示，单用例是布尔、整批是比例（`meta.value_type`）。
- `verdicts` 里的 NOT_EVALUATED 计没有产生报告的用例（执行失败、待裁决等）。

## 3. quality_score.json（单用例，平台展示评分）

评估末尾由 eval_run.py 调 `score_report.py` 自动生成，零模型参与；平台展示直接读这个文件。

```json
{
  "total_score": 83,
  "dimensions": {
    "env_failure":   {"value": true,  "score": 100, "reason": {"default": "通过:未发现执行环境(ENV_FAILURE)类问题。", "zh_CN": "通过:未发现执行环境(ENV_FAILURE)类问题。", "en_US": "Passed: no Execution Environment (ENV_FAILURE) issue detected."}},
    "input_gap":     {"value": true,  "score": 100, "reason": {"default": "…", "zh_CN": "…", "en_US": "…"}},
    "binding_gap":   {"value": true,  "score": 100, "reason": {"default": "…", "zh_CN": "…", "en_US": "…"}},
    "knowledge_gap": {"value": true,  "score": 100, "reason": {"default": "…", "zh_CN": "…", "en_US": "…"}},
    "criteria_gap":  {"value": true,  "score": 100, "reason": {"default": "…", "zh_CN": "…", "en_US": "…"}},
    "verdict_gap":   {"value": false, "score": 0,   "reason": {"default": "未通过(VERDICT_GAP):<机械摘要,逐字取自 verdict_detail.reason>", "zh_CN": "…", "en_US": "Failed (VERDICT_GAP). See zh_CN for mechanical details."}}
  },
  "meta": {"case_id": "…", "scenario": "…", "round": 1, "verdict": "FAIL", "evaluation_time": "…", "error_class": "VERDICT_GAP"}
}
```

口径（与"六关按序、命中即停"严格一致，不做程度打分）：

- 六维就是六类错误，顺序即评估顺序；`value_type` 为 BOOLEAN：通过 `value: true, score: 100`；命中 `value: false, score: 0`；没评到 `value: null, score: null`（展示层渲染成 N/A）。
- total_score 等于通过维度数除以 6 乘 100 取整：全过 100，挂第 1 关 0，挂最后一关 83，挂得越早分越低。
- PASS 与 PASS_WITH_GAPS 同待遇：六维全 true、总分 100。数据缺口记在 verdict 层，六关判定全部通过，不因缺口扣分。
- `reason` 三语模板化（default 等于 zh_CN；en_US 固定模板，不做自由翻译），失败维度的 zh_CN 附逐字机械摘要。
- EVAL_ERROR 不产生此文件，宁缺评分，不拿假分冒充。

## 4. failure_signatures.json（内部便利文件）

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

## 5. feedback_to_generate.json（只在需要反馈时生成）

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

字段说明与更多场景见 `feedback_schema.md`。

## 6. expected.json（输入，用例库）

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

字段约束见 `expected_schema.md`。
