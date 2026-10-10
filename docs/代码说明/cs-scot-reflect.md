# cs-scot-reflect 代码说明

## 技能概述

cs-scot-reflect 是评估环节。按 `docs/design.md` 的流程，生成环节（cs-scot-generate）产出场景技能，执行环节（cs-scot-execute 与 fault-tree-rca）对一批现场数据跑出 `result-<运行号>/` 目录，本技能在这之后独立运行：把执行结果与用例库里记录的期望根因逐条比对（下文称对账），判定每个用例通过与否，把没通过的原因归到六类错误（ENV_FAILURE、INPUT_GAP、BINDING_GAP、KNOWLEDGE_GAP、CRITERIA_GAP、VERDICT_GAP）之一，算出整批的通过率与得分，并写出反馈文件交给 cs-scot-generate-optimize 的反馈处理线。判定与分类全部由脚本按固定规则完成，模型只负责运行脚本并转述产出文件。期望文件 `expected.json` 只放在 `cases/` 目录，不传给生成与执行环节，以免泄露答案。

输入有四类：用例库 `cases/<case_id>/expected.json`，记录期望根因、是否阴性用例（健康数据、期望没有根因的用例，`negative` 为 `true`）与期望事实，格式见 `references/expected_schema.md`；执行结果目录里的 `run.json`、`rca_input.json`、`rca.json`，格式见 `references/result_schema.md`；场景技能目录下的树索引 `assets/tree/*.tree_index.json` 与分析脚本 `assets/scripts/*_analyzer.py`；可选的上一轮 `evaluation_report.json`，用于接续轮次。输出写在评估输出目录：每个用例一个子目录 `<case_id>-v<round>-<时间戳>/`，内有评估报告 `evaluation_report.json`（唯一对外出口）、单用例评分 `quality_score.json`、评估元数据 `eval_run.json`，有问题时还有失败签名 `failure_signatures.json`；根目录有整批汇总 `quality_score_batch.json`、反馈 `feedback_to_generate.json`、全部失败签名 `failure_signatures.json`，以及跨轮累计的熔断历史 `fuse_history.jsonl`（熔断指同一用例、同一节点、同一类错误连续三轮没修好，标记转人工）。

`scripts/` 下十个脚本分三组。评估主线由 `eval_run.py` 编排，它以子进程方式调用四个单一职责的检查脚本，再在进程内导入两个评分模块；一键回归与建用例库是另外两条入口：

```
eval_run.py                    主入口：单用例（--case）或批量（--case-dir）
 ├─ check_binding.py           子进程，整批只跑一次：树索引 + 分析脚本 → 未绑定节点列表
 └─ 对每个用例依次：
     ├─ reconcile.py           子进程：expected.json + rca.json → 对账结果
     ├─ check_tree.py          子进程：期望节点编号是否在树索引里
     ├─ classify.py            子进程：run.json + rca_input.json + rca.json + 上面三份结果 → 错误类别
     ├─ score_report.py        进程内导入：evaluation_report.json → quality_score.json
     └─ batch_summary.py       进程内导入：各用例评分 → quality_score_batch.json
regress.py                     一键回归：逐用例运行执行器与根因分析脚本，再以子进程调用 eval_run.py --case，
                               最后用 batch_summary.py 汇总；只有节点交付包时先调用 case_gen.py 建用例库
case_gen.py                    从迁移记录与节点交付包生成 cases/<case_id>/expected.json
make_drill.py                  生成离线演练目录（迷你产物、九个用例、构造好的执行结果）
```

数据流向是：`reconcile.py` 的对账计数决定要不要查错；`classify.py` 只在有漏判（期望根因没有出现在任何结论等级里）或假阳（阴性用例却出现确证根因）时按 0 到 5 的顺序查错，命中即停；`eval_run.py` 根据对账与分类结果算出评级 `verdict`（PASS、PASS_WITH_GAPS、PARTIAL_PASS、FAIL、EVAL_ERROR 五个值，只回答这个用例这次要不要管），写报告，再调 `score_report.py` 把报告换算成六维评分，最后由 `batch_summary.py` 把各用例评分汇总成整批文件。下文先说明主线上的 `eval_run.py`、`reconcile.py`、`classify.py`、`score_report.py`、`batch_summary.py`，再说明 `case_gen.py`、`regress.py`、`check_binding.py`、`check_tree.py`、`make_drill.py` 以及 `SKILL.md` 和 `references/` 下的文档。

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 给模型执行的操作手册：建用例库、评估、一键回归三个入口的命令与参数，评估规则，输出位置，防泄露答案的规定 |
| `references/error_classification.md` | 参考文档 | 六类错误各自的定义、检测条件、证据字段与处理方式摘要，以及对账结果类型与熔断机制 |
| `references/expected_schema.md` | 参考文档 | 用例期望文件 `expected.json` 的字段、约束与防泄露答案的规则 |
| `references/feedback_schema.md` | 参考文档 | 反馈文件 `feedback_to_generate.json` 的位置、结构与字段说明 |
| `references/output_files.md` | 参考文档 | 评估每个输出文件的完整例子与计算规则，业务取数与平台展示按它读 |
| `references/result_schema.md` | 参考文档 | 执行结果 `run.json`、`rca_input.json`（两种形态）、`rca.json` 的结构，是评估的输入格式要求 |
| `references/score_dimensions.md` | 参考文档 | 六个评分维度的键名、中英文名称、描述与排序，供平台配置置信度展示 |
| `scripts/eval_run.py` | 脚本 | 评估主入口：定位用例与结果目录，依次调用对账、绑定检查、树节点检查、分类脚本，算出评级，写报告、评分、反馈与熔断历史 |
| `scripts/reconcile.py` | 脚本 | 对账：逐条期望根因与 `rca.json` 的确证、疑似根因比对，输出精确命中、祖先命中、降级命中、漏判、假阳、真阴的计数与明细 |
| `scripts/classify.py` | 脚本 | 分类：在有漏判或假阳时按 0 到 5 的顺序检查六类错误，命中即停，输出错误类别、分类代号与证据 |
| `scripts/score_report.py` | 脚本 | 把 `evaluation_report.json` 机械换算成单用例六维评分 `quality_score.json` |
| `scripts/batch_summary.py` | 脚本 | 整批汇总算法的唯一来源：通过率、平均分、六维通过比例，写 `quality_score_batch.json` |
| `scripts/case_gen.py` | 脚本 | 从迁移记录与节点交付包机械生成用例库，没有迁移记录时按节点名唯一匹配 |
| `scripts/regress.py` | 脚本 | 一键回归：对用例库逐用例运行执行器与根因分析的确定性步骤，再调用评估并汇总通过率；也可只对既有结果做评估 |
| `scripts/check_binding.py` | 脚本 | 静态检查树索引里有判据的节点在分析脚本中是否有算子绑定，输出未绑定节点列表 |
| `scripts/check_tree.py` | 脚本 | 检查给定的节点编号是否存在于树索引，供 KNOWLEDGE_GAP 判定 |
| `scripts/make_drill.py` | 脚本 | 生成自带迷你产物、九个用例与构造好的执行结果的演练目录，离线跑通评估与反馈处理 |

## 各文件说明

### scripts/eval_run.py

**功能与作用**

评估的主入口与编排层，1372 行。它把一个用例（`--case`）或整个用例库（`--case-dir`）的执行结果与期望比对，产出评估报告。运行方式有两种：模型按 `SKILL.md` 入口二直接运行；`regress.py` 在一键回归里对每个用例以子进程方式运行它的单用例模式。输入是用例目录、执行结果目录（直接指到含 `rca.json` 的结果目录，或含多个 `result-*` 子目录的父目录）、场景技能目录，以及可选的 `--out`、`--prev-evaluation`、`--scenario`。输出是「技能概述」列出的全部评估文件。退出码：参数不合法时 `argparse` 以退出码 2 报错；批量模式下用例目录不存在退出 1；其余情况正常结束返回 0，用例层面的失败记在报告的 `success` 与 `verdict` 字段里，不用退出码表达。

**实现介绍**

脚本按「准备、定位结果目录、逐用例处理、熔断、评级与报告」几段组织，下面按处理顺序说明。

#### 准备（`main`、`_parse_args`、`_collect_case_ids`）

`_parse_args` 解析参数并校验 `--case` 与 `--case-dir` 必须且只能给一个。`main` 先确定用例根目录：批量模式用 `--case-dir`，单用例模式用 `--cases-dir`（默认 `cases`）。代码注释记录了早先的错误：`--case-dir` 曾只被当作模式开关，扫描却用 `--cases-dir` 的默认值，传绝对路径就报「case-dir 不存在: cases」。没给 `--out` 时由 `build_default_out_dir` 推导输出目录：结果目录名以 `-result` 结尾则去掉后缀作场景名，名字正好是 `result` 则取父目录名，否则用 `--scenario` 或目录名本身；输出目录是 `<结果目录的父目录>/<场景名>-eval`。临时文件放系统临时目录（`tempfile.mkdtemp`，前缀 `cs-scot-reflect-`），其中 `progress.jsonl` 由 `update_progress` 逐步记录各任务状态。

