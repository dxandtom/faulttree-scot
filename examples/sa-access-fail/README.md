# 示例场景:SA场景接入失败(合成演示)

本目录是 `scot-generate` 产出的一个**自包含场景 skill** 示例。所有数据均为**虚构**,
仅用于演示端到端链路与契约,不含任何真实/保密数据。

## 怎么来的
由合成的故障子树 + 合成 kpi/chr 样例,经 scot-generate 三级链生成:
1. 故障子树 → `tree/SA场景接入失败.md`
2. 元提示词 → 各指标 codegen 提示词(`codegen_prompts/`)→ 解析脚本(`scripts/*_analyzer.py`)
3. 生成并保存 SCoT → `plan.json`

## 跑一遍
```bash
python scripts/runner.py --plan plan.json \
  --data kpi=fixtures/kpi.csv --data chr=fixtures/chr.csv
```
会产出 `result/kpi.json`、`result/chr.json`、`result/rca_input.json`。
最后一步 RCA 是模型判断,由 agent 依据 `references/rca_rules.md` 完成 `result/rca.json`。

## 本样例数据下,各条件命中情况
- kpi:`SetupAtt(1200) - SetupSucc(1100) = 100 > 0` ✓;`NoRes(60) > 0` ✓;`NG.NoRsp(0) > 0` ✗
- chr:`RrcReject 占比 40% > 30%` ✓;`TransLink 占比 10% > 20%` ✗
- 注:kpi 里 `N.RRC.SetupFail.NoRes<span` 故意带 HTML 污染,演示计数器名清洗。

## RCA 期望输出(示意,由模型据 rca_rules 产出)
```json
[
  {"Root_cause":"无线资源不足","Fault_tree_path":["RRC建立失败","无线资源不足"],
   "evidence_path":["N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0","N.RRC.SetupFail.NoRes > 0"]},
  {"Root_cause":"接纳拒绝","Fault_tree_path":["RRC建立失败","接纳拒绝"],
   "evidence_path":["N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0","DCErrType 中 RrcReject 占比 > 30%"]}
]
```
注意:evidence 全部**逐字摘自故障子树正文**,不含任何数据数值。
