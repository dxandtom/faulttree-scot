# cs-scot-execute 代码说明

## 技能概述

cs-scot-execute 是七个技能里的执行环节。cs-scot-generate 为一个场景生成一次产物（执行计划 `plan.json`、树索引 `<短名>.tree_index.json`、每个数据源一份分析脚本），之后每来一批现场数据，就由本技能按执行计划把这些分析脚本顺序运行一遍，得到逐条件的事实，再把事实与故障树组装成根因分析的输入 `rca_input.json`，交给 fault-tree-rca。确定性的统计都在分析脚本里完成，本技能的脚本不加工数据、不判断节点成立与否；根因分析是唯一由模型判断的步骤。本技能同时是每个场景技能的执行指引：场景技能打包时，`SKILL.md`、`scripts/` 下的三个脚本和 `references/` 复制到场景技能的根目录，诊断资源放在 `assets/` 下。

输入是执行计划、产物目录下的分析脚本与树索引，以及用户提供的数据文件（已解析的表，或经 zip2csv 转成三张表的原始日志，另加原始目录 `raw`）。输出写在每轮独立新建的结果目录 `result-[标签-]<运行号>/` 里：`rca_input.json`（根因分析输入）、`run.json`（运行记录：输入指纹、缺口、逐节点执行状态）、`_compression.txt`（剪枝统计），并在结果根目录的 `index.jsonl` 追加一行索引。随后 fault-tree-rca 在同一目录产出 `rca.json`，由它的 `render_report.py` 渲染报告；cs-scot-reflect 评估时读取 `run.json`、`rca_input.json` 与 `rca.json`。

三个脚本的分工与调用关系如下：

```
用户给出的数据路径
  │  check_data.py        识别数据形态，打印 --data 的传入建议
  ▼
runner.py --plan assets/plan.json --data <指标>=<文件> ... --result-base <结果根目录>
  ├─ script 节点：子进程运行 assets/scripts/<name>.py，产出存入工作记忆 $memory
  └─ agent_skill 节点：导入 tree_prune.compose_rca_payload
        ├─ 有树索引且剪枝划算：局部子树（pruned_nodes、outline、覆盖报告）
        └─ 无树索引或剪枝不划算：完整故障树正文 + 全量事实（indicator_results）
     再附加 missing_indicators 与 knowledge.md 命中条目，写 result-<运行号>/rca_input.json
  ▼
fault-tree-rca（模型判断步骤）读 rca_input.json，在同一目录写 rca.json
```

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 执行指引：命令参数、场景技能的五步诊断流程、执行模型、输出格式 |
| `scripts/runner.py` | 脚本 | 执行计划的执行器：核对数据源、运行分析脚本、组装根因分析输入、写运行记录 |
| `scripts/check_data.py` | 脚本 | 数据入口检查：识别用户给的路径是原始采集目录、已解析表还是采集清单，给出传入建议 |
| `scripts/tree_prune.py` | 脚本 | 按树索引把事实挂到节点、剪出局部子树、生成覆盖报告与"怎么判的"说明；被 `runner.py` 导入 |
| `references/data_entry.md` | 参考文档 | 三种数据形态的识别、转换与缺口措辞 |
| `references/feedback_fix.md` | 参考文档 | 用户反馈某节点判错时的定位、分诊、修改、验证流程 |
| `references/plan_schema.md` | 参考文档 | 执行计划格式的执行侧视图：节点种类、引用、扩展方式 |
| `references/reply_format.md` | 参考文档 | 渲染出的三份报告文件、最终回复的组织、英文提问的处理 |

三个脚本和 `SKILL.md` 在 `skills/cs-scot-migrate/engine/` 下各有一份逐字节相同的副本，供 cs-scot-migrate 单独部署时使用；副本只能由 `skills/cs-scot-migrate/scripts/sync_engine.py` 生成，`tests/test_engine_sync.py` 核对两边一致。

## 各文件说明

### scripts/runner.py

**功能与作用**

