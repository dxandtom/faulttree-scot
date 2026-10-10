# cs-scot-migrate 代码说明

## 技能概述

cs-scot-migrate 处于 Skill 飞轮的优化阶段。业务同事用 datacom-fault-tree-tester 把一个故障树节点的判断方法写成文档、配上判断脚本并用样本数据验证，打成节点交付包；本技能把这个交付包装进 cs-scot-generate 生成的场景技能：把判断方法文档全文替换到故障树对应节点的判据上，清掉该节点原有的全部绑定，换成一条 S 类绑定，执行时由通用适配函数 `fault_check`（在 `custom_lib.py` 里注册）直接运行交付的脚本，脚本一行不改。除此之外它还承担三件事：按平台给的对应表（全量表）批量增删节点、更新判据和解决方案；把场景技能里的工具链公共文件刷新到本机 skills 目录下其他技能的当前版本；把过旧的 `cross_analyzer.py` 框架代码就地升级。使用者是工具链开发者，或者由 cs-scot-generate-optimize 作为平台入口调用。

输入有三类：节点交付包（单包目录、多节点合集目录或 zip，zip 内可再嵌套 zip）；产物目录（场景技能的 `assets/`，给技能根目录也能自动定位）；可选的对应表（`--map` 指定，或放在交付包里自动发现）。输出全部写在产物目录里：交付脚本及其共享模块进入 `scripts/custom/`；调优表 `tree/<短名>.tuning.xlsx` 被修改后由产物自带的 `import_tree_excel.py` 编译回树索引 `tree/<短名>.tree_index.json` 等文件；迁移记录写入 `tree/migrations.json`；被刷新的工具链公共文件旧版留 `.bak`。标准输出打印汇总行、逐节点动作和失败清单。退出码 0 表示全部成功，3 表示有节点没匹配上或有行没有生效，其余错误以 `SystemExit` 带文字信息结束，退出码为 1。

文件之间的配合如下。SKILL.md 指导模型运行 `scripts/migrate_fault_check.py`；该脚本不自己改写树索引，所有树和绑定的修改都通过调优表往返完成，即用子进程调用产物 `scripts/` 下的 `export_tree_excel.py` 和 `import_tree_excel.py`。格式检查复用 datacom-fault-tree-tester 的 `check_contract.py`，读 xlsx 复用 cs-scot-generate 的 `xlsx_reader.py`，升级 `cross_analyzer.py` 借用 cs-scot-generate 的 `assemble_scenario.py`；这些文件一律先在同一 skills 目录下的其他技能里找，找不到才用本技能 `engine/` 里的副本。`engine/` 由 `scripts/sync_engine.py` 从源文件复制生成。

```
SKILL.md（模型按步骤执行）
  └─ scripts/migrate_fault_check.py
       ├─ 读：交付包（判断方法*.md、fault_check_*.py、search_*.py、测试数据/）、对应表、
       │       产物 tree/*.tree_index.json、tree/migrations.json、tree/solutions.json
       ├─ 子进程：产物 scripts/export_tree_excel.py、scripts/import_tree_excel.py（--out <产物>）
       ├─ 动态加载：check_contract.py（格式检查）、xlsx_reader.py（读对应表）、
       │            assemble_scenario.py（升级 cross_analyzer.py 框架代码）
       ├─ 复制来源：skills/cs-scot-generate、cs-scot-execute、fault-tree-rca、zip2csv、
       │            datacom-fault-tree-tester；旁边没有这些技能时用 engine/
       └─ 写：scripts/custom/*、tree/<短名>.tuning.xlsx、tree/migrations.json、
              各工具链公共文件（旧版 .bak）
scripts/sync_engine.py ──复制──▶ engine/（上述来源文件的副本；--check 只核对不写）
```

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 迁移流程的步骤、命令、退出码处理、无包刷新与维护说明 |
| `references/mapping_table.md` | 参考文档 | 对应表的表头识别、每行处理规则、三种模式与防误删检查 |
| `references/script_check.md` | 参考文档 | 交付脚本格式检查的两档处理、允许的修改范围与配套机制 |
| `scripts/migrate_fault_check.py` | 脚本 | 迁移主程序：解析交付包、匹配节点、格式检查、修改调优表并导入、刷新工具链公共文件、写迁移记录 |
| `scripts/sync_engine.py` | 脚本 | 维护用：把 `engine/` 副本与源文件对齐，`--check` 只核对 |
| `engine/assemble_scenario.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/assemble_scenario.py` |
| `engine/condition_kernels.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/condition_kernels.py` |
| `engine/custom_lib.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/custom_lib.py` |
| `engine/export_tree_excel.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/export_tree_excel.py` |
| `engine/import_tree_excel.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/import_tree_excel.py` |
| `engine/xlsx_reader.py` | 副本 | 源文件 `skills/cs-scot-generate/scripts/xlsx_reader.py` |
| `engine/runner.py` | 副本 | 源文件 `skills/cs-scot-execute/scripts/runner.py` |
| `engine/check_data.py` | 副本 | 源文件 `skills/cs-scot-execute/scripts/check_data.py` |
| `engine/tree_prune.py` | 副本 | 源文件 `skills/cs-scot-execute/scripts/tree_prune.py` |
| `engine/check_contract.py` | 副本 | 源文件 `skills/datacom-fault-tree-tester/scripts/check_contract.py` |
| `engine/search_cmd.py` | 副本 | 源文件 `skills/datacom-fault-tree-tester/scripts/search_cmd.py` |
| `engine/search_config.py` | 副本 | 源文件 `skills/datacom-fault-tree-tester/scripts/search_config.py` |
| `engine/search_diag_txt.py` | 副本 | 源文件 `skills/datacom-fault-tree-tester/scripts/search_diag_txt.py` |
| `engine/search_log.py` | 副本 | 源文件 `skills/datacom-fault-tree-tester/scripts/search_log.py` |
| `engine/cs-scot-execute/SKILL.md` | 副本 | 源文件 `skills/cs-scot-execute/SKILL.md` |
| `engine/fault-tree-rca/SKILL.md` | 副本 | 源文件 `skills/fault-tree-rca/SKILL.md` |
| `engine/fault-tree-rca/references/rca_rules.md` | 副本 | 源文件 `skills/fault-tree-rca/references/rca_rules.md` |
| `engine/fault-tree-rca/scripts/draft_rca.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/draft_rca.py` |
| `engine/fault-tree-rca/scripts/finalize_rca.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/finalize_rca.py` |
| `engine/fault-tree-rca/scripts/rca_query.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/rca_query.py` |
| `engine/fault-tree-rca/scripts/render_report.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/render_report.py` |
| `engine/fault-tree-rca/scripts/render_tree.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/render_tree.py` |
| `engine/fault-tree-rca/scripts/validate_rca.py` | 副本 | 源文件 `skills/fault-tree-rca/scripts/validate_rca.py` |
| `engine/zip2csv/SKILL.md` | 副本 | 源文件 `skills/zip2csv/SKILL.md` |
| `engine/zip2csv/references/csv_columns.md` | 副本 | 源文件 `skills/zip2csv/references/csv_columns.md` |
| `engine/zip2csv/scripts/config2csv.py` | 副本 | 源文件 `skills/zip2csv/scripts/config2csv.py` |
| `engine/zip2csv/scripts/logbuffer2csv.py` | 副本 | 源文件 `skills/zip2csv/scripts/logbuffer2csv.py` |
| `engine/zip2csv/scripts/parse_diag_to_csv.py` | 副本 | 源文件 `skills/zip2csv/scripts/parse_diag_to_csv.py` |
| `engine/zip2csv/scripts/zip2csv.py` | 副本 | 源文件 `skills/zip2csv/scripts/zip2csv.py` |
| `engine/zip2csv/vendor/ifilter.py` | 副本 | 源文件 `skills/zip2csv/vendor/ifilter.py`（zip2csv 技能随带的第三方件） |

