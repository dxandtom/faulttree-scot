---
name: cs-scot-eval
description: 用"合成故障注入"测三段:绑定(大白话判据→系统列)、执行(数据→事实)、RCA(事实→根因)。在多棵不同大小的真实/合成故障树(~27~320 节点)上埋已知根因(ground truth),造数据+金标准绑定、跑真实内核出事实,和标准答案比,出 绑定准确率(选列命中等)+ RCA 准确率(命中/档位/无假阳/诚实)+ 按规模分档。补 cs-scot-benchmark(无 ground truth)不覆盖的准确率维度。当用户要评测绑定/RCA 准不准、做准确率实验数据。
---

# cs-scot-eval —— 三段准确率评测(合成故障注入)

**本文件是操作手册,已加载,不要再次加载。** 任务:对一套**自带标准答案**的合成场景,跑出准确率表。

## 测哪三段(对应整条诊断链的两个模型步 + 中间确定性步)
```
生成: 树判据(大白话)+数据 →[模型绑定]→ CONDITIONS     ← ① 绑定准确率(score_binding)
执行: 数据+绑定 →[真实内核]→ 事实 → rca_input            ← ② 执行链(这里就是用真实内核算的,自带验证)
RCA : rca_input →[模型推理]→ 根因                         ← ③ RCA 准确率(score_accuracy)
```
每个场景同时备好三样:`data/*.csv`(数据)、`binding_question.json`(绑定题)、`rca_input.json`(RCA 题);答案在 `_answers/`。

## 为什么这么测
真实故障没人标过"正确根因/正确绑定",准确率没法直接测 → **自己造**:埋已知根因、造能让内核算成立的数据、写金标准绑定,正确答案我自己知道。
- 场景在 **`suite_real/`**:`trees/` 下多棵不同大小的树(脱敏真实树+子树+合成树,~27/44/95/178/320 节点)上注入根因,埋深处 + 撒"事实成立但路径断"的诱饵 + 不成立/数据不足噪声 → 出"准确率 vs 规模"。
- **`rca_input` 与生产完全一致**:事实是 `data + 金标准绑定 → 真实内核 evaluate_binding` 算出来的(不是手写),就是生产里 `analyzer→tree_prune` 喂给 RCA 的样子。
- **绑定题有"语义鸿沟"**:树判据是大白话("RRC建立成功率低于98%"),数据列是系统名(`SA_RRC_ConnSetup_SuccRate(%)`),模型要**跨措辞对列**——这才是 generate 绑定阶段最难、最易错的地方。

> **考题已冻结提交,本地不要重新生成**(出题方=可信侧,答题方=你本地模型,防止自出自答找便宜路)。
> `suite_real/<场景>/rca_input.json` = **答题模型唯一该读的**;`suite_real/_answers/` = **答案区,做 RCA 时别读**,只给打分器。

## ⛔ 诚信红线(违反则结果作废)
**这套测的是【模型自己的 RCA 推理】。绝对禁止**:
1. **写任何脚本/程序去批量产 `rca.json`**(例如读 `predicate_satisfied` 后遍历树的 `batch_rca` 之类)——那测的是脚本不是模型,**结果无意义**;
2. 偷看 `_answers/`;
3. 拿 `reference_rca` 当答案。
**正确做法**:每个场景**逐个**由模型**自己阅读 `rca_input.json`、推理、写 `rca.json`**。
> 最稳的做法见下方"步骤 1B":用 `make_prompts.py` 把每个场景做成题面,喂给**普通模型(纯聊天,不带写代码的工具)**作答——模型就没法用脚本绕过推理了。

## 两步跑法(主:真实大树 `suite_real`,考题已预置)

### 步骤 1:逐场景做 RCA(**模型判断,正是被打分的对象;不许写求解脚本**)
对 `suite_real/` 下**每个**场景(`_answers/` 不是场景),用 **fault-tree-rca** 技能读它的 `rca_input.json` 做根因判断,把结果按该技能的契约写到**同目录** `rca.json`。一次一个,**自己读、自己判**。

### 步骤 1B(防作弊推荐):题面喂纯模型
```bash
python "<本目录>/scripts/make_prompts.py"   # 每个场景生成自包含题面 <场景>/prompt.txt
```
把每个 `prompt.txt` 发给**普通模型/聊天(别用能写代码的 agent)**,它**只能靠推理**答 JSON;输出原样存成同目录 `rca.json`。详见生成的 `suite_real/_HOWTO_prompts.md`。