`find_tree_indexes` 在 `<skill-dir>/assets/tree/` 下找 `*.tree_index.json`，该目录不存在时退回全目录递归查找；后续绑定检查、树节点检查、分类都只用排序后的第一个。`_run_shared_binding_check` 调用 `check_binding.py` 一次，结果所有用例共用，因为它只依赖树索引与分析脚本，与用例无关。分析脚本目录由 `find_skill_scripts_dir` 确定，依次尝试树索引所在目录的兄弟目录 `scripts/`、`<skill-dir>/assets/scripts`、`<skill-dir>/scripts`。`_load_fuse_history` 从 `<out>/fuse_history.jsonl` 读入熔断历史，每行一条 JSON。

#### 定位结果目录（`detect_result_mode`、`find_case_result_dir` 及辅助函数）

`detect_result_mode` 把 `--result-dir` 分为三种：直接含 `rca.json` 的 `single`、含 `result-*` 子目录的 `multi`、不存在或为空的 `empty`。`find_case_result_dir` 在 `single` 模式原样采用。在 `multi` 模式先用 `find_result_subdirs` 列出按修改时间倒序的子目录（名字以 `-abandoned` 或 `-superseded` 结尾的不参与，这是人工标记的空目录或不完整轮次），只保留含 `rca.json` 的；一个都没有时，恰有一个子目录就采用它，否则返回 `None`。有候选时按五步匹配，多个命中都取最新：① 目录名含完整 `case_id`；①b `_suffix_token_match` 用正则 `_SUBDIR_SFX_RE` 取出目录名里时间戳之前的后缀段，与 `case_id` 按段精确比对，后缀 `n5` 只匹配 `n5-*`，不匹配 `n50-*`；② `_run_inputs_match` 读该目录 `run.json` 的 `inputs` 里记录的数据路径，与用例的数据目录（`expected.json` 的 `data_source` 和 `cases/<case_id>/data`）经 `_norm_path` 归一化后互为前缀即认定归属；③ 只剩一个候选则采用并警告；④ 都对不上返回 `None`，打印错误并建议执行时用 `runner --result-suffix <case_id>` 标注。拒绝猜测的原因写在注释里：拿别的用例的结果对账，会把漏判与假阳全部记到错误的用例上。

#### 逐用例处理（`process_case` 及 `_run_reconcile`、`_run_tree_check`、`_run_classify`）

`process_case` 先读 `expected.json`，不存在则返回 `ok` 为 `False` 的结果；再定位结果目录，读入 `run.json`、`rca_input.json`、`rca.json`，缺哪个就是空字典。然后依次运行子脚本：`_run_reconcile` 调 `reconcile.py` 写对账文件，`rca.json` 存在却没产出对账文件才算评估失败；共享的绑定检查结果写成 `<case_id>_binding.json` 供分类脚本读取；`_run_tree_check` 把期望根因的 `node_id` 列表传给 `check_tree.py`；`_run_classify` 调 `classify.py`，把 `run.json`、`rca_input.json`、`rca.json`、树索引、`expected.json` 以及对账、绑定检查、树节点检查三份结果文件（有才传）一起传入。`run_sub_script` 统一用 `subprocess.run` 执行，默认超时 60 秒，返回 `ok`、`returncode`、`stdout`、`stderr`。四个检查脚本有发现时按设计返回退出码 1，所以 `process_case` 一律以输出文件是否存在且可解析判断成败，不看退出码；分类脚本没产出文件时返回 `ok` 为 `False`，带上 `error` 文字。成功时返回的字典包含 `expected`、`run`、`rca_input`、`rca`、`reconcile`、`binding_check`、`tree_check`、`classify`、`result_dir`。

#### 熔断（`_update_fuse_history`）

每个用例处理完立即更新熔断历史。分类出错误时，在历史里查同一 `case_id`、同一 `error_class`、同一 `node_id` 的既往记录（不含熔断标记行），追加本轮记录；既往记录已有 2 条，即算上本轮连续 3 轮，就再追加一条 `fused` 为 `True` 的记录（`fuse_reason` 为「同节点同类连改3轮不过」），并把分类结果的 `details["fused"]` 置为 `True`，后面生成建议时据此在文字末尾加「【熔断警告】」。循环结束后整份历史写回 `<out>/fuse_history.jsonl`。

#### 评级与报告（`generate_report`、`_emit_case_report` 及辅助函数）

`generate_report` 对每个用例调用 `_emit_case_report`，再调用 `_write_batch_outputs` 写根目录文件。`_emit_case_report` 的处理顺序：

1. `compute_round_and_feedback_iteration` 算轮次。没有 `--prev-evaluation` 时 `round` 为 1、`feedback_iteration` 为 0。传入的是文件时，`_prev_report_from_file` 检查它的 `meta.case_id` 是不是本用例，不是就退回目录模式；目录模式由 `_latest_prev_report_in_dir` 在目录下找 `<case_id>-v<round>-*` 中轮次最大的报告。`_prev_round_and_iteration` 从报告 `meta.round` 读轮次（目录名解析值作备用），新轮次加 1；上轮 `verdict` 是 `PARTIAL_PASS` 或 `FAIL` 时 `feedback_iteration` 加 1，否则清零。
2. `success` 取 `process_case` 的 `ok`。`success` 为假时 `verdict` 为 `EVAL_ERROR`；否则 `_compute_verdict` 按规则计算：漏判数与假阳数都为 0，且是阴性用例或期望根因数大于 0，算全部命中，再看 `run.json` 有 `missing_inputs` 则 `PASS_WITH_GAPS`，没有则 `PASS`；不是全部命中时，分类脚本命中了某类则 `FAIL`，没命中则 `PARTIAL_PASS`。期望根因数为 0 的阳性用例因此落到 `PARTIAL_PASS`，文档字符串说明这是把用例本身有问题的情况显露出来。
3. `_collect_case_issues` 生成失败签名与反馈条目，各至多一条：以分类结果的 `node_id` 为锚点，没有就取第一个漏判节点，从对账明细取该节点的实际对账结果。反馈条目的 `suggestion` 由 `_generate_suggestion` 按类别给出固定模板，`action` 依次为 `CHECK_ENV`、`SUPPLY_DATA`、`ADD_BINDING`、`ADD_NODE`、`REVIEW_CRITERIA`、`REVIEW_VERDICT`，未知类别为 `MANUAL_REVIEW`。有反馈条目时 `_build_feedback` 组装成一个用例的反馈对象，`next_action` 固定为 `REGENERATE`。
4. 组装报告：`reconciliation` 把对账文件顶层的 `details` 并入计数字典；`case_quality` 取对账给出的「期望粒度偏粗」提示；`data_gaps` 由 `_build_data_gaps` 从 `missing_inputs` 生成，按是否全部命中写不同的影响说明；`verdict_detail` 含 `verdict`、`error_class`（未分类为 `null`）与 `reason`；`checks` 由 `build_v9_checks` 生成。`checks` 六个键对应六类错误，`passed` 有三种取值：命中的类号记为 `ec`，序号小于 `ec` 的为 `true`，等于的为 `false`，大于的为 `null`，表示按序检查已在前面终止、这一类没有评到；`ec` 取自 `_classified_error_class`，未分类时六项全 `true`。`detail` 由 `_check_details` 提供，只在 `passed` 不为 `null` 时附带，内容依次是执行失败的步骤、缺的数据源、未绑定节点列表、树索引里缺的节点，后两类以及类 4、类 5 没有列表时用分类出的节点编号。注释记录了改成三种取值的原因：早先把没评到的类写成 `passed: true`，平台与评分读到的是「通过」。
5. 写文件：在 `<out>/<case_id>-v<round>-<时间戳>/` 下写 `evaluation_report.json`；再由 `_write_quality_score` 用 `importlib` 加载同目录的 `score_report.py`，调 `write_score` 生成 `quality_score.json`，`EVAL_ERROR` 时该函数抛 `ValueError`，跳过不写，其他异常只打印提示，不影响报告；然后写 `eval_run.json`（含 `temp_dir`）和本用例的 `failure_signatures.json`。返回给 `generate_report` 的摘要含 `case_id`、`verdict`、`quality_score`、`dimensions`、`round` 与相对路径 `report_path`。

`_write_batch_outputs` 调 `batch_summary.write_batch_score` 写 `quality_score_batch.json`，有内容时再写根目录的 `failure_signatures.json` 与 `feedback_to_generate.json`（数组，每个触发反馈的用例一个对象）。`main` 最后打印 `format_line` 的一行汇总与各评级的计数。

**修改时要注意**