## 各文件说明

### scripts/migrate_fault_check.py

**功能与作用**

本文件是技能的主程序，2790 行，按 SKILL.md 的命令由模型或平台以命令行运行。它把节点交付包迁移进场景技能的产物目录：解析交付包，把包内节点与故障树节点匹配，对交付脚本做格式检查，把脚本复制进产物 `scripts/custom/`，通过修改调优表并导入的方式替换判据、把绑定换成 S 类，升级过旧的 `cross_analyzer.py` 框架代码，刷新工具链公共文件，最后写迁移记录。它支持三条处理流程：单节点、合集包逐子包批量、按对应表批量；另有 `--refresh-only` 只刷新工具链公共文件、不迁移任何包。

输入是命令行参数指定的交付包、产物目录和可选的对应表；读取产物的树索引、迁移记录、`tree/solutions.json`，以及产物 `scripts/` 下的导入导出脚本。输出是对产物目录的修改和标准输出上的汇总与清单。退出码 0 为全部成功；3 为有节点没匹配上或有对应表行没有生效（已打印候选和补充匹配命令）；其余错误以 `SystemExit` 带文字信息结束，退出码 1，常见的是预导入现有调优表失败、格式检查不通过、对应表格式不对。

**实现介绍**

#### 命令行入口与处理方式的自动选择

`main()` 先把标准输出和标准错误的编码切换为 UTF-8，再用 `_build_parser()` 解析参数。参数分四组：定位输入的 `--package`、`--product`、`--map`、`--only`、`--script`；控制处理方式的 `--auto`、`--map-mode`（`auto`、`multi`、`all`）、`--refresh-only`、`--force`、`--no-clear`、`--rebuild-tuning`；手工指定脚本入口的 `--node`、`--entry`、`--per-ne`、`--entry-kwargs`。`--rebuild-tuning` 存进模块级变量 `REBUILD_TUNING`，供后面修改调优表的步骤读取。

`_resolve_product()` 要求产物目录下同时有 `tree/` 和 `scripts/`；若给的是技能根目录（`assets/tree` 与 `assets/scripts` 都在），自动进入 `assets/`，否则停止并报错。`--refresh-only` 时直接走 `_refresh_only()`：只刷新工具链公共文件并升级 `cross_analyzer.py` 框架代码，逐项打印动过的文件后返回，不碰任何交付包；没有 `--refresh-only` 又没有 `--package` 时报错。

其余情况在一个临时目录里按顺序分流：

1. `unpack()` 把交付包统一成目录形态。
2. `_dispatch_map()`：给了 `--map`，或者没有任何手工参数且 `find_map_table()` 在包里发现恰好一张对应表，就进入 `migrate_map()`，之后不再返回。显式的 `--map` 与 `--node`、`--only`、`--script`、`--entry`、`--per-ne`、`--entry-kwargs` 任一同用都停止并报错，因为对应表是全自动批量，手工参数不知道该给哪一行；有手工参数时也不做自动发现。
3. `find_subpackages()` 按判断方法文档分出子包；`_pick_subpackage()` 处理合集包：带 `--only` 就选定那一个子包继续走单节点流程；多个子包且带 `--auto` 进入 `migrate_batch()`；多个子包不带 `--auto` 则打印两种处理方式和包里的节点名后以退出码 3 结束；多个子包带 `--node` 等参数却没有 `--only` 也停止并报错。
4. 单节点流程：`_single_probe()` 解析包、选脚本、探测入口；`_single_pick_node()` 决定节点；`_single_arbitrate()` 试跑检验；`_single_migrate()` 执行迁移。

三条流程共用同一组底层函数：复制脚本、格式检查、修改调优表并导入、升级框架代码、写迁移记录。

#### 交付包的解析（单包、合集、zip）

`_unzip()` 优先用 `metadata_encoding="gbk"` 打开 zip（注释说明在 Windows 上打包的 zip 文件名常是 GBK 编码），老版本 Python 不支持该参数时退回默认；文件不是合法 zip 时报错并打印开头 120 字节，提示若以 `{` 或 `<` 开头多半是下载服务返回的报错体被当成文件保存。`_explode()` 就地递归解开嵌套 zip，最多 3 层，解到同名目录后删除原 zip。`unpack()` 的规则：目录里没有 zip 原样返回；目录里有 zip 则先整个复制到临时目录再解，不改动用户目录；zip 解压后若顶层只有一个子目录且没有 `.md`，返回那个子目录。

