# 示例:kpiData(带表头的表)+ 跨命名映射

演示 v1 改版后的真实数据形态:**kpiData 是带表头的表**(中文列名 + 真实值),而故障树条件常用**英文/code 名**——名字对不上是常态。本例刻意复现三个难点。

## 文件
- `fixtures/kpiData.csv`:8 行、2 个小区(CellA 正常 / CellB 异常),中文列名取自真实脱敏图例。
  - 故意**没有 SINR 列**;`SA_QoSFlow建立成功率(22B)` 有一个 `NIL`;`上行RSSI均值` 取值 `0.01/154.555`(明显不是 dBm)。
- `tree/示例-接入与干扰.md`:用英文 code 名写的三条条件(弱覆盖 RSRP、干扰 RSRP+SINR、QoSFlow 成功率)。

## 跑
```bash
python skills/cs-scot-generate/scripts/analyzer.py \
  examples/kpi-tabular/fixtures/kpiData.csv -o /tmp/kpi_facts.json \
  --fault-tree examples/kpi-tabular/tree/示例-接入与干扰.md
```

## 预期(事实层,引擎只摆事实)
- `projection.mode = kept_all_fallback`:树是英文名、列是中文 → 子串投影全落空 → **保留全部列**,映射交给 RCA。
- `SA_QoSFlow建立成功率(22B)`:`nulls:1`(NIL 当缺测,不当类别);十分位 p0=79.5、p30≈84.4 → "<85%" 可判。
- `上行RSSI均值`:十分位 0.01~154.555 → 一眼非 dBm。

## RCA 应当怎么判(判断层,见 `skills/fault-tree-rca/references/rca_rules.md`)
- **弱覆盖 `RSRP < -120`**:数据只有 `上行RSSI均值`(RSSI≠RSRP、且取值非 dBm)→ 映射须标注差异、判**数据不足/低置信**,不可当干净"不成立"。
- **干扰 `... AND SINR < 0`**:**没有 SINR 列** → **数据不足**;**绝不**把 SINR 顶到"语音用户数"(本地模型曾犯此错)。
- **QoSFlow `成功率 < 85%`**:映射到 `SA_QoSFlow建立成功率(22B)`(语义一致)→ CellB 多个时段 <85% 可判**成立**;`NIL` 那行判该行**数据不足**。