- 分析脚本目录找错时，`check_binding.py` 看不到任何绑定，全树被当作未绑定，一切漏判都会归到 BINDING_GAP。`find_skill_scripts_dir` 的注释和 `_run_shared_binding_check` 的警告都为此而设，改目录布局时先核对这里。
- 不要放宽结果目录匹配的第 ④ 步。注释记录过两个问题：首次试跑只提供部分数据的不完整轮次参与匹配时会取代正式轮次，所以有 `-abandoned`、`-superseded` 后缀的退出机制；曾有 9 个用例因短后缀匹配不上完整 `case_id`、`run.json` 又指向合并副本目录而被误判 `EVAL_ERROR`，①b 的按段精确比对就是为此加的。
- 四个检查子脚本的退出码 1 是「有发现」不是「出错」，判断成败只能看输出文件。按退出码丢弃结果，会把「确有未绑定节点」这类信息恰好扔掉。
- 熔断历史必须放在 `--out` 目录，临时目录每轮都换，存那里历史永远为空。`_update_fuse_history` 必须在 `generate_report` 之前调用，否则 `details["fused"]` 来不及影响建议文字。
- `checks.passed` 的 `null` 不能改回 `true`；`verdict` 的取值永远不复用六类错误的名字；分类脚本没产出文件只能是 `EVAL_ERROR`，不能当作未分类的 PASS。
- 多个树索引文件时只用排序后的第一个，`classify.py` 收到的 `--tree-index` 也是它。
- `--scenario` 只影响默认输出目录，而且结果目录名以 `-result` 结尾或正好叫 `result` 时会被目录名推断覆盖而不生效。
- 临时目录脚本不删除，路径记在 `eval_run.json` 的 `temp_dir`。
- `batch_summary` 靠 `sys.path.insert` 导入，`score_report.py` 靠 `importlib` 按文件路径加载，三个文件必须同目录。
- 单用例报告里 `feedback_to_generate` 字段固定为 `null`，真正的反馈只写在根目录文件，见 `references/feedback_schema.md`。

### scripts/reconcile.py

**功能与作用**

对账层：读一个用例的 `expected.json` 与执行结果的 `rca.json`，逐条期望根因判断它落在确证根因、疑似根因还是哪里都不在，输出计数与明细。由 `eval_run.py` 的 `_run_reconcile` 以子进程调用，参数 `--expected`、`--rca`、`--out`，也可单独运行查看。退出码 0 表示没有漏判与假阳，1 表示有漏判或假阳（调用方视为预期结果）；输入文件不存在或无法解析也退出 1，此时不写输出文件。

**实现介绍**

对账结果共六种，定义在类 `ReconResult` 里：

| 结果类型 | 含义 | 计数字段 |
|---|---|---|
| `HIT_CONFIRMED` | 期望根因就是某条确证根因的候选自身，精确命中 | `hit_confirmed` |
| `HIT_ANCESTOR` | 期望根因是某条确证根因的路径上级，即诊断落在期望子树内、比期望更深；算通过，但单独计数并给出「期望粒度偏粗」的用例质量提示 | `hit_ancestor` |
| `HIT_DEGRADED` | 期望根因出现在疑似根因里，候选自身或其路径上级都算，称降级命中 | `hit_degraded` |
| `MISSED` | 期望根因不在任何结论等级里，即漏判 | `missed` |
| `FALSE_CONFIRM` | 阴性用例却有确证根因，即假阳 | `false_confirm` |
| `TRUE_NEGATIVE` | 阴性用例且确证根因为空，即真阴 | `true_negative` |

前五种是 `SKILL.md`「评估怎么判」列出的对账结果，第六种只在阴性用例没有确证根因时出现。

处理顺序：`reconcile` 读两个文件，用 `index_entries` 把 `rca_result` 与 `probable_causes` 各整理成 `[{self_id, ancestors, name}]`。其中 `entry_node_ids` 决定一条候选的「自身」与「路径上级」：自身优先取条目顶层的 `node_id`，没有则取 `evidence_path` 最后一个节点的 `node_id`；路径上级是 `evidence_path` 除最后一个以外的节点；`Fault_tree_path` 是节点名字列表，只有元素形如 `n1`、`v2`（正则 `_NODE_ID_RE` 为 `^[nv]\d+$`）时才并入上级集合作备用来源。阳性用例对每条期望根因调用 `reconcile_single`，按先确证自身、再确证上级、再疑似（自身或上级）、否则漏判的顺序判定，返回与报告 `reconciliation.details` 对齐的明细：`node_id`、`node_name`、`expected_result`（固定 `confirmed`）、`actual_result`（`confirmed`、`confirmed_descendant`、`probable`、`none` 之一）、`recon`、`actual_node_id`、`hint`、`evidence_source`（取自 `expected_facts[node_id].evidence_source`）。祖先命中时 `hint` 建议把用例期望精化到实际确证节点，理由写在提示文字里：粗粒度期望无法区分同一子树内的兄弟叶子；这些提示汇总到输出的 `case_quality` 列表。阴性用例不看期望根因：`rca_result` 非空则 `false_confirm` 计确证条目数，每条确证根因各出一条 `FALSE_CONFIRM` 明细；为空则 `true_negative` 为 1，出一条 `TRUE_NEGATIVE` 明细。

输出结构是 `{case_id, scenario, negative, reconciliation: {total_expected, 六个计数}, details, case_quality}`，`details` 在顶层而不在 `reconciliation` 里，`eval_run.py` 组装报告时再合并。`main` 先写文件再打印，注释说明这是为了打印环节出任何问题都不丢对账结果。

**修改时要注意**

- 方向不对称是有意设计：期望比实际更深（诊断只到期望的上级）不算命中，照常记 `HIT_DEGRADED` 或 `MISSED`，因为那是诊断没有到底。不要为「对称」把它改成命中。
- 候选自身必须用顶层 `node_id` 或 `evidence_path` 末节点判断。`Fault_tree_path` 存的是名字，不能拿它直接比对编号。
- 阴性用例的 `total_expected` 按 `expected_root_causes` 长度计，格式上应为 0；`eval_run.py` 的 `_compute_verdict` 对阴性用例单独放行，改计数定义时要一起看。
- 输出里 `details` 与 `reconciliation` 是并列字段，`eval_run.py` 与 `classify.py` 都按这个位置读取。

### scripts/classify.py

**功能与作用**

分类层：在对账发现漏判或假阳之后，按 0 到 5 的固定顺序检查六类错误，命中即停，输出错误类别、分类代号与证据。由 `eval_run.py` 的 `_run_classify` 以子进程调用，必填参数 `--run`、`--rca-input`、`--rca`、`--tree-index`、`--expected`，可选 `--binding-check`（`check_binding.py` 的输出）、`--tree-check`（`check_tree.py` 的输出）、`--reconcile`（`reconcile.py` 的输出）、`--out`。退出码 1 表示分类出了某一类错误，0 表示未分类（全部通过），都不是脚本出错；输入文件读不到时 `load_json` 只打警告并当作空字典继续。

**实现介绍**

`ERROR_CLASSES` 把 0 到 5 映射到六个类别名，`CLASS_CODES` 把类别名映射到分类代号 `run_invalid`、`needs_data`、`needs_binding`、`needs_knowledge`、`needs_criteria_review`、`needs_adjudication`。分类代号只是输出里的 `class_code` 字段，与评估报告的评级 `verdict` 无关。

`classify` 的流程：先读对账结果的 `missed` 与 `false_confirm`；提供了对账结果且两者都为 0 时直接返回未分类（`classified` 为 `False`，其余字段 `null`），数据缺口由 `eval_run.py` 在评级层按 `PASS_WITH_GAPS` 处理；没有提供对账结果时无从判断，照旧全查。然后从对账明细取出漏判节点列表 `missed_nodes`，按下表顺序逐个调用检查函数，第一个返回非空结果的就是分类结果。检查函数可以自行报出与所在位置不同的类别（`result.get("class", class_num)`），目前只有类 1 的改判用到。

| 类号 | 类别 | 含义 | 判断条件（检查函数） | 处置方式 |
|---|---|---|---|---|
| 0 | ENV_FAILURE | 执行层失败，与判断质量无关 | `check_env_failure`：`run.json` 不存在或读不到，或 `execution_status` 里任一步骤 `ok` 为 `false`；结果附 `failed_steps` | 节点交付脚本（`scripts/custom/`）崩溃走自动档，由优化技能按迁移记录重迁；其他环境故障走人工档，修好后重跑执行 |
| 1 | INPUT_GAP | 使用方没提供必要的数据源，并因此漏判 | `check_input_gap`：`missing_inputs` 非空且漏判数大于 0，且缺的源本可提供（见下文三条细化规则） | 人工档，使用方补数据后重跑执行；改判为类 2 的走自动档 |
| 2 | BINDING_GAP | 算子绑定层的不一致 | `check_binding_gap`：条件一，`rca.json` 的 `tree_issues.orphan_node_ids` 非空，即执行产出了事实但树索引里没有这个节点，典型原因是删节点没清绑定；条件二，某个漏判的期望根因自身在绑定检查的 `unbound` 列表里，即有判据却没有任何算子绑定 | 自动档：清掉指向已删节点的残留绑定；补绑定，有节点交付包就由 migrate 重迁，没有就在调优表补绑定行后导回。绑定清除冲突走人工档 |
| 3 | KNOWLEDGE_GAP | 期望的节点在故障树里不存在 | `check_knowledge_gap`：树节点检查结果的 `missing` 非空，取第一个 | 用例库过期（节点名在、编号重排）走自动档重建用例库；故障树确实缺节点走人工档，向交付方提增加节点的需求 |
| 4 | CRITERIA_GAP | 判据算出的事实与期望事实不一致 | `check_criteria_gap`：`expected_facts` 里某节点的 `predicate_satisfied` 与 `rca_input.json` 中该节点的实际值不相等 | 自动档，按证据修到与判据文档一致，判定标准本身不动；疑为用例期望标错的留证，熔断后人工复核 |
| 5 | VERDICT_GAP | 事实全对，根因分析给出的结论等级仍与期望不符 | `check_verdict_gap`：对账有假阳（阴性用例出现确证根因），或有漏判（期望根因不在确证根因里） | 人工档，出争议说明交人，不自动改根因分析规则 |