`runner.py` 是执行计划的执行器，场景技能五步流程中第 3 步"执行"的入口。命令形式为 `python scripts/runner.py --plan <产物>/plan.json --data <指标>=<文件> ... [--base <目录>] [--result-base <目录>] [--result-suffix <标签>] [--run-id <编号>]`。它读执行计划，从 `entry` 节点出发沿 `transition.next_on_success` 顺序遍历：`script` 节点在子进程里运行分析脚本并把产出存入工作记忆；`agent_skill` 节点组装根因分析输入并写成 `rca_input.json`，然后打印待办提示由模型接手，它自身不调用模型。每轮运行在结果根目录下新建独立的 `result-[标签-]<运行号>/`，写 `run.json`、`rca_input.json`、`_compression.txt`，并向 `index.jsonl` 追加一行。只提供了一部分数据源时不报错，缺源步骤跳过并在结尾打印缺口汇总。

**实现介绍**

启动顺序在 `main()` 里：解析参数；`base` 取 `--base`，默认是 `plan.json` 所在目录；`check_product_format(base)` 读 `base/manifest.json` 的 `product_format_version`，与常量 `EXPECTED_PRODUCT_FORMAT = 3` 比对，缺失、读取失败或不一致都只打印 `[warn]`，不阻断；`_parse_data_map()` 把 `--data key=path` 列表转成字典；缺 `=` 或计划里没有 `nodes` 都以退出码 2 结束。

数据源核对由 `_check_data_sources()` 完成。它遍历 `kind` 为 `script` 的节点，用 `_input_key()` 从 `inputs.data` 取出 `$input.<key>` 的 key（`"$input.*"` 返回 `None`，不计入必需源），得到 `required`（key 到节点 id 列表）；`provided` 是 `--data` 里值非空的 key。`diag` 与 `cmd` 视为同一类数据的两代名字：计划要其中一个而只提供了另一个时，把同一路径登记到计划要的名字下并写回 `data_map`。随后 `_warn_data_gaps()` 打印提示：多给的源在有跨源节点时只说明会递给跨源步骤，否则警告 key 可能打错；必需源一个都没给时以退出码 2 退出，唯一例外是只给了 `raw` 且计划含 `"$input.*"` 节点；有跨源节点却没给 `raw` 也会警告。返回的 `missing` 是缺失 key 到受影响节点 id 的字典。

工作记忆 `ctx` 是一个字典，有 `input`（即 `data_map`）、`memory`、`step`、`output` 四个命名空间，另加 `_skipped_memory` 记录被跳过步骤的 `$memory` 路径对应哪个缺失源。`memory_init` 里的值先经 `resolve_ref()` 解析写入 `memory`。引用解析由 `resolve_ref()`、`_get_path()`、`_set_path()` 实现，支持 `$input.x`、`$memory.a.b[0]`、`$step.<id>.<field>`、`$output.x`，路径不存在返回 `None`，不以 `$` 开头的值按字面量返回。`store_outputs()` 把节点产出按 `outputs` 映射写入 `$memory` 或 `$output`：产出是字典且含该字段名时取字段，否则存整个产出。

结果目录由 `_make_result_dir()` 决定：运行号取 `--run-id`，否则由 `_gen_run_id()` 生成 `run-<UTC 时间戳>-<3 字节随机数>`；目录名为 `result-<标签>-<stamp>` 或 `result-<stamp>`，`stamp` 是去掉 `run-` 前缀并只保留字母、数字、`-`、`_` 后的运行号（为空时用 `unnamed`），标签同样清洗；目录已存在时抛出 `SystemExit`（重名只可能来自自定义 `--run-id`）。接着 `_write_run_manifest()` 写 `run.json`：`run_id`、`created_at`、`plan_id`、`product_dir`、`inputs`（每个已提供的数据源经 `_file_fingerprint()` 得到路径、是否存在、字节数、修改时间和 sha1，超过 512 MB 不算 sha1，记 `sha1_skipped`）、`missing_inputs`（有缺口时）、`missing_kind`（每个缺口是 `collection_absent` 还是 `not_provided`，提供了 `raw` 即判为前者）；再向结果根目录的 `index.jsonl` 追加一行，含 `run_id`、`created_at`、`dir` 与各输入的 sha1 或字节数。

