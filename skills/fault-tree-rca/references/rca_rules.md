# 根因分析规则(fault-tree-rca,**指标无关**)

本技能只做一件事:**拿故障树 + 已解析好的各路事实,判定根因**。
它**不关心**指标具体是什么(今天是这几个,明天可以是任意新指标),下面的法则对**所有指标一视同仁**。

## 输入
- **fault_tree_md**:故障子树(`#/##/###` 层级,标题下是该节点的判断条件;人工编写、可能不规整)。
- **indicator_results**:dict,键是指标名,值是该指标已解析好的**事实**(只有数据、没有判断):每个字段/项的真实分布、数值摘要或取值。
  - 注意:有的指标提供的是**定义/规则**(某量怎么算、阈值是多少),而**不是实测值**。**定义不是测量。**
- 多数情况下每个指标的结果是**逐条件事实**:`{indicator,source,summary,conditions:[{id,node,condition_text,class,mapping_reason,fact}]}`,
  `fact.predicate_satisfied` 是**仅在解析到的列上做的字面比较**、`fact.status` 可能是 `insufficient_data`/`manual`。
  - 这是**输入证据,不是最终结论**:你仍要 ① 看 `mapping_reason` 判**语义是否真等**(如 RSRP 顶 RSSI、单位不符 → 降置信/数据不足,别因 `predicate_satisfied:true` 就当成立);② `status:insufficient_data/manual` → 该节点判"数据不足";③ **跨源组合**(同一判据的合取项分散在不同指标结果里)由你 AND/OR 合;④ 路径回溯与数据不足传播。**字面为真 ≠ 该条件成立。**
- 上下文可能已被**压缩成局部子树**:`fault_tree` 含 `outline`(`id→{name,parent,status}` 紧凑导航,**很小**)+ `pruned_nodes`(相关节点,事实挂在 `facts` 里,另有 `condition`/`combine`/`pass_through`/`data_source`)+ `tree_issues` + `unmapped_facts`。没有 index 时退回 `fault_tree_md`。**两种形态法则一样。**
  - **⚠ 输入可能很大:别一次性把整个 rca_input 读进上下文(会超长报错)。** 先看 `outline` 把握全局,再**按分支用工具只取相关 `pruned_nodes[id]`** 的条件+事实。
  - `outline.status`(`hold/fail/insufficient/pass/uncovered`)只是**粗导航**——仍要按 `pruned_nodes` 的条件+事实**重判**。
  - node_id 是短 id(`nN`);`pass_through:true` 节点(汇总/"测试")**永不阻断路径**;`combine` 是 `AND/OR/unknown` 提示。
  - **node_id 权威**:`Fault_tree_path` 只用 `pruned_nodes` 里真实节点;`tree_issues.orphan_node_ids`/`unmapped_facts` 是绑定填错的信号,别据此硬判。

## 核心:一个通用判定法则(不分指标)
对故障树**每条判断条件**,照此走:
1. 看清这条条件要的是**哪个量/字段/取值**(记作 **X**)。
2. 到 `indicator_results` 找 X 的**实际值**:
   - **找到 X 的实测值** → 代入条件的阈值/比较/算术 → 判"**成立 / 不成立**"。
   - **没找到**(没有这个字段;或某指标只给了 X 的**定义/规则**而非实测值;或 X 要的数据这次没提供)→ 判"**数据不足**"。
3. **绝不拿别的量 Y 顶替 X**:条件要 X、你手上只有相关的 Y,**不许用 Y 算个数就声称 X 成立**。"有定义"≠"有测量","有旁证"≠"该条件成立"。
4. **精确**:条件点名的取值/标识要**精确**命中(差一个字符就是没命中 → 数据不足),不许用相近值顶。

## 字段映射(树的量 ↔ 数据列):显式、按语义、可审、不许编
真实数据里**名字对不上是常态**:树的条件常用英文/code 名(如 `SUSRLastAvgRsrp`、`SA_QosFlow_Setup_Success_Rate`),
数据列却是中文(如 `上行RSSI均值`、`SA_QoSFlow建立成功率(22B)`)。所以第 1 步"找 X 的实际值"必须先**显式建立映射**,且:
1. **按含义映射,不按字符串**:在数据列里找"语义上就是这个量"的列。把这次映射**写进 evidence**(`mapping` + `mapping_reason`),让人能复核。
2. **找不到就数据不足,绝不抓个相关列顶替**:没有"就是 X"的列(如树要 SINR、数据里根本没有 SINR 列)→ 数据不足。
   **最隐蔽的错就是把 SINR 映到"语音用户数"这种只是恰好挨着/相关的列**——映射一旦错,后面数照样算得出、结论却全错且不报错。宁可数据不足,不可硬认。
3. **语义/单位近似要标注、降置信**:只有"相近但不等"的列(树要 RSRP、数据只有 RSSI;或量纲/口径不同,如本应 dBm 的列取值是 0.01/154 这种),
   可暂用但必须在 `mapping_reason` 写明差异,judgment 倾向"**数据不足/低置信**",**不要**当干净的"成立/不成立"。
