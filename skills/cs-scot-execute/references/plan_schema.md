# SCoT Plan Schema —— 执行侧视图

> 与 `cs-scot-generate/references/plan_schema.md` 同一份契约。`plan.json` 是真正的
> **SCoT 计划**(SCoT v4.0 的子集),`cs-scot-execute` 据此执行。

## 执行模型
1. 读 `entry` → 当前节点;用 `memory_init` 初始化工作记忆。
2. 按 `kind` 分派当前节点:
   - **`script`**:`name`→`scripts/<name>.py`;解析 `inputs`(`data` 解析成数据文件),
     跑 `python scripts/<name>.py <数据> -o <临时>`,产出 JSON 按 `outputs` 写入 `$memory`。
   - **`condition_eval`**:跑同目录 `eval_conditions.py`(+ `validate_conditions.py` 自检),
     按 `inputs.conditions`(绑定文件)+ `inputs.data`(各源 `$input`)算出 `condition_facts` 存入 `$memory`。
   - **`agent_skill`**:模型步骤,**可重入**。带 `produces`(如 `conditions.json` / `result/rca.json`):
     产物已存在 → 载入续跑;不存在 → 备好 `result/<id>_input.json`、打印待办、**暂停**(产出后重跑同一命令续上)。
3. 沿 `transition.next_on_success` 走到下一节点;`transition.end=true` 结束。

## 引用
`$input.<key>`(由 `--data key=<文件>` 绑定)/ `$memory.<path>` / `$step.<id>.<field>` / `$output.<key>`。
路径不存在 → `null`。

## 扩展
新增 kind / 转移特性时:① 对齐 SCoT schema;② 在 `scripts/runner.py` 的分派里加分支。
当前实现 `script` / `agent_skill` + 直线转移。