主循环对每个节点调用 `_run_node()`，返回值存入 `ctx["step"][节点 id]`；访问超过 1000 个节点抛 `RuntimeError`，防止转移成环；转移指向不存在的节点抛 `KeyError`；`transition.end` 为真时结束。`_run_node()` 按 `kind` 分派。`script` 节点若其数据源在 `missing` 中，打印 `[skip]`，把 `outputs` 里的 `$memory` 路径登记到 `skipped_memory`，返回占位字典 `{"_skipped_missing_input": <源名>}`（只进 `$step`，不写 `$memory`，下游解析到的是 `None`），并记状态 `{"skipped": ...}`；否则调用 `run_script_node()`，成功记 `{"ok": True}`，失败记 `{"ok": False, "error": <末 600 字>}` 后照旧抛出。`agent_skill` 节点调用 `run_agent_skill_node()`，成功记 `{"ok": True, "note": ...}`。其他 `kind` 抛 `ValueError`。每步状态由 `_record_step_status()` 立即合并写入 `run.json` 的 `execution_status`，这个字段是 cs-scot-reflect 的 `classify.py` 判断执行环境故障的依据。

`run_script_node()` 按 `name`（缺省用节点 id）找 `base/scripts/<name>.py`，不存在抛 `FileNotFoundError`。普通节点解析 `inputs.data` 得到数据文件（为空抛 `ValueError`，相对路径以 `base` 为基准，文件不存在抛 `FileNotFoundError`），运行 `python <脚本> <数据文件> -o <临时目录>/out.json`；`inputs.data` 为 `"$input.*"` 的跨源节点则把 `ctx["input"]` 里路径存在的每个源以 `--data <源>=<路径>` 逐个传入，路径不存在的源只打印警告，不静默丢弃。子进程用 `subprocess.run()` 执行，超时 600 秒，退出码非零时抛 `RuntimeError` 并附 stderr 的末 2000 字；成功后读出 JSON，经 `store_outputs()` 存入工作记忆。

`run_agent_skill_node()` 组装根因分析输入。先用 `resolve_inputs()` 解析全部输入，再用 `_missing_indicator_inputs()` 剔除上游没有产出的指标输入：解析结果为 `None` 或为跳过占位字典的输入，按引用形式（`$memory.<路径>` 查 `_skipped_memory`，`$step.<id>` 或 `$step.<id>.<field>` 查该步的占位）找出缺的是哪个数据源，写成 `{输入名: 原因}`。`fault_tree_md` 输入经 `_md_path_of()` 解析成 `.md` 路径，`_compose_payload()` 据此找同名的 `.tree_index.json`：存在则导入 `tree_prune.compose_rca_payload()` 得到局部子树形态的输入与压缩说明，导入或剪枝抛异常时回退；没有树索引或回退时输入是 `{"fault_tree_md": 正文, "indicator_results": 各源事实}`。有缺失源时加顶层键 `missing_indicators`。`_attach_knowledge()` 读 `tree/knowledge.md`：`_parse_knowledge_entries()` 按 `### 标题` 切条目，只识别正文开始前的 `适用:` 与 `阶段:` 行（全角冒号归一为半角，标签按 `、`、`,`、`，`、`/` 和空白切分）；`_select_knowledge()` 剔除正文为空的条目、阶段标了"绑定"或"生成"且不含"诊断"的条目，以及带标签但不含"通用"且任一标签都不出现在 `name_pool` 中的条目。`name_pool` 在局部子树形态下是场景名加全部保留节点的名称与判据的拼接串，完整形态下是整篇故障树正文。命中条目挂到 `payload["knowledge"]`。最后把输入写到 `<结果根目录>/<结果子目录>/<节点 id>_input.json`（计划里该节点 id 为 `rca`，所以文件名是 `rca_input.json`），`_print_agent_skill_todo()` 打印待办提示（缺失的数据源、压缩说明并写入 `_compression.txt`、该用哪个技能产出结果、技能未注册时改读其 `SKILL.md` 的备用做法）；再把占位字典 `{"_pending_skill": 技能名, "_input": 输入路径}` 按 `outputs` 存入工作记忆。