`find_subpackages()` 收集所有文件名以 `判断方法` 开头、不含 `规范` 的 `.md`，按它所在的顶层子目录分组，返回 `{子包根目录: [文档路径]}`；单节点包的键就是包根。顶层和子目录同时有文档时停止并报错，因为分不清哪份文档配哪个脚本。`parse_method_md()` 用 `_md_sections()` 按二级标题分段（三级标题算正文），返回字典：`name` 取 `## 节点名称` 段首行，没有则取标题行 `# 判断方法：<节点名>`，再没有用文件名去掉 `判断方法_` 前缀；`path` 取 `## 节点路径` 段首行，按斜线、反斜线、大于号和右箭头字符切分；`condition` 是文档全文，判据替换用全文，文档里的故障描述、数据来源表等一项不丢；`core` 是 `## 判断条件` 段，只用于相似度匹配，缺这一段时退化为全文并打印警告。

`find_scripts()` 收集包里全部 `fault_check_*.py`，经 `_pick_fc()` 选定一个：`--script` 指定优先，其次包里唯一一个，没有脚本则报错，多个脚本交给 `_pick_fc_by_name()` 按交付命名规范 `fault_check_<节点名>.py` 精确命中，同名多份且字节相同取路径最浅的一份，内容不同或无一同名则停止并报错，不作猜测。共享模块 `search_*.py` 由 `_needed_deps()` 用 `ast` 解析 `import` 语句做闭包收敛，只带脚本及其依赖链实际引用的模块，同名多份优先与脚本同目录的那份。

#### 节点匹配的三级

第一级由代码精确匹配。单节点流程的 `_single_pick_node()` 和批量流程的 `_batch_match_node()` 都先在树索引里找名字与包内节点名一字不差的节点：恰好一个就直接采用（`--auto` 下直接迁入）；多个同名时调用 `_path_pick()`，用包内 `## 节点路径` 经 `resolve_node_by_path()` 消歧，唯一命中且属于同名集合才采用。`--node` 指定的节点号直接采用，但必须在树里，否则停止并报错。

第二级由模型按语义匹配。精确匹配不成时，`rank_nodes()` 对全树每个节点算相似度：`difflib.SequenceMatcher` 得到的名字相似度乘 0.6，加上判据正文（各取前 800 字）相似度乘 0.4；包节点名与树节点名互为包含时分数至少 0.85；取前 5 个打印，一字不差的候选前面标 `=`。单节点流程打印候选后，带 `--auto` 以退出码 3 结束，否则正常返回；批量流程把候选和补充匹配的命令（`--only "<包节点名>" --node <节点号>`）记入失败清单。模型读候选和判断方法文档，确信是同一故障时带 `--node` 重跑。

第三级交由用户决定。模型无法确定时把候选列表交给用户指定节点号。第二、三级都在 SKILL.md 的步骤里完成，脚本本身只负责第一级和输出候选，不在分数接近时自行取最高分。

#### 按对应表批量处理

对应表模式的入口是 `migrate_map()`，一轮运行的状态都放在 `_MapCtx`（继承批量模式共用的累计器 `_Acc`）里：`oks`、`fails`、`skips` 三份清单，`seen_nid`、`seen_fc` 用于查重，`gates` 存格式检查信息，另有 `adds`（带脚本的新增行）、`sadds`（只有判据或只建结构的新增行）、`jd_edits`（判据同步）、`struct_miss`、`used_roots`，以及 `fp_groups` 与 `tangled`（按内容指纹分组，不同 zip 名却内容逐字节相同的包视为对应到了错误的节点，注释记录了联调时平台把 5 个对象分配到 16 行的实际情况）。

**表头识别。**`_read_table()` 读 csv（依次尝试 `utf-8-sig`、`utf-8`、`gbk`、`gb18030`、`latin-1` 解码）或 xlsx（用 cs-scot-generate 或 `engine/` 里的 `xlsx_reader.read_xlsx`），返回表头和行字典。`_parse_map_header()` 先用 `_norm_header()` 把表头去空格、全角括号转半角，再由 `_is_tuning_header()` 排除本工具链自己的调优表（有 `node_id` 列且带 `判据:`、`绑定:` 或 `meta` 开头的列，并且没有标记列）。`_scan_map_header()` 逐列归类：层级列匹配 `_MAP_LVL_RE`（`L<n>` 可带任意括号后缀，不分大小写）或 `_MAP_LVL_CN_RE`（`L<n>_Name_CN`）；`L<n>_Name_EN` 与 `Product_Obs` 忽略；zip 列先认整格为 `zip` 的列，其次 `Product_Name`，再其次唯一一个列名含 `zip` 的列；标记列按 `_is_marker_col()`（列名含 `_MARK_KEYS` 里的词，或整格等于 `_MARK_EXACT` 之一），且在判据列之前识别，避免"判据是否修改"这类列名被当成判据列；解决方案列按列名含 `解决方案` 或 `solution`；判据列按 `_is_judgment_col()`（含 `_JD_KEYS` 里的词）。中英成对的列由 `_pick_cn()` 取中文列。没有层级列或 zip 列时整个返回 `None`，`load_map_excel()` 据此报"对应表格式不对"。英文列由 `_parse_map_header_en()` 单独识别（`L<n>_Name_EN`、`L<n>(English)`、名字带 `_en` 或 `english` 的判据列和解决方案列），只做内容同步，不参与定位。`Node_ID` 列整列忽略，节点一律靠路径名字定位。

**每行的三种语义。**`load_map_excel()` 逐行调用 `_map_row_cells()` 取出 zip 名、判据文本、标记和有值的层级格，再由 `_map_sort_row()` 归类：zip 有值且已标记是迁移行，返回为 `entries`；zip 空、判据有值（或表根本没有判据列）是结构行，进入 `zipless`；zip 空、判据空、表有判据列且已标记是清除判据的行，进入 `clears`；zip 有值但未标记的行记入 `unmarked_zip` 并按结构行处理；zip 有值而层级列全空直接停止并报错。标记行的非空解决方案收进 `sols`；表有英文列时每行的英文名（本行最深层的英文格）、英文判据、英文解决方案收进 `en_rows`。`_map_nothing_to_do()` 发现没有任何可执行内容时停止并报错。

