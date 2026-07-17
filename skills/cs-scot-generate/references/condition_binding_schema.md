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
| `decision`(可选) | 模型**已判本条无法机械评估**时填 `"insufficient_data"`(或 `"manual"`;也认 `"not_applicable"`,按数据不足出):直接出该状态、**不跑内核、不给判断**。**比写一堆 has/lacks/column 再在 reason 里说"数据不足"既更诚实、又更快、又更短。** |
| `filter`(可选) | **行级预过滤**,给「EAV 窄表」(一行一个量,如 `DataItem/DataValue/Unit` 形状)用:先把行缩到命中子集、内核再对子集算。子句 `{column, equals:值\|[值…]}` 或 `{column, contains:…}`,list=AND。**只作用于 A/B/C/F/G/I**(E 有内建 `filter`、H 有内建 `where`,语义不变);过滤后 0 行=该指标项本次未采集 → 数据不足(不是"不成立")。 |
| …类参数… | 见下表,直接平铺在本对象上 |

## 类别 → 内核参数
| 类 | 名称 | 关键参数 | 产出事实 |
|---|---|---|---|
| **A** | 阈值比较 | `column`,`op`(`< <= > >= == !=`,`=`同`==`),`threshold`,`scope`(`aggregate`/`count_rows`),`agg`(mean/min/max/first/last/sum),`transforms` | aggregate 给 `value`;count_rows 给 `count_satisfied`/`ratio` |
| **B** | 差值计算 | `minuend`:[列…],`subtrahend`:[列…],`op`,`threshold`,`scope`,`transforms` | `value`=Σ被减−Σ减;**缺任一原始计数列→数据不足** |
| **C** | 字段匹配 | `column`,`equals`:[值…] 或 `contains`:[子串…],`match`,`case_insensitive`,`require`(默认1);`absent:true`=负存在(命中**0行**才算故障成立,与 `require` 互斥;列全空→数据不足) | `matched` 命中行数 + 样例 |
| **D** | 多条件组合 | `combine`(`AND`/`OR`),`sub`:[子绑定…](每个子绑定自带 `class`,可自带 `source`) | AND:有子False→False、有数据不足→数据不足、否则True;OR 对称 |
| **E** | 占比/TopN | `group_by`,`filter`:{column,equals/contains},`op`,`threshold` | `top1`/`top1_ratio` |
| **F** | 配置检查 | `param`,`equals`;或 `not_equals`/`negate:true`(故障=参数**≠**期望值) | 需配置数据源;无→数据不足 |
| **G** | 日志匹配 | `column`(日志文本列),`contains`:[码/串…] | 同 C(子串) |
| **H** | 信令流程 | `msg_column`,`key`(会话/callid),`has`:[消息…],`lacks`:[消息…],`where`:{column,equals/contains}(或其列表;同一行须满足全部,表达"某消息**且携带** cause=X") | `matched_sessions`;需 trace;无→数据不足 |
| **I** | 特征分析 | `feature`/`column`,`metric`(`row_threshold`/`all_same`/`all_null`),`op`,`threshold`,`require`(默认1),`transforms` | TA 距离 `156*TA/2`=`scale:78`;`/100`=`scale:0.01` |
| **J** | 人工确认 | `note` | `status:"manual"`,不给判断 |

