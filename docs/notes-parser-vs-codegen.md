# 设计笔记:解析脚本——通用解析器 vs 每场景生成代码(待定)

> 状态:**决策待定**。本笔记只记录分析与建议,代码暂不改(用户:先不动代码)。
> 触发:用真实数据跑了一版「SA场景接入失败」(chr 117 列真实 CHR + kpiTree.csv),
> 观察生成的 `chr_codegen_prompt.md / kpi_codegen_prompt.md / chr_analyzer.py / kpi_analyzer.py`。

## 两个待决问题
1. `referenced`(把"故障树点名的字段"单拎出来)这层,是帮 RCA 减负、还是 LLM 多此一举、反而读错/读少?
2. 有没有必要每场景都生成"指标专属 prompt → 专属代码"?能不能一步到位,用**一个**通用脚本?

## 本次真实运行结果(SA场景接入失败,20,496 条 CHR)
- **最终 `rca.json` 是诚实的**:`rca_result: []` + `undetermined`,理由是树的判据大量依赖话统计数器(N.xxx),而 kpiTree 无实测值 → 无法验证;并且**正确拒绝了近似值**(树要 `DCInnerCause=0x810D176C`,CHR 实际是相近的 `0x810D176B`,判"不成立")。这正是要的真实性。
- **但控制台叙述过度**:同一次还输出了"发现 5 条根因、AMF 为主、无悬置"的表格,把"数据不足"的节点当成了根因——这是我们明令禁止的"硬找根因"。即:落盘 `[]` 是对的,聊天总结过度了。
- RCA 输出还用了非契约 schema(`rca_result`/`undetermined`/`hit=null`,`hit` 是旧措辞)——疑似装的是改造前的旧版 RCA 技能,或模型自行发挥;建议重装匹配版本。

## 现状观察(均有据可查于生成产物)
- **统计内核 100% 通用**,无场景特异逻辑:
  - 表格型(chr):遍历表头 → 每列 `Counter` top-N + 样例 / 或 min·max·mean;
  - 目录型(kpi):逐行 `json.loads` → 遍历每行所有键统计 + 建 `parentId→id` 树。
  - "算法"仅 `collections.Counter` 频次 + min/max/mean,无其它。
- **唯一随场景变的**,是"这棵树引用了哪些 token"——而那是**树的投影(数据)**,不是代码逻辑。

## 现实现的三处害处(`referenced` 由 LLM 拍脑袋生成导致)
1. **LLM curate 还 padding(有铁证)**:`chr_analyzer.py` 的 `referenced_names` 比它自己的 codegen prompt 列的**多出十几个**(`PLMN / gNodeBID / NRCellID / Msg5AckCnt / Msg5NackCnt / UL BLER / DL AVG MCS / DL AVG RANK …`),prompt 未点名,是写代码的 LLM 自加。能多塞→能漏;三级链各级**对不上(drift)**。
2. **kpi 匹配了错误字段 + 模糊**:脚本拿树的 `N.xxx` 去匹 `kpiIndex`/`name`(那是 KPI 标签,如 `CSSR`/`RRC Setup Success Rate`),但**原始计数器其实写在 `formula` 里**(如 `"L.RRC.ConnReq.Att"-"L.RRC.ConnReq.Succ"`)。字段就找错了,又用子串模糊匹配 → 大量错配/漏配。正解(同事 prompt 也是这思路):**从 `formula` 正则抽 `N./L.` 计数器**,再对树计数器算覆盖、收集用到它的 KPI 及其 `formula`/阈值。
3. **无意义字段 / 死代码**:chr `referenced[...]."value" = nums[0] if nums else None`——对类别列取"第一个能转数字的值",无意义且与 `distribution` 重复;kpi 里 `find_kpi_by_name_or_index()` 定义了但从未调用(生成残留)。
4. 另:`is_numeric_col` 用**列名关键词**(rsrp/cnt/time…)猜列类型,会误判(带 `time/cnt` 的列被强行当数值)。应改为**按值**判定(抽样解析成功率),不看列名。

