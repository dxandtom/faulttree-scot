---
name: cs-scot-execute
description: 执行一份 SCoT 计划 plan.json(cs-scot-generate 产出的场景诊断产物里那份):从 entry 出发按 transition 遍历 nodes,用工作记忆与引用表达式在节点间传数据,跑各指标解析脚本,再做综合根因分析,输出根因路径与判断依据。当用户要执行 SCoT、运行某故障诊断场景、或对一个 plan.json 跑出根因。
---

# 轻量 SCoT 计划执行器

**本文件已加载,不要再次加载或调用本技能;"加载"≠"完成",真正跑完计划、产出结果才算完成。** 现在立刻用 Bash 跑 `python "<本目录>/scripts/runner.py" --plan "<plan.json>" --data <指标>=<文件> ...` 执行计划,把输出汇报出来。

执行真正的 **SCoT 计划**(`plan.json`,SCoT v4.0 的子集)。格式见 `references/plan_schema.md`。

## 用法
```bash
python scripts/runner.py --plan <场景产物>/plan.json --data kpi=<kpi数据> --data chr=<chr数据>
```
- `--data <指标>=<文件>`:绑定计划里 `$input.<指标>` 引用到实际数据文件(xlsx/csv);有几个指标传几次。
- **部分数据也能跑**:plan 需要的数据源(如 rb/chr/config)用户只给了一部分时,**不报错**——缺源的解析
  步骤自动 `[skip]`,其余步骤照跑,结尾打印「⚠ 数据源缺口汇总」(缺了啥、跳过了哪些步骤、补数命令);
  缺口同时写进 `result/run.json`(`missing_inputs`)和 `rca_input.json`(顶层 `missing_indicators`),
  RCA/辩论引擎把这些源的判据按「数据不足」对待(**不是**"已排除")。**向用户汇报结果时必须带上缺口**:
  结论只代表已提供数据能看到的部分。注意:一个数据源都没给仍是硬报错(用法错误);`--data` 的 key 打错
  会触发「plan 用不到的数据源」告警——看到它先核对 key 名,别让 typo 静默变成跳过。
- `--base <dir>`:相对路径基准目录,默认是 `plan.json` 所在目录。

## 场景打包形态(assets/)——一句话执行

本技能常以**场景技能**形态发布(`package_scenario.py` 打包:技能目录名=场景英文短名,如
`layer-3-service-issues`;名字变了,本文件的执行逻辑不变):诊断资源内置在本技能目录的
`assets/` 下——`assets/plan.json`、`assets/scripts/`(各指标解析脚本)、`assets/tree/`
(故障树 + 可选 solutions.json)、`assets/fault-tree-rca/`(根因分析规则副本,rca 步骤按它执行)。

此形态下用户只需一句话(如「数据在这里,帮我诊断」),按以下默认约定跑完全程,**不需要用户指路**:
1. **数据入口**:数据以用户提供的形式为准——目录/散文件直接用;给的是压缩包(zip 等)就先解开再用。**不预设形式**,用户给什么接什么。
2. **数据↔指标匹配**:指标名 = `assets/scripts/` 下的 `<指标>_analyzer.py` 前缀;数据文件按
   文件名与指标名匹配(相等或包含,如 `config_20250701.csv` → `config`)。**匹配不上的文件
   跳过**、plan 需要但没给的源自动 `[skip]`(runner 内建)——两类都必须写进汇报,不许静默。
3. **执行**:`python "<本目录>/scripts/runner.py" --plan "<本目录>/assets/plan.json" --data <指标>=<文件> ...`
   (`result/` 落在 `assets/` 下);rca 步骤按 `assets/fault-tree-rca/` 的规则做。
4. **【输出汇报】固定模板(默认执行,无须用户要求;各节内容与 rca.json 严格口径一致,别自创表格样式/emoji)**:
   ```
   ## 执行过程
   每步一行:脚本/校验的输出要点(含 validate_rca 结果)
   ## 诊断结论
   ### 确证根因(N 条)
   | 根因 | 故障树路径 | 关键证据 | 推荐解决方案 |
   ### 疑似根因(N 条)
   high/medium 逐条详述:根因/卡在哪(blocked_at)/证据/方案(有 solutions.json 时);
   low 合并成一行一条的简表
   ### 待确认(N 条)
   各缺什么数据、补什么能定
   ## 数据缺口与被跳过的文件
   plan 缺的源、没匹配上的文件——结论只代表已提供数据能看到的部分
   ## 树/绑定问题
   tree_issues 非空时才有此节
   ```
   **validate_rca 的 ⚠ 软提示必须先逐条处置(升档/补报/excluded_note 留痕/阻塞写实)并重跑校验,
   再产出本报告——"硬检查通过"不等于"没事了"。**
