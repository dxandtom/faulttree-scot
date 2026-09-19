# 计划格式：执行侧视图

与 `cs-scot-generate/references/plan_schema.md` 是同一份格式约定。`plan.json` 是 SCoT 计划（SCoT v4.0 的子集），cs-scot-execute 据此执行。本文只讲执行器怎么读这份计划，字段的完整定义看生成侧那份。

## 执行模型

1. 读 `entry` 得到当前节点，用 `memory_init` 初始化工作记忆。
2. 按节点的 `kind` 分派：
   - `script` 节点：按 `name` 找到 `scripts/<name>.py`；解析 `inputs`（其中 `data` 解析成数据文件）；运行 `python scripts/<name>.py <数据> -o <临时文件>`；产出的 JSON 按 `outputs` 写入 `$memory`。
     特例：`inputs.data` 写成 `"$input.*"` 的节点是跨源判据脚本 cross_analyzer（产物格式版本 3 起有）。runner 把本轮已提供的全部数据源以 `--data <源>=<路径>` 逐个递给它；这个节点不进"缺源即跳过"的清单，给多少数据就用多少。
   - `agent_skill` 节点：解析 `inputs`，`fault_tree_md` 指向的路径读成故障树正文，其余输入归到 `indicator_results`；上游被跳过或没有产出的输入不进 `indicator_results`，改记到顶层的 `missing_indicators`（键是源名，值是原因），根因分析按"数据不足"对待。汇总结果写到本轮结果目录的 `rca_input.json`，由 `name` 指定的技能（fault-tree-rca）产出结果。
     注意：`indicator_results` 是完整形态，只在没有 `tree_index.json` 或剪枝不划算时出现。产物带 `tree_index.json` 时（常态），runner 会把 rca_input 剪成局部子树形态（`fault_tree.outline`、`pruned_nodes` 等，没有 `indicator_results` 键）。这两种形态的权威定义见 fault-tree-rca 技能的 SKILL.md。
3. 沿 `transition.next_on_success` 走到下一节点；`transition.end` 为 true 时结束。

## 引用

节点之间的数据靠四种引用传递：`$input.<key>`（由 `--data <key>=<文件>` 绑定）、`$memory.<path>`、`$step.<id>.<field>`、`$output.<key>`。引用的路径不存在时得到 `null`。

## 扩展

当前实现了 `script`、`agent_skill` 两种节点和直线转移。要新增节点种类或转移特性时：先对齐 SCoT 的 schema，再在 `scripts/runner.py` 的分派逻辑里加分支。
