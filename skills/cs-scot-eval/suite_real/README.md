# 冻结的诊断准确率考题(真实大树注入)

这些场景由可信侧预先生成并提交,**本地不要重新生成**(避免出题方=答题方、自找便宜路径)。

- `<场景>/rca_input.json` —— **答题模型只读这个**(局部子树+事实)。
- `<场景>/rca.json` —— 答题模型把 RCA 结果写这里(本地产物,不提交)。
- `_answers/<场景>.json` —— **答案区(truth+满分参考),做 RCA 时别读**,只给打分器用。

跑分:`python ../scripts/score_accuracy.py --suite . -o accuracy_report.txt`
自测打分链路:`... score_accuracy.py --suite . --use-reference`(应 100%)。