> **C/G 的 `contains` 三条铁规**(违者=假阳/假阴,validate 只能软告警,规矩在这就得守):
> ① **每个 target 都必须独立是"判据成立的充分信号"**(任一命中→判据确实成立):多个等价错误码/告警名
> 的不同写法=合法 OR;**字段标签(`CRC:`/`Rx Power`)、命令名、协议名(`LLDP`/`BFD`)、章节标题=禁**——
> 命中了也不代表成立,而 contains 是任一命中即命中,一个泛词毒整条。超短 ASCII 串(≤3字符)几乎必踩
> (`"NP"` 是 `"snmp"` 的子串)。
> ② **值否定/为空语义禁 G 捞子串**("字段为空/查不到/未建立/状态非UP"):标签存在≠字段为空,文本捞子串
> 表达不了否定——**数据有携带极性的状态枚举列(值如 `Down`/`FAILED`)→ 直绑 `equals` 或 F 类 `not_equals`
> 最轻**;没有才写 custom(抽值判空)或 `decision:insufficient_data`。
> **但"负存在"是另一回事**("**没有配置X/未启用X**"=判某行内容整个不存在):这可判,**缺行就是证据**——
> 绑 C/G + `absent:true`(命中0行=故障成立;列全空时内核改判数据不足,不会把"没导出"误判成"没配置");
> "配了A但没配B"= D 组合(AND)+ 存在子条件 + `absent` 子条件。**禁止以"无法确认不存在"为由投
> `insufficient_data`**——配置源在场时那不是数据不足,是把确定证据白扔了(实测因此漏诊过根因)。
> **字面模板(照抄字段名——子条件列表叫 `sub`、目标叫 `contains` 列表;`sub_conditions`/`target`
> 等野字段内核不认,实测有整条因此变数据不足的)**:
> ```python
> {"class": "D", "combine": "AND", "sub": [
>     {"class": "G", "column": "level2", "contains": ["preempt-mode timer delay"]},
>     {"class": "G", "column": "level2", "contains": ["vrrp recover-delay"], "absent": True}]}
> ```
> 两个子条件长得像、极性相反:第一条**不带** absent=命中≥1行才成立(要求"配了");
> 第二条**带** `absent`=命中0行才成立(要求"没配")。
> ③ **趋势语义(增长/震荡/刷新)看有没有现成检测码**:判据给出了对应检测告警/日志码(如
> `hwIfMonitorCrcErrorRising`,设备自己已判过"增长")→ 绑告警名合法;要人比较两次数值的 → 单快照数据
> 判不了,`decision:insufficient_data`(注明"需两次采样对比")。

> **F 类极性**:让 `predicate_satisfied` 永远表示"**故障成立**"。树写"参数=坏值才算故障"→ `equals:坏值`;树写"参数**≠**好值才算故障"(如 `SsbPeriod≠20ms`、`TxRxMode≠64T64R`)→ 用 `not_equals:好值`(或 `negate:true`+`equals`),**别绑好值再靠 `mapping_reason` 反推**(那样 `predicate_satisfied` 含义反了,RCA 一不留神就判错)。
>
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

## 要全覆盖:**遍历每个叶子、树驱动**绑(别只绑"这次数据里出现的值")
上一节说"别多绑",这节说"**别少绑**"——两者合起来 = **本源能答的叶子,一个不多、一个不少**。

- **❌ 数据驱动(最常见的欠绑定病根)**:翻这次数据里 `DCInnerCause`/`SgNBAddRejCause` **出现了哪些值**,看到一个挂一个,没出现的叶子就不写。后果:① 一整支故障(码这次没出现)被**静默漏诊**;② 脚本**过拟合本次数据**,换份数据查不到别的码——违背"一次生成、反复执行"。
- **✅ 树驱动**:把 `tree_index.json` 里**本源能答的每个叶子都走一遍**,按**树条件要它查的列+值**绑一条(C 类 `contains` 那个码),**不管这次数据里出没出现**。判据依据是"树这个叶子要在 `<列>` 里找 `<值>`",**不是**"这次出现了 N 次"。
- **`mapping_reason` 里不许只拿"出现 N 次"当绑定理由**——出现次数是**运行时结果**,不是绑定依据。写成"树要 `DCInnerCause` 含 `<码>` → C 绑该列",才是格式+语义、可反复执行。
- **值这次没出现 ≠ 不绑、≠ 数据不足**:列在本源就照绑,运行时 `matched=0` = **确定排除**(对 RCA 比"压根没绑/数据不足"有用得多)。**`insufficient_data` 只给"查不了"(列/源不在本源),不给"查了=0"。**
- **机械兜底(第3步必跑)**:`check_tree_coverage.py <tree_index.json> --analyzer <ind>_analyzer.py --columns "<本源列名,逗号分隔>"`——它**树驱动**列出每个没被任何绑定认领的叶子;**漏绑(条件正文提到已喂列却没绑)= 不合格,回去补**;"整支未触达/缺源"逐支确认是真没喂对应数据(信令/配置)还是漏判一整支。

