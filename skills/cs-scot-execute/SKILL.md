---
name: cs-scot-execute
description: 执行 cs-scot-generate 产出的场景 SCoT 计划 plan.json:按顺序跑各指标解析脚本,再做综合根因分析,输出根因路径与判断依据。当用户要执行 SCoT、运行某故障诊断场景、或对一个 plan.json 跑出根因时,务必使用本技能。它通常已被打包进每个自包含场景 skill(scripts/runner.py),也可独立对任意 plan.json 使用。
---

# cs-scot-execute —— SCoT 计划执行器

按 `plan.json`(SCoT 执行计划)顺序执行各步骤。计划格式见 `references/plan_schema.md`。

## 用法
```bash
python scripts/runner.py --plan plan.json --data kpi=<kpi数据> --data chr=<chr数据>
```
- `--data <指标>=<文件>`:把计划里 `$data.<指标>` 占位绑定到实际数据文件(xlsx 或 csv);有几个指标就传几次。
- `--base <dir>`:路径解析基准目录,默认是 `plan.json` 所在目录。

## 支持的 step kind(与 plan_schema v1 一致)
- **`script`**:跑某指标解析脚本(确定性)。runner 执行 `python <run> <绑定的数据> -o <out>`,校验产出存在。
- **`rca`**:综合根因分析。runner 把子树正文 + 各指标结果 JSON + 规则汇总成 `result/rca_input.json`;这是**模型判断**步骤——由所在 agent harness 依据 `references/rca_rules.md` 产出 `result/rca.json`(若环境配了可调用模型也可在此直接完成)。
- 遇到未定义的 kind 会直接报错(避免静默跳过)。新增 kind 时在 runner 里加一个分支即可。

## 输出
- 每个 `script` 步骤:`result/<指标>.json`
- `rca` 步骤:`result/rca_input.json`(汇总输入)→ `result/rca.json`(根因结果)

`result/rca.json` 契约:
```json
[{"Root_cause":"根因节点","Fault_tree_path":["父1","父2","根因节点"],
  "evidence_path":[{"node":"父1","condition":"<逐字判断条件>","actual":"<实际数值>","judgment":"<代入比较 → 成立/不成立>"}]}]
```
每条 evidence 含 `condition`(逐字规则)+ `actual`(实际数值)+ `judgment`(怎么判断与结论),取自各指标解析结果的 `conditions`。

## 设计要点
- 计划是**直线**的:故障树的分支判断在 RCA 内部完成,执行层只顺序跑。
- 确定性计算都在 `script` 步骤(脚本里);`rca` 是唯一的模型判断步骤。
