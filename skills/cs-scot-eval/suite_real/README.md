# 冻结的诊断准确率考题(多树故障注入,不带 predicate)

可信侧预生成、已提交,**本地勿重新生成**。每个 `<场景>/rca_input.json` 是答题模型**唯一该读**的;
事实**不含 predicate_satisfied**,模型须拿实际值自己判 condition 成立/不成立,再导航。
`_answers/` 是答案区(truth+node_states+满分参考),**做题时勿读**,只给打分器。

跑分:`python ../scripts/score_accuracy.py --suite . -o accuracy_report.txt`;自测:`... --use-reference`(应 100%)。
