# feedback_to_generate.json 的结构

评估反思在有用例没通过时，把需要修的问题写成反馈文件 `feedback_to_generate.json`，由 cs-scot-generate-optimize 的反馈处理线承接：运行 `route_feedback.py --feedback <该文件> --product <产物目录>`，为每条问题给出处理方式。本文按 `eval_run.py` 实际写出的内容说明字段；完整例子见 `output_files.md` 第 5 节。

## 位置

文件写在评估输出根目录（`--out` 目录）下，与整批汇总 `quality_score_batch.json`、失败签名 `failure_signatures.json` 并列；单用例目录 `<case_id>-v<round>-<时间戳>/` 里不再单独放一份（评估报告里的 `feedback_to_generate` 字段为 null）。只在至少有一个用例触发反馈时才生成。

```
<out>/
├── quality_score_batch.json
├── failure_signatures.json
├── feedback_to_generate.json          # 本文说明的文件
└── <case_id>-v<round>-<时间戳>/
    ├── evaluation_report.json
    ├── quality_score.json
    ├── failure_signatures.json
    └── eval_run.json
```

## 整体结构

文件内容是一个 JSON 数组，每个触发反馈的用例一个对象：

```json
[
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
        "actual_result": "MISSED",
        "reason": "节点n4有判据但在tree_index里无任何绑定",
        "suggestion": {
          "action": "ADD_BINDING",
          "target_script": "diag_analyzer.py",
          "target_node": "n4",
          "detail": "节点 n4（LDP会话down）有判据但无任何绑定。在 skill-dir/scripts/ 的 analyzer 脚本中添加该节点的 binding 配置。",
          "template": "参考同类节点的 binding 写法"
        },
        "priority": "high",
        "previous_attempts": 0
      }
    ],
    "next_action": "REGENERATE",
    "summary": "第2轮评估：ldp-lsp-001存在1个问题，需generate修复后重新生成技能包"
  }
]
```

## 字段说明

### 每个用例对象

| 字段 | 类型 | 说明 |
|---|---|---|
| meta.case_id | string | 用例标识 |
| meta.round | integer | 评估轮次，从 1 起；传 `--prev-evaluation` 时加 1 |
| meta.feedback_iteration | integer | 已触发反馈的次数 |
| meta.evaluation_time | string | 评估时间（ISO8601） |
| meta.source_result_dir | string | 本次评估读取的执行结果目录 |
| meta.scenario | string | 场景名 |
| issues | array | 问题列表，见下；六类错误按序命中即停，所以一个用例通常只有一条 |
| next_action | string | 固定为 `REGENERATE`，表示交给优化技能处理；具体做什么以 `route_feedback.py` 给出的处理项为准 |
| summary | string | 一句话摘要 |

### issues[] 条目

| 字段 | 类型 | 说明 |
|---|---|---|
| error_class | string | 错误类别名，取值 ENV_FAILURE、INPUT_GAP、BINDING_GAP、KNOWLEDGE_GAP、CRITERIA_GAP、VERDICT_GAP 之一（存的是名字，不是 0 到 5 的编号） |
| node_id | string | 问题节点的编号；有的类别没有具体节点，为空字符串 |
| node_name | string | 节点名称，可能为空 |
| expected_result | string | 期望状态，目前固定为 `confirmed` |
| actual_result | string | 该节点的对账结果，如 `MISSED`、`HIT_DEGRADED`、`FALSE_CONFIRM` |
| reason | string | 分类脚本给出的原因 |
| suggestion | object | 修复建议，四个固定字段：`action`（按类别取 CHECK_ENV、SUPPLY_DATA、ADD_BINDING、ADD_NODE、REVIEW_CRITERIA、REVIEW_VERDICT，未知类别为 MANUAL_REVIEW）、`target_script`、`target_node`、`detail`（说明文字，触发熔断时末尾带【熔断警告】）、`template` |
| priority | string | 固定为 `high` |
| previous_attempts | integer | 评估写出时固定为 0；反馈处理线按各轮报告和处理记录自行计数 |

## 与失败签名的关系

`failure_signatures.json` 是同一批问题的另一种记录（字段 expected_node、recon_result、error_class、error_category、reason、evidence、priority），供人查看。反馈处理线两种文件都能识别：按条目里的 `error_class` 或 `error_category` 取值在六类错误名里判定。

## 熔断

同一用例、同一节点、同一类错误连续 3 轮没修好即熔断：熔断历史记在评估输出根目录的 `fuse_history.jsonl`，触发时该问题的 `suggestion.detail` 末尾带【熔断警告】，反馈处理线把它归入人工档。