处置方式以 `cs-scot-generate-optimize/references/feedback_routing.md` 为准，本脚本只输出类别与证据，不执行处置。

类 1 的三条细化规则与改判规则写在 `check_input_gap` 的文档字符串里，依据是「用例自带的数据就是判定所需的充分数据」这一与节点交付方的约定：

1. `run.json` 的 `missing_kind` 把某个源标为 `collection_absent`（执行器认定采集本身不含该类数据，不是漏传）的，从缺源里剔除。
2. 用例自带数据里本来就没有该类内容的，剔除。`_case_data_names` 扫描 `expected.json` 的 `data_source` 目录（递归，最多约 5000 个名字，转小写），`_source_forms` 判断某个源名是否出现在文件名里，`_SRC_SYN` 把 `cmd` 与 `diag` 视为同义。注释记录的问题：22 个没有日志采集的用例曾被归为 INPUT_GAP，开出「使用方补数据」这种无法执行的指令。
3. 规则 1、2 剔完为空则类 1 不成立，返回 `None` 继续查绑定层。

改判规则：剔除后仍有可提供的缺源时，`_only_raw_form` 检查这些源在用例数据里是否只有原始采集文本形态，即目录名或 `.txt`、`.log` 可见，没有 `.csv`、`.xlsx`、`.xls` 表文件（后缀列表在 `_TABLE_SUFFIXES`）。是，则不是没有数据，而是现有表类绑定处理不了已存在的原始文本，`_binding_form_mismatch` 返回类 2 的结果对象（`binding_form_mismatch` 为 `True`），修复在绑定层：有节点交付包就由 migrate 重迁成直接读原始文本的判断脚本，没有就用 zip2csv 转表并写 `data_map.json` 映射。注释记录的问题是 mpls 场景 `n33` 节点用老式命令回显表绑定，原始目录里命令回显存在却被归为类 1，要求使用方补数据。表文件存在却没传给脚本的仍是类 1。`data_source` 没记录或目录不存在时 `_case_data_names` 返回 `None`，此时只有规则 1 生效，不做改判。

类 2 只看漏判的期望根因自身，不看它的上级，也不把全树其余未绑定节点定为错误。文档字符串给了两条依据：fault-tree-rca 的执行规则规定未绑定或数据不足的上级节点默认放行、不阻断叶子确证，上级未绑定解释不了叶子漏判，按上级开处理项只会引导人去补没有交付包依据的绑定；人工清空某个中间节点的绑定让它默认放行，是合规的调优方式。与漏判无关的未绑定节点只进报告 `checks.binding_gap.detail` 的信息栏。条件一先于条件二检查，而且不依赖漏判节点列表。

类 4 的实际事实由 `extract_actual_facts` 从 `rca_input.json` 提取，兼容两种形态：局部子树形态读 `fault_tree.pruned_nodes[<node_id>].facts[]`，每条事实可能是字段在顶层的扁平结构，也可能嵌套在 `fact` 字段下；完整形态读 `indicator_results.<源>.conditions[]`。同一节点多条事实取「或」，任一 `predicate_satisfied` 为真即为真。期望里有、实际里没有的节点跳过不比。

每个检查只报第一个命中的节点；输出的 `details` 带 `class`、`error_category`、`class_code`、`reason` 及各类自己的证据字段，如 `failed_steps`、`missing_inputs`、`orphan_nodes`、`node_id`、`node_name`、`evidence`、`expected_predicate`、`actual_predicate`、`expected_in`、`actual_in`。

**修改时要注意**

- 检查顺序就是语义：前一类命中即停，后面的类不再评。新增检查时要在 `checks` 列表里放对位置，并在返回的结果对象里写明 `class` 与 `error_category`，否则按位置归类。
- 改类 1 时记住三条规则与改判的前提都是 `data_source` 可读；没有它时只剩规则 1，行为变保守。`_SRC_SYN` 与 `_TABLE_SUFFIXES` 的改动会直接改变改判结果。
- 不要把类 2 扩大到上级节点或全树。文档字符串记录了这样做会把合规的「清空绑定、默认放行」当错误反复追查。
- `--tree-index` 是必填参数，但读入的 `tree_index_data` 目前没有任何检查函数使用，树节点是否存在由 `check_tree.py` 的结果提供；`check_knowledge_gap` 的 `expected_roots` 参数同样未使用。改接口时注意这两个未用参数。
- `load_json` 读不到文件不退出而是返回空字典，所以 `run.json` 缺失直接变成 ENV_FAILURE，这是设计；其他文件缺失也会静默地变成空字典，排查时先看 stderr 的警告。

### scripts/score_report.py

**功能与作用**

把单用例的 `evaluation_report.json` 机械换算成平台展示用的 `quality_score.json`，不经过模型。`eval_run.py` 在写出报告后通过 `importlib` 加载本文件并调用 `write_score`；也可对任意历史报告手动运行 `python score_report.py <evaluation_report.json 或其所在目录> [-o <输出路径>]` 补出评分。退出码：报告文件不存在或评估自身失败（抛 `ValueError`）返回 2，正常返回 0。

**实现介绍**

`DIMS` 定义六个维度的键名与中英文名，顺序即评估顺序，也是平台的 `sort_order`：`env_failure`、`input_gap`、`binding_gap`、`knowledge_gap`、`criteria_gap`、`verdict_gap`；`CLASS_NAMES` 是对应的六类错误名。`batch_summary.py` 动态导入这里的 `DIMS`，不另存一份。

`build_score` 的判定来源是报告的 `verdict_detail`。`success` 为假或 `verdict` 为 `EVAL_ERROR` 时抛 `ValueError`，不出评分文件，注释说明是宁可没有评分也不拿假分数充当有效评分。`verdict` 以 `PASS` 开头（含 `PASS_WITH_GAPS`）时命中类号 `ec` 置为 `None`，六维全部通过；注释记录了早先只精确匹配 `PASS` 字符串，把 `PASS_WITH_GAPS` 错评成全部未评估、0 分。非 `PASS` 却没有 `error_class` 时，六维全记未评估、总分 0，并在 stderr 提示。其余情况按 `ec` 推导：序号小于 `ec` 的维度通过，等于的未通过，大于的未评估。

评分公式：

- 维度分：通过 `value: true, score: 100`；未通过 `value: false, score: 0`；未评估 `value: null, score: null`，展示层渲染成 N/A。
- 总分 `total_score = round(通过维度数 × 100 / 6)`，含义是六个维度通过了几个：全过 100，第 1 个维度未通过 0，最后一个维度未通过 83；可能的取值只有 0、17、33、50、67、83、100。

`_tri_reason` 按通过、未通过、未评估三种状态生成三语 `reason`：`default` 等于 `zh_CN`；未通过维度的 `zh_CN` 附报告 `verdict_detail.reason` 压缩空白后的前 300 字，`en_US` 用固定英文模板，不做自由翻译；未评估的说明按序评估在哪个维度终止。`_meta_echo` 把报告的 `case_id`、`scenario`、`round`、`verdict`、`evaluation_time` 与命中类名回填到 `meta`。`write_score` 读报告、算分、写到报告同目录的 `quality_score.json`（或 `-o` 指定路径），返回输出路径。`main` 先把标准输出改成 UTF-8，支持传目录，最后打印一行各维度状态与总分。

**修改时要注意**

- 判定只读 `verdict_detail.error_class`，不读 `checks.passed`。旧版报告把没评到的维度写成 `passed: true`，读它会把未评估展示成通过；按 `error_class` 推导对新旧报告都成立。
- `DIMS` 是维度键名与名称的唯一来源，`batch_summary.py`、`references/score_dimensions.md` 与平台置信度配置都依赖它，改键名或顺序要三处同步。
- 不要给 `EVAL_ERROR` 产评分文件，也不要把「非 PASS 且无 `error_class`」改成有分，两处都是有意的保守处理。

### scripts/batch_summary.py

**功能与作用**

整批汇总算法的唯一定义处，是被导入的模块，没有命令行入口。`eval_run.py` 的批量输出与 `regress.py` 的一键回归都 `from batch_summary import format_line, read_case_score, write_batch_score`，把各用例的评级与评分汇成评估输出根目录的 `quality_score_batch.json`，供平台与业务取数；单用例目录里的 `quality_score.json` 不由它改动。

**实现介绍**

`build_batch_score(cases, evaluation_time)` 接收用例摘要列表，每条至少有 `case_id`，可带 `verdict`（缺省按 `NOT_EVALUATED`）、`quality_score`（单用例总分）、`dimensions`（六维的 `true`、`false`、`null`）。计算公式：