全部节点运行完后，有缺口则 `_print_missing_summary()` 打印缺口汇总：提供了 `raw` 时说明缺口是采集数据本身不含该类内容，不向用户要数据；否则给出补数据重新运行的 `--data` 参数。

**修改时要注意**

- `EXPECTED_PRODUCT_FORMAT` 与 `cs-scot-generate/scripts/assemble_scenario.py` 的 `PRODUCT_FORMAT_VERSION` 成对维护，产物结构有破坏性变化时两边一起加 1。注释记录：版本 2 引入绑定的 `filter` 字段时，旧产物里的旧内核副本会静默忽略 `filter` 造成假阳性，比对提示重新生成就是为此。
- 本文件在 `skills/cs-scot-migrate/engine/runner.py` 有逐字节相同的副本，由 `sync_engine.py` 生成，`tests/test_engine_sync.py` 用 `--check` 核对。改完源文件要运行 `python skills/cs-scot-migrate/scripts/sync_engine.py`，不要手改副本；`sync_engine.py` 的注释记录副本曾落后源文件 4 天。
- cs-scot-migrate 的 `migrate_fault_check.py` 用字符串 `RUNNER_CAP = "execution_status"` 判断一份 `runner.py` 是否为当前版本（源文件和副本都必须含它才可信）；改名 `execution_status` 字段会让迁移刷新把当前版本当成旧版。cs-scot-reflect 的 `classify.py`、`eval_run.py` 也按 `execution_status`、`missing_kind` 的字段名读取 `run.json`。
- `rca_input.json` 的文件名来自节点 id（`<id>_input.json`），cs-scot-reflect 与 fault-tree-rca 的脚本都按 `rca_input.json` 这个名字读取；执行计划里 `agent_skill` 节点的 id 不能随意改。
- 缺源步骤的占位字典只进 `$step` 不进 `$memory`，`_missing_indicator_inputs()` 依赖这一约定识别三种引用形式，改动其中一处要同步另一处。跨源节点对不存在的路径只警告不丢弃，注释说明否则事实层只会报"无数据源"，定位不到真正原因。
- 外部知识的阶段过滤源于一次实际问题：绑定注意事项标了"通用"被整段下发，诊断阶段用不上却占用上下文。`_load_knowledge()` 在文件不存在或读取失败时返回空列表，知识加载不能影响主流程。
- `_print_agent_skill_todo()` 的备用做法要始终打印，注释说明能力最弱的运行环境恰恰需要它，不要因为产物目录里没有 fault-tree-rca 副本而省略。
- `_run_node()` 捕获的是 `BaseException`，记录完状态后原样抛出；不要改成吞掉异常，评估侧靠 `ok: false` 归因。
- `--data key=` 的空值视为未提供，`_write_run_manifest()` 过滤空值的原因是 `Path('')` 会指向当前工作目录。

### scripts/check_data.py

**功能与作用**

`check_data.py` 是场景技能五步流程中第 1 步"数据入口"的第一个动作：在运行执行器之前，先确认用户给的路径里是什么、层级对不对、该怎样传给 `runner.py`。用法是 `python check_data.py <用户给的数据路径>`，没有第三方依赖。退出码 0 表示识别出可用形态，报告末尾带"建议的传入方式"，照抄即可；退出码 2 表示形态无法识别或只有采集清单，此时把报告原样给用户，不强行继续。文件开头的注释记录了设置它的两起实际问题：`--data raw=` 传错一层目录，S 类脚本一个网元都找不到，全程"数据不足"；采集清单 xlsx 被当成数据表传入，42 个判据一起报"找不到列"。