4. **成功率 vs 原始计数**:树要 `尝试-成功`(差值)而数据只有百分比成功率、没有原始计数 → 要么数据不足,
   要么用"成功率<100%"重述同一问题**并在 `mapping_reason` 写明这是替代口径**,不得假装算出了差值。
5. **NIL / 空 = 缺测 = 数据不足**(对该单元格/该行),**绝不当"条件不成立"**——否则把"没测到"悄悄变成了"正常"。
   (解析事实里每列带 `nulls` 计数,缺测是显式可见的,别忽略。)
6. **阈值出处**:按故障树写的阈值用(那是现有权威);若树未标注出处,在 judgment 注明"阈值取自故障树、出处未核",不要当已验证门限。
7. **数值阈值条件怎么判**:数值列事实给了 `min/max/mean` 和**十分位 `deciles`**。
   "X < 阈值" 这类:用 deciles 估"有多少落在阈值一侧"(如阈值低于 `p0` → 基本无一命中;介于 `p20~p30` → 约两三成命中),
   并在 `actual` 写明你引用了哪些分位数;**够不够判一条成立由你按比例决定,但所引用的数必须逐字来自事实**。

## 路径分档:confirmed / probable / undetermined / excluded
一条候选 = 从某叶子回溯到根的路径;**路径上每个带条件的节点都要"成立"**才是确证根因。按路径上节点的状态分档:
- 每个带条件节点都**成立** → **confirmed**(进 `rca_result`)。
- 有任一节点**不成立** → **excluded**(连疑似都不是,不报;可在文字里一句"已排除")。
- **无"不成立"、但有节点"数据不足",且叶子/关键节点有正向证据** → **probable(疑似)**(进 `probable_causes`)。
- 其余(数据不足、且没什么正向证据) → **undetermined**。

**只有这几条状态线是硬的(confirmed 必须全成立;有不成立就 excluded);其余全靠你判断:**
- **哪些够格的路值得报"疑似"、信心多高(high/medium/low)、怎么排序、写什么 caveat —— 你说了算**(用你的泛化能力)。机器不卡强度。
- **疑似必须诚实披露**:卡在哪个节点(数据不足)、叶子证据是什么 + **有效样本量/空值比例**(如"96% 满足,但仅 1814/20496 非空")、为什么仍只是"疑似"。
- **绝不为了凑 confirmed 编节点/改路径**:路径只能用真实存在的节点;有数据不足节点就老实放 probable,**别造"测试""xx排查"之类不存在的节点把路凑完整**。
- **疑似 ≠ 确证**:别把疑似说成"已定位根因";它是"证据强、但路径未闭合(上游缺数据)"。

> 自检 `validate_rca.py` 只兜结构底线:`rca_result` 里若有路径含"数据不足/不成立"节点会被揪出。**它报错时,把那条挪进 `probable_causes`(或 excluded),而不是去把路径凑完整、更不是造假节点**——这是给你的建设性提示,别和它较劲。

## tree_issues(顺带把子树的毛病报出来)
输入带 `tree_issues` 时,在输出 `tree_issues` 字段**原样带出、可补充**:`uncovered_condition_nodes`(有条件却没人绑/无数据源的节点)、`orphan_node_ids`(绑定填了不存在的节点)。这是给人改故障树/补数据的线索,**不影响根因判定**。

## 输出契约
```json
{
  "rca_result": [ {"Root_cause":"…","Fault_tree_path":["…"],"support":"…",
     "evidence_path":[{"node":"…","node_id":"…","condition":"<逐字取自故障树>",
        "mapping":"<树的量 X → 命中的数据列;没有写'无对应列'>","mapping_reason":"<为何这么对/差异/为何数据不足>",
        "actual":"<逐字引用事实(可引 deciles/nulls);没有写'事实中无此值'>","judgment":"成立/不成立/数据不足"}]} ],
  "probable_causes": [ {"Root_cause":"…","Fault_tree_path":["…"],"confidence":"high/medium/low",
     "blocked_at":"<路径上数据不足的节点>","caveats":"<有效样本量/空值比例/语义近似等>",
     "support":"<叶子证据>","evidence_path":[ …同上结构… ]} ],
  "undetermined": [ {"Fault_tree_path":["…"],"blocked_at":"…","reason":"缺哪个量","observed":["…(仅观察,非根因)"]} ],
  "tree_issues": { "uncovered_condition_nodes":[…], "orphan_node_ids":[…] }
}
```
- 每条 evidence 把 **条件 + 实际值 + 判断** 写全;`pass_through` 中间节点注明"无独立条件"。
- **口径一致**:文字总结必须和 JSON 一致——`rca_result` 是 `[]` 就别说"发现了 N 条根因";疑似就叫疑似。
- `rca_result` 为 `[]` **很正常**(上游话统/信令缺数据时);**强证据放 `probable_causes`,别硬塞 `rca_result`、更别造节点凑路径**。

## 一句话
事实是别人查好的;你只做"拿故障树比对、如实判定"。**全成立才算确证根因;有不成立就排除;强证据但上游缺数据 → 老实归"疑似"(带 caveat);其余数据不足。绝不用旁证硬顶、绝不造节点凑路径、绝不无中生有。**