- 通过：`verdict` 属于 `PASS_VERDICTS`，即 `PASS` 或 `PASS_WITH_GAPS`。
- `pass_rate = 通过数 / 用例总数`，保留四位小数；分母是全部用例，执行失败、评估异常、一键回归里因候选冲突待人工判定（`pending_adjudication`）的用例都算不通过。
- `total_score = Σ 各用例 quality_score / 用例总数`，保留一位小数，没评出分的用例按 0。
- 六个维度（`_dimension_ratios`）：`value = 该维度为 true 的用例数 / 用例总数`，保留四位小数；`score = round(value × 100)`；另给 `passed_cases` 与三语 `dimension_name`。`null`（没评到）按不通过计。因为单用例总分也是「通过维度数 / 6」，整批总分与六个维度分的平均只差单用例取整带来的误差。
- `verdicts`（`_verdict_counts`）按 `VERDICT_KEYS` 六个键计数，不认识的或缺失的评级归入 `NOT_EVALUATED`。

输出对象还有 `level: "batch"`、`total_cases`、`passed`、`meta`（`evaluation_time`、`value_type: "RATIO"` 与一句说明）以及原样带回的 `cases`。`write_batch_score(out_dir, cases, evaluation_time)` 建目录并写 `quality_score_batch.json`，返回汇总对象。`read_case_score(path)` 读单用例 `quality_score.json`，返回 `(total_score, {维度: value})`，文件不在或读不了返回 `(None, {})`，调用方据此把该用例计 0 分、六维全不通过。`format_line` 生成终端一行汇总：通过数、通过率、总得分及计算方式说明。`_dims` 用 `importlib` 加载同目录的 `score_report.py` 取 `DIMS`。

**修改时要注意**

- 计算方式只在这里定义。`eval_run.py` 与 `regress.py` 都不能自带一份算法，否则两条路径写出的 `quality_score_batch.json` 会不一致。
- 分母含全部用例、未评分按 0 是有意的，`references/output_files.md` 说明在第一个维度就未通过的用例把平均分拉低是设计目标，不要为了提高数字改成只统计有评分的用例。
- 维度名依赖 `score_report.py` 与它同目录，移动文件要一起移。

### scripts/case_gen.py

**功能与作用**

用例库生成脚本。评估需要用例库，即 `cases/<case_id>/expected.json` 的集合，每个用例写明"用这份数据诊断，期望得出的根因节点是哪一个"。本脚本不靠人手写用例，而是利用节点交付包自带的标注：每个节点交付包对应一个节点的判断方法，并附一份"跑出来就该是该节点"的测试数据；迁移时 cs-scot-migrate 已在场景技能的迁移记录 `tree/migrations.json` 里记下每个节点对应哪个包。脚本据此为每个有迁移记录的节点生成一个用例目录 `<cases-out>/<node_id>-<包节点名>/`，内含 `expected.json`，按需把包内测试数据拷成 `data/`。故障树结构调整（增删节点、节点编号重排）后重跑一遍即可刷新全库。

它是 SKILL.md 的入口一，由人直接运行，也由 `regress.py` 在给了 `--packages` 时代为运行。输入是产物目录或打包技能目录（`--product`）、节点交付包（`--packages`，可以是包根目录、单包目录、合集 zip 或放着多个 zip 的目录）、可选的对应表（`--map`）；输出是用例库目录。阴性用例（健康数据、期望没有根因的用例）没有机械来源，不由本脚本生成，按 `references/expected_schema.md` 人工编写。

**实现介绍**

`main` 的处理顺序如下。

1. 定位产物。`locate_product` 接受产物目录（下有 `tree/`）或打包技能目录（下有 `assets/tree/`），后者自动进入 `assets/`，两者都不像时以 `SystemExit` 退出。`load_tree` 读 `tree/` 下按名排序的第一个 `*.tree_index.json`，把 `nodes` 统一成以节点编号为键的字典（列表形态按 `node_id` 或 `id` 字段转换），同时返回 `scenario`。

2. 准备包目录。`prepare_packages_root` 把 `--packages` 整理成可扫描的根目录清单，与 cs-scot-migrate 的解包规则一致：纯目录原样使用；目录里含 zip 时（跳过路径含 `__pycache__` 的）只把 zip 文件拷进临时区下的 `packages/` 子目录解包，同名 zip 以 `~N` 后缀改名共存，原目录仍作为第二个根只读扫描，于是返回两个根；`--packages` 本身是 zip 时解到临时区里以 zip 文件名（经 `sanitize`）命名的子目录。解包由 `_unzip`（优先以 `metadata_encoding="gbk"` 打开，兼容 Windows 打包的中文文件名）和 `_explode`（就地递归解嵌套 zip，最多 3 层，解完删 zip）完成。临时区用 `tempfile.mkdtemp(prefix="case-gen-")` 创建并用 `atexit` 注册清理，因此只要用到了临时区（返回值 `ephemeral` 为真），`copy_data` 就被强制置真，测试数据必须拷进用例目录。

3. 建立候选包索引。`index_packages` 对每个根做一遍全量扫描，每个候选包记成字典 `{dir, aliases, title, src_zip, data, path}`。包的身份优先取包内 `判断方法*.md`（文件名含"规范"的是规范文档，跳过）：`_md_name` 先取标题行 `# 判断方法: <名字>`，没有再取文件名去掉"判断方法"前缀，这与 cs-scot-migrate 的 `migrate_fault_check.py` 用同一条正则；文档所在目录名、从根到该目录的各级中间目录名都记为别名；`_md_path` 读取可选的 `## 节点路径` 一节（根到节点、以 `/`、`\`、`>` 等分隔），供同名节点消歧。没有判断方法文档、但直接含"测试数据"子目录的目录也收录，以目录名为别名；一个候选都没有而根本身带测试数据时，根就是单个包。`find_test_data` 取目录名含"测试数据"的最浅目录，没有再找平台导出名 `formated_result` 或 `formatted_result`。所有名字经 `_norm` 归一化：全角转半角、字母小写、去掉空白、下划线、连字符和点。`src_zip` 只在扫描临时区时记录，取包在临时区下的顶层目录名（去掉 `~N` 后缀）归一化，即来源 zip 的文件名。

4. 读对应表。给了 `--map` 时 `load_node_zip_map` 读 csv（标准库）或 xlsx（`openpyxl`，缺少时退出并提示另存为 csv，多页取第一页）：表头必须有 `node_id` 列，zip 列是表头含 `zip`、`产物`、`文件名`、`优化包`、`package` 之一的列，识别不出就取该行里以 `.zip` 结尾的单元格；值只取文件名去扩展名后归一化；同一节点出现多个不同 zip 时取首见并告警。结果是 `{node_id: 归一化 zip 名}`。

5. 列出待配对项。`_plan_items` 在有迁移记录时按记录逐项（键是节点编号，值里用到 `package_node` 与 `fingerprint` 字段）；没有迁移记录（`ledgerless`）时退回按名字匹配：树上每个有名字的节点都作为待配对项，`package_node` 取节点名，并统计同名节点数 `name_dup`：

```python
    items = [(nid, {"package_node": str(nd.get("name") or "").strip()})
             for nid, nd in sorted(nodes.items(),
                                   key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 10 ** 9)
             if str(nd.get("name") or "").strip()]
```

6. 逐项配包并写用例，核心在 `_generate_cases`。节点编号在树上已不存在的记录项记为问题跳过。配包由 `_match_item` 完成：对应表里有该节点时走 `_match_by_map`，按 `src_zip` 精确找包，同一 zip 解出多个子包时在子集内再按名字匹配；否则走 `match_package`，按 `_match_tiers` 的三级规则取第一个非空的一级：

```python
def _match_tiers(pkgs: List[Dict[str, Any]], key: str) -> tuple:
    """按归一化名取三级候选:①别名精确相等 ②别名包含包名 ③包名包含别名(别名≥4字,防止短名误配)。"""
    return (
        [p for p in pkgs if key in p["aliases"]],
        [p for p in pkgs if any(key in a for a in p["aliases"])],
        [p for p in pkgs if any(a and len(a) >= 4 and a in key for a in p["aliases"])],
    )