- `--run-id <id>`(可选):本次运行号(**默认自动**「时间戳+随机」)。**版本管理是默认行为**:`result/` 永远=当前这一轮;重跑前 runner 把**上一轮整体归档到 `runs/<上轮run-id>/`**(`rca_input.json`/`rca.json`/`run.json`/`_compression.txt`),历史不丢、不覆盖、无歧义。每轮写 `result/run.json`(本轮 run_id + 输入数据指纹:路径/大小/sha1)并追加 `runs/index.jsonl`,可溯源、可检索。eval/benchmark 仍读 `result/`(=当前轮),不受影响。(`--new-run` 已是默认,保留仅为兼容。)

## 执行模型(真 SCoT)
1. 从 `entry` 节点开始,用 `memory_init` 初始化工作记忆;
2. 按节点 `kind` 分派:
   - **`script`**:`name`→`scripts/<name>.py`(generate 生成的逐条件分析脚本),解析 `inputs.data`→数据文件,跑脚本,产出该源**逐条件事实** JSON 按 `outputs` 写入 `$memory`;
   - **`agent_skill`**:把解析好的 inputs(故障树读成正文 + 各源逐条件事实)备到 `result/rca_input.json`,由 `name` 指定的技能(**fault-tree-rca**)产出结果。该步有**两条可互换路径**:默认模型手工判断(fault-tree-rca),或改跑辩论引擎(**fault-tree-rca-debate** 技能的 `run_debate_rca.py`)——两者吃同一个 rca_input、写同一个 rca.json、过同一个 validate_rca;
3. 沿 `transition.next_on_success` 前进,`end=true` 结束;
4. 节点间数据全靠 `$input`/`$memory`/`$step`/`$output` 引用传递。

未定义的 kind 会直接报错(避免静默跳过)。典型链:`各 <指标>_analyzer(script,快)`→`rca(agent_skill,唯一模型步骤,慢)`。**统计已在生成脚本里算好,execute 不再加工数据。**

## 输出
- `script` 节点产出存入 `$memory`(不单独落盘);
- `agent_skill`(RCA)节点:把汇总输入写到 `result/rca_input.json` → 由 fault-tree-rca 产出 `result/rca.json`。**`result/` 永远=当前一轮**(干净、无 `.prev` 杂物);重跑前**上一轮整体自动归档到 `runs/<run-id>/`**,历史按版本留存——agent 不会再被"已存在的 rca.json 是上轮还是本轮"困惑。

`result/rca.json` 契约:**权威定义在 fault-tree-rca 技能**——四档 dict(不是裸数组),顶层四键恒在:
```json
{
  "rca_result": [ {"Root_cause":"…","Fault_tree_path":["…"],"support":"…",
     "evidence_path":[{"node":"…","node_id":"…","condition":"<逐字>","actual":"<逐字引用事实>","judgment":"成立/不成立/数据不足"}]} ],
  "probable_causes": [ {"Root_cause":"…","Fault_tree_path":["…"],"confidence":"high/medium/low","blocked_at":"…","caveats":"…","support":"…","evidence_path":[…]} ],
  "undetermined": [ {"Fault_tree_path":["…"],"blocked_at":"…","reason":"缺哪个量","observed":["…"]} ],
  "tree_issues": { "uncovered_condition_nodes":[…], "orphan_node_ids":[…] }
}
```
`rca_result` 为 `[]` 是**正常输出**(上游缺话统/信令时,强证据在 `probable_causes`),不是失败。

## 设计要点
- 启动先比对**产物铭牌** `manifest.json` 的格式契约版本:产物较旧 / 缺铭牌只**告警不阻断**(提示"由旧工具链生成,建议重新 generate"),避免旧产物配新执行器静默跑出怪结果。
- 计划是**直线**的:故障树的分支判断在 RCA 内部完成,执行层只顺序遍历节点。
- **局部子树压缩带零损失下界**:剪枝后的 RCA 输入若**不比**完整树小(小/密的树本就无可剪),自动**回退完整树**、压缩 0%,绝不倒贴;孤儿事实瘦身、未覆盖清单只留 id+name,避免覆盖报告把 payload 顶大。
- 确定性计算都在 `script` 节点(脚本里);`agent_skill`(RCA)是唯一的模型判断步骤。
- 轻量:暂不实现 conditions/parallel/budget/reflection 等 SCoT 重型特性(schema 兼容,需要再加)。