**实现介绍**

`main()` 先把标准输出切到 UTF-8，参数个数不对或路径不存在都返回 2。输入是单个文件时交给 `_dir_of_input_file()`：zip 文件只打印用 `assets/zip2csv/scripts/zip2csv.py` 转换的指引并返回 0，其他文件按它所在目录继续检查。随后按形态依次识别，结果累积在三个列表里：`usable`（识别出的可用形态）、`problems`（发现的问题）、`suggest`（建议的 `--data` 参数）。

形态 A 是原始采集目录。`KINDS` 定义了五个分类目录名 `CONFIG`、`CMD`、`LOG`、`DIAG`、`OPR`，其中 `DIAG` 是 `CMD` 的旧名。`_kind_dirs()` 返回某目录下存在的分类目录。路径下直接有分类目录时，`_report_flat_ne()` 按"平铺单网元"报告，网元名取目录名，建议 `--data "raw=<该目录>"`，分类目录里没有任何 `.txt` 或 `.log` 文件记为问题。否则 `_report_ne_parents()` 调用 `_find_ne_parents()` 向下最多 4 层寻找"网元层父目录"，即子目录里至少有一个带分类目录的目录；最多报告 3 个父目录、每个列 8 个网元及各分类的文件数，建议 `--data "raw=<父目录>"`，父目录不是用户给的顶层时特别提醒要传父目录这一层。

形态 B（已解析表）与形态 C（采集清单）由 `_scan_tables()` 处理：扫描根目录下的 `*.csv`、`*.xlsx`、`*.xlsm`（跳过 `~$` 开头的临时文件），用 `_csv_header()`（依次尝试 utf-8-sig、utf-8、gbk、gb18030、latin-1 编码）或 `_xlsx_header()` 读表头。`_xlsx_header()` 优先导入同技能 `assets/scripts/` 或脚本同目录下的 `xlsx_reader.py` 调用 `sheet_briefs()`，没有再试 `openpyxl`，都不行返回 `None`。表头与 `_MANIFEST_SIGNS`（`fileType`、`relativePath`、`zipFile`）至少有 2 列交集的判为采集清单；与 `_TABLE_SIGNS` 任一指标的指纹列有交集的判为该指标的数据表（`cmd`：`CmdName`、`ObjContent`、`CmdResult`；`log`：`Summary`、`Log`、`Level`、`Content`；`config`：`level1`、`CfgType`、`L2Module`）。`_report_tables()` 打印识别结果：数据表给出 `--data "<指标>=<表路径>"` 的建议，采集清单点名禁止当数据表传入，读不了表头的文件先跳过。

`_conclude()` 收尾：列出问题；`usable` 非空则打印建议并返回 0，识别出 `raw` 时追加提醒要用 zip2csv 转成三张表；一个形态都没识别出时，若目录里能找到散落的 `.txt`、`.log`、`.zip`、`.rar` 文件，提示先用 zip2csv 转换（它会调用内置的 ifilter 归类）再重新运行本检查，否则提示把报告给用户确认，两种情况都返回 2。

**修改时要注意**

- 本文件在 `skills/cs-scot-migrate/engine/check_data.py` 有副本，同样由 `sync_engine.py` 生成和核对，改完源文件要同步。迁移刷新打包技能时用字符串 `def main` 作为本文件的版本标记。
- 采集清单的表头含 `neName` 这类列，容易被误认为数据表；注释说明 `neName` 会误导"看见 NEName 就是数据"的判断，所以 `_scan_tables()` 先按 `_MANIFEST_SIGNS` 判清单，再按 `_TABLE_SIGNS` 判数据表。调整指纹列时要保持这个先后顺序。
- 识别清单只覆盖 cmd、log、config 三类表；`references/data_entry.md` 说明 EAV 窄表（`DataItem`、`DataValue`）不在识别清单里，报告不会列出它，这类文件按文件名匹配后直接传入。扩充识别清单时同步修改该文档。
- 退出码 0 与 2 的含义被 `SKILL.md` 和 `data_entry.md` 直接引用（"退出码 2 就把报告原样给用户"），不要改动退出码的语义。