```

某一级只有一个候选就取它；多个候选交给 `_pick_from_tier`：所有候选的 `title`（判断方法文档标题的归一化名）相同且非空，视为同一个包的不同批次版本，按 `_version_stamp`（路径里最大的"8 位日期加可选 6 位时刻"串，没有则按目录修改时间）取最新并写注记；标题不同就是真歧义，返回问题说明交人，不猜。有迁移记录时配不上包的项逐条记为问题；按名字匹配模式下树节点配不上包是常态，静默跳过。

按名字匹配模式还要处理同名节点：某节点名在树上出现多次、且该节点没有对应表行时，`_pick_same_name_node` 用包内声明的节点路径消歧，要求该节点从根到自身的名字链按顺序全部出现在包的节点路径里，且同名节点中只有一个满足，才返回该节点编号；包内没有节点路径或命中不唯一则记一次问题并跳过；路径指向的是别的同名节点时当前项静默跳过。

配上的包没有测试数据目录时记为问题跳过。用例编号为 `f"{nid}-{sanitize(package_node)}"`，目录已存在且未给 `--force` 时计入跳过清单。`_write_case` 创建目录，`copy_data` 为真时删掉旧 `data/` 再整目录拷贝，否则只在注记里写明包内数据路径；`build_expected` 生成 `expected.json`：`negative` 为 false，`expected_root_causes` 只有该节点一项（`note` 写明来自哪个包及匹配注记），`expected_facts` 写入该节点 `predicate_satisfied` 为 true，另加 `generated_by`（工具名、包节点名、迁移记录的 `fingerprint`、生成时间）和 `data_source`（`data/` 路径或包内数据路径）。

7. 收尾。按名字匹配模式下 `_leftover_problems` 把带测试数据却没配到任何节点的包逐个点名。`_report` 打印生成、跳过、未能生成三份清单；一个用例都没生成也没跳过时退出码为 1，其余情况退出码为 0，问题清单不影响退出码。

**修改时要注意**

- 临时区在进程退出时清理，来自 zip 的测试数据必须拷进用例目录，否则 `expected.json` 里的 `data_source` 会指向已删除的路径。改动 `prepare_packages_root` 时保持"用了临时区就强制拷贝"的联动。
- 目录里含 zip 时只拷 zip 文件、不整目录复制，注释记录的原因是用户包目录里 `_work/__pycache__` 等深层嵌套会超出 Windows 路径长度上限；单个 zip 解进以 zip 名命名的子目录，是为了保住 `src_zip`，否则内容平铺的 zip 无法与对应表匹配。
- `_md_name` 的标题正则与 `migrate_fault_check.py` 相同，迁移记录里的 `package_node` 就是由它得出的；改任一处都会使记录与包匹配不上。
- 匹配规则的原则是宁可不生成也不猜：真歧义点名交人，第三级要求别名至少 4 个字符以防短名误配。新增匹配规则时不要引入猜测性的取舍。
- `_pick_same_name_node` 的函数说明写的是节点路径须为祖先链的子序列，代码实际检查的是祖先链为节点路径的子序列，方向相反；交付包把节点路径写全（从根到节点）时两者一致，只写部分路径时按代码会匹配不上。
- 判断方法文档没有标题行时 `title` 为空，`_pick_from_tier` 会把同名多版本当成真歧义，此时应补标题或改用 `--map`。
- 没有 `--force` 时已存在的用例目录一律跳过，以保护人工修改；`--force` 会删掉该用例的 `data/` 重拷。
- `regress.py` 只看本脚本的退出码判断建库是否成功，而问题清单不改变退出码，所以建库部分成功时回归照常进行，要看 `case_gen.log` 里的未能生成清单。
- 按名字匹配模式按节点编号的数字部分排序，编号不是 `n` 加数字的节点排在最后。

### scripts/regress.py

**功能与作用**

一键回归驱动脚本。一键回归指用一条命令把用例库里的用例逐个执行诊断、评估并汇总通过率，用来确认场景技能改动之后原来通过的用例没有被改坏。它是 SKILL.md 的入口三，由人直接运行，三种用法：给了 `--packages` 时先调用 `case_gen.py` 建库再回归（入口 A）；已有用例库时直接回归（入口 B，日常主路径）；给了 `--eval-only --result-dir` 时跳过执行，只对既有结果评估（入口 C，等价于批量运行 `eval_run.py`）。`--case` 可多次指定只跑部分用例。

输入是场景技能目录（`--skill-dir`，也接受裸产物目录）、用例库目录（`--case-dir`）和工作目录（`--out`）。输出都在工作目录下：`results/` 是每个用例的执行结果目录，`eval/` 是评估输出（含整批汇总 `quality_score_batch.json`），`logs/` 是逐用例、逐步骤的日志，`regress_summary.json` 是回归自己的明细。执行链所需的执行器与根因分析脚本从场景技能自带的副本、同一 skills 目录下的 cs-scot-execute 与 fault-tree-rca、发行包技能 `cs-scot-generate-optimize` 内的副本三处按序查找，不需要为回归单独部署执行技能。

**实现介绍**

脚本从 `batch_summary.py` 导入 `write_batch_score`、`read_case_score`、`format_line`，整批汇总的算法只在那里定义。`SKILLS_ROOT` 取脚本所在目录的上两级，即 `skills/`。

`_run` 是统一的子进程封装：用当前解释器运行命令，合并标准输出与标准错误写进指定日志文件，返回退出码和输出末尾 1200 字符；超时返回退出码 124。各步骤的超时为建库 900 秒、执行器 900 秒、候选清单生成 300 秒、结果拼装 300 秒、报告渲染 120 秒、评估 600 秒。

`main` 的顺序：解析参数；给了 `--packages` 时 `_build_case_library` 调用 `case_gen.py`（透传 `--map`，不透传 `--force` 与 `--copy-data`），失败返回退出码 2；`_collect_cases` 收集用例库里带 `expected.json` 的子目录并按名排序，`--case` 指定的用例不在库里或库为空时返回退出码 2；`_resolve_exec_chain` 解析执行链，`--eval-only` 模式只要求 `--result-dir`，否则创建 `<out>/results`，用 `find_assets`（`<skill>/assets/plan.json` 或 `<skill>/plan.json`）定位产物根，`find_runner` 按序找 `<skill>/scripts/runner.py`、`<skills>/cs-scot-execute/scripts/runner.py`、发行包技能下的 `cs-scot-execute/scripts/runner.py` 与 `execute/scripts/runner.py`，都没有再在发行包技能的一层子目录里搜 `*/scripts/runner.py`；`find_rca_scripts` 优先 `assets/fault-tree-rca/scripts/`（与产物同代），缺 `draft_rca.py` 时退到同一 skills 目录下的 fault-tree-rca 或发行包技能内的副本并打印提示。缺产物、执行器或根因分析脚本时以 `SystemExit` 或退出码 2 结束。

然后逐用例循环，每个用例一个 `row` 字典（`case_id`、`stage`、`detail`，后续追加评估字段）。非 `--eval-only` 时先 `_execute_case`，成功再 `_evaluate_case`。

`_execute_case` 只跑确定性链，即不需要模型参与、重复运行结果相同的步骤，依次是执行器、候选清单生成、结果拼装、报告渲染：

```
runner.py --plan <assets>/plan.json --result-base <out>/results --result-suffix <后缀> --data raw=<原始数据根> [--data <表名>=<路径> ...]
draft_rca.py <rca_input.json> -o <结果目录>/rca_draft.json
finalize_rca.py <结果目录>/rca_draft.json -o <结果目录>/rca.json --rca-input <rca_input.json> [--solutions <assets>/tree/solutions.json]
render_report.py <结果目录>/rca.json
```

用例没有 `data/` 目录时记 `exec_fail`。结果目录后缀由 `case_suffix` 生成：取用例编号按 `-` 切分的首段，去掉 `[0-9A-Za-z_-]` 之外的字符（执行器只接受这些字符），为空时用 `c<序号>`。执行器运行前后比较 `results/` 下 `result-*` 目录的集合，`newest_result_dir` 取新出现且修改时间最新的那个；退出码非零或没有新目录记 `exec_fail`。`find_rca_input` 在结果目录里找 `rca_input.json`，没有再找第一个 `*_input.json`。候选清单生成失败记 `exec_fail`。结果拼装 `finalize_rca.py` 退出码非零时按等待人工判断处理（见下）。报告渲染只尽力运行，失败不影响回归。

传给执行器的数据参数由 `build_data_args` 生成，规则是机械的：一律带 `--data raw=<原始数据根>`，这是 S 类内核算子（节点判断脚本）读取原始采集数据的入口，注释记录实际运行中最常见的问题就是漏传它。原始数据根由 `find_raw_root` 定位：正确层级是"网元层父目录"，其下每个子目录是一个网元，网元目录下是 `CMD`、`CONFIG`、`LOG` 等类别目录；用例数据常在外面多包一层（如 `formated_result/`），直接把 `data/` 当原始数据根会使节点判断脚本的输入适配报告目录布局不符，整份数据都无法判断。定位规则与 `check_data.py` 的建议同源：

```python
    for _ in range(4):
        try:
            subs = [d for d in cur.iterdir() if d.is_dir()]
        except OSError:
            return data_dir
        for ne in subs:
            try:
                if any(g.is_dir() and g.name.lower() in _NE_KINDS for g in ne.iterdir()):
                    return cur
            except OSError:
                continue
        if len(subs) != 1:
            return data_dir
        cur = subs[0]
