# 示例场景:SA场景接入失败(合成演示)

本目录是 `cs-scot-generate` 产出的一份**场景诊断产物**(资产,不是 skill)示例。
所有数据均为**虚构**,仅演示链路与契约,不含真实/保密数据。

## 产物构成
- `plan.json` —— **真正的 SCoT 计划**(v4.0:`entry` + `nodes` + `transition` + `$ref`)。
- `scripts/kpi_analyzer.py`、`scripts/chr_analyzer.py` —— 各指标解析脚本(**自包含、精简**)。
- `tree/SA场景接入失败.md` —— 故障树 md。
- `codegen_prompts/` —— 生成各脚本所用提示词(留痕);`fixtures/` —— 演示用假数据。

## plan.json 是怎样一份 SCoT
直线三节点:`kpi`(script)→ `chr`(script)→ `rca`(agent_skill = fault-tree-rca);
节点间用 `$input.kpi` / `$memory.kpi_result` / `$output.final_answer` 等引用传数据。

## 跑一遍(用 cs-scot-execute 的执行器)
```bash
python ../../skills/cs-scot-execute/scripts/runner.py \
  --plan plan.json \
  --data kpi=fixtures/kpi.csv --data chr=fixtures/chr.csv
```
runner 从 `entry` 出发遍历节点:跑两个 script 节点(结果存入工作记忆),到 `agent_skill`
节点时把"故障树正文 + 各指标解析结果"汇总成 `result/rca_input.json`,交给
**fault-tree-rca 技能**产出 `result/rca.json`。

## 本样例数据下,各条件的判断(脚本算出的 `judgment`)
- kpi:`1200 - 1100 = 100 > 0 → 成立`;`N.RRC.SetupFail.NoRes = 60 > 0 → 成立`;`N.NG.Setup.Fail.NoRsp = 0 > 0 → 不成立`
- chr:`RrcReject = 4 / 总数 10 = 40.0% > 30% → 成立`;`TransLink = 1 / 总数 10 = 10.0% > 20% → 不成立`
- 注:kpi 里 `N.RRC.SetupFail.NoRes<span` 故意带 HTML 污染,演示计数器名清洗。

## RCA 期望输出(示意,由 fault-tree-rca 产出)
```json
[
  {"Root_cause":"无线资源不足",
   "Fault_tree_path":["RRC建立失败","无线资源不足"],
   "evidence_path":[
     {"node":"RRC建立失败","condition":"N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0","actual":"N.RRC.SetupAtt.Sum = 1200, N.RRC.SetupSucc.Sum = 1100","judgment":"1200 - 1100 = 100 > 0 → 成立"},
     {"node":"无线资源不足","condition":"N.RRC.SetupFail.NoRes > 0","actual":"N.RRC.SetupFail.NoRes = 60","judgment":"60 > 0 → 成立"}
   ]},
  {"Root_cause":"接纳拒绝",
   "Fault_tree_path":["RRC建立失败","接纳拒绝"],
   "evidence_path":[
     {"node":"RRC建立失败","condition":"N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0","actual":"N.RRC.SetupAtt.Sum = 1200, N.RRC.SetupSucc.Sum = 1100","judgment":"1200 - 1100 = 100 > 0 → 成立"},
     {"node":"接纳拒绝","condition":"DCErrType 中 RrcReject 占比 > 30%","actual":"RrcReject = 4 / 总数 10 = 40.0%","judgment":"40.0% > 30% → 成立"}
   ]}
]
```