### scripts/tree_prune.py

**功能与作用**

`tree_prune.py` 把树索引与各数据源的逐条件事实组装成根因分析的输入。它只做确定性的整理，不判断节点成立与否：一是剪枝，保留有事实的节点及其到根的祖先，其余分支去掉，每个保留节点把事实挂上，使根因分析既有结构（父子、路径、AND/OR）又有事实，而不必读整棵树；二是覆盖报告，列出没有任何事实的节点，以及事实引用了树索引里不存在的节点编号的情况。`runner.py` 的 `_compose_payload()` 导入它的 `compose_rca_payload()`；它也可以单独运行：`tree_prune.py --index <tree_index.json> --results <源>=<事实 json> ... -o <输出>`，加 `--md <故障树正文>` 可按与 runner 相同的规则报压缩率。

**实现介绍**

入口是 `compose_rca_payload(index, results, md_text)`，注释称之为唯一出口，runner、基准测试和单独测压缩的脚本都从它取结果。它先调用 `prune()` 得到剪枝结果，组装成候选输入 `cand`：`fault_tree`（含 `scenario`、`root`、`outline_legend`、`outline`、`pruned_nodes`）、`unmapped_facts`、`tree_issues`（即覆盖报告）、`uncovered_values`；再构造完整形态 `md_payload`（`fault_tree_md` 加 `indicator_results`），分别序列化比较长度。两条回退规则：事实总数大于 0 且填了 `node_id` 的事实不足一半时，`mode` 为 `fallback_coverage`，回退完整形态；有故障树正文且局部子树的长度不小于完整形态时，`mode` 为 `fallback_floor`，同样回退，压缩率记 0。其余情况 `mode` 为 `pruned`，返回局部子树和节省百分比。返回字典含 `payload`、`mode`、`note`（给终端打印的中文说明）、`before`、`after`、`eff_after`、`saved`、`floored`、`kept`、`total_nodes`、`coverage`。覆盖率用 `total - facts_without_node_id` 计算，填了 `node_id` 但在树索引里找不到的事实也算入"已填"。

`prune(index, results)` 的处理顺序：`_alias_table()` 建别名表，节点键（如 `n3`）和 `code` 字段都指向同一节点，绑定里填哪种编号都能挂上；`_facts_by_node()` 遍历每个源输出的 `conditions` 列表，按别名解析后的 `node_id` 归组，每条事实保留 `indicator`、`id`、`class`、`condition_text`、`mapping_reason`、`fact`，没填 `node_id` 的归入 `__no_node_id__`，解析不到的保留原值；`_kept_node_ids()` 计算保留集合：有事实的真实节点及其沿 `parent` 到根的祖先、`root` 节点，以及任一保留节点的子节点中名称或判据含"非典型"、"其他"、"其它"、`unspecified`、`unknown`、`other`、`misc` 的节点（子串匹配，不区分大小写），注释说明保留这类节点是为了给漏绑的记录一个可归入的真实节点。对每个保留节点，`_drop_insufficient_if_established()` 在该节点已有 `status` 为 `ok` 且 `predicate_satisfied` 为真的事实时，剔掉同节点的 `insufficient_data` 事实；注释记录的实际问题是某源声明无法提供而另一源实测成立时，模型把前者当作阻碍、不肯把结论等级从疑似根因提升到确证根因。`ok` 且不成立的事实和 `manual` 事实照留。`_node_record()` 生成 `pruned_nodes[id]`：`name`、`parent`、`level`，有则带 `name_en`、`condition`、`combine`、`pass_through`，非叶子节点列出 `uncovered_children`（带判据、不带 `pass_through` 标记、没有事实的子节点；`pass_through` 为真的节点是不参与判断的分类节点，代码里称免检），叶子标 `is_leaf`，事实经 `_slim_fact()` 精简后放在 `facts`。`_coarse()` 给 `outline` 填粗状态：带 `pass_through` 标记的节点为"免检"，无事实为"未覆盖"，任一事实 `ok` 且成立为"成立"，任一 `ok` 且不成立为"不成立"，有 `insufficient_data` 或 `manual` 为"数据不足"，否则为 `?`。`OUTLINE_LEGEND` 随输入下发，注释记录曾用英文缩词 `hold`、`pass` 被模型误读成"搁置待查"，因此改为不可误读的中文并把定义随数据一起给出。