```

其中 `_NE_KINDS` 为 `{"cmd", "config", "log", "diag", "logbuf"}`。即本层任一子目录下有这些类别目录，本层就是原始数据根；否则只在恰有唯一子目录时下探一层，多个分叉不猜；最多下探 4 层；探不到就原样返回 `data/`，由执行器的报错给出正确传法。此外，用例目录下（与 `data/` 并列）若有 `data_map.json`，形如 `{"cmd": "相对 data 的路径或绝对路径"}`，逐条追加 `--data <键>=<路径>`；相对路径按 `data/` 解析，指向不存在文件的条目跳过并告警，文件解析失败则忽略整份映射只传 `raw`。没有映射时按单源表绑定的判据会得到"数据不足"，这是有意的边界：表映射属于需要识别数据形态的步骤，机械层不猜。

等待人工判断的用例怎么计分。`finalize_rca.py` 在候选清单里有候选未能由程序按规则确定结论等级（真冲突需要权衡）时以非零退出码停止，本脚本绝不代替做判断。默认（无人值守规则）把该用例直接记为不通过：`row` 置 `stage="evaluated"`、`verdict="FAIL"`、`error_category="pending_adjudication"`，不再运行评估，`detail` 指向结果目录和 `<后缀>-finalize.log`。加 `--pending-hold` 时改为挂起：`stage="pending_adjudication"`，不写 `verdict`，终端点名，等人按 fault-tree-rca 的规则在该结果目录补模型判断结果文件 `decisions.json`、重跑 `finalize_rca.py` 与 `render_report.py` 后，再用 `--eval-only` 或 `--case <该用例>` 补评。`--pending-as-fail` 只是解析了的参数，代码里没有读取它，因为它描述的已是默认行为。

`_evaluate_case` 以单用例模式调用 `eval_run.py`，`--result-dir` 传整个 `results/` 根，用例与结果目录的匹配交给 `eval_run.py` 的规则（执行器把本脚本传入的数据路径记在 `run.json` 里，是主要的匹配依据）。随后 `find_eval_report` 在 `eval/` 下找 `<case_id>-v*` 中最新且含 `evaluation_report.json` 的目录，读出 `verdict`（`meta.verdict`，没有再取 `verdict_detail.verdict`）、`error_class`、`error_category`，并用 `read_case_score` 读 `quality_score.json` 得到总分与六维取值；拿不到报告记 `eval_error`。

`_summarize` 把全部 `row` 交给 `write_batch_score` 写出 `eval/quality_score_batch.json`：

```python
    index = write_batch_score(eval_out, [{"case_id": r["case_id"],
                                          "verdict": r.get("verdict") if r["stage"] == "evaluated" else None,
                                          "quality_score": r.get("quality_score"), "dimensions": r.get("dimensions") or {},
                                          "stage": r["stage"], "error_category": r.get("error_category"),
                                          "report_dir": r.get("report_dir", "")}
                                         for r in rows], datetime.datetime.now().isoformat(timespec="seconds"))
```

因此计分结果是：默认规则下等待人工判断的用例以 `FAIL` 进入 `verdicts` 计数，`quality_score` 为空按 0 计入 `total_score`，六维全部按不通过计，并计入 `pass_rate` 的分母；`--pending-hold` 下该用例 `verdict` 为空，`batch_summary.py` 记为 `NOT_EVALUATED`，同样计入分母并按 0 分计，只是不算 `FAIL`。执行失败与评估异常的用例同理记 `NOT_EVALUATED`。`regress_summary.json` 记录 `total`、`evaluated`、`passed`、`pass_rate`、`total_score`（后两者直接取整批汇总的值）、`pending_adjudication` 与 `exec_or_eval_failed` 两份用例清单和全部 `row`。终端打印汇总行（`format_line`）、整批汇总文件路径、挂起与异常用例清单。退出码：没有挂起、没有异常且已评估用例全部通过时为 0，否则为 1。

**修改时要注意**

- `_execute_case` 把 `finalize_rca.py` 的任何非零退出都记为等待人工判断，不区分原因；而 `finalize_rca.py` 在输入文件解析失败、模型判断结果文件格式不合要求等情况下同样以非零退出，这些情况也会被记成 `pending_adjudication`，要看 `<后缀>-finalize.log` 才能分辨。
- SKILL.md 与 `--pending-hold` 的参数说明都写挂起用例"不计分母"，但代码把全部 `row` 交给 `write_batch_score`，挂起用例以 `NOT_EVALUATED` 计入分母并按 0 分计。改任一边前先统一说法。
- 入口 A 调用 `case_gen.py` 时不传 `--copy-data`；`--packages` 是不含 zip 的纯目录时测试数据不会拷进用例，而 `_execute_case` 要求用例目录下有 `data/`，这种组合下全部用例会记 `exec_fail`。
- 日志文件名和结果目录后缀都只取用例编号的首段。`case_gen.py` 生成的编号首段是节点编号，天然唯一；人工编写的编号如 `ldp-lsp-001`、`ldp-lsp-002` 首段相同，日志会互相覆盖，结果目录靠运行号仍能区分。
- `_NE_KINDS` 与 `check_data.py` 的 `KINDS`（`CONFIG`、`CMD`、`LOG`、`DIAG`、`OPR`）不完全一致，本脚本多 `logbuf`、少 `OPR`；改一处时对照另一处。
- `SKILLS_ROOT` 按脚本自身位置推算，把本技能目录移到别处（例如放进发行包内另一层）会改变执行器与根因分析脚本的查找范围。
- 根因分析脚本优先用产物内挂的副本，与产物同代；退到别处的副本时会打印提示，版本不同代可能导致候选清单格式不一致，按提示跑一次 migrate 刷新产物副本。
- 执行链各步骤有固定超时，大数据量的用例在执行器 900 秒超时后记 `exec_fail`（退出码 124）。
- 一键回归供日常回归；对外发布的量化数字仍按双会话方式运行，即诊断与评估分在两个会话，诊断会话不读 `expected.json`。

### scripts/check_binding.py

**功能与作用**

静态绑定检查脚本：对照树索引与分析脚本，找出有判据却在任何分析脚本里都没有算子绑定的节点。它是六类错误中 BINDING_GAP 的检测输入之一：`eval_run.py` 在批量评估开始时通过 `_run_shared_binding_check` 以子进程方式运行它一次（参数 `--tree-index`、`--scripts-dir`、`--out`），结果所有用例共用；`classify.py` 的 `check_binding_gap` 读取结果里的 `unbound` 列表，只有漏判的期望根因自身出现在该列表中才判为 BINDING_GAP。也可以单独运行查看某个场景技能的绑定情况。

**实现介绍**

`load_tree_index` 读树索引，失败退出码 1。`find_analyzer_scripts` 取脚本目录下按名排序的 `*_analyzer.py`（不递归，目录不存在返回空列表）。`extract_bindings_from_script` 不导入脚本，而是对脚本文本做正则扫描：

```python
    node_id_pattern = re.compile(r'''["']node_id["']\s*:\s*["']([nv]\d+)["']''')
