# 冻结的诊断准确率考题(多树故障注入)

可信侧预生成、已提交,**本地勿重新生成**。每个 `<场景>/rca_input.json` 是答题模型**唯一该读**的,
格式**与生产 rca_input 完全一致**(含 predicate_satisfied/value/ratio…);模型按 fault-tree-rca
正常流程判断,把结果写同目录 `rca.json`。`_answers/` 是答案区(truth+node_states+满分参考),
**做题时勿读**,只给打分器。

跑分:`python ../scripts/score_accuracy.py --suite . -o accuracy_report.txt`;自测:`... --use-reference`(应 100%)。