`_slim_fact()` 决定哪些事实字段进入根因分析输入：`id`、`indicator`、截到 100 字的 `mapping_reason`，以及 `_FACT_KEEP` 白名单内的字段；S 类脚本的 `evidence` 最多 5 条、每条经 `_ev_str()` 转成文字并截到 300 字，`matched_nes` 最多 8 个并另存 `matched_nes_total`；非 `ok` 事实带截到 160 字的 `reason`；`explain` 由 `_explain()` 按事实类别机械拼成"怎么判的"说明，拼不出时用事实自带的 `explain` 截到 300 字。`_explain()` 对 `insufficient_data` 返回"查不了"加原因，对 `manual` 返回需人工确认；其余按 `expand` 逐值展开（`_explain_expand()`）、C 与 G 类字段匹配（`_explain_match()`，含 `absent` 反向判定的说明）、A 类阈值（`_explain_threshold()`，区分 `count_rows` 与聚合）、P 类两侧对比（`_explain_pair()`）、D 类组合（`_explain_combo()`，含 `group_by` 时走 `_explain_grouped()`，子条件由 `_sub_explains()` 递归拼接，最多展开 4 条）分别生成，`_explain_prefix()` 补上参数化查询代入的参数和 `filter` 筛出的行数。所有数字取自事实原字段，任何异常都返回空串，不影响剪枝。

覆盖报告部分：`uncovered_condition_nodes` 收录所有不带 `pass_through` 标记且没有事实的节点，只留 `id` 与 `name`（此处不检查节点是否带 `condition`，与 `uncovered_children` 的筛法不同）；`_unmapped_facts()` 返回引用了不存在节点编号的编号列表 `orphan_node_ids`，并把这些事实与没填 `node_id` 的事实精简后合并为 `unmapped`；`_uncovered_values()` 先用 `_column_claims()` 汇总 C、G 类 `ok` 事实在每个 `resolved_column` 上认领的 `targets`、匹配模式、总行数和取值分布 `top`（带行级 `filter` 的事实不参与），再找出每列里出现、却没被任何绑定认领的取值，跳过占比不低于 50% 的取值（多为该列的正常值），每列最多 8 个。注释特别说明不按认领值个数去噪，因为只绑了一个值却漏了一批失败码正是最该报的情况。

命令行入口 `main()` 解析 `--index`、多个 `--results name=path`、`-o`，调用 `prune()` 写出结果并打印保留节点数与覆盖统计；给了 `--md` 时再走 `compose_rca_payload()` 打印实际压缩率。`--results` 缺 `=` 以退出码 2 退出。

**修改时要注意**

- `_FACT_KEEP` 是进入模型上下文的字段白名单，与 `cs-scot-generate/scripts/condition_kernels.py` 有约定：内核算子输出的审计字段（如 `matched_by_target`）依赖不在白名单内而不流入根因分析，往白名单加字段前要确认该字段是给模型看的。`_explain()` 读取的是精简前的完整事实，所以说明文字能用到 `filter`、`targets`、`sub_facts` 等不在白名单里的字段。
- `compose_rca_payload()` 是唯一出口，两条回退规则都在它里面；不要在调用方另写剪枝或回退逻辑。`uncovered_values` 键必须恒在（空则为空字典），注释说明 fault-tree-rca 的规则据此把漏绑记录归到"非典型/其他"节点报出。
- cs-scot-migrate 用字符串 `TREE_PRUNE_CAP = "matched_nes"` 判断一份 `tree_prune.py` 是否为保留 S 类脚本证据的当前版本；改名该字段会让迁移刷新把当前版本当成旧版。本文件在 `skills/cs-scot-migrate/engine/tree_prune.py` 有副本，改后要运行 `sync_engine.py` 同步。
- `_drop_insufficient_if_established()` 只剔除已确立节点上的 `insufficient_data`，未确立节点上的保留给待确认与疑似根因作依据；放宽剔除范围会丢掉"数据不足"的证据。
- `_sub_explains()` 的注释提醒 `sub_facts` 不进精简后的产物，说明文字不要引用它的字段路径。
- `OUTLINE_LEGEND` 的措辞是给模型读的，修改 `_coarse()` 的状态词时要同步图例，并与 fault-tree-rca 的 `rca_query.py`（读取 `outline` 与 `outline_legend`）保持一致。