每一迁移行由 `_map_row()` 处理，分四步。`_map_locate()` 用 `build_zip_registry()` 建好的登记（键是 zip 文件名、去后缀名和子目录名，值是解开后的子包根目录）找包，再用 `resolve_node_by_path()` 定位节点，树上没有该节点而父路径能定位时记为新增，`multi` 模式拒绝新增。`_map_fresh()` 算内容指纹，与迁移记录一致就跳过（判据格变了的话只同步判据），落在 `tangled` 里的包进失败清单。`_map_probe()` 解析文档、按表行节点名优先选脚本（`_pick_for_row()`）、探测入口、找齐包里缺少的共享模块的来源、试跑仲裁。`_map_admit()` 查重（同一节点两行、同名脚本内容不同）、给包内名与表名不一致的行加备注、运行格式检查，并确定判据正文：判据列有值以表为准，否则用判断方法文档全文。通过的行进入 `oks` 或 `adds`。

`resolve_node_by_path()` 的规则：目标是路径最深一格的名字；全树唯一同名直接命中；多个同名时要求节点从根到自身的名字链（`_name_chain()`）按顺序出现在表中路径里（有序子序列），因此平台表的高层可以比本地树多；仍不唯一或一个都对不上时返回失败原因和候选说明。

结构行由 `_map_sync_zipless()` 处理：树上有该节点且判据与表不同，已标记就记入 `jd_edits`（只改判据，绑定不动），未标记只提示；树上没有且是 `all` 模式，父路径能定位就记入 `sadds` 建结构节点。解决方案由 `_map_sync_solutions()` 与产物 `tree/solutions.json` 里的 `zh` 字段比较，不同才同步，空值不表示删除。英文由 `_map_sync_english()` 处理，规则在 `_en_cond_changed()` 与 `_map_en_delta()`：英文名、英文解决方案非空且与树不同才存；本次中文判据被改的节点，英文判据严格以表为准，表里为空就清空；中文判据为空的节点、本次被清除或删除的节点不动英文判据。判据文本的比较都经 `_norm_jd()` 压掉全部空白，单元格与 Markdown 的排版差不算变化。

**三种模式。**`--map-mode` 对应平台优化按钮的 optimizeType。`auto`（缺省）：迁移、判据同步、清除判据、解决方案同步都做，带 zip 的新行在父节点下新增，迁移记录里有而表里没出现的节点只在 `_map_reconcile()` 里列出不删。`multi`：不动树结构，新增行进失败清单，也不提示"表里已无"的节点，因为部分表里不出现是常态。`all`：树结构以表为准，只有判据的新行和定位不到的清除行（`_map_parse_clears()` 里视为平台的无判据结构层）都补建结构节点，表里没出现的节点删除。

**防误删检查。**删除候选由 `_map_delete_candidates()` 调 `_delete_targets()` 计算，只在 `all` 模式进行：把迁移行、结构行、清除行的路径并起来，树上任一非根节点的名字链不是其中任何一条路径的子序列就是候选。因为用的是与定位相同的子序列规则，后代在表里则祖先必在，不会出现祖先删了后代留着。三道检查任一触发就一个节点都不删：树根名在整张表里找不到，判定表与树对不上；节点深度超过表的层级列数，表表达不了，单独列出不删；候选数超过 `max(3, 非根节点数 // 2)`，判定为部分表被误标成全量表，确认要大改版时加 `--force`。清除判据有对称的检查，在 `_map_parse_clears()` 里：要清的节点数超过 `max(3, 有判据节点数 // 2)` 时一个节点都不清，加 `--force` 才执行；`_clear_targets()` 对判据已空的节点直接跳过，重复执行不产生动作。

**标记列。**平台正式列名 `Need_Optimize`，`_mk_true()` 把 `y`、`yes`、`1`、`true`、`是`、`要`、`需要`、`修改`（不分大小写）视为"要改"，其余含空值视为"不改"；没有标记列时所有行视同已标记。内容动作（迁移、判据同步、清除、解决方案同步）只对标记行执行，未标记行只用来对照结构：zip 不迁并打印一行提示，判据与树不同也不改。删除候选和 `all` 模式下的结构新增看行的出现与消失，不看标记；英文同步也不看标记。全部为 FALSE 且表与树一致时，`_print_map_summary()` 明确说明本次只刷新了工具链公共文件或没有任何改动。

`migrate_map()` 的执行顺序：读表；`_map_preimport()`（调优表比树索引新就先导入）；读树索引和迁移记录，建 zip 登记；逐行 `_map_row()`；结构行、解决方案、清除行解析、删除候选、英文；`_refresh_standard()` 刷新工具链公共文件；`_map_write()` 一轮写入（复制脚本、修改调优表并导入、升级框架代码、写迁移记录；新增节点的编号由导入分配，`_map_claim_new()` 重读树索引，用 `_find_child()` 按父节点和名字找回新节点号写入迁移记录；删除节点记 `deleted_at`）；`_map_exec_clears()` 单独一轮写入清除判据的行并记 `cleared_at`；打印汇总与各类清单；`_map_reconcile()` 列出迁移记录里有而本次没有被任何动作覆盖的节点；`_factory_report()` 打印退回交付方的清单。有失败行或清除行定位失败时退出码 3。

#### 脚本入口函数的探测

`detect_entry()` 用 `ast` 解析脚本顶层函数，按 `_ENTRY_CANDIDATES` 的顺序（`fault_check`、`diagnose`、`main`、`check_ne`、`diagnose_ne`、`check`）选第一个存在且至少有一个位置参数的函数；0 参函数一律跳过并记入说明，因为运行期适配函数必传数据目录参数，选它必然触发 `TypeError`。入口名为 `check_ne` 或 `diagnose_ne` 时视为单网元入口（`per_ne=True`）。`_required_kwargs()` 把首参之外没有默认值的位置参数收成 `{参数名: {}}`，作为 `entry_kwargs` 的默认值，并打印提示让人核对。单节点流程里 `--entry`、`--per-ne`、`--entry-kwargs` 覆盖探测结果，但手工指定的 0 参函数同样被拦截。