## 建议方向(倾向)
**收敛成一个固定的通用解析器**(测一次、稳定),不再每场景生成代码:
- ① **全字段统计**(全量事实,RCA 兜底);字段类型按"值"判定,不靠列名关键词。
- ② **机械精确投影 `referenced`**:脚本**读故障树**,`N./L.` 计数器用正则抽、chr 字段名用"与真实列名取交集"抽,**精确匹配**;匹不上就老实 `found:false`。**不让 LLM curate、不模糊匹配、不 padding。**
- ③ 自动判别 表格型 / 目录型 两种数据形态(目前真实数据就这两种)。
- `meta_prompt.md` 退化为**文档/契约说明**,不再每跑一次生成代码 → 不会反复长出 padding/模糊匹配/null value。
- 取舍:此前担心"通用 parser 不行";但两份真实代码证明 95% 是同一套通用遍历,真正变的只有"树点名的 token 清单(数据)"。通用脚本 + 树当数据更干净、更可控。

> 备选:保留每场景生成,但硬性禁止 padding、禁止模糊匹配(只精确)、删无意义 value。能止血,但每跑一次仍可能复发。

## 两个数据缺口(不论走哪条路都要处理)
1. **kpiTree = 计算方式/阈值字典,不是实测值(已确认,用户:只有 kpiTree)**:每行核心是 `formula`(KPI 由哪些原始计数器、怎么算)+ `absExceptionThreshold/high/lowCondition`(判异阈值)+ `definition`(含义)。例:`{"formula":"\"L.RRC.ConnReq.Att\"-\"L.RRC.ConnReq.Succ\"","name":"RRC Setup Failure Times"}`。所以 kpi 解析的**正确职责是抽取这套知识**(覆盖率 + formula + 阈值 + 含义),**不是取值**。
   - 真数据证实:本次 kpi `referenced[...].value` 全 `null`;`N.UECntx.FailEst.AMF > 0` 这类**光靠 kpiTree 永远算不了**。
   - **唯一实测数据是 CHR(逐记录)**。好在故障树很多节点**自带 CHR 判据**(如某叶子 `CHR DCInnerCause = 0x810D176C`),这些能直接用 CHR 评估;话统类计数器还可由 CHR **按树给出的语义映射计数**近似(显式、可审计),把"几乎全 undetermined"变成"有 CHR 证据的根因"。
2. **数值阈值条件无法判**:树里有 `RSRP < -120`、`SINR < 0`。脚本只给 min/max/mean,**答不了"有多少条 < -120"**。应让解析器(通用、由树驱动)算出"满足该原始谓词的**记录数/占比**"——这是事实不是判断,RCA 才有得判。

## 进展(已落地)
- **通用引擎**(`scripts/analyzer.py`):自动判别 tabular/catalog,只产事实/规则,删掉三级 codegen 链。已用真实数据跑通(20496 CHR / 5255 kpiTree 节点)。
- **kpi 实测值来源**:用户确认只有 kpiTree(无实测)→ 话统条件如实"数据不足",不搞 CHR 反推计数器。
- **RCA 改造**:① 改成**指标无关**(去掉 CHR/话统/kpiTree 耦合,只剩通用法则"要 X 没 X 就数据不足、绝不拿 Y 顶 X、含数据不足节点的路径不算根因");② 加机械自检 `scripts/validate_rca.py`,揪"无实测却判成立"的自相矛盾 + 假根因(对较弱的运行模型尤其必要——实测 MiniMax 跑出来仍会硬顶)。
- **输出过长**:引擎加**按故障树投影**(`--fault-tree`,或自动探测脚本旁 `../tree/*.md`),只留树提到的字段/计数器/规则;真实 kpiTree 几千条 → 压到个位/十几条。

## 待办 / 观察
- 数值阈值条件(`RSRP < -120`)目前引擎只给 min/max/mean,答不了"多少条 < -120"。若要支持,需让 RCA 端按谓词计数,或引擎在投影时对被点名的数值字段额外给分位/阈值计数(仍是事实,不是判断)。
- `docs/design.md` 仍停在旧的三级链描述,待同步到"通用引擎 + 投影 + 通用 RCA"。