### SKILL.md

`SKILL.md` 是本技能的说明，也是每个场景技能的执行指引正文：cs-scot-migrate 刷新场景技能时把它的正文替换为当前版本，只保留场景技能自己的名称与描述。读者是在平台上执行诊断的模型和用户。内容包括术语表、`runner.py` 的命令与参数、场景技能形态下的五步流程（数据入口、数据与指标匹配、执行、输出汇报、用户反馈）、执行模型、`rca.json` 的输出格式与设计要点，并指明每一步何时去读 `references/` 下的哪份文档。

### references/data_entry.md

`data_entry.md` 说明三种数据形态（已解析的表、零散的原始文件、原始日志目录）各怎么识别、转换和传入，以及缺口如何向用户措辞。读者是按 `SKILL.md` 第 1 步执行的模型。它与 `check_data.py` 直接对应：解释退出码 0 和 2 的处理，列出该脚本识别数据表的表头指纹，说明 EAV 窄表不在识别清单里；也对应 `runner.py` 对 `raw`、`diag` 与 `cmd` 两代源名、`missing_kind` 的处理。

### references/feedback_fix.md

`feedback_fix.md` 规定用户指出某节点判错时的四步处理：定位（查树索引、`index.jsonl` 最后一行指向的结果目录里的 `rca_input.json` 与 `rca.json`）、分诊（按 `resolved_column`、`match.targets`、`absent`、`hits`、`top`、`explain` 等字段判断是算子绑定问题、判读规则问题还是树的问题）、修改（改分析脚本 `CONDITIONS` 里的算子绑定，或往 `assets/tree/knowledge.md` 加条目）、验证。读者是执行诊断的模型。事实字段由 `tree_prune.py` 的 `_slim_fact()` 放入 `rca_input.json`，`knowledge.md` 的条目格式由 `runner.py` 的 `_parse_knowledge_entries()` 解析。

### references/plan_schema.md

`plan_schema.md` 是执行计划格式的执行侧视图，与 `cs-scot-generate/references/plan_schema.md` 是同一份格式约定，字段的完整定义以生成侧为准。它描述 `runner.py` 读计划的方式：`script` 与 `agent_skill` 两种节点的处理、`"$input.*"` 跨源节点的特例、`missing_indicators` 的来源、两种输入形态，以及四种引用。读者是要改执行器或排查计划问题的开发者；新增节点种类时它要求先对齐 SCoT 的 schema，再在 `runner.py` 的分派逻辑（`_run_node()`）里加分支。

### references/reply_format.md

`reply_format.md` 规定诊断结论的呈现：由 fault-tree-rca 的 `render_report.py` 渲染出 `rca_report.md`、`rca_tree.txt`、`rca_reply.md` 三份文件，最终回复正文原样贴 `rca_reply.md` 全文，前面加执行过程，末尾的补充说明是唯一可以自己写的部分；用户整段用英文提问时加 `--lang en`，并规定哪些内容由脚本输出英文、哪些由模型逐句直译、哪些不翻。读者是执行诊断的模型。它不涉及本技能的脚本，但执行过程一段要写进 `runner.py` 打印的缺口汇总与被跳过的步骤。