探测出的入口还要经 `arbitrate_entry()` 试跑检验：把脚本和依赖复制到临时目录（与迁入后 `scripts/custom/` 同布局），`_smoke_call()` 以库方式加载模块，隔离 `sys.argv`、截获标准输出和标准错误、传入一个空的临时数据目录调用一次，返回值用 `_shape_ok()` 检验形状：必须是字典，且含 `matched`、`error` 或 `status` 任一键，或 `details.devices` 是字典，或 `results` 是以网元为键的此类字典，或本身就是以网元为键的此类字典。传入的路径对象是 `_StrPathLite`，与 `custom_lib._StrPath` 同款（`str` 子类，支持 `/` 拼接和 `Path` 方法），保证试跑与运行期入参同型。试跑后清掉 `sys.modules` 里以 `search_`、`smoke_` 开头的模块，防止批量时后一个包用到前一个包的共享模块。试跑不通过时：`--entry` 人工指定的只警告、不更换；自动探测的按候选顺序换下一个，谁返回可识别的形状谁当入口；全部不通过则停止并报错，逐个列出原因。

探测不到入口时，批量与对应表流程调用 `_stage_no_entry()`：把脚本副本和 `.orig` 基准放进 `scripts/custom/`，写一份内容为 `{"status": "pending_repair", "reason": "no_entry", "fix": ...}` 的记录文件，提示在副本里补一个 `fault_check(data_dir)` 封装入口后重跑同一条命令；单节点流程则直接停止并报错。

#### 格式检查与两档处理

格式检查由 `_contract_gate()` 完成，在写绑定之前执行。`_contract_mod()` 从 datacom-fault-tree-tester 或 `engine/` 加载 `check_contract.py`（要求源里有 `def check_script`），两处都没有或加载失败时跳过检查（加载失败会打印警告），结果缓存在 `_CC_CACHE`。`_gate_run_check()` 把脚本的生效版（见下文 `_effective()`）、依赖和补齐的共享模块复制到临时目录，带上包里的 `测试数据/` 目录调用 `cc.check_script(..., entry=, per_ne=, entry_kwargs=)`，旧版检查组件不认这些参数时退回老签名；结束后清掉 `search_`、`cc_` 开头的模块缓存并还原 `sys.path`。

检查项分 FAIL 与 WARN 两档，`_gate_triage()` 再做一次调整：FAIL 里编号在 `_ABSORBED`（`S1` 入口名或签名、`D2x` 空目录调用崩溃、`D3x` 多网元根目录调用崩溃）之内的，因为能走到这一步说明试跑仲裁已确认适配函数能驱动，降为 WARN 放行；补齐过共享模块的追加一条 `S6+` WARN，提醒交付包必须自包含。之后的处理分两档：

- 有 FAIL：`_gate_fail()` 先把脚本复制进 `scripts/custom/`（绑定不写），为每个文件保存 `.orig` 修复基准，写记录文件 `<脚本名去后缀>.contract.json`（`status` 为 `pending_repair`，带 `fails`、`warns`、`baseline`），返回带修法的清单文本；单节点流程据此报错，批量与对应表流程把它记入失败清单。人工 `--entry` 指定时例外：FAIL 全部降为 WARN 放行并打印警告。
- 只有 WARN 或全部通过：记录文件 `status` 写 `repaired` 或 `clean`，返回 `{"warns", "warn_items", "repairs", "baseline"}`，由 `_gate_extra()` 转成迁移记录的 `contract_warns`、`repairs` 字段，由 `_gate_note()` 并进节点备注，由 `_factory_report()` 汇总成退回交付方的清单。

修复流程靠三个函数配合。`_effective()` 判断脚本的生效版：`scripts/custom/` 里同名副本和 `.orig` 都在且 `.orig` 与包里原件字节相同（包没变），生效的就是修过的副本，否则是包里原件。`_copy_scripts()` 复制脚本时遵守同一规则：包没变就保留修复副本不覆盖；包变了就删掉过期的 `.orig` 正常覆盖；脚本 `import` 了而包里没带的 `search_*.py` 由 `_with_supplied()` 从 tester 或 `engine/` 补入，不覆盖已有文件。`_gate_regression()` 做回归保护：上一次记录为 `pending_repair`、包里带测试数据且本次是修复后重跑时，修复前后的 `matched` 基线经 `_bl_norm()` 归一（`undetermined` 与 `insufficient_data` 同属"判不了"）后不一致就拦截，因为修改输入输出格式不许改变判断结论。

#### 工具链公共文件的刷新

`refresh_engine()` 把产物里与场景无关、由工具链维护的文件与同一 skills 目录下其他技能的源逐文件比对，内容不同就覆盖并留 `.bak`，返回动过的清单。来源查找有两个辅助函数：`_mat()` 找素材目录，依次尝试 `skills/<名>`、发行包形态的 `generate/<名>`（cs-scot-generate 在发行包里就叫 `generate`）、`engine/<名>`；`_engine_src()` 找单个文件，先看同一 skills 目录下的技能，再看 `engine/`，两处都要求文件里带齐能力标记才可信。比对用 `_norm_text()` 统一行尾，所以 CRLF 与 LF 的差异不算差异。`_sync_file()` 执行覆盖：旧文件存在就复制为 `<原名>.bak`，再复制源文件，记入清单并打印一行。

刷新分五步，第一步对所有产物执行，后四步只在产物的上一级是打包技能（有 `SKILL.md` 和 `scripts/runner.py`）时执行：

