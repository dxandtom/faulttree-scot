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
| `node_id` | 该节点在 `tree/<场景>.tree_index.json` 里的 **key(形如 `nN`,短、推荐)或 `code`**(两者都认)。**必填**——RCA 靠它把事实挂回树;**别填节点名**;填错/不存在会被当"孤儿"报出来 |
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
| **I** | 特征分析 | `feature`/`column`,`metric`(`row_threshold`/`all_same`/`all_null`),`op`,`threshold`,`require`(默认1),`transforms` | TA 距离 `156*TA/2`=`scale:78`;`/100`=`scale:0.01` |
| **J** | 人工确认 | `note` | `status:"manual"`,不给判断 |

> **标签 = 路由分类,不是 N 个互斥实现**:A–J 对齐的是**故障树的说话方式**(树写"日志"→G、"配置"→F、"特征"→I),方便选型;底层原语共享——**G 就是 C**(同一匹配内核)、F≈C 的"全等"版、I 的 `row_threshold`≈A 的 `count_rows`。所以遇到"日志/配置/特征"别硬塞进 C/A,**按树的措辞选标签**;也别去"合并去重"它们,标签稳定才好绑。`require`:A/B/I 的 `count_rows`/`row_threshold` 统一为"满足行数 ≥ require 才算成立"(默认1)。

## 处理变换词汇(`transforms`,固定可审,别写任意代码)
`scale:<f>`(乘,如 `/100`→`scale:0.01`)、`abs`、`neg`、`round:<n>`、`log10`(量级/对数标度条件用;x≤0 无对数 → 该值当缺测/null,不编值)。
**组前缀**(`Common:`/`Voice:`/`Interference:`)由列解析器**机械剥离**(按首个冒号),绑定里 `column` 写**裸名**即可命中。

## 列解析(机械,绑定写错也不乱配)
精确 → 剥组前缀后精确 → 归一化(去前缀/空格/大小写)唯一 → 嵌套路径(`A->B->C`)取末段。
**解析不到或有歧义 → 数据不足,`resolved_column:null`,绝不挑个相近列顶替。**

## A 的 `agg` 选择(避免"逐周期率"被无加权平均带偏)
`agg:mean` 是**无加权**平均:对"逐小时成功率"列求 mean ≠ 总体率(各小时样本量不同)。
**树条件多半要的是"存在劣化时段"** → 优先 `scope:count_rows`(数有几个周期越线)而非 `aggregate+mean`;
确需整体水平再用 `mean`,并知道它没按样本量加权(内核不持有原始样本量,无法加权)。

## 多页签 xlsx(如信令)
一个数据文件多个页签(NG/UU/汇总…,schema 各异)。**`source` 字段填页签名**即指到该页签;不填=活动页。
- 先 `python analyzer.py <数据> -o x.json`(多页签会列出每个页签的列),再 `--sheet <名>` 看某页签详情;
- 条件/组合子条件各自 `source` 到自己的页签,脚本会按需加载;同一文件跨页签的组合在本脚本内即可算。

## 只绑用得到的列(控制给 RCA 的数据量)
**只为故障树真正点到的判据建 `CONDITIONS`、只绑它要的那一列**;表里其它无关字段**别**搬进来。
生成脚本只算 `CONDITIONS`,所以 RCA 收到的是"每条判据一小撮数字"、而非整张表——
这正是把统计放进生成脚本的好处:模型读树+表头时就把用不到的字段筛掉了,RCA 不会被海量数据淹没。

## 生成要泛化:按"格式+语义"绑,别被这次的具体数据带偏
生成脚本是**一次生成、同场景反复执行**(只换输入数据)。所以绑定只该取决于**数据格式**(有哪些列、量纲/口径)+ **条件语义**,**不该被"这次数据碰巧缺哪列/值多少"带偏**。

**✅ 合法重述(该用,不是代理)**:节点级"**整体有没有失败**"(`某流程 Att−Succ>0`)→ 若本源有**该流程自己、且同事件**的成功率列,**直接用它**(A 类,`op <`,阈值=100 或按量纲)——这只是把"差值>0"换成代数等价的"成功率<100",**同一个量、同一件事**。例:`RRC建立失败 ↔ SA_RRC Setup Success Rate`、`NgSig建立失败 ↔ SA_NgSig Setup Success Rate`、`QosFlow建立失败 ↔ SA_QosFlow Setup Success Rate`。
> **怎么算"同一件事":看该列度量什么(建立 vs 掉线、哪个流程),别看名字。** 反例:`上下文建立失败(N.UECntx.Est.Att−Succ>0)` 想要 UEContext 的**建立**成功率,但本源只有 `SA UEContext Drop Rate`(**掉线**率 = 建立成功**之后**才发生,异于建立失败)→ **仍 `insufficient_data`**。名字像 ≠ 同一件事。
> **机械自检(绑率前两条都过)**:① 计数器流程段 vs 列名流程词**逐字相同**(`N.UECntx`→UEContext、`N.RRC`→RRC、`N.NGSig`→NgSig、`N.QosFlow.Est`→QosFlow);② 计数器**不含** `.FailEst.`/`.Fail<原因>`(含=特定子因)。任一不过 → `insufficient_data`(或 C 类查错误码字段)。

**❌ 代理(禁)**,只指这三种:
- **拿通用/异流程的率去顶"特定失败子因"**:`N.QosFlow.FailEst.RNL>0` / 安全模式失败 / PUCCH失败 / AMF失败… 是**专属子原因**,总成功率分不出来(成功率<100% ≠ "是 RNL"),对不上就 `insufficient_data`,**绝不**拿 `SA_RRC Setup Success Rate` 之类去凑。
- **把多条不同判据塞进同一列**:语义错 + 条件恒成立 + 丢泛化。一条判据对不上它**专属**的量,就数据不足。
- **树要的原始计数器不在本源列里**(如某 `N.xxx` 不在 KPI 导出、又无同事件成功率可重述)→ `decision:insufficient_data`(格式事实、每次都缺、泛化成立),或**路由到有它的源**。

- **允许的"看数据"只到格式层**:看列名、看量纲(0–100 还是 0–1 → 定阈值/`scale`)、看哪些列在。**不允许**因某列恰好缺失就改判据语义。
- 一句话:**同流程同事件的率 = 重述(该用);异流程/通用率顶特定子因 = 代理(禁);对不上就老实数据不足。**

## 三条诚实铁律(机械层先守,RCA 再复核)
1. **找不到列 / 无对应数据源,或缺原始计数又无同事件成功率可重述 → `status:"insufficient_data"` + `reason`**,绝不给 `predicate_satisfied`、绝不编值。
2. **NIL/空 = 缺测**,计入 `nulls`,**不当"不成立"、不当 0**。
3. **路由要诚实**:树的量在别的源(如 RSRP 在 CHR 不在 KPI)→ 写清 `mapping_reason` 并标数据不足/换源,**不拿本源相近列顶**(别重蹈 `SINR→语音用户数`)。

> 真实例子见 `examples/kpi-tabular/scripts/kpi_analyzer.py`(填好的 `CONDITIONS` + 一条 `custom`)。
> 跨源组合:把各合取项填进**各自源**的脚本,AND/OR 留给 RCA 合(单脚本只见一个源)。
