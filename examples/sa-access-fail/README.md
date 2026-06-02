# 示例场景:SA场景接入失败(合成演示)

本目录是 `cs-scot-generate` 产出的一份**场景诊断产物**(资产,不是 skill)示例。
所有数据均为**虚构**,仅演示链路与契约,不含真实/保密数据。

## 产物构成
- `plan.json` —— **真正的 SCoT 计划**(v4.0:`entry` + `nodes` + `transition` + `$ref`)。
- `scripts/kpi_analyzer.py`、`scripts/chr_analyzer.py` —— **同一个通用解析引擎 `analyzer.py` 的副本**(按指标命名)。不再每场景写代码;引擎自动判别数据形态。
- `tree/SA场景接入失败.md` —— 故障树 md;`fixtures/` —— 演示用假数据。

## 一个引擎,看数据形态做不同的事
- **kpiTree 是"规则字典"(catalog)**:`fixtures/kpi.csv` 每行一段 JSON,只有 `formula`/阈值/定义,**没有实测值**。引擎产出 `rules`(各 KPI 的公式/阈值/定义)+ `counter_index`(计数器 → 用到它的 KPI),`result/kpi.json` 里**没有任何计数器取值**。
- **CHR 是"逐记录测量"(tabular)**:`fixtures/chr.csv` 是表格,引擎逐列统计(`DCErrType` 分布等),`result/chr.json`。
- 引擎**只产事实/规则,不下判断、不做故障树匹配**——通篇没有 `hit`/`judgment`/`conditions`。
- **按故障树投影**:脚本旁有 `tree/`,引擎自动只保留故障树提到的字段/计数器/规则(`result/*.json` 里有 `projection` 计数)。本例 chr 只留 `DCErrType`、kpi 只留 `RRC Setup Success Rate` 规则;真实 kpiTree 几千条会被压到树真正引用的那几条。

## 跑一遍(用 cs-scot-execute 的执行器)
```bash
python ../../skills/cs-scot-execute/scripts/runner.py \
  --plan plan.json \
  --data kpi=fixtures/kpi.csv --data chr=fixtures/chr.csv
```
runner 跑两个 script 节点(引擎副本),到 `agent_skill` 节点把"故障树正文 + 各指标事实/规则"
汇总成 `result/rca_input.json`,交给 **fault-tree-rca 技能**产出 `result/rca.json`。

## 故障树与本数据下的诚实判断(RCA 现算)
故障树三个叶子:
- `接纳拒绝`:**CHR** `DCErrType 中 RrcReject 占比 > 30%` → RrcReject 4/10 = 40% > 30% → **成立**;父节点 `RRC建立失败` 无判断条件 → 这条路径完整证实 → **根因**。
- `传输中断`:**CHR** `TransLink 占比 > 20%` → 1/10 = 10% < 20% → **不成立**。
- `接入成功率低`:**话统** `RRC Setup Success Rate 低于 absExceptionThreshold` → kpiTree 给了规则(`formula = ("L.RRC.ConnReq.Succ"/"L.RRC.ConnReq.Att")*100`,阈值 `<90`)但**无实测值** → **数据不足**。

## RCA 期望输出(示意,由 fault-tree-rca 产出)
`judgment` 由 RCA 用引擎给的真实值现算;话统无实测值就如实"数据不足"。
```json
[
  {"Root_cause":"接纳拒绝",
   "Fault_tree_path":["SA场景接入失败","RRC建立失败","接纳拒绝"],
   "support":"主要支持:DCErrType=RrcReject 4/10",
   "evidence_path":[
     {"node":"RRC建立失败","condition":"","actual":"","judgment":"路径中间节点,无独立判断条件"},
     {"node":"接纳拒绝","condition":"CHR DCErrType 中 RrcReject 占比 > 30%","actual":"RrcReject = 4 / 总数 10 = 40.0%","judgment":"40.0% > 30% → 成立"}
   ]}
]
```
并附 `undetermined`:`接入成功率低` —— `RRC Setup Success Rate` 规则与阈值 `<90` 已知,但 kpiTree 无实测值,数据不足无法判定(`传输中断` 不成立,不列为根因)。