1. `_refresh_engine_scripts()`：`assets/scripts/` 下 `ENGINE_CAPS` 列出的五个文件（`condition_kernels.py`、`xlsx_reader.py`、`custom_lib.py`、`import_tree_excel.py`、`export_tree_excel.py`），每个文件对应一组能力标记字符串。拿不到可信源时，现有副本能力齐全就保留，否则停止并报错。`custom_lib.py` 里有源文件没有的 `@register("...")` 手工注册函数时，由 `_manual_registered_skip()` 决定：能力齐全就不整体覆盖并列出函数名，能力不齐则警告后照常刷新，提醒从 `.bak` 把函数复制回来。
2. `_refresh_skill_scripts()`：技能根 `scripts/` 下的 `runner.py`（标记 `RUNNER_CAP`，即 `execution_status`）、`check_data.py`（`def main`）、`tree_prune.py`（`TREE_PRUNE_CAP`，即 `matched_nes`），以及 cs-scot-execute 的 `references/` 逐文件。
3. `_refresh_zip2csv()`：`assets/zip2csv/` 缺则整目录补齐；源是旧版（主脚本里没有 `run_ifilter` 或缺 `vendor/ifilter.py`）时只补不刷；否则逐文件同步。
4. `_refresh_rca()`：`assets/fault-tree-rca/` 缺则补齐；已有时由 `_refresh_rca_existing()` 判断：源的 `draft_rca.py` 带 `gap_kind` 与 `f.get("evidence")` 两个标记才可信；源可信，且副本缺标记、缺 `render_report.py` 或 `render_tree.py`、或任一文件与源不同时整目录替换，旧副本改名为 `fault-tree-rca.bak`；源不可信而副本是旧版时只打印警告。
5. `_refresh_skill_md()`：技能 `SKILL.md` 的正文换成 cs-scot-execute 当前的正文，frontmatter（技能名和描述）保留，旧文备份 `SKILL.md.bak`；源正文必须含 `check_data.py`、`draft_rca.py`、`rca_reply.md` 三处引用。

`_refresh_standard()` 是批量与对应表流程的包装，打印"刷新 N 处"或"无需刷新"并返回数量；单节点流程直接调用 `refresh_engine()`。`upgrade_cross_scaffold()` 单独处理 `assets/scripts/cross_analyzer.py`：文本里已有 `raw_dir` 与 `load_all` 就是新框架，不动；否则加载 cs-scot-generate 的 `assemble_scenario.py`，调用它的 `_upgrade_cross_scaffold(text, CROSS_SCAFFOLD)`，该函数保留 `CONDITIONS` 与 `CUSTOM` 赋值块原文、只换框架部分，发现框架之外的顶层定义时拒绝升级并给出原因；升级成功时旧文件备份为 `.py.bak`。

#### 修改调优表并导入

所有对树和绑定的修改都由 `_surgery()` 走调优表往返完成，一次调用只做一轮导出、修改、导入：

1. `_surgery_prepare()`：`REBUILD_TUNING` 为真时把现有调优表改名为 `.bak`；调优表比树索引新（可能有未导入的人工修改）时先用 `run_step()` 运行 `import_tree_excel.py` 导入，失败时在原报错后补一段提示，说明失败原因在现有调优表而不是本次交付包。
2. `run_step(product, "export_tree_excel.py", ...)` 导出当前树为调优表。
3. `_surgery_apply()` 按固定顺序改表：`edit_tuning_excel()` 改既有行（判据写入 `判据:原文` 列，其他 `判据:` 标签列清空，全部 `绑定:` 列清空后只在 `绑定:cross` 写入 S 类绑定的 JSON；判据与绑定都传空就是清除判据的行）；`edit_tuning_add()` 新增行（`node_id` 留空由导入分配，祖先各层名字逐格复制，自己的名字落在父层加一的层级列，由 `_subtree_end()` 找到父节点子树最后一行后插在其后以保持深度优先顺序，层级列不够就补 `L<d>(Chinese)` 列，英文格由 `_write_add_en()` 写）；`edit_tuning_condition()` 只改判据；`edit_tuning_solution()` 只改 `Recommend Solution(Chinese)` 列；`edit_tuning_en()` 只改英文名、`Judgment Condition(English)`、`Recommend Solution(English)` 列（`None` 不动，空串清格）；`edit_tuning_del()` 最后按 `node_id` 删行。
4. `run_step(product, "import_tree_excel.py", ...)` 编译回产物，再 `run_step(product, "export_tree_excel.py", ...)` 回导一次让调优表与树同步。

`_open_tuning()` 是所有修改调优表函数的公共入口：发现 `~$*.xlsx` 锁文件（表可能正被 Excel 打开）、`tree/` 下调优表不是恰好一份、第一行没有 `node_id` 列都停止并报错。`_ensure_col()` 按表头名找列，没有就在最右补一列，因为导入按表头名识别、与位置无关。`run_step()` 用 `sys.executable` 以子进程运行产物 `scripts/` 下的脚本，参数 `--out <产物目录>`，超时 600 秒，失败时打印其标准输出和标准错误各最后 1500 个字符并停止；由于导入在任一行编译不过时整体失败，一轮里的全部修改要么全部生效要么全部不生效。

S 类绑定由 `_binding()` 生成，`args.script` 指向 `scripts/` 下的相对路径：

```python
return {"class": "S", "func": "fault_check",
        "args": {"script": f"custom/{fc_name}", "entry": entry,
                 **({"per_ne": True} if per_ne else {}),
                 **({"entry_kwargs": kwargs} if kwargs else {})},
        "mapping_reason": ...}
```

#### 迁移记录与按内容指纹跳过

迁移记录是 `tree/migrations.json`，键是节点号，代码里相关函数以 `_ledger` 命名。`_ledger()` 为每个节点写入 `package_node`（包内节点名）、`script`（`custom/<脚本名>`）、`entry`、`per_ne`、`entry_kwargs`、`fingerprint`、`migrated_at`，以及格式检查带来的 `contract_warns`、`repairs`；同一节点重复迁移时覆盖。`_ledger_stamp()` 给已有记录补时间戳字段：删除节点写 `deleted_at`，清除判据和绑定写 `cleared_at`；没有记录的节点不写。`_ledger_read()` 读取记录，文件不存在返回空字典。

