---
name: fault-tree-rca
description: 综合根因分析。输入故障子树 + 各指标解析结果 JSON,遍历子树匹配判断条件,输出所有成立的根因路径与判断依据。当执行到 SCoT 的 rca 步骤、或需要根据已解析的指标数据结合故障树定位根因。它专门处理人工编写、可能不规整的故障树,用模型判断弥补规则漏洞;判断依据要把"规则、实际数值、怎么判断、结论"写清楚。
---

# fault-tree-rca —— 综合根因分析

**本文件已加载,不要再次加载或调用本技能;"加载"≠"完成",真正产出根因 JSON 才算完成。** 现在立刻依据 runner 备好的 `result/rca_input.json` 和下文规则产出根因、写到 `result/rca.json`(路径以 runner 打印为准)。
**runner 已在本轮启动时把上一轮整体归档到 `runs/<上轮run-id>/`,`result/` 此刻只有本轮的新 `rca_input.json`**——基于它产出 `rca.json` 即可,不会有过期 rca.json 干扰,也别去 `runs/` 里翻旧的当本轮答案。

> `result/` 目录已由 runner 建好——**直接写 `result/rca.json`,别再 `mkdir`**。Windows 路径用正斜杠 `/`,**勿以反斜杠结尾**(带引号路径写成 `"...\"` 会让 shell 一直等闭合引号而卡住)。

诊断链路的最后一步:把"各指标已经解析好的事实"和"故障子树"放在一起,判定根因。**本技能与指标种类无关**——今天是这几个、明天换成任意新指标,法则都一样。

## 上游喂给你什么(输入契约)
通常由 `cs-scot-execute` 汇总在 `result/rca_input.json` 里:
- **`fault_tree_md`**:故障子树正文(`#/##/###` 层级 + 条件)。**人工编写,可能不规整。** —— 或:
- **`fault_tree`(压缩形态)**:`pruned_nodes`(只含相关节点,每个把事实挂在 `facts` 里 + `condition`/`combine`/`pass_through`/`node_id`),另带 `tree_issues`(覆盖报告)、`unmapped_facts`、**`uncovered_values`(判别列里没被绑定认领的取值+条数 → 据此把漏绑的记录归到保留的"非典型/其他"兜底节点报出,别漏诊)**。两种形态法则一样;`pass_through` 节点不阻断路径;`node_id` 权威、别造不存在的节点。
- **`indicator_results`**(完整形态时):dict,键是指标名(**任意、不固定**),值是该指标已解析好的**事实**(**只有数据、没有判断**):各字段/项的真实分布、数值摘要或取值。
  - 有的指标给的是**定义/规则**(某量怎么算、阈值多少),**不是实测值**。**定义不是测量。**
  - **解析端不下判断、也不替你做匹配;判断全是你的活。**
- **`missing_indicators`**(可选,两种形态都可能带):本轮**整个数据源没提供**(execute 部分数据运行,
  解析步骤被跳过)的源名 → 原因。这些源绑定的判据一律「数据不足」——**既未证实也未证伪,别当"不成立/已排除"**;
  最终结论要注明缺口(相关候选进 `probable_causes`/`undetermined`,`blocked_at` 写清缺哪个源)。

## 你要做什么(一个通用法则,不分指标)
遍历子树,对**每条判断条件**:
1. 看清它要的是**哪个量/字段/取值**(记作 X);
2. 到 `indicator_results` 找 X 的**实际值**:找到实测值 → 代入算"成立/不成立";没找到(没这个字段 / 只有定义没有实测值 / 这次没提供该数据)→ "**数据不足**";
3. 回溯到根 → 输出根因 + 逐节点判断依据。

详细规则见 `references/rca_rules.md`(**务必先读**)。

