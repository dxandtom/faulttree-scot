# SCoT Plan Schema —— 执行侧视图

> 与 `cs-scot-generate/references/plan_schema.md` 同一份契约。`plan.json` 是真正的
> **SCoT 计划**(SCoT v4.0 的子集),`cs-scot-execute` 据此执行。

## 执行模型
1. 读 `entry` → 当前节点;用 `memory_init` 初始化工作记忆。
2. 按 `kind` 分派当前节点:
   - **`script`**:`name`→`scripts/<name>.py`;解析 `inputs`(`data` 解析成数据文件),
     跑 `python scripts/<name>.py <数据> -o <临时>`,产出 JSON 按 `outputs` 写入 `$memory`。
     特例 `inputs.data = "$input.*"`(跨源判据脚本 cross_analyzer,格式契约 v3 起):runner 把本轮
     **全部**已提供数据源以 `--data 源=路径`(可多次)递入;该节点不进"缺源即跳过"清单,给多少吃多少。
   - **`agent_skill`**:解析 `inputs`(`fault_tree_md` 路径读成正文,其余归到
     `indicator_results`;上游被跳过/未产出的输入**不进** `indicator_results`,改记到顶层
     `missing_indicators`(键→原因),RCA 按「数据不足」对待),备到 `result/rca_input.json`,
     由 `name` 指定的技能(fault-tree-rca)产出结果。注:`indicator_results` 是**回退形态**——
     产物带 `tree_index.json` 时(常态),runner 把 rca_input 剪成局部子树形态
     (`fault_tree.outline`/`pruned_nodes` 等,无 `indicator_results` 键),权威定义见 fault-tree-rca 的 SKILL.md。
3. 沿 `transition.next_on_success` 走到下一节点;`transition.end=true` 结束。

## 引用
`$input.<key>`(由 `--data key=<文件>` 绑定)/ `$memory.<path>` / `$step.<id>.<field>` / `$output.<key>`。
路径不存在 → `null`。

## 扩展
新增 kind / 转移特性时:① 对齐 SCoT schema;② 在 `scripts/runner.py` 的分派里加分支。
当前实现 `script` / `agent_skill` + 直线转移。