## 生成要泛化:按"格式+语义"绑,别被这次的具体数据带偏
生成脚本是**一次生成、同场景反复执行**(只换输入数据)。所以绑定只该取决于**数据格式**(有哪些列、量纲/口径)+ **条件语义**,**不该被"这次数据碰巧缺哪列/值多少"带偏**。

**✅ 合法重述(该用,不是代理)**:节点级"**整体有没有失败**"(`某流程 Att−Succ>0`)→ 若本源有**该流程自己、且同事件**的成功率列,**直接用它**(A 类,`op <`,阈值=100 或按量纲)——这只是把"差值>0"换成代数等价的"成功率<100",**同一个量、同一件事**。例:`RRC建立失败 ↔ SA_RRC Setup Success Rate`、`NgSig建立失败 ↔ SA_NgSig Setup Success Rate`、`QosFlow建立失败 ↔ SA_QosFlow Setup Success Rate`。
> **怎么算"同一件事":看该列度量什么(建立 vs 掉线、哪个流程),别看名字。** 反例:`上下文建立失败(N.UECntx.Est.Att−Succ>0)` 想要 UEContext 的**建立**成功率,但本源只有 `SA UEContext Drop Rate`(**掉线**率 = 建立成功**之后**才发生,异于建立失败)→ **仍 `insufficient_data`**。名字像 ≠ 同一件事。
> **机械自检(绑率前两条都过)**:① 计数器流程段 vs 列名流程词**逐字相同**(`N.UECntx`→UEContext、`N.RRC`→RRC、`N.NGSig`→NgSig、`N.QosFlow.Est`→QosFlow);② 计数器**不含** `.FailEst.`/`.Fail<原因>`(含=特定子因)。任一不过 → `insufficient_data`(或 C 类查错误码字段)。

**❌ 代理(禁)**,只指这三种:
- **拿通用/异流程的率去顶"特定失败子因"**:`N.QosFlow.FailEst.RNL>0` / 安全模式失败 / PUCCH失败 / AMF失败… 是**专属子原因**,总成功率分不出来(成功率<100% ≠ "是 RNL"),对不上就 `insufficient_data`,**绝不**拿 `SA_RRC Setup Success Rate` 之类去凑。
- **把多条不同判据塞进同一列**:语义错 + 条件恒成立 + 丢泛化。一条判据对不上它**专属**的量,就数据不足。
- **树要的原始计数器不在本源列里**(如某 `N.xxx` 不在 KPI 导出、又无同事件成功率可重述)→ `decision:insufficient_data`(格式事实、每次都缺、泛化成立),或**路由到有它的源**。

> **一把通用尺子(任何类、任何场景都适用,不绑某网元/接口):一条绑定若在「正常样本」上也几乎恒为真,它就分不出故障——是代理,不是证据。** 上面三种之外最隐蔽的一种:**拿某分类列里占比畸高(≥90%、近乎恒在)的取值去顶一个特定子因/信号**——正常数据里也是这个值,它压根不区分故障与正常。要坐实特定子因,得对上它**专属**的列/取值;对不上、或本源根本不含那个信号(哪怕你已确知「本源无此信号」,也别拿相近的高频值顶),就老实 `insufficient_data`,别用高频通用值冒充成立。

- **允许的"看数据"只到格式层**:看列名、看量纲(0–100 还是 0–1 → 定阈值/`scale`)、看哪些列在。**不允许**因某列恰好缺失就改判据语义。**也不允许在绑定层判"某值存不存在"**——概览是截断的(带「词汇不完整」标注的列尤其),概览里没看到 ≠ 数据里没有;"有没有"由内核运行时数出来(见下方诚实铁律 1 的"查不了 vs 查了=0")。
- 一句话:**同流程同事件的率 = 重述(该用);异流程/通用率顶特定子因 = 代理(禁);对不上就老实数据不足。**

## 三条诚实铁律(机械层先守,RCA 再复核)
1. **找不到列 / 无对应数据源,或缺原始计数又无同事件成功率可重述 → `status:"insufficient_data"` + `reason`**,绝不给 `predicate_satisfied`、绝不编值。
   > **分清"查不了"和"查了=0"**:列**在本源**、只是树要的那个**取值这次没出现** → 仍写 C 绑定(`matched` 自然=0,= 确定排除),**不是** `insufficient_data`。`insufficient_data` 只留给"列/源根本不在本源"。把"值没出现"当数据不足 = 白白把一个确定的"不是它"降级成"说不清"。
