---
name: cs-scot-execute
description: 执行一份 SCoT 计划 plan.json(cs-scot-generate 产出的场景诊断产物里那份):从 entry 出发按 transition 遍历 nodes,用工作记忆与引用表达式在节点间传数据,跑各指标解析脚本,再做综合根因分析,输出根因路径与判断依据。当用户要执行 SCoT、运行某故障诊断场景、或对一个 plan.json 跑出根因。
---

# cs-scot-execute —— 轻量 SCoT 计划执行器

**本文件已加载,不要再次加载或调用本技能;"加载"≠"完成",真正跑完计划、产出结果才算完成。** 现在立刻用 Bash 跑 `python "<本目录>/scripts/runner.py" --plan "<plan.json>" --data <指标>=<文件> ...` 执行计划,把输出汇报出来。

执行真正的 **SCoT 计划**(`plan.json`,SCoT v4.0 的子集)。格式见 `references/plan_schema.md`。

## 用法
```bash
python scripts/runner.py --plan <场景产物>/plan.json --data kpi=<kpi数据> --data chr=<chr数据>
```
- `--data <指标>=<文件>`:绑定计划里 `$input.<指标>` 引用到实际数据文件(xlsx/csv);有几个指标传几次。
- `--base <dir>`:相对路径基准目录,默认是 `plan.json` 所在目录。

## 执行模型(真 SCoT)
1. 从 `entry` 节点开始,用 `memory_init` 初始化工作记忆;
2. 按节点 `kind` 分派:
   - **`script`**:`name`→`scripts/<name>.py`,解析 `inputs.data`→数据文件,跑脚本,产出 JSON 按 `outputs` 写入 `$memory`;
   - **`agent_skill`**:把解析好的 inputs(故障树读成正文 + 各指标结果)备到 `result/rca_input.json`,由 `name` 指定的技能(**fault-tree-rca**)产出结果;
3. 沿 `transition.next_on_success` 前进,`end=true` 结束;
4. 节点间数据全靠 `$input`/`$memory`/`$step`/`$output` 引用传递。

未定义的 kind 会直接报错(避免静默跳过)。

## 逐条件绑定与求值(诊断的"会变"部分,进 RCA 之前)
故障树条件五花八门(阈值/差值/字段匹配/占比TopN/信令流程/特征分析/人工…,见 fault-tree-rca 的 A–J 分类),
数据列名/格式更千变万化。**不写死、也不每次重写统计逻辑**:A–J 每类的统计逻辑已**固化测过**在
`scripts/condition_kernels.py`;**你(模型)运行时只做"绑定"**——看故障树 + 各数据源真实表头/样本,
为每条条件产出 `conditions.json`(选哪个类别内核、对哪列、剥前缀/缩放等变换、路由到哪个源、阈值;**对不上就标数据不足**)。
契约见 `references/condition_binding_schema.md`。然后跑:
```bash
python scripts/eval_conditions.py --conditions conditions.json --data kpi=<kpi> --data chr=<chr> -o result/condition_facts.json
python scripts/validate_conditions.py result/condition_facts.json   # 机械自检:没解析到列却给判断 → 报错
```
- 先用 `<ind>_analyzer.py` 跑出各源**概览事实**(列清单 + 数值摘要/十分位 + nulls),据此对列、定阈值;
- `condition_facts.json` 每条带 provenance(命中哪列、怎么命中、nulls)+ `predicate_satisfied`(**仅字面比较**);
- 把 `condition_facts.json`(+ 概览)交给 **fault-tree-rca** 做**最终裁决**:语义是否真等(RSRP≠RSSI 要降置信)、数据不足传播、路径回溯。

## 输出
- `script` 节点产出存入 `$memory`(不单独落盘);
- `agent_skill`(RCA)节点:`result/rca_input.json`(汇总输入)→ 由 fault-tree-rca 产出 `result/rca.json`。

`result/rca.json` 契约:
```json
[{"Root_cause":"根因节点","Fault_tree_path":["父1","父2","根因节点"],
  "evidence_path":[{"node":"父1","condition":"<逐字判断条件>","actual":"<实际数值>","judgment":"<代入比较 → 成立/不成立>"}]}]
```

## 设计要点
- 计划是**直线**的:故障树的分支判断在 RCA 内部完成,执行层只顺序遍历节点。
- 确定性计算都在 `script` 节点(脚本里);`agent_skill`(RCA)是唯一的模型判断步骤。
- 轻量:暂不实现 conditions/parallel/budget/reflection 等 SCoT 重型特性(schema 兼容,需要再加)。
