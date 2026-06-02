# 解析引擎契约(`scripts/analyzer.py` 做什么、产出什么)

> 历史上这里是"生成解析脚本的元提示词";现已收敛为**一个通用引擎** `scripts/analyzer.py`,
> 本文档描述它的契约。**不再每场景生成代码**——引擎看数据形态、做对应的事。

## 最高铁律:只查数据 / 抽规则,绝不下判断
引擎只产**事实 / 规则**:绝不输出 `hit`/`judgment`/`conditions`、绝不替你做"故障树匹配"、绝不发明阈值。
是否成立、根因、以及"故障树引用了哪些字段 / 计数器",**全部由 fault-tree-rca 拿着故障树去做**。

## 自动判别数据形态
- **tabular(表格型,如 CHR)**:多列记录。逐列**数据驱动**统计(按"值"判别数值 / 类别,**不看列名**):
  - 数值列 → count/min/max/mean;类别列 → top-N 计数 + distinct + 每个 top 值几个样例标识。
- **catalog(目录型,如 kpiTree)**:单列、每行一段 JSON。当"规则字典":
  - 遍历每行所有键做统计(`field_stats`);
  - 对有 `formula`/阈值的节点抽**规则**:`formula`、`absExceptionThreshold`/`highCondition`/`lowCondition`、`unit`、`definition`,
    并从 `formula` 正则抽 `N./L.` 原始计数器(段内可含 `-`,清洗 `<span`/`&gt;` 污染);
  - 建 `counter_index`:计数器 → 用到它的 KPI(`name`/`kpiIndex`/`formula`/阈值)+ `usage_count`。
  - **注意:kpiTree 记录的是"计算方式与阈值",不是实测值** —— 所以这里**没有计数器取值**。

## 输出 JSON(只有事实 / 规则,无判断)
- **tabular**:`{ "indicator","kind":"tabular","summary":{total_records,columns},"field_stats":{<列>:{...}} }`
- **catalog**:`{ "indicator","kind":"catalog","summary":{total_nodes,levels,rule_count,distinct_counters},`
  `"field_stats":{<键>:{...}},"rules":[{id,name,kpiIndex,formula,counters,absExceptionThreshold,...}],`
  `"counter_index":{"<计数器>":{in_kpis:[{name,kpiIndex,formula,absExceptionThreshold}],usage_count}} }`

通篇**没有** `conditions`/`hit`/`judgment`。

## 给 RCA 的提示(分工)
- **CHR 类**条件(点名某列 / 取值)→ 用 `field_stats` 评估;
- **话统计数器**(`N.xxx`)条件 → `counter_index`/`rules` 给定义 / 阈值 / 公式作背景,但**无实测值 → 如实判"数据不足"**;
- 真实性高于一切:测不到就说测不到,不拿 CHR 分布替代话统阈值。
