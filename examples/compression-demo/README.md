# 压缩示例:整树 → 局部子树 + 疑似根因 + tree_issues

一个**端到端、可直接跑**的小场景,演示两件新能力:
1. **上下文压缩**:RCA 拿到的不是整棵故障树,而是**剪过的局部子树**(只含相关节点,事实挂在节点上)。
2. **疑似根因档**:叶子证据强、但上游节点数据不足时,作为 **probable**(疑似)如实上报,既不硬报成根因、也不埋没。

## 场景设计(故意覆盖各种情况)
故障树(`fault_tree.csv` → `tree/接入失败示例.md` + `.tree_index.json`):
```
接入失败示例(无条件,过路节点)
└ QoS建立失败            条件:N.QosFlow.Est.Att - N.QosFlow.Est.Succ > 0   ← kpi 无原始计数 → 数据不足
  ├ 干扰导致             条件:DCErrType=RNL_Unspecified 且 RelSinr<0       ← CHR 有 → 成立(强证据)
  ├ 弱覆盖               条件:RelRsrp < -120                               ← CHR 0 命中 → 不成立
  └ License受限          条件:DBG日志 CU_UEM_LOG_CODE(0x133D)              ← 无 DBG 数据源 → 未绑定
```
数据:`data/chr.csv`(ErrType/RelRsrp/RelSinr)、`data/kpi.csv`(只有成功率,无 N.QosFlow 原始计数)。
脚本:`scripts/chr_analyzer.py`、`scripts/kpi_analyzer.py`(模型填好的,**每条带 node_id**;`干扰导致` 用 D 组合)。

## 跑
```bash
python skills/cs-scot-execute/scripts/runner.py --plan examples/compression-demo/plan.json \
  --data chr=$PWD/examples/compression-demo/data/chr.csv \
  --data kpi=$PWD/examples/compression-demo/data/kpi.csv
```

## 你会看到
runner 打印 **「已用局部子树:保留 4/5 节点;未覆盖条件节点 1」**,并把 `result/rca_input.json` 写成**局部子树**(不是整棵 md):
- `fault_tree.pruned_nodes`:接入失败示例(过路)、QoS建立失败(挂 `数据不足` 事实)、干扰导致(挂 `成立` 事实)、弱覆盖(挂 `不成立` 事实);**License受限被剪掉**(无事实),但进了 `tree_issues.uncovered_condition_nodes`。
- 没有 `fault_tree_md`(整树没进上下文)。

`result/rca.json` 是一份**示范产出**(展示终态,真实由 RCA 模型生成):
- `rca_result: []` —— 没有确证根因(每条路径都卡在 `QoS建立失败` 数据不足)。
- `probable_causes: [干扰导致]` —— 叶子证据强,但上游数据不足 → **疑似**(带 `confidence` + `caveats` 含有效样本量)。
- `弱覆盖` —— `不成立`,**排除**(不报)。
- `tree_issues` —— `License受限` 无数据源、未覆盖(给人补数据/改树的线索)。

跑校验应通过(疑似含"数据不足"节点是允许的;无"不成立"混入):
```bash
python skills/fault-tree-rca/scripts/validate_rca.py examples/compression-demo/result/rca.json
```

## 要点
- **整树 → 4 节点局部子树**:上下文小了、RCA 更快更稳。
- **强证据不丢也不夸大**:干扰导致进"疑似"而非硬报根因,也不为了凑 confirmed 编节点。
- **node_id 权威**:RCA 只能引用真实节点;填错会进 `orphan_node_ids`。
- 真实产物里 `condition_kernels.py` 会被拷到 `scripts/` 同目录;本示例为免重复从 generate 技能导入。
