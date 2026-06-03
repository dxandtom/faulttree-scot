# 示例:kpiData(真实表头)+ 生成的逐条件分析脚本

演示最终架构:**generate 时模型为每个源填一份"逐条件分析脚本"(读表→统计),execute 当普通脚本跑**。
默认套 A–J 模板,模板不够用才写 custom。

## 文件
- `fixtures/kpiData.csv`:6 行 2 小区,**真实英文表头(带 `Common:`/`Voice:`/`Interference:` 组前缀)**,含一个 `NIL`、`N.UL.RSSI.Avg` 非 dBm 取值。
- `scripts/kpi_analyzer.py`:模型为「kpi」源**填好的脚本**——3 条套模板(A 聚合 / A 计数 / A 掉线率)+ 1 条 `custom`(「同列极差>10」,模板没有的算法)。
- `tree/示例-接入与干扰.md`:英文 code 名条件(配合概览引擎的"跨命名兜底")。

## 跑(execute 就是这样跑这个脚本)
```bash
python examples/kpi-tabular/scripts/kpi_analyzer.py examples/kpi-tabular/fixtures/kpiData.csv -o /tmp/kpi.json
python skills/cs-scot-generate/scripts/validate_conditions.py /tmp/kpi.json   # 诚实性自检
```

## 预期(4 条全 ok,校验通过)
- **第22行-合取1**(A 聚合):`Common:` 自动剥前缀,均值 0.974 < 0.99 → `true`。
- **QoSFlow建立成功率**(A count_rows):`nulls:1`(NIL 计缺测),5 行有效里 2 行 <85。
- **QoSFlow掉线率**(A 聚合):均值 0.6583 > 0.5 → `true`。
- **上行干扰抬升**(custom):模板无极差算法 → 自定义同列 `max-min=15 > 10` → `true`。

## 填脚本前看真实列(概览引擎)
```bash
python skills/cs-scot-generate/scripts/analyzer.py examples/kpi-tabular/fixtures/kpiData.csv -o /tmp/overview.json
```
英文树名 vs 中文/前缀列名 → 子串投影落空 → `kept_all_fallback` 保留全部列;数值列带十分位 + nulls,供填 `CONDITIONS` 时对列/定阈值。