### 步骤 2:打分出表(确定性)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite_real" -o "<本目录>/accuracy_report.txt"
```
对每个场景的 `rca.json` 比对标准答案,出四项:
- **命中**:埋的根因有没有落在【应在的档位】(确证→`rca_result`;疑似→`probable_causes`;无因类→不报确证);
- **档位准**:没把"该疑似的"报成确证、没漏档;
- **无假阳**:没把"路径断了的干扰叶子"等 `must_not_report` 节点报成根因;
- **诚实**:对真值查"伪造成立"——凡判"成立"的节点必须确实是埋下的 `hold` 节点;在 fail/数据不足 节点上判成立 = 不诚实。
综合 = 四项全过;聚合成准确率 % + **按树规模分档**。

## 另一套:绑定阶段(测 generate 第 2.5 步,同样防作弊)
同一批场景还各带一道**绑定题**(`binding_question.json` = 大白话判据 + 各源列概览;数据在 `data/*.csv`)。
1. `python make_prompts.py` 会顺带生成每场景的 `binding_prompt.txt`;
2. 把它发给**普通模型**(同样别用能写代码的 agent),让它输出 **CONDITIONS 的 JSON 列表**,存成同目录 `binding.json`;
3. 打分:`python scripts/score_binding.py --suite suite_real -o <…>/binding_report.txt`。
出 **类别命中 / 选列命中 / 参数命中 / 完全正确**——其中**选列命中率**最关键(大白话判据→系统列,generate 最易错处)。

## 自测打分链路(可选,不测模型)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite_real" --use-reference   # RCA 链路,应 100%
python "<本目录>/scripts/score_binding.py"  --suite "<本目录>/suite_real" --use-reference   # 绑定链路(金标准自测),应 100%
```
用满分参考/金标准当输入,应出 **100%**——证明注入+剪枝+打分链路本身没毛病。

## v2:端到端评测(`suite_v2`,零重写 gap)
v1 复用真实内核但**自己算** `rca_input`(绕过 generate/execute 的技能壳);**v2 把手工题库喂给真
`excel_to_tree`+`assemble_scenario`+`runner` 端到端跑,再评它们的产物**——测的就是三个 skill 串起来准不准。
题库**手工精心编排**(非脚本随机生成):一棵因果连贯的 SA 接入失败树(17 节点)+ 18 个有意义场景
(10 个叶子因各确证一次,覆盖 A阈值/C占比/G告警/H信令 四类内核 + 疑似/排除/数据不足/抗诱饵/多根因),
全部真实内核校验过。编译产物落生产同款 `scot产物/<场景>/`。**join key 是 code**:
`gold_tree`/`truth` 按 code 索引,真 excel_to_tree 重分配 id 后用重解析的 `tree_index` 映射回来。
```bash
python "<本目录>/scripts/v2_run.py"                       # 真 excel_to_tree+assemble+runner → 各场景 rca_input.json + binding_question.json
python "<本目录>/scripts/make_prompts.py" --v2            # RCA 18 道(scenarios/*/prompt.txt)+ 绑定 1 道(binding_prompt.txt)
#   发给普通模型答题 → 存 rca.json(各场景)/ binding.json(suite 根)
python "<本目录>/scripts/score_v2.py"                     # 同时出 绑定 + RCA
python "<本目录>/scripts/score_v2.py" --use-reference     # 自测打分链路(应 100%)
```
RCA 路用**金标准绑定**算事实,隔离绑定误差、单测大树推理;绑定阶段(大白话→系统列)单独评。细节见 `suite_v2/README.md`。
维护题库:改 `v2_handcrafted.py` 的 `TREE`/`SPEC`/`SCENARIOS` → `python v2_handcrafted.py` 重生(每个场景内核校验须全过)。

## 换树 / 扩充场景(维护用,平时不跑;改完要重新提交冻结)
- **加树/换规模**:`make_trees.py` 从真实树切子树 + 生成合成树到 `trees/`(改 `SUBTREES`/`SYNTH` 调规模);要加你自己的脱敏真实树,丢进 `trees/` 即可(只需拓扑,condition 可 null)。
- **重生成考题**:`inject_on_tree.py` 遍历 `trees/` 每棵树自动出 6 类场景到 `suite_real/`(确定性),改完重新提交冻结。
- **加业务判据**:在 `binding_personas.py` 的 `PERSONAS` 加/改人设(树判据大白话 `tree_cond` + 系统列 `col` + hold/fail 数据值 + 金标准绑定参数)→ 判据更丰富。加完务必 `python binding_personas.py` 风格自测(真实内核须能复现 hold/fail/insuf)。

## 注意(写进汇报必须说清)
- 这是**【受控合成故障注入】下的 RCA 大树推理稳健性**:在生产同款输入上,按树结构组合判定 + 在几十~几百节点里追闭合路径 + 抗诱饵 + 诚实,是**必要不充分**——连埋的因都找不对则现网必不行,找对了≠现网准确率(真实故障更脏)。口径写"受控合成下的诊断推理准确率",别写成"真实准确率"。
- 判据/数值是**可信侧合成的真实业务样例**(非现网实测);考题由可信侧出、本地只答题(出题≠答题)。**不**测 data→事实的绑定环节(那要金标准绑定,专家未定)。
- 与 `cs-scot-benchmark` 互补:benchmark 测 省/稳/可复现/诚实/转换/内核回归(**无** ground truth),本技能测**准确率**(**有** ground truth)。
- Python 3.11;路径有空格/中文务必加引号。