## 更新(v1 改版):kpiTree 是错误数据;真 kpiData 是表;可变只锁在"映射"
> 用户确认:之前的 **kpiTree 是错的数据**(单列 JSON 的"公式字典"),真正要用的是 **kpiData——带表头的表**(58 列:`Time/Object/ObjectType/cellName` + 各 KPI/计数器列,有真实值)。
> 于是数据形态收敛为**只有"带表头的表"**(CHR、kpiData 皆然),catalog 那套作废。

**作废/删除**:`analyzer.py` 里整条 catalog 支路(`_parse_catalog`/`_analyze_catalog`/`_counters_in`/`counter_index`/`_COUNTER_RE`、`_load`/`_project` 的 catalog 分支)——都是给假 kpiTree 写的,已删。引擎纯表格化。

**新发现(决定本版设计)**:真数据里**树的量与数据列名对不上是常态**——树用英文/code 名(`SUSRLastAvgRsrp`、`SA_QosFlow_Setup_Success_Rate`),列是中文(`上行RSSI均值`、`SA_QoSFlow建立成功率(22B)`)。所以:
- 旧的"子串投影"对 kpiData **几乎全落空**(会把数据投空、害 RCA 瞎判)。
- 本地模型实测把映射做错且**自信地编**:`SINR < 0` 被映到"语音用户数"(其实根本没有 SINR 列);`RSRP` 映到 `RSSI`(语义/单位不符)。**映射错误是静默的**:数照样算得出、结论却全错且不报错。

**本版结论:可变的只该是"统计之前的映射/规整",不是统计本身。** 三段:① 装载(固定)② 映射/规整(随数据自适应、按语义、显式可审)③ 统计(固定)。统计模板可枚举、要稳;真正没法预知的是"这份表的列怎么对到树的量",那才交给模型——且**必须显式记录映射+理由,找不到就数据不足,绝不抓相关列硬顶**。

**已落地(v1)**:
- 引擎纯表格 + 数值列加**十分位 `deciles`(p0..p100)**(让 `<阈值` 条件可估)+ **`nulls`**(把 `NIL` 当缺测显式计数,绝不当类别/正常)。`NIL` 已纳入空值集。
- 投影改**跨命名兜底**:子串命中过稀(<3 列或 <10%)→ `mode:"kept_all_fallback"` 保留全部列,映射交给 RCA 语义完成。
- `rca_rules.md` 增"字段映射"硬规则(语义映射、找不到→数据不足、近似要标注降置信、成功率vs差值、NIL=数据不足、阈值出处、用 deciles 判数值阈值);RCA 输出 evidence 增 `mapping`/`mapping_reason`(落盘可审)。
- 新示例 `examples/kpi-tabular/`(kpiData 形态 + 英文 code 名树)跑通:`projection=kept_all_fallback`、成功率列 `nulls:1`、无 SINR 列 → 条件2 应判数据不足。

**待办(v2 候选)**:
- 数值阈值仍是"用 deciles 估比例",非精确计数。若要精确,引入"映射规格(列+op+阈值,带理由/置信)+ 固定执行器"算精确命中数/占比(仍是事实)。
- 旧示例 `examples/sa-access-fail/fixtures/kpi.csv` 是**废弃的假 kpiTree**,仅作历史;真形态以 `examples/kpi-tabular/` 为准。
- `validate_rca.py` 暂未校验"映射编造"(机械难判);靠 `rca_rules.md` 硬规则 + evidence 里 `mapping_reason` 人工/二次复核。

## 更新(v2 落地):A–J 内核模板 + 运行时 LLM 绑定(每次生成)
> 真实故障树条件分 **A–J 十类**(阈值/差值/字段匹配/组合/占比TopN/配置/日志/信令流程/特征分析/人工)。
> E/H/I 几乎每条是定制小程序(嵌套路径、分组键、`156*TA/2`、`/100`、版本分支)——**纯固定模板覆盖不了**;
> 真实 KPI 表头还带 `Common:`/`Voice:`/`Interference:` 组前缀,且很多条件根本不在 KPI 表(在 CHR/trace/配置/日志)。
> 用户拍板:**先每次运行生成**;形态=**预制按类统计模板 + LLM 按具体数据生成绑定/处理**。