```

以及一条匹配含 `node_id` 的字典字面量的正则，两者都把捕获到的节点编号加入集合；`collect_all_bindings` 合并所有脚本的结果。`check_binding_gaps` 兼容 `nodes` 为字典（键即节点编号，节点内容里没有 `node_id` 字段）或列表两种树索引形态，跳过 `pass_through` 节点，把有 `condition` 或 `criteria` 的节点列为条件节点，再按是否在绑定集合里分为已绑定与未绑定，输出 `total_condition_nodes`、`fully_bound_count`、`unbound_count`、`binding_rate` 和 `unbound` 列表（每项含 `node_id`、`name`、`condition`、`reason`）。`main` 打印统计和前 5 个未绑定节点，给了 `--out` 就写 JSON；有未绑定节点时退出码为 1，作为自动化检测的警告信号，否则为 0。

**修改时要注意**

- 退出码 1 是设计上的警告而非错误，`eval_run.py` 的注释说明它只要结果文件存在就加载，不按退出码丢结果；改退出码语义要同步改 `eval_run.py`。
- 检查是文本正则扫描：分析脚本里任何一处 `"node_id": "nX"` 字样都算绑定，包括注释和被停用的条目；节点编号必须是 `n` 或 `v` 加数字，其他格式的编号扫不到，会被当成未绑定。
- `--scripts-dir` 指错目录时一个绑定都看不见，全树都会报未绑定，评估会把一切漏判归到 BINDING_GAP；`eval_run.py` 的 `find_skill_scripts_dir` 按树索引所在目录的兄弟目录 `scripts/` 定位，改布局时要同步。
- 模块说明里写输出含 `fully_bound` 与 `partially_bound` 列表，实际返回只有计数和 `unbound`，也没有部分绑定的判断；`classify.py` 只读 `unbound`。

### scripts/check_tree.py

**功能与作用**

节点存在性检查脚本：查给定的节点编号是否存在于树索引中。它是六类错误中 KNOWLEDGE_GAP 的检测输入：`eval_run.py` 的 `_run_tree_check` 按用例以子进程运行它，`--node-ids` 传该用例 `expected_root_causes` 里全部节点编号（逗号分隔），`--out` 指定结果文件；`classify.py` 的 `check_knowledge_gap` 读取结果里的 `missing` 列表，非空即判为 KNOWLEDGE_GAP，证据取第一项的 `node_id`。也可单独运行。

**实现介绍**

`load_tree_index` 读树索引，失败退出码 1。`collect_tree_node_ids` 收集全部节点编号，兼容两种形态：`nodes` 为字典时取节点内容的 `node_id` 或 `id` 字段，都没有就用字典的键；为列表时取每项的 `node_id` 或 `id`。`check_nodes_exist` 对每个目标编号做精确字符串比对，输出 `total_checked`、`found_count`、`missing_count`、`found` 列表和 `missing` 列表（每项含 `node_id`、`reason`）。`main` 把 `--node-ids` 按逗号切分并去空白，为空时退出码 1；打印统计与缺失节点，给了 `--out` 就写 JSON；有缺失节点时退出码为 1（警告），否则为 0。

**修改时要注意**

- 退出码 1 在有缺失节点时属预期，`eval_run.py` 的注释已说明，调用方不应据此中断评估。
- 比对是大小写敏感的精确匹配，不做归一化；用例里的编号与树索引不一致（例如多了前缀或大小写不同）会直接报缺失。
- 节点编号重排后用例库过期也会表现为缺失节点，这属于 KNOWLEDGE_GAP 里"重新生成用例库"的自动档；它与故障树真缺节点的区分不在本脚本，由后续的反馈处理判断。

### scripts/make_drill.py

**功能与作用**

演练数据生成脚本：在指定目录生成一个自包含的演练工作区，用来离线演示和验证从评估到反馈处理的闭环，不需要真实场景技能与真实数据。工作区包含一份迷你产物（5 个节点的树索引、一条迁移记录、一个只绑定了 `n3`、`n6`、`n7` 的 `log_analyzer.py`）、9 个用例以及每个用例对应的一份"已执行完"的结果目录（`run.json`、`rca_input.json`、`rca.json` 按该类错误的特征构造），还有一份 `演练指引.md`，列出逐个用例运行 `eval_run.py` 和运行 cs-scot-generate-optimize 的 `route_feedback.py` 的命令及每个用例的期望结论。`tests/test_reflect_summary.py` 用它生成材料，验证 `eval_run.py` 批量评估与 `regress.py --eval-only` 写出的整批汇总完全一致。

**实现介绍**

模块顶部的常量就是演练材料：`TREE` 是树索引（`n1` 为 `pass_through` 的根，`n3` 下有叶子 `n6`、`n7`，`n9` 有判据但分析脚本里没有绑定）；`ANALYZER` 是分析脚本文本，`CONDITIONS` 列表只含 `n3`、`n6`、`n7`；`LEDGER` 是只含 `n9` 的迁移记录；`RCA_HIT`（确证根因为 `n6`）、`RCA_EMPTY`（`rca_result`、`probable_causes`、`undetermined` 三个列表与 `tree_issues` 都为空）和 `RCA_INPUT`（局部子树形态，`n6` 的事实用扁平写法、`n7` 的用嵌套写法，有意覆盖两种结构）。`_run` 构造 `run.json`：`execution_status` 含 `log_analyzer` 与 `rca` 两步，`fail_step` 指定的步骤置 `ok` 为 false 并附模拟错误信息，`missing` 写入 `missing_inputs`，`inputs.raw.path` 记数据路径。`_exp` 构造 `expected.json`。`main` 先写产物文件，再遍历 `drills` 列表，每项是（用例编号、期望、`run.json`、`rca_input.json`、`rca.json`、期望评估结论说明）六元组，依次为通过基线 `drill-pass`（全命中但缺 `config` 源，期望 PASS_WITH_GAPS）、类 0 `drill-0-env`、类 1 `drill-1-input`、类 2 的三种情形 `drill-2-form`（绑定形态不匹配）、`drill-2-orphan`（绑定指向的节点在故障树里已不存在，`orphan_node_ids` 含 `n88`）、`drill-2-binding`（迁移记录有 `n9` 而绑定为空，属绑定清除冲突）、类 3 `drill-3-knowledge`（树上没有 `n999`）、类 4 `drill-4-criteria`（`expected_facts` 期望 `n7` 成立而实测不成立）、类 5 `drill-5-verdict`。每个用例的 `data/` 放一个占位文件；`drill-1-input` 另放表文件 `logbuffer采集.csv`，因为类 1 的定义要求缺的数据源是用例数据本可提供的表文件；`drill-2-form` 另放 `网元A/CMD/采集.txt`，只有原始采集形态没有表，对应改判类 2 的情形。`expected.json` 的 `data_source` 与 `run.json` 的 `inputs.raw.path` 都写成该用例的 `data/` 路径，供评估按运行记录把用例对到结果。结果写在 `results/<用例编号>/result-drill-<用例编号>/`，最后生成 `演练指引.md`。

**修改时要注意**

- 每份结果都是按 `classify.py` 当前的检测条件手工构造的，改分类规则或 `result_schema.md` 的字段后，要同步改这里的材料和期望结论说明，否则 `tests/test_reflect_summary.py` 会失败（它断言只有基线用例通过，且 `env_failure` 维度通过数为总数减 1）。
- 模块说明与结束时的打印都写"8 个用例"，实际 `drills` 列表有 9 项（类 2 有三种情形）。
- `drill-1-input` 与 `drill-2-form` 的数据文件形态是分类边界的依据，增删文件会改变归类。

### SKILL.md

本技能给模型执行的操作手册，也是接手人了解评估流程的第一份文档。它说明三个入口（`case_gen.py` 建库、`eval_run.py` 评估、`regress.py` 一键回归）各在手上有什么材料时使用、命令与参数，给出术语表，规定执行规矩（不用 `python -c` 解析 JSON 或 CSV、不做手册外的预检、汇报以脚本打印和产出文件原文为准、防止泄露答案的两条规则），再用对账结果类型表、六类错误表和 verdict 表讲清评估怎么判，最后列出输出目录结构与上下游配合。细节放在 references 并注明何时去读。

### references/error_classification.md

六类错误（ENV_FAILURE、INPUT_GAP、BINDING_GAP、KNOWLEDGE_GAP、CRITERIA_GAP、VERDICT_GAP）的检测条件、证据字段和处理方式摘要，并给出对账结果类型表和熔断机制（同一用例、同一节点、同一类错误连续三轮没修好即停止自动处理转人工，历史存评估输出根目录的 `fuse_history.jsonl`）。处理方式以 cs-scot-generate-optimize 的 `feedback_routing.md` 为准。改 `classify.py`、`reconcile.py` 前先读它；`make_drill.py` 的用例按它构造。文中说明 `class_code` 是分类代号，与报告 `meta.verdict` 的评级是两回事。

### references/expected_schema.md

用例期望文件 `expected.json` 的输入格式要求：目录形态 `cases/<case_id>/`，字段（`case_id`、`scenario`、`negative`、`expected_root_causes`、`expected_facts`、`expected_excluded`、`expected_undetermined`）及约束，验证规则（阴性用例不得有期望根因、节点编号须在树索引中存在、阳性用例至少一个期望根因），防止泄露答案的规则与示例。`case_gen.py` 按它写文件，`eval_run.py`、`reconcile.py`、`classify.py` 按它读；阴性用例由人按它编写。

### references/feedback_schema.md

反馈文件 `feedback_to_generate.json` 的结构：写在评估输出根目录，只在有用例触发反馈时生成，内容是数组，每个触发反馈的用例一个对象（`meta`、`issues`、`next_action`、`summary`），`issues` 条目的 `error_class` 存类别名而非编号，`suggestion` 固定四个字段。还说明它与 `failure_signatures.json` 的关系，以及熔断时 `suggestion.detail` 末尾带【熔断警告】。由 `eval_run.py` 写，cs-scot-generate-optimize 的 `route_feedback.py` 读。

### references/output_files.md

评估各输出文件的完整例子与计算规则：`evaluation_report.json`（`checks` 六键的 `passed` 有 true、false、null 三种取值，按命中即停的链条推导）、`quality_score_batch.json`（`pass_rate` 分子是 PASS 加 PASS_WITH_GAPS、分母是全部用例，`total_score` 是各用例总分平均，没评出分按 0，由 `batch_summary.py` 统一计算）、`quality_score.json`（由 `score_report.py` 生成，总分等于通过维度数除以 6 乘 100 取整）、`failure_signatures.json`、`feedback_to_generate.json` 与 `expected.json`。业务取数和平台展示看这份文档；`regress.py` 写出的整批汇总也按它。

### references/result_schema.md

执行结果目录三个文件的输入格式要求：`run.json`（`execution_status` 各步骤的 `ok`、`exitcode`、`error`；`missing_inputs` 与 `missing_kind`，其中 `collection_absent` 表示采集里本来没有该类数据，不判 INPUT_GAP）、`rca_input.json`（局部子树形态 `fault_tree.pruned_nodes` 与完整形态 `indicator_results` 两种，事实条目有扁平与嵌套两种写法，多事实取或）、`rca.json`（`rca_result`、`probable_causes`、`undetermined`、`tree_issues`，对账用顶层 `node_id`）。文末列出每个文件用于哪类错误的检测。`reconcile.py`、`classify.py`、`eval_run.py` 按它读结果，`make_drill.py` 按它构造结果。

### references/score_dimensions.md

`quality_score.json` 六个维度的对照表，供平台配置置信度：`sort_order`、`dimension_key`（`env_failure`、`input_gap`、`binding_gap`、`knowledge_gap`、`criteria_gap`、`verdict_gap`）、中英文名称与描述、`value_type` 一律 BOOLEAN；计分规则是六个维度按序检查、命中即停，总分等于通过维度数除以 6 乘 100 取整，六维等权。维度定义在 `score_report.py` 的 `DIMS`，`batch_summary.py` 通过加载 `score_report.py` 取用，不另外维护一份。