`_fingerprint()` 计算子包的内容指纹：只取迁移真正使用的文件，即全部 `.py` 和以 `判断方法` 开头、不含 `规范` 的 `.md`（注释说明 `执行记录.md` 每次运行 tester 都变，测试数据不进产物，算上它们会把没改的包误判为改了）；剥掉所有文件共有的外层目录，使同一个包从合集里解出和从单个 zip 解出得到相同指纹；按相对路径排序后把路径与文件字节依次送入 SHA-256，取前 16 个十六进制字符。三条流程在迁移前都比对指纹：与迁移记录里 `fingerprint` 相同且没有 `--force` 就跳过，汇总里记 `=`，这样全量交付时没变的包不会覆盖此后的人工调优；对应表流程里包没变但判据格变了，只同步判据。

**修改时要注意**

- `ENGINE_CAPS` 的能力标记决定旧产物副本会不会被刷新。注释要求：引擎文件的行为语义有变时，要在这里加一个只在新版存在的字符串，否则旧副本因"标记齐全"永远不被刷新；`results` 包裹形态的识别就是靠新增标记下发的。
- `_ENTRY_CANDIDATES` 的顺序有实际问题作依据：注释记录候选名单曾漏掉 `fault_check`，脚本入口就叫这个名字时误选了内部函数 `check_ne`（接收 `Path`、返回元组），运行期出错。增减候选或调整顺序前先核对 tester 规范的约定入口名。
- `_StrPathLite` 必须与 `custom_lib._StrPath` 同型，`_shape_ok()` 必须与 `custom_lib._fc_normalize` 同一规则；两边不一致会出现试跑放行而运行期出错，或者反过来。改一边要同时改另一边。
- `_smoke_call()`、`_gate_run_check()` 结束时清掉 `search_`、`smoke_`、`cc_` 开头的模块缓存。新增任何动态加载交付脚本的地方都要同样处理，否则批量时后一个包会用到前一个包的共享模块。
- openpyxl 的 `ws.cell(..., value=None)` 不是清空而是不赋值，清格必须写 `.value = None`，`edit_tuning_excel()` 的注释专门提醒了这一点。
- `_surgery_apply()` 的顺序固定为改既有行、新增行、判据同步、解决方案、英文格、删行。`edit_tuning_add()` 靠插在父节点子树末尾保持深度优先顺序，导入按"上级和上文对得上"重建父链；父节点也是本次新增的子节点要运行第二遍才能挂到父节点下，代码不在一轮里处理。
- `_fingerprint()` 只算 `.py` 和判断方法文档。如果改动纳入的文件集合，所有已迁移节点的指纹都会变，下一次全量交付会全部重迁一遍。
- `_map_preimport()` 与 `_surgery_prepare()` 里的预导入功能重复，前者是有意为之：注释说明全部跳过或全部失败的轮次进不了写入阶段，只靠后者则调优表里的人工修改永远导入不了。
- 记录文件的文件名在两处不一致：`_stage_no_entry()` 写 `<脚本文件名>.contract.json`（含 `.py`），`_contract_gate()` 读写 `<脚本名去后缀>.contract.json`，`_gate_prev()` 只读后者，所以探测不到入口时写下的记录不会在后续格式检查中被读到。
- 单节点流程里指纹一致时 `_single_migrate()` 在刷新工具链公共文件之前就返回，不做刷新；批量与对应表流程不论有没有写入都调用 `_refresh_standard()`。`refresh_engine()` 的后四步只对打包技能形态执行，独立产物目录只刷新 `assets/scripts/` 下的五个文件。
- 来源查找要走 `_mat()` 与 `_engine_src()`，它们已处理发行包里 cs-scot-generate 目录叫 `generate` 的情况。新增需要从其他技能取的文件时，`sync_engine.py` 的清单要一起加，否则本技能单独安装时找不到副本。
- 防误删与防误清的阈值都是 `max(3, 总数 // 2)`，只有 `--force` 能越过；`_delete_targets()` 的子序列规则必须与 `resolve_node_by_path()` 保持一致，注释说明正是这一点保证候选集对子树封闭。
- 相关测试：`tests/test_platform_table.py`、`tests/test_refresh_policy.py`、`integration/platform1/tests/test_platform1_e2e.py` 直接运行本脚本。

### scripts/sync_engine.py

**功能与作用**

维护用脚本，135 行，只有工具链开发者在改动了其他技能的源文件之后运行，日常迁移不需要它。它把本技能 `engine/` 目录里的副本与同一 skills 目录下其他技能的当前文件对齐：不带参数时从源覆盖副本并删除多余文件；带 `--check` 时只核对不写，有差异退出码 1。`tests/test_engine_sync.py` 用它核对副本是否过期。模块说明里记录了做这个脚本的原因：副本靠手工维护必然出现漂移，实测 `runner.py` 曾落后源文件 4 天，所以副本只能由脚本生成，不许手工修改。

**实现介绍**

路径常量从 `__file__` 推出：`HERE` 是 `scripts/` 目录，`ENGINE` 是 `../engine`，`SKILLS` 是上两级的 `skills/`。`SRC` 字典列出 14 个单文件副本及其源目录：cs-scot-generate 的 `condition_kernels.py`、`xlsx_reader.py`、`custom_lib.py`、`export_tree_excel.py`、`import_tree_excel.py`、`assemble_scenario.py`；cs-scot-execute 的 `runner.py`、`check_data.py`、`tree_prune.py`；datacom-fault-tree-tester 的 `check_contract.py` 与 `search_config.py`、`search_log.py`、`search_cmd.py`、`search_diag_txt.py`。`_sync_flat()` 逐个处理：源文件缺失以 `sys.exit` 带信息结束（提示在仓库根目录运行）；副本与源字节相同跳过；不同时 `--check` 模式记入 `drift`，否则 `shutil.copy2` 覆盖并记入 `changed`。

