# 示例:kpiData(真实表头)+ A–J 逐条件绑定/求值

演示运行时流程:**A–J 统计内核已固化**,LLM 看故障树+真实数据产出**绑定** `conditions.json`,
执行器算出**事实**,校验器揪"静默编造",再交 RCA 裁决。

## 文件
- `fixtures/kpiData.csv`:6 行、2 个小区,**真实英文表头(带 `Common:`/`Voice:`/`Interference:` 组前缀)**,取自真实脱敏表头;
  含一个 `NIL`、`N.UL.RSSI.Avg` 取值 `0.01/154.555`(明显非 dBm)。
- `conditions.json`:手写的绑定(模拟 LLM 运行时产出),覆盖 A/B/C/D/E/J 与跨源、缺列、缺计数。
- `tree/示例-接入与干扰.md`:用英文 code 名写的条件(配合 analyzer 的"跨命名投影兜底"演示)。

## 跑(逐条件绑定/求值)
```bash
python skills/cs-scot-execute/scripts/eval_conditions.py \
  --conditions examples/kpi-tabular/conditions.json \
  --data kpi=examples/kpi-tabular/fixtures/kpiData.csv \
  --data chr=examples/sa-access-fail/fixtures/chr.csv \
  -o /tmp/condition_facts.json
python skills/cs-scot-execute/scripts/validate_conditions.py /tmp/condition_facts.json
```

## 预期(8 条:ok=4 / 数据不足=3 / 人工=1,校验通过)
- **第22行-合取1(A)**:`Common:` 被机械剥前缀命中(`stripped_prefix`),均值 0.974 < 0.99 → `predicate_satisfied:true`。
- **第22行(D 组合 AND)**:合取1 true、合取2/3 本表无 → 数据不足 → 整体 `insufficient_data`(**不可只凭合取1判成立**)。
- **第78行 RSRP(A)**:`SUSRLastAvgRsrp` 不在本表 → 数据不足,`resolved_column:null`,**没顶到 `N.UL.RSSI.Avg`**。
- **第9行(B 差值)**:无原始计数 Att/Succ → "无法算差值"数据不足。
- **成功率阈值(A count_rows)**:`Common:` 命中,`nulls:1`(NIL 计缺测),5 行有效里 2 行 <85。
- **C/E/J**:DCErrType 精确匹配 4 命中;TopN 占比 0.5(<0.7→false);人工确认 `manual`。

## 概览事实(analyzer,辅助对列/定阈值)
```bash
python skills/cs-scot-generate/scripts/analyzer.py \
  examples/kpi-tabular/fixtures/kpiData.csv -o /tmp/kpi_overview.json \
  --fault-tree examples/kpi-tabular/tree/示例-接入与干扰.md
```
英文树名 vs 中文/前缀列名 → 子串投影落空 → `kept_all_fallback` 保留全部列;数值列带十分位 + nulls。