2. **NIL/空 = 缺测**,计入 `nulls`,**不当"不成立"、不当 0**。
3. **路由要诚实**:树的量在别的源(如 RSRP 在 CHR 不在 KPI)→ 写清 `mapping_reason` 并标数据不足/换源,**不拿本源相近列顶**(别重蹈 `SINR→语音用户数`)。

## EAV 窄表(一行一个量)与空表的语义
- **EAV 窄表**:有的源不是"一列一个量"的宽表,而是「指标名列 + 取值列」的窄表(如数通 diag 的
  `DataItem`(282 种指标名)/`DataValue`/`Unit`)。绑法:`filter` 先选中"哪个量"的行,`column` 指向取值列。
  例:「CPU 占用率>80%」→ `{"class":"A","filter":{"column":"DataItem","equals":"SYS_CpuUsage"},
  "column":"DataValue","op":">","threshold":80,"scope":"count_rows"}`。
- **空表(仅表头、0 行)→ 一律「数据不足」**(所有读表类),**filter 命中 0 行 → 「数据不足」**
  (A/B/C/F/G/I),内核自动兜住——没数据≠排除,别让 `matched=0/0` 冒充"确定不成立"。
  (非空表上 `matched=0` 仍是**确定排除**,语义不变。)
- **E 类例外**:E 的内建 `filter` 语义独立——只接受**单个子句**(list 会被拦成数据不足)、
  **大小写敏感**、命中 0 行按"排除"算。EAV 场景要占比/TopN 时,优先把行选窄的活交给 A/C+filter。
- **D/H 顶层不吃 filter**(响亮报数据不足,不静默忽略):D 把 filter 写进各 `sub` 子绑定;H 用内建 `where`。
- **回显计数器红线**:「回显里出现某告警名」可用 G(告警名只在发生时出现);「回显里某计数器>阈值」
  **禁用 G**——`CRC:` 这类标签恒在、值为 0 也会子串命中(假阳),必须 custom 用 `mo_config.extract_metric`
  抽出数值再比较。详见 `references/datacom_log_adapter.md`。

## 一个节点多条判据怎么绑
- **同源多条(尤其 AND)**:**优先 D 类**组合成一条绑定(AND/OR 语义由内核合成:有假即假、有缺即缺)。
  拆成多条独立绑定虽也支持(同 node_id 全挂上),但辩论通道对多条独立事实按"任一成立即成立"塌缩,
  AND 节点会被高估;D 类的 `predicate_satisfied` 本身就是合成结论,无此问题。
- **跨源组合**:把各合取项填进**各自源**的脚本(同 node_id),AND/OR 留给 RCA 合(单脚本只见一个源)。
- 覆盖闸门(check_tree_coverage)是**节点级**的:它保证每个叶子至少被认领,但"一个节点的第 2、3 条
  判据漏没漏"没有机械闸门——多判据节点用 D 类,就把"绑没绑全"收敛回了节点级,闸门看得住。

> 真实例子见 `examples/kpi-tabular/scripts/kpi_analyzer.py`(填好的 `CONDITIONS` + 一条 `custom`)。
> **接入类「配置拓扑」场景**(NSA 的 S1/X2 链路、路由、EPGroup、多运营商等):配置常是 `MOName:key=val,...;` **文本**(非表格)→ 用 `.txt` 直接喂、每个 MO 类型当**页签**、`source=<MO类型>` 套 F/A/C;模板搞不定的(IP 网段匹配、跨 MO 追溯、分号多值、动态表头)写 custom + 用 `mo_config` 原语。**详见 `references/nsa_config_adapter.md`(4 个可照抄的 custom 例子)。**
> **数通「诊断文件包」场景**(路由器/交换机的 cmd/diag/config/dblg/log/opr/bas 一包 CSV,混血格式故障树·统一走 v2):
> 7 类文件的内核对号表、EAV 窄表 `filter` 绑法、回显计数器红线、空表语义——**详见 `references/datacom_log_adapter.md`。**