**三条铁律(最重要)**:
1. **只用真实事实,绝不顶替**:`actual` 逐字引用事实里的真实值;禁止引入事实里没有的数字;**条件要 X 就不能拿相关的 Y 算个数顶成"X 成立"**("有定义/有旁证"≠"成立");**精确匹配**(差一个字符即没命中)。
2. **要 X 却拿不到 X 的实测值 → 该条件"数据不足"**,不得当成立或不成立。
3. **四档分流**(详见 rca_rules.md):路径每个带条件节点都成立 → **确证根因**`rca_result`;有节点不成立 → **排除**;无不成立但有"数据不足"且叶子有强证据 → **疑似**`probable_causes`(带 confidence + caveat,**别凑路径、别造节点**);其余 → `undetermined`。**`rca_result` 为 `[]` 很正常(上游缺话统/信令时),强证据放疑似。**

> 聚合数据里常并存多种失败模式 → **每个有正向事实的不同叶子/失败模式都各自报一条(别过度合并、别只挑最强的一两条,尽量报全)**;确证的要完整证实、疑似的要标 caveat,去重 + 排序。**有"未覆盖取值"且树里有"非典型场景/其他"兜底节点的,归过去报一条 + 附原始特征码,别漏诊。**
> **口径一致**:文字总结必须和落盘 `rca.json` 一致——`rca_result` 是 `[]` 就**别**说"发现 N 条根因";疑似就叫疑似。

## 输入很大时:别一次性读,用导航助手
`scripts/rca_query.py <result/rca_input.json>`:`--outline`(全局粗状态)/ `--holding`(有"成立"事实的候选节点+根→叶路径)/ `--node nX`(某分支整条路径的条件+事实)。**按分支取,据此判**——别把整个 rca_input 读进上下文(会超长报错),也别自己写脚本硬算 RCA。

## 自检(机械兜底,推荐每次跑)
产出后跑 `python "<本技能目录>/scripts/validate_rca.py" result/rca.json result/rca_input.json`:对 **confirmed 严**(自相矛盾/假根因/缺路径),对 **probable 松**(只查理由齐不齐:`blocked_at`/`caveats`/`support`),**任何档**给"无事实"节点判"成立"→揪(伪造),外加**空诊断**。**报错就挪档/补理由,不是凑路径、不是造节点。**(带上 `rca_input.json` 才能核对"成立有事实"。)

## 输出契约
```json
{
  "rca_result": [ {"Root_cause":"…","Fault_tree_path":["…"],"support":"…",
     "evidence_path":[{"node":"…","node_id":"…","condition":"<逐字>","actual":"<逐字引用事实;没有写'事实中无此值'>","judgment":"成立/不成立/数据不足"}]} ],
  "probable_causes": [ {"Root_cause":"…","Fault_tree_path":["…"],"confidence":"high/medium/low",
     "blocked_at":"<数据不足的节点>","caveats":"<有效样本量/空值比例/语义近似>","support":"<叶子证据>","evidence_path":[…]} ],
  "undetermined": [ {"Fault_tree_path":["…"],"blocked_at":"…","reason":"缺哪个量","observed":["…(仅观察,非根因)"]} ],
  "tree_issues": { "uncovered_condition_nodes":[…], "orphan_node_ids":[…] }
}
```
每条 evidence 把 **条件 + 实际值 + 判断** 写全。详细分档/字段见 `references/rca_rules.md`。

> **推荐解决方案**:产物 `tree/solutions.json` 存在时,定完根因后对 `rca_result`/`probable_causes`
> 报出的节点按 node_id 点查,条目加 `"recommend_solution"`(优先中文),文字汇报也带上——
> 只查报出的那几个节点,查不到不加、**绝不编方案**。详见 rca_rules.md 输出契约。

## 分工(重要)
- **解析端只把数据查清楚摆出来**(事实),**不下任何判断、不做故障树匹配**。
- **判断全在你这一步**:把真实值代入故障树条件 → 比较 → 成立/不成立;要 X 没 X 就数据不足;再做路径回溯、逐节点取证、处理子树的不规整。两边都不许编。
