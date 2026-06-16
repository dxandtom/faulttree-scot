# 冻结考题(多树注入 · 数据+绑定+RCA 三段)

可信侧预生成、本地勿重生成。每场景:
- `rca_input.json`(RCA 题,与生产一致)+ `data/*.csv`(数据)+ `binding_question.json`(绑定题);
- `_answers/<场景>.json` 是答案(truth/node_states/reference_rca/gold_binding),**做题时勿读**。

测 RCA:逐场景模型读 rca_input → 写 rca.json,再 `score_accuracy.py`。
测绑定:逐场景模型读 binding_question + data → 写 binding.json(CONDITIONS),再 `score_binding.py`。
