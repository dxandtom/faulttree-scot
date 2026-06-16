# suite_v2 —— v2 端到端评测题库(手工编排)

v2 与 v1(`suite_real`)的根本区别:
- **v1**:复用真实内核,但**自己算** `rca_input`(`inject_on_tree.py`),绕过了 generate/execute 的技能壳;
- **v2**:把题库喂给**真** `excel_to_tree` + `assemble_scenario` + `runner`(见 `scripts/v2_run.py`)端到端跑,
  再评它们的产物 —— **零重写 gap**,测的就是这三个 skill 串起来到底准不准。

题库**手工精心编排**(`scripts/v2_handcrafted.py`,非脚本随机生成):一棵【因果连贯】的 SA 接入失败树
(17 节点 / 15 个带条件节点:RRC 建立→随机接入→上行干扰·弱覆盖;UE 上下文→安全模式·鉴权;
NGAP/AMF→AMF 过载·传输资源;QoS 流→无线链路;网元告警→驻波比·小区不可用),
每个节点固定一条**大白话判据**(不含系统列名,制造"语义对列"难点)+ 真实 hold/fail 取值。
9 个**有意义**的场景,每个都用真实内核校验过能算出预期 成立/不成立/数据不足:

| 场景 | 类别 | 考点 |
|---|---|---|
| S1 弱覆盖致接入失败 | confirmed | 三层闭合路径 |
| S2 AMF 过载 | confirmed | NGAP 原因码占比 |
| S3 无线链路失败 | confirmed | CHR 错误码占比 |
| S4 鉴权失败 | confirmed | 释放原因占比 |
| S5 上下文层疑似 | probable | 中间层成立、叶子数据不足 → 不能确证 |
| S6 网络正常排除 | excluded | 全不成立 → 不报根因 |
| S7 数据不足 | honest_insufficient | 全无数据 → 诚实判数据不足 |
| S8 抗诱饵(弱覆盖断路) | false_positive_resist | 诱饵叶子自身成立但路径被父级断开 → **不许报** |
| S9 多根因 | confirmed | 弱覆盖 + 无线链路两条独立路径都要报 |

## 提交什么 / 重生什么
- **提交(手工源题库)**:`fault_tree.csv`(真 Excel 格式)、`gold_tree.json`(code→金标准绑定)、
  `scenarios/<场景>/data/*.csv`(数据)、`scenarios/<场景>/truth.json`(答案,**答题时别看**)。
- **不提交(一条命令确定性重生)**:`产物/`(真 excel_to_tree+assemble 的编译产物)、
  `scenarios/*/rca_input.json`(真 runner 产出)、`binding_question.json`、题面 `prompt.txt`、模型答案、报告。

join key 是 **code**(`FT-0XX`):`gold_tree`/`truth` 都按 code 索引;真 `excel_to_tree` 会重新分配 id(n1,n2…),
打分时用 `产物` 里重解析出的 `tree_index` 的 code→id 映射对回来。

## 跑法
```bash
# 1) 真 excel_to_tree + assemble + runner 端到端(用金标准绑定算事实,隔离绑定误差),产出题面
python ../scripts/v2_run.py
# 2) 出题面(发给普通模型/聊天,别用能写代码的 agent)
python ../scripts/make_prompts.py --v2          # 见生成的 _HOWTO_prompts.md
#    RCA:9 道 scenarios/<场景>/prompt.txt → 模型答 → 存同目录 rca.json
#    绑定:1 道 binding_prompt.txt → 模型答 → 存 binding.json
# 3) 打分(绑定 + RCA 两段)
python ../scripts/score_v2.py
python ../scripts/score_v2.py --use-reference   # 自测打分链路(金标准/参考),应 100%
```

> RCA 路用**金标准绑定**算事实,单测大树推理(判条件/导航/抗诱饵/诚实);绑定阶段(大白话→系统列)单独评。
> 受控合成,**必要不充分**:口径写"受控合成下的诊断推理准确率",别写成"真实准确率"。