**架构(三段,可变只锁在中间)**:① 装载(固定)② **绑定/规整(运行时 LLM 生成)** ③ 统计(A–J 预制内核,固定测过)。
- A–J 内核固化在 `cs-scot-execute/scripts/condition_kernels.py`(把占比TopN/差值/阈值计数/字段匹配这些**易错逻辑**测一次、稳定);
- LLM 运行时只产**绑定** `conditions.json`(选类别内核、对哪列、剥前缀/`scale`变换、路由到哪个源、阈值;对不上标数据不足),契约见 `cs-scot-execute/references/condition_binding_schema.md`;
- `eval_conditions.py` 执行 → `condition_facts.json`(只产事实 + provenance + **字面** `predicate_satisfied`);`validate_conditions.py` 机械揪"没解析到列却给判断";`成立/不成立`最终裁决仍归 RCA。
- **列解析机械化**:精确→剥组前缀→归一化唯一→嵌套末段;解析不到/歧义→`resolved_column:null`+数据不足,**绝不挑相近列顶**(防 `SINR→语音用户数`)。
- **处理变换=固定词汇**(`scale`/`abs`/`neg`/`round`),不放任意代码;真需要再扩词汇或加受控 hook(暂未开)。

**为何不放任意 codegen**:`SINR→语音用户数` 那类**静默错**会从"映射错"变成"生成代码错",更难发现。所以生成物(绑定)落盘可见 + 过 `validate_conditions` + RCA 复核;统计逻辑本身用测过的内核,不每次重写。

**已验证(`examples/kpi-tabular/`)**:真实英文表头(带 `Common:` 前缀)+ 8 条绑定跑通,ok=4/数据不足=3/人工=1,校验通过:
`Common:` 自动剥前缀命中;第22行 D 组合跨源→合取1 true 但合取2/3 缺列→整体诚实数据不足;RSRP 不在本表→数据不足不顶 RSSI;B 差值缺原始计数→数据不足;NIL 计入 nulls 不当不成立。

> **注意:v2 把统计放在 execute 运行时,已被 v3 修正(见下)。**

## 更新(v3 修正):统计放回 generate 生成的脚本;execute 只跑脚本 + 末尾 RCA
> 用户指正:v2 把 A–J 内核 + 绑定 + eval 塞进 execute,职责糊了——execute 该"只执行脚本、直接拿到统计好的数据",
> 统计代码应在 **generate** 里生成。形态 = **预制 A–J 模板 + 模型按数据决定:能直接套就套、模板不够用才在其上改**(折中,逐条件由模型判)。

**最终架构**:
- **generate(编译)**:`condition_kernels.py`(A–J 模板库)+ `analyzer.py`(概览)搬到 `cs-scot-generate/scripts`;assemble 把模板库拷进产物、为每指标写**待填骨架** `<ind>_analyzer.py`;模型看概览+树,按 `condition_binding_schema.md` 填 `CONDITIONS`(默认套模板 `evaluate_binding`,不够用才写 `CUSTOM` 函数);步骤3 用真实数据真跑 + `validate_conditions.py` 自检(custom 写崩当场暴露)。
- **execute(只跑)**:`runner.py` 退回纯 `script + agent_skill` 单趟——按序跑各 `<ind>_analyzer.py`(快,直接吐该源逐条件事实)→ 末尾一次 `fault-tree-rca`(慢,唯一模型步)。删了 execute 里的 `condition_eval`/可重入/`eval_conditions.py`。
- **plan**:`各<ind>_analyzer(script)→rca(agent_skill)`(回到直线)。跨源组合的合取项分填各源脚本,AND/OR 由 RCA 合。
- **保留**:Windows 下 `subprocess(text=True)` 的 gbk 解码崩溃修复(`encoding="utf-8"`);列解析机械化 + 三条诚实铁律;custom 逃生口由步骤3真跑兜底。

