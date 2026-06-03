# 逐条件绑定契约(`conditions.json`)——LLM 每次运行产出

诊断的"会变"部分**只在这里**:运行时 LLM 看**故障树条件 + 各数据源的真实表头/样本**,为每条条件产出一个**绑定**:
选哪个**类别内核**(A–J,统计逻辑已在 `scripts/condition_kernels.py` 固化测过)、对哪列、做什么处理变换、
路由到哪个数据源、阈值多少;**对不上就老实标数据不足**。然后:
```bash
python scripts/eval_conditions.py --conditions conditions.json --data kpi=k.csv --data chr=c.csv -o condition_facts.json
python scripts/validate_conditions.py condition_facts.json    # 机械自检:揪"没解析到列却给判断"
```
内核只产**事实**(算出的量 + provenance + `predicate_satisfied` 的**字面**比较);
`成立/不成立/数据不足` 的**最终裁决 + 路径回溯 + 语义是否真等(如 RSRP≠RSSI 要降置信)** 仍由 `fault-tree-rca` 做。

## 顶层结构
```json
{ "scenario": "场景名",
  "conditions": [ { 一条绑定 }, ... ] }
```

## 一条绑定的公共字段
| 字段 | 说明 |
|---|---|
| `id` | 条件标识(如树里的行号/节点名) |
| `node` | 故障树节点名 |
| `condition_text` | **逐字**取自故障树的判断条件(给 RCA 复核) |
| `class` | `A`–`J`,决定用哪个内核 |
| `source` | 数据源逻辑名(对应 `--data <name>=<file>`);J 类可为 null |
| `mapping_reason` | **为何这么对/语义或单位差异/为何判数据不足**(必写,可审) |
| …类参数… | 见下表,直接平铺在本对象上 |

## 类别 → 内核参数
| 类 | 名称 | 关键参数 | 产出事实 |
|---|---|---|---|
| **A** | 阈值比较 | `column`,`op`(`< <= > >= == !=`),`threshold`,`scope`(`aggregate`/`count_rows`),`agg`(mean/min/max/last/sum),`transforms` | aggregate 给 `value`;count_rows 给 `count_satisfied`/`ratio` |
| **B** | 差值计算 | `minuend`:[列…],`subtrahend`:[列…],`op`,`threshold`,`scope`,`transforms` | `value`=Σ被减−Σ减;**缺任一原始计数列→数据不足** |
| **C** | 字段匹配 | `column`,`equals`:[值…] 或 `contains`:[子串…],`match`,`case_insensitive`,`require`(默认1) | `matched` 命中行数 + 样例 |
| **D** | 多条件组合 | `combine`(`AND`/`OR`),`sub`:[子绑定…](每个子绑定自带 `class`,可自带 `source`) | AND:有子False→False、有数据不足→数据不足、否则True;OR 对称 |
| **E** | 占比/TopN | `group_by`,`filter`:{column,equals/contains},`op`,`threshold` | `top1`/`top1_ratio` |
| **F** | 配置检查 | `param`,`equals` | 需配置数据源;无→数据不足 |
| **G** | 日志匹配 | `column`(日志文本列),`contains`:[码/串…] | 同 C(子串) |
| **H** | 信令流程 | `msg_column`,`key`(会话/callid),`has`:[消息…],`lacks`:[消息…] | 需 trace 数据源;无→数据不足 |
| **I** | 特征分析 | `feature`/`column`,`metric`(`row_threshold`/`all_same`/`all_null`),`op`,`threshold`,`transforms` | TA 距离 `156*TA/2`=`scale:78`;`/100`=`scale:0.01` |
| **J** | 人工确认 | `note` | `status:"manual"`,不给判断 |

## 处理变换词汇(`transforms`,固定可审,别写任意代码)
`scale:<f>`(乘,如 `/100`→`scale:0.01`)、`abs`、`neg`、`round:<n>`。
**组前缀**(`Common:`/`Voice:`/`Interference:`)由列解析器**机械剥离**(按首个冒号),绑定里 `column` 写**裸名**即可命中。

## 列解析(机械,绑定写错也不乱配)
精确 → 剥组前缀后精确 → 归一化(去前缀/空格/大小写)唯一 → 嵌套路径(`A->B->C`)取末段。
**解析不到或有歧义 → 数据不足,`resolved_column:null`,绝不挑个相近列顶替。**

## 三条诚实铁律(机械层先守,RCA 再复核)
1. **找不到列/缺原始计数/无对应数据源 → `status:"insufficient_data"` + `reason`**,绝不给 `predicate_satisfied`、绝不编值。
2. **NIL/空 = 缺测**,计入 `nulls`,**不当"不成立"、不当 0**。
3. **路由要诚实**:树的量在别的源(如 RSRP 在 CHR 不在 KPI)→ 写清 `mapping_reason` 并标数据不足/换源,**不拿本源相近列顶**(别重蹈 `SINR→语音用户数`)。

> 真实例子见 `examples/kpi-tabular/conditions.json` + 跑出来的 `condition_facts.json`。