`DIRS` 字典列出 3 个整目录副本：`fault-tree-rca` 与 `zip2csv` 整目录（`only` 为 `None`），`cs-scot-execute` 只收 `SKILL.md`。`_dir_files()` 给出应纳入的文件：有 `only` 清单就只取清单里的，否则递归遍历源目录并用 `_ignored()` 按 `_IGNORE`（`__pycache__`、`*.pyc`、`*.py.bak`、`*.bak`、`runs`、`result*`、`.DS_Store`）排除。`_sync_dir()` 先对应纳入的文件做与单文件相同的比对与复制，再反向扫描 `engine/<目录>/` 里不在清单、也不属于忽略模式的文件：`--check` 模式记为"游离"，否则删除，这样源里删除或改名的文件不会在副本里残留。返回应纳入的源文件数。

`_stray_entries()` 找 `engine/` 顶层不在 `SRC` 里的 `.py` 和不在 `DIRS` 里的子目录（`__pycache__` 除外）。`main()` 从 `sys.argv` 判断是否 `--check`，依次执行单文件与整目录同步，有游离项先打印提示（要么加进清单，要么删掉）。`--check` 模式下有漂移就逐条打印，没有则打印"引擎副本与源一致（14 个平铺脚本 + N 个素材文件）"，有漂移或游离项返回 1；非检查模式打印刷新了哪些文件，只在有游离项时返回 1。程序以 `sys.exit(main())` 结束，所以退出码就是返回值。

**修改时要注意**

- 增删一个副本要改两处：这里的 `SRC` 或 `DIRS`，以及 `migrate_fault_check.py` 里使用它的地方（刷新用的 `ENGINE_CAPS` 与 `_engine_src()` 调用、补共享模块的 `_shared_lib_src()`、找目录的 `_mat()`）。只放进 `engine/` 不登记会被 `_stray_entries()` 报出，`--check` 退出码 1。
- 非检查模式会删除整目录副本里多出来的文件，不要把临时文件放进 `engine/`。
- 路径由 `__file__` 推出，脚本必须留在 `skills/cs-scot-migrate/scripts/` 下，且源技能必须在同一 `skills/` 目录里。
- 发行包由 `cs-scot-generate-optimize/scripts/package_skill.py` 打出，`EXCLUDE_TOP_BY_PKG = {"migrate": {"engine"}}` 把 `engine/` 整个排除，所以副本只影响本技能单独安装的场合。

### engine/ 目录

`engine/` 里的 30 个文件全部是同一 skills 目录下其他技能文件的副本，对应的源文件见文件清单。它存在的原因是本技能可能被单独安装、旁边没有其他技能，而 `migrate_fault_check.py` 的几项功能依赖那些技能的文件：刷新旧产物 `assets/scripts/` 下的工具链公共文件（`_engine_src()` 在同一 skills 目录下找不到可信源时取 `engine/` 里的同名文件）；格式检查要加载 `check_contract.py`；交付包缺共享模块时从 `search_*.py` 补齐；读对应表要用 `xlsx_reader.py`；升级 `cross_analyzer.py` 框架代码要用 `assemble_scenario.py`；刷新打包技能的 `assets/fault-tree-rca/`、`assets/zip2csv/` 和 `SKILL.md` 正文要用三个整目录副本（`_mat()` 的最后一个候选）。旁边有其他技能时一律先用它们，`engine/` 只是备用。发行包里其他技能都在包根，`package_skill.py` 把 `engine/` 排除在外。

维护方法：副本只能由 `scripts/sync_engine.py` 生成，不要手工修改。其他技能改了文件后在仓库根目录运行 `python skills/cs-scot-migrate/scripts/sync_engine.py` 对齐；`python skills/cs-scot-migrate/scripts/sync_engine.py --check` 只核对，有差异或游离文件退出码 1，`tests/test_engine_sync.py` 会运行这项核对。撰写本文时运行 `--check` 的结果是"引擎副本与源一致（14 个平铺脚本 + 16 个素材文件）"。注意刷新产物时副本还要通过 `ENGINE_CAPS` 等能力标记的核验，副本过旧会被 `_engine_src()` 拒绝，这也是要及时同步的原因。

### SKILL.md

技能说明，读者是执行迁移的模型和工具链开发者。它定义术语（节点交付包、合集包、对应表、调优表、迁移记录、工具链公共文件、格式检查），给出 `migrate_fault_check.py` 的命令和脚本按输入自动选择处理方式的规则，规定按退出码处理：0 汇报，3 由模型按语义补充匹配或问用户，其他原样报错。它还说明脚本自动做的六件事、`--refresh-only` 会刷新哪些文件与不碰哪些文件、迁移后执行时 `--data raw=` 的用法，以及 `engine/` 的维护方法（`sync_engine.py`）。正文只写流程，细节交给两份参考文档。

### references/mapping_table.md

参考文档，模型收到对应表时读。它对应 `migrate_fault_check.py` 里 `_parse_map_header()`、`load_map_excel()`、`_map_sync_zipless()`、`_map_parse_clears()`、`_delete_targets()` 等函数的行为：表头各列怎么识别、怎样区分对应表与调优表、每行按判据格和 zip 格分成迁移行、结构行、清除判据的行与新增行，清除判据的规则，`auto`、`multi`、`all` 三种模式的对照表，三道防误删检查，以及汇总行里 `✓`、`=`、`~`、`≈`、`+`、`✂`、`✗` 各符号的含义。改动对应表处理逻辑时要同步更新本文。

### references/script_check.md

参考文档，脚本报出格式检查结果时读。它对应 `_contract_gate()`、`_gate_triage()`、`_copy_scripts()`、`_effective()`、`_gate_regression()` 的行为：原则是不修改判断逻辑，只允许改输入输出格式的适配且只改 `scripts/custom/` 里的副本；WARN 由通用适配函数在运行期处理并放行，FAIL 不写绑定、按修法改副本后重跑同一条命令；配套机制包括补齐共享模块、用 `测试数据/` 做回归比对、退回交付方的清单，以及 `.orig`、`<脚本名去后缀>.contract.json`、迁移记录里 `contract_warns` 与 `repairs` 这些留痕。最后一节说明入口函数自动探测与 `--entry`、`--per-ne`、`--entry-kwargs` 的用法。