**已验证**:`examples/kpi-tabular/scripts/kpi_analyzer.py`(3 条套模板 + 1 条 custom「同列极差>10」)单跑 4/4 ok、校验通过;assemble→填脚本→reverted runner 端到端跑通(`kpi(script)→rca` 单趟,RCA 输入含逐条件事实)。

**待办**:
- F(配置)/H(信令)需对应数据源才出实判,无源→诚实数据不足。
- 模板词汇/custom 之外更复杂的 I 类(TA+TMSI 联合)按需扩。
- `validate_parser.py` 仍按旧"概览"字段校验;**条件脚本改用 `validate_conditions.py` 校验**。

## 更新(v4):局部子树压缩 + 疑似根因档 + tree_issues
> 背景:真实跑一次 RCA 慢(~9min)且不稳——模型对着 69KB 整树 + 全量事实,容易过度上报根因,被 `validate_rca` 反复打回,甚至**编假节点("测试""干扰排查")凑路径**。两个目标:① 压上下文(快、稳、可包装);② 给模型"疑似"出口(用泛化,别死磕)。

**压缩(权威局部子树)**:
- `excel_to_tree.py` 多产 `tree/<场景>.tree_index.json`:用 `Ln Code` 当**稳定 node_id** + 父子 + 逐字条件 + AND/OR 提示(从判法文字启发式抽,拿不准给 `unknown`)+ 数据源 + `pass_through`(无条件节点如"测试")。
- 生成脚本每条 `CONDITIONS` 带 **`node_id`**;事实里因此带 node_id。
- `cs-scot-execute/scripts/tree_prune.py`:`index + 各源事实 → 局部子树`(保留有事实的节点 + 祖先,把事实挂到节点上)+ 覆盖报告。**只做结构剪枝,不下判断。**
- `runner.py` 在备 RCA 输入时:有 `tree_index` 就剪枝、把 `fault_tree`(局部子树,含事实)+ `tree_issues` + `unmapped_facts` 写进 `rca_input`,**替代 69KB 整树**;`node_id` 覆盖 <50% 或剪枝失败 → **回退完整 md**(老行为)。实测 e2e:整树→3 节点局部子树,事实挂在节点上。
- **防造假**:RCA 只能引用 `pruned_nodes` 里真实节点;绑定填了不存在的 id → `orphan_node_ids`(tree_issues)。

**疑似根因档(模型自分类 + 机器只兜结构底线)**:
- RCA 输出四档:`rca_result`(确证:路径全成立)/ `probable_causes`(疑似:无不成立、有数据不足、叶子有强证据;模型给 `confidence` + `caveats` 含有效样本量)/ `undetermined` / 排除(有不成立)。
- `validate_rca.py`:只兜结构——`rca_result` 含数据不足/不成立 → 揪出(**建设性提示:挪去 probable/excluded,别凑路径、别造节点**);`probable_causes` 只拦"不成立"(数据不足是疑似正常特征,放行)。**不卡强度/排序(那是模型泛化)。** 不再"报错逼重写到死"——给了合法出口,重生成变成自我归位。
- `tree_issues`:`uncovered_condition_nodes`(漏绑/无源)、`orphan_node_ids`(填错节点)——顺带帮人改树/补数据。

**配套修的**:`decision:insufficient_data/manual`(模型判不了一句声明、不跑内核);H 内核加 `where`(消息**且携带** cause=X,治过宽误报);多页签 xlsx(`load_table(sheet=)` + `analyzer --sheet` + 概览列页签);懒加载 + decision/J 不读数据(满屏数据不足的脚本几乎零 IO);`validate_conditions` 认全内核痕迹字段(H 的 `msg_column` 等,修误报);runner 提示 result/ 已建好别 mkdir + Windows 路径别以 `\` 结尾(治执行卡死)。

**测**:excel_to_tree index、tree_prune(含孤儿/未覆盖)、validate_rca 四档好坏例、端到端(assemble→填 node_id→runner 出局部子树)全过;kpi-tabular 示例(无 index)走回退路径仍 4/4。
