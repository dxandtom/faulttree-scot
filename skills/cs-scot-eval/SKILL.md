---
name: cs-scot-eval
description: 评测故障树诊断三个 skill(generate 的绑定 / execute / fault-tree-rca)的准确率。在自带标准答案的合成场景上,跑真 excel_to_tree+assemble+runner 端到端产出,再给【绑定】(大白话判据→系统列)和【RCA】(命中/档位/无假阳/诚实)打分。当用户要评测绑定或 RCA 准不准、要准确率数据时用。
---

# cs-scot-eval —— 绑定 / RCA 准确率评测

**操作手册,已加载,别再加载。** 默认跑 **v2 端到端**:跑真 generate/execute,自己逐场景推理产出答案,再打分。`<本目录>` = 本技能目录;路径含空格/中文务必加引号(Python 3.11)。

## ⛔ 红线(违反则本次结果作废)
1. **严禁写任何脚本/程序批量产 `rca.json` / `binding.json`**(例如读 `predicate_satisfied` 遍历树的 `batch_rca` 之类)——那测的是脚本不是你;**每个场景逐个由你自己读、自己推理、自己写**。
2. 不许看 `scenarios/*/truth.json`(标准答案,只给打分器)。
3. 不许重新生成已冻结的题库(`fault_tree.csv` / `data/` / `truth.json`)。

## v2 跑法(默认)
1. **编排**(确定性脚本,可执行):
   `python "<本目录>/scripts/v2_run.py"`
   → 真 excel_to_tree+assemble 落 `suite_v2/scot产物/SA接入失败/`(`plan.json`/`scripts/*_analyzer.py`/`tree/*.md`);真 runner 用金标准绑定算事实 → 各 `suite_v2/scenarios/<场景>/rca_input.json` + suite 级 `suite_v2/binding_question.json`。
2. **绑定题(1 道)**:读 `suite_v2/binding_question.json`(每条大白话判据 + 各源列概览),自己把每条**映射到系统列**、选内核类别、给参数,写成 CONDITIONS 的 JSON 列表到 `suite_v2/binding.json`。**键名照抄下面、别自创**(尤其用 `class` 不是 `category`、用 `contains` 不是 `value`,否则真实内核认不出、绑定跑不起来):
   - A 阈值:`{"node_id","class":"A","column":"<列裸名>","op":"<或>等","threshold":数}`
   - C 字段/错误码**占比**(已知具体取值,数它占多少):`{"node_id","class":"C","column","contains":["取值"],"require":条数}`
   - G 告警存在:`{"node_id","class":"G","column","contains":["告警码"]}`
   - H 信令缺失:`{"node_id","class":"H","key":"<会话键列>","msg_column":"<消息列>","lacks":["缺的消息"]}`
   - E 占比TopN(**不指定取值**、看最大分组占比):`{"node_id","class":"E","group_by":"<列>","op",">","threshold":0.5}`
   - B 差值:`{"node_id","class":"B","minuend":["列"],"subtrahend":["列"],"op",">","threshold":0}`
   - **C vs E 关键**:某原因/错误码"**占比≥X%**"——只要判据点名了**具体取值**(如 amf-overload、RNL),就是 **C**(数该取值条数 ÷ 总数);只有"不点名、看 TopN/最大分组占比"才用 E。
   - 数据里没有该判据要的列 → `{"node_id","decision":"insufficient_data"}`,别硬绑别的列。
3. **RCA 题(逐场景)**:对 `suite_v2/scenarios/` 下**每个**场景,用 **fault-tree-rca** 技能:
   - 输入是该场景 `rca_input.json`;**别一次性全读进上下文**——先 `python "<fault-tree-rca 技能>/scripts/rca_query.py" <该 rca_input.json> --outline` 看全局,再 `--node nX` 按分支取条件+事实;
   - **每个节点的状态内核已算好**(`outline`:hold/fail/insufficient/pass,与 facts 的 `predicate_satisfied` 一致):**直接采用,别拿实际值自己重算或推翻它**(实际值只用于逐字引用)。你只做"沿路径组合";
   - 按 fault-tree-rca 的四档法则推理:整条祖先全 hold 的叶子才算根因;**叶子自己 hold 但任一祖先 fail → 绝不报(抗诱饵)**;**多条独立全-hold 路径要都报(多根因)**;把根因 JSON 写到**同目录** `rca.json`。一次一个。
4. **打分**:
   `python "<本目录>/scripts/score_v2.py" -o "<本目录>/suite_v2/v2_report.txt"`
   出【绑定】(类别 / 选列 / 参数 / 完全正确)+【RCA】(命中 / 档位 / 无假阳 / 诚实,综合 = 四项全过)。

> 更严的防作弊(可选):`python "<本目录>/scripts/make_prompts.py" --v2` 把每题导成自包含题面 `prompt.txt` / `binding_prompt.txt`,发给**纯聊天模型(不带写代码的工具)**作答——它就没法写脚本绕过推理。见生成的 `suite_v2/_HOWTO_prompts.md`。

## 打分四维(诚实档说明)
命中 / 档位准 / 无假阳 / **诚实**(凡判"成立"的**带条件**节点必须真是埋下的 `hold`;在 fail/数据不足节点上判成立 = 不诚实。`pass_through` 节点无条件,标不标"成立"都不计入)。

## 自测(不测模型,只验打分链路)
`python "<本目录>/scripts/score_v2.py" --use-reference` —— 用金标准/参考答案当输入,应 **100%**。

## 维护题库(平时不跑;改完重新提交冻结)
改 `scripts/v2_handcrafted.py` 的 `TREE`/`SPEC`/`SCENARIOS` → `python scripts/v2_handcrafted.py`(每场景内核校验须全过)→ 再 `v2_run.py`。细节见 `suite_v2/README.md`。

## 备注
- **受控合成、必要不充分**:报数字时写"受控合成下的诊断推理准确率",别写成"现网真实准确率"。
- RCA 路用金标准绑定算事实(隔离绑定误差,单测推理);绑定阶段单独评。
- 另有 v1(`suite_real`,多树多规模、`rca_input` 由 `inject_on_tree.py` 自算并用 `score_accuracy.py`/`score_binding.py` 打分);v2 是当前主线。
