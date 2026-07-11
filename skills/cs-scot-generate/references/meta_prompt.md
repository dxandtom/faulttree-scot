# 概览引擎契约(`scripts/analyzer.py` 做什么、产出什么)

> 历史上这里是"生成解析脚本的元提示词",后收敛为一个通用统计引擎;**现行架构里它是"概览引擎"**:
> 主链路的逐条件统计由 assemble 写骨架、模型在步骤 2.5 填 `CONDITIONS` 的 `<ind>_analyzer.py` 完成,
> `analyzer.py` 负责让模型**先看清各源真实列再绑**——步骤 2.5 用 `--lean` 出精简概览(列名/min·max·mean/
> 类别取值),多页签 xlsx 先不带 `--sheet` 看页签清单、再 `--sheet <名> --lean` 看某页。
> 它仍保留全量按表统计形态(十分位/top-N),可独跑摆事实(kpi-tabular 这类旧式产物就直接跑它)。

## 最高铁律:只查数据 / 抽规则,绝不下判断
引擎只产**事实 / 规则**:绝不输出 `hit`/`judgment`/`conditions`、绝不替你做"故障树匹配"、绝不发明阈值。
"树的量 ↔ 数据列"的**语义映射**在 cs-scot-generate 步骤 2.5 绑定时由模型完成并写实 `mapping_reason`(可审);
成立与否的**最终裁决**由 fault-tree-rca 做。本引擎两头都不掺和,只把数据长相如实摆出来。

## 数据都是"带表头的表"(CHR、kpiData 皆然)
逐列**数据驱动**统计(按"值"判别数值 / 类别,**不看列名**):
- **数值列**(>=90% 可转数)→ `count`/`min`/`max`/`mean` + **十分位 `deciles`(p0..p100)** + `nulls`。
  - 十分位让"`RSRP < -120`、`成功率 < 85%`"这类阈值条件可判(RCA 据分位估"多少落在阈值一侧")。
- **类别列** → `top-N` 计数 + `distinct` + 每个 top 值几个样例标识 + `nulls`。
- **`nulls`**:该列空值/缺测条数,**含 `NIL`**(大小写不敏感);NIL 永远当缺测,绝不当一个类别值——
  让"没测到"显式可见,RCA 才不会把缺测误当"正常/不成立"。

## 输出 JSON(只有事实,无判断)
`{ "indicator","kind":"tabular","summary":{total_records,columns,id_column},"field_stats":{<列>:{...}} }`

通篇**没有** `conditions`/`hit`/`judgment`/`mapping`——语义映射在步骤 2.5 绑定时做、裁决在 RCA,都不在这里。

## 按故障树投影(可选,压缩输出)
给 `--fault-tree <md>`(或脚本旁唯一的 `../tree/*.md` → 自动探测)时,尝试只保留**故障树文本子串命中**的列,并加 `projection` 元信息。
- 树写 `DCErrType`、CHR 列也叫 `DCErrType` 时有效,把上百列压到树引用的那几列(`mode:"substring"`)。
- 但树常用英文/code 名(`SUSRLastAvgRsrp`),数据列常为中文(`上行RSSI均值`)——子串会几乎全落空。
  命中过稀(<3 列或 <10%)时**保留全部列**(`mode:"kept_all_fallback"`),字段↔列映射交给 RCA 按语义显式完成,
  **绝不把数据投空、害 RCA 瞎判**。

## 分工(现行)
- 本引擎只摆事实(各列分布 / 数值摘要 + 十分位 + nulls),**不下判断、不做映射**。
- "树的量 ↔ 数据列"映射在 generate 步骤 2.5 由模型**按语义、显式、可审**地写进 CONDITIONS(契约见
  `condition_binding_schema.md`);阈值代入、逐条件统计由 `<ind>_analyzer.py` 跑;最终裁决在 fault-tree-rca。
- 真实性高于一切:**没有就是没有**——树要的列找不到、或单元格是 NIL → 如实"数据不足",绝不抓相关列硬顶。
