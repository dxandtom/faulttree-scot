# 逐条件绑定契约——generate 时填进 `<ind>_analyzer.py` 的 `CONDITIONS`

诊断的"会变"部分**只在这里**:generate 时模型看**故障树条件 + 各源真实表头/样本**(先用 `analyzer.py` 出概览),
为每条判据写一个**绑定**:选哪个**类别内核**(A–J,统计逻辑已在 `condition_kernels.py` 固化测过)、对哪列、
做什么处理变换、路由到哪个源、阈值多少;**对不上就老实标数据不足**。绑定平铺进对应源脚本的 `CONDITIONS` 列表。

脚本跑起来对每条 `evaluate_binding(绑定, {源:表}, 源)` → 事实(算出的量 + provenance + `predicate_satisfied` 的**字面**比较);
`成立/不成立/数据不足` 的**最终裁决 + 路径回溯 + 语义是否真等(RSRP≠RSSI 要降置信)** 仍由 `fault-tree-rca` 做。

**默认套模板;模板不够用**(如"同列极差"这种内核没有的算法)**才给该条加 `"mode":"custom"`,在脚本 `CUSTOM` 里按 id 写返回 fact 的函数**——会在 generate 步骤3 拿真实数据真跑、出错即暴露。

## 一条绑定的公共字段
| 字段 | 说明 |
|---|---|
| `id` | 条件标识(如树里的行号/节点名) |
| `node` | 故障树节点名 |
| `condition_text` | **逐字**取自故障树的判断条件(给 RCA 复核) |
| `class` | `A`–`J`,决定用哪个内核 |
| `source` | 数据源逻辑名(对应 `--data <name>=<file>`);J 类可为 null |
| `mapping_reason` | **为何这么对/语义或单位差异/为何判数据不足**(必写,可审) |
| `decision`(可选) | 模型**已判本条无法机械评估**时填 `"insufficient_data"`(或 `"manual"`):直接出该状态、**不跑内核、不给判断**。**比写一堆 has/lacks/column 再在 reason 里说"数据不足"既更诚实、又更快、又更短。** |
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
| **H** | 信令流程 | `msg_column`,`key`(会话/callid),`has`:[消息…],`lacks`:[消息…],`where`:{column,equals/contains}(或其列表;同一行须满足全部,表达"某消息**且携带** cause=X") | `matched_sessions`;需 trace;无→数据不足 |
| **I** | 特征分析 | `feature`/`column`,`metric`(`row_threshold`/`all_same`/`all_null`),`op`,`threshold`,`transforms` | TA 距离 `156*TA/2`=`scale:78`;`/100`=`scale:0.01` |
| **J** | 人工确认 | `note` | `status:"manual"`,不给判断 |

## 处理变换词汇(`transforms`,固定可审,别写任意代码)
`scale:<f>`(乘,如 `/100`→`scale:0.01`)、`abs`、`neg`、`round:<n>`。
**组前缀**(`Common:`/`Voice:`/`Interference:`)由列解析器**机械剥离**(按首个冒号),绑定里 `column` 写**裸名**即可命中。

## 列解析(机械,绑定写错也不乱配)
精确 → 剥组前缀后精确 → 归一化(去前缀/空格/大小写)唯一 → 嵌套路径(`A->B->C`)取末段。
**解析不到或有歧义 → 数据不足,`resolved_column:null`,绝不挑个相近列顶替。**

## 多页签 xlsx(如信令)
一个数据文件多个页签(NG/UU/汇总…,schema 各异)。**`source` 字段填页签名**即指到该页签;不填=活动页。
- 先 `python analyzer.py <数据> -o x.json`(多页签会列出每个页签的列),再 `--sheet <名>` 看某页签详情;
- 条件/组合子条件各自 `source` 到自己的页签,脚本会按需加载;同一文件跨页签的组合在本脚本内即可算。

## 只绑用得到的列(控制给 RCA 的数据量)
**只为故障树真正点到的判据建 `CONDITIONS`、只绑它要的那一列**;表里其它无关字段**别**搬进来。
生成脚本只算 `CONDITIONS`,所以 RCA 收到的是"每条判据一小撮数字"、而非整张表——
这正是把统计放进生成脚本的好处:模型读树+表头时就把用不到的字段筛掉了,RCA 不会被海量数据淹没。

## 三条诚实铁律(机械层先守,RCA 再复核)
1. **找不到列/缺原始计数/无对应数据源 → `status:"insufficient_data"` + `reason`**,绝不给 `predicate_satisfied`、绝不编值。
2. **NIL/空 = 缺测**,计入 `nulls`,**不当"不成立"、不当 0**。
3. **路由要诚实**:树的量在别的源(如 RSRP 在 CHR 不在 KPI)→ 写清 `mapping_reason` 并标数据不足/换源,**不拿本源相近列顶**(别重蹈 `SINR→语音用户数`)。

> 真实例子见 `examples/kpi-tabular/scripts/kpi_analyzer.py`(填好的 `CONDITIONS` + 一条 `custom`)。
> 跨源组合:把各合取项填进**各自源**的脚本,AND/OR 留给 RCA 合(单脚本只见一个源)。
