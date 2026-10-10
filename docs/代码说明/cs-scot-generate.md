# cs-scot-generate 代码说明

## 技能概述

cs-scot-generate 是七个技能里的生成环节，处在「生成、执行、评估、优化」闭环的起点，由工具链开发者使用，一个场景生成一次。它把业务同事维护的故障树 Excel 与各数据源的数据（真实数据或样例）编译成一个场景专属的场景技能，技能的 `assets/` 下装着：树索引 `tree/<短名>.tree_index.json` 与故障树 Markdown、执行计划 `plan.json`、每个数据源一份逐条件分析脚本 `scripts/<指标>_analyzer.py`、跨源判据脚本 `scripts/cross_analyzer.py`、调优表 `tree/<短名>.tuning.xlsx`，以及一组随产物分发的工具链公共文件。生成之后由 cs-scot-execute 反复执行，不再需要模型参与。按设计原则「代码算，模型判」，生成阶段模型只做一件事：把每条判据对应到某个数据源的某一列和某类内核算子，写成算子绑定（分析脚本 `CONDITIONS` 列表里的条目）；切批、嵌入数据概览、验收、合并、写入脚本这些机械工作全部由脚本完成。

输入是故障树 xlsx（老格式单页签，或新格式多页签；数通场景常见两者混合的表）、场景名，以及每种数据源一份带表头的 csv 或 xlsx（原始日志先经 zip2csv 转表）。输出默认直接是技能目录 `<xlsx 所在目录>/scot技能/<英文短名>/`，产物在它的 `assets/` 下（SKILL.md 称之为 `<OUT>`）；取不到英文短名或本机缺少打包素材时退回只生成产物目录 `scot产物/<场景名>/`，最后用 `package_scenario.py` 单独打包。流程按 SKILL.md 分为步骤 1 建树、步骤 2 组装、步骤 2.5 填写算子绑定、步骤 3 用真实数据验证、步骤 4 清理临时文件、步骤 5 退化情形打包、步骤 6 人工调优，每一步对应一个或几个脚本。

脚本之间的调用与数据流如下（箭头右侧是产出）：

```
步骤 1   excel_to_tree.py（老格式）/ excel_to_tree_v2.py（新格式与混合格式，默认）
           读表：xlsx_reader.py
           v2 复用 excel_to_tree.py 的文本清洗、组合关系推断、英文名写入、产物目录函数
           v2 直接搭技能目录：package_scenario.find_material / scaffold_skill
           → tree/<短名>.md、tree/<短名>.tree_index.json、[tree/solutions.json]
步骤 2   assemble_scenario.py
           → scripts/ 下的工具链公共文件副本（condition_kernels.py、xlsx_reader.py、custom_lib.py、
             mo_config.py、analyzer.py、export_tree_excel.py、import_tree_excel.py、condition_binding_schema.md）
           → 空白分析脚本 scripts/<指标>_analyzer.py、scripts/cross_analyzer.py
           → plan.json、manifest.json、tree/knowledge.md（首次组装时复制技能级 knowledge.md）
           → 子进程运行 export_tree_excel.py 导出 tree/<短名>.tuning.xlsx
步骤 2.5 analyzer.py --lean         → overview/<指标>_overview.json
         make_dispatch.py --route   → dispatch/route_batch<N>.txt   （子代理写 route_batch<N>_result.json）
         make_dispatch.py           → dispatch/<指标>_batch<N>.txt  （子代理写 <指标>_batch<N>_result.py）
         merge_conditions.py        → 写入各 <指标>_analyzer.py 的 CONDITIONS、dispatch/cross_candidates.json、刷新调优表
         make_dispatch.py --cross   → dispatch/cross_batch<N>.txt   （子代理写结果后再跑 merge_conditions.py，写入 cross_analyzer.py）
步骤 3   <指标>_analyzer.py <数据> -o facts.json → validate_conditions.py、check_tree_coverage.py
步骤 5   package_scenario.py（仅步骤 1 没有直接生成技能目录时）
步骤 6   export_tree_excel.py / import_tree_excel.py（调优表与产物之间往返）
```

分析脚本运行时只依赖同目录的 `condition_kernels.py`（内核算子库）、`xlsx_reader.py`、`mo_config.py` 与 `custom_lib.py`，所以产物目录自包含，不依赖本技能目录。

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 给模型执行的操作手册：术语、执行规矩、各步骤的命令与完成标志、产物目录结构、最终汇报要求 |
| `knowledge.md` | 参考文档 | 技能级出厂知识库，首次组装时复制进产物 `tree/knowledge.md`，按「适用」标签载入任务说明与根因分析输入 |
| `references/binding_dispatch.md` | 参考文档 | 步骤 2.5 的操作细则：分派、任务说明生成、派发、合并、跨源补绑及失败处理 |
| `references/binding_rules.md` | 参考文档 | 怎样算把一条判据绑对，以及步骤 3 校验警告的两级处理与假阳性检验 |
| `references/condition_binding_schema.md` | 参考文档 | 算子绑定的格式约定：公共字段、各类别参数、参数化查询、S 类写法、`contains` 规则、极性与列解析 |
| `references/datacom_log_adapter.md` | 参考文档 | 数通「诊断文件包」场景接入说明：7 类 CSV 与内核算子的对应、EAV 窄表绑法、常见问题 |
| `references/plan_schema.md` | 参考文档 | 执行计划 `plan.json` 的格式（SCoT v4.0 子集）与引用命名空间 |
| `scripts/excel_to_tree.py` | 脚本 | 老格式（单页签、判据单列）故障树 Excel 转场景子树 Markdown 与树索引 |
| `scripts/excel_to_tree_v2.py` | 脚本 | 新格式（多页签、判据按源分列）与混合格式故障树转子树，生成英文短名，默认直接搭好技能目录 |
| `scripts/assemble_scenario.py` | 脚本 | 组装产物目录：复制工具链公共文件、写空白分析脚本与跨源脚本、生成执行计划与 manifest、导出调优表 |
| `scripts/make_dispatch.py` | 脚本 | 为子代理生成自包含的任务说明：分派任务说明、按源切批的绑定任务说明、跨源补绑任务说明 |
| `scripts/merge_conditions.py` | 脚本 | 验收并合并子代理的批次结果，写入各分析脚本的 `CONDITIONS`，汇总跨源候选，刷新调优表 |
| `scripts/package_scenario.py` | 脚本 | 把产物目录与 cs-scot-execute、fault-tree-rca、zip2csv 的副本打成可上传的场景技能目录 |
| `scripts/analyzer.py` | 脚本 | 数据概览工具：逐列统计列名、量纲、类别取值，供填写算子绑定前看清数据的列 |
| `scripts/xlsx_reader.py` | 脚本 | 零第三方依赖的 xlsx 读取器，本技能所有读 xlsx 的地方共用 |
| `scripts/condition_kernels.py` | 脚本 | 内核算子库：按判断类别计算每条算子绑定的事实，含列解析、单元格取值、数值变换 |
| `scripts/custom_lib.py` | 脚本 | S 类（代码类）预制函数库，含运行节点交付脚本的通用适配函数 `fault_check` |
| `scripts/mo_config.py` | 脚本 | MO 配置文本解析为表的适配模块，并提供 custom 函数用的提取与追溯原语 |
| `scripts/kernel_selftest.py` | 脚本 | 内核算子的回归自测：合成小表加算子绑定，断言期望事实 |
| `scripts/validate_conditions.py` | 脚本 | 对分析脚本输出的逐条件事实做机械检查，查出无中生有、缺理由、越权判定、类别缺失与替代指标 |
| `scripts/check_tree_coverage.py` | 脚本 | 按树索引枚举应绑定的叶子节点，列出没有被任何算子绑定认领的节点，漏绑退出码 1 |
| `scripts/import_tree_excel.py` | 脚本 | 调优表导入：把 xlsx 编译回树索引、Markdown、solutions.json、各分析脚本的 `CONDITIONS` 与执行计划 |
| `scripts/export_tree_excel.py` | 脚本 | 调优表导出：把树、判据、各源算子绑定与解决方案汇成单页签 xlsx |

下文先说明建树、组装、任务说明、合并、打包、概览与读表八个脚本，再说明内核算子库等八个随产物分发或负责检查的脚本，最后是 `SKILL.md`、`knowledge.md` 与五份参考文档。

## 各文件说明

### scripts/excel_to_tree.py

**功能与作用**

老格式故障树的建树脚本，对应 SKILL.md 的步骤 1。老格式指单页签、层级列写成 `L1(Chinese)`…`Ln(Chinese)`、全部判断条件集中在 `Judgment Condition(Chinese)` 一列的导出表。命令行运行 `python excel_to_tree.py "<故障树.xlsx>" --start-node "<场景名>"`，输入 xlsx（也接受 csv），输出两个文件：层级 Markdown `tree/<场景名>.md` 和树索引 `tree/<场景名>.tree_index.json`。不带 `-o` 时默认写到 `<xlsx 所在目录>/scot产物/<场景名>/tree/` 下，结束时打印该产物目录，供后续步骤使用。脚本自包含，只依赖同目录的 `xlsx_reader.py`；`excel_to_tree_v2.py` 从它导入 `_clean_text`、`_infer_combine`、`_is_skip_judgment`、`_set_name_en`、`scenario_out_dir` 五个函数复用，所以它同时是 v2 的基础库。

**实现介绍**

处理顺序由 `excel_to_tree_markdown` 串起来：读表、识别列、建嵌套树并渲染 Markdown、建扁平树索引、写文件。

读表 `_read_table`：xlsx 交给 `xlsx_reader.read_xlsx` 读活动页；csv 依次尝试 `_ENCODINGS` 里的 utf-8-sig、utf-8、gbk、gb18030、latin-1 解码，全部失败时忽略错误强行解码，返回表头与行字典列表。

识别列：`find_level_columns` 用正则 `^L(\d+)(?:\(Chinese\))?$` 找层级列，裸写的 `L7` 也认（注释记录的问题：手工补的列常没有 `(Chinese)` 后缀，老正则把整层静默丢掉，该层判据错挂到上一层），同一层既有裸列又有中文列时取中文列；`find_judgment_column` 只认表头恰为 `Judgment Condition(Chinese)` 的列；`find_code_columns` 找 `Ln Code` 稳定编码列；`find_english_columns` 找 `Ln(English)` 或 `Ln_Name_EN` 与 `Judgment Condition(English)` 或 `Diagnosis_Method_EN`。

判据文本处理：`_clean_text` 先删 Markdown 图片，再按白名单 `_HTML_TAG` 删已知 HTML 标签，然后解码 `&gt;` 等实体，最后归一空白，这个顺序是为了保留判据里的 `<`、`>` 比较运算符。`_is_skip_judgment` 把空值和 `_PASSTHROUGH_JUDGMENTS`（不涉及、无条件进入、无条件、无判断、无判断条件）当作没有判据。`_split_judgment` 从文本里抽出「数据源(X)：Y」行作为 `data_source` 字典，取「判断方法：」之后的正文作为方法；`_infer_combine` 按关键词粗判多条判据的组合关系：满足其一一类给 `OR`，同时满足、且、`&` 给 `AND`，其余给 `unknown`。英文判据由 `_row_judgment_en` 取得，只在中文判据存在时才存。

路径截取 `_cut_path`（`build_tree` 与 `build_index` 共用）：给了 `--start-node` 时，优先要求该名字正好在 `--start-level` 层（默认 2），否则在本行任意层找到也以它为根，都没有就跳过本行；没给场景名时从 `--start-level` 层起截取。两处都先做相邻同名层折叠：平台导出常把叶子名向右填充几百列，不折叠会建出上千级的重名链。`--exclude-pattern`（默认 `萃取新增节点-.*`）命中任一层级值就整行排除。

`build_tree` 产出嵌套字典，判据挂在路径最深节点的 `_judgment_` 列表里；`tree_to_markdown` 用显式栈迭代渲染成 `#`、`##`、`###` 层级标题，判据正文紧跟标题，注释说明输出与原递归版逐字节一致。`build_index` 产出扁平树索引：每行由 `_row_triples` 拆成（层级、名字、编码）三元组，以「编码优先、缺则名字」拼成链，`_short` 按首次出现顺序给链分配短编号 `n1`、`n2`……，节点字段为 `name`、`parent`、`level`、`code`、`children`、`condition`、`combine`、`data_source`、`pass_through`（默认 `True`）；`_set_name_en` 写入英文名；`_attach_judgment` 把方法正文累加进最深节点的 `condition`、重算 `combine`、把 `pass_through` 置为 `False`，并合并数据源与英文判据。返回 `{"scenario", "root", "nodes"}`。

异常处理：找不到层级列抛 `ValueError`「未找到 Ln(Chinese) 格式的层级列」，没有建出节点也抛 `ValueError`；`main` 统一捕获后打印 `错误: <异常信息>` 并以退出码 1 结束。成功后打印节点数与带判据节点数，带判据节点为 0 时在标准错误输出醒目提示停下检查表头，不要往下绑定。

**修改时要注意**

- 层级列正则必须继续接受裸 `Ln`，相邻同名层折叠必须保留，两条都是注释记录的问题修复。
- `_clean_text` 的三步顺序（图片、白名单标签、实体）不能换，也不能改成删除所有 `<…>` 的通用正则，否则判据里的比较运算符会被当成标签删掉。
- 本脚本没有「高层不判断」逻辑：任何挂了判据的节点 `pass_through` 都是 `False`，与 `excel_to_tree_v2.py` 的 `_exempt_top_levels` 不同。SKILL.md 步骤 1 把这条规则写在两种脚本之下，代码里只有 v2 实现了。
- `node_id` 按首次遇到顺序分配，改 `--start-level`、`--exclude-pattern` 或母表行序都会重排编号，已填的算子绑定里的 `node_id` 随之失效，要重做步骤 2.5。
- 五个被 v2 导入的函数改签名要同步改 v2；`scenario_out_dir` 在 `assemble_scenario.py` 里另有一份同样的实现，两处要一致。
- `build_tree` 与 `build_index` 必须走同一套截取与排除规则，否则 Markdown 与树索引会不一致。

### scripts/excel_to_tree_v2.py

**功能与作用**

新格式故障树的建树脚本，也是步骤 1 的默认脚本。新格式指多页签、层级列写成 `L1`…`L6`、判断条件按数据源分列（`CHR判断方法`、`X2 cause`、`RB日志判断方法`、`配置数据判断方法`、`DBG日志判断方法`、`信令判断方法`、`话统辅助判断方法` 等）；数通场景的混合格式表（同层有 `L1` 与 `L1(Chinese)` 两列、判据仍是 `Judgment Condition` 单列）也由它处理。命令行先用 `--list` 列出场景名及出处页签，再用 `--start-node` 跨页签合并出该场景子树；`--sheet` 限定页签，`--start-level` 改起始层，`--include-old` 保留名字带 old 或旧的页签，`--bind-top-levels` 关闭高层节点不参与判断的规则，`--slug` 手工指定英文短名。输出与老脚本同形：`tree/<短名>.md`、`tree/<短名>.tree_index.json`，表里有推荐解决方案列时另出 `tree/solutions.json`。它还负责确定产物位置：默认借 `package_scenario.scaffold_skill` 直接搭出技能目录 `scot技能/<短名>/`，产物写到其 `assets/`。

**实现介绍**

表头归类 `classify`：层级列（正则同老脚本）；`KPI索引`、`解决方案`、`备注` 归元数据；`数据链接`、`产品`、`制式` 丢弃；表头含 `_SRC_KEYWORDS` 里的关键词归判断方法列并映射到规范源标签（CHR、X2、RB、DBG 或 DEBUG 归 DBG、信令、配置、话统或 PFM 归话统）；含 `judgment condition` 或 `判断条件` 的归「通用」标签，注释说明不认这一列会把混合格式表的判据全部丢光。`sheet_schema` 把一页表头整理成主层级列、判断列字典、元数据列字典、层级备选列：同一层多列时主列取带 Chinese 的，其余进备选表，取值时由 `_lvl_value` 逐格回退（中文格空、英文格有值就用英文格）；判断列只对「通用」标签去重，优先留带 Chinese 的那列，其他标签的多列（话统加 PFM、DBG 加 DEBUG）是不同的判断列，全部保留。`sheet_en_cols` 据此找出英文层级列与英文判据列。

短名：`slugify` 把英文名转成小写连字符，含中文字符或转换后为空返回 `None`，超过 60 字符截断；`find_english_slug` 在场景名所在层的英文备选列取第一个能转成合法短名的值；`_pick_slug` 的优先级是 `--slug`（无效退出码 2）、英文列自动转换、场景名本身就是英文、都不行则沿用中文名。`resolve_scenario_alias` 处理用户传的是英文列叫法的情况，翻成主列真名重建。

产物位置 `default_out`：目标技能目录已有 `SKILL.md` 和 `assets/` 则续用；尝试导入 `package_scenario`，导入失败（单文件部署）退回产物目录；用 `package_scenario.find_material` 找 cs-scot-execute 与 fault-tree-rca，没有英文短名、素材缺失、目录已存在但不完整三种情况都退回产物目录 `scot产物/<短名>/` 并给出说明；否则调用 `scaffold_skill` 搭出技能目录，返回技能目录与其 `assets/`。

建索引 `build_index`：遍历每个树页签，`_row_levels` 取各层值并折叠相邻同名层（注释记录 994 层递归溢出的问题），`_scenario_path` 按与老脚本相同的规则截出以场景为根的一段；`_row_methods` 把本行各判断列按源标签累积成「标签到文本」的字典，`_row_metas` 收元数据，`_row_cond_en` 取英文判据；节点编号链只用名字（`code` 为 `None`），`sid` 分配 `nN`；`_merge_leaf` 在路径最深节点累积出处页签、英文判据、各源判据，并把 `condition` 写成每行 `[标签] 文本` 的形式，用 `_infer_combine` 推组合关系，`pass_through` 置 `False`，元数据按标签首次写入，`Recommend Solution` 列按中英文收进 `solutions`。节点比老脚本多 `sheets` 与 `meta` 两个字段。

主流程 `main`：`_split_sheets` 分出树页、非树页、旧版页；`--list` 走 `_print_scenarios` 后返回；没有 `--start-node` 退出码 2；`_build_scenario_index` 建索引，建不出时试英文别名，仍建不出退出码 1；`_warn_depth` 在树深超过 32 层时警告层级列可能识别错位；`_exempt_top_levels` 把带判据且层级不大于 2 的节点与场景根节点标为 `pass_through`：

```python
    topjudge = [nid for nid, nd in index["nodes"].items()
                if nd.get("condition") and ((nd.get("level") or 99) <= 2 or nid == index.get("root"))]
    for nid in topjudge:
        index["nodes"][nid]["pass_through"] = True
```

注释给出依据：高层判据多为场景分类与选路说明，绑成判定会让全树只能得到疑似根因；场景根不看层号一律不参与判断，是因为实测场景在母表更深层时，根节点「参考下级节点」的判据被派去绑定，谁都绑不上，数据不足挂在每条路径上。随后 `index_to_markdown` 用显式栈渲染 Markdown，遇到环时以 `SystemExit` 点名节点；`solutions` 从索引里取出单独写文件；有短名时写入 `index["slug"]`；`_write_outputs` 写三个文件，本次没有解决方案时删掉旧的 `solutions.json`；`_print_summary` 打印短名、页签来源、节点数、带判据节点数（不含 `pass_through`），为 0 时提示停下，并打印产物目录与技能目录或退回原因。

**修改时要注意**

- `sheet_schema` 只对「通用」标签去重，不要改成按标签收敛，否则话统加 PFM 这类多列判据会整列丢失；同层双列必须保留逐格回退，整列取舍会把漏译的行挤层或丢支。
- 没有解决方案时必须删旧 `solutions.json`：`node_id` 按遇到顺序分配，树一变旧表就错位，陈旧的表会把别的节点的方案挂给根因，下游没有人再校验。
- `_exempt_top_levels` 的两条规则（层级不大于 2、场景根一律不参与判断）都来自实测问题；调整前要同时考虑 `make_dispatch.load_conditions` 与 `check_tree_coverage.py` 都按 `pass_through` 排除节点。
- `default_out` 依赖 `package_scenario.find_material` 与 `scaffold_skill`，并且必须在 `ImportError` 时仍能出树；改这两个函数的签名要一起改。
- 改 `--start-level`、页签取舍或 `--include-old` 都会重排 `node_id`，算子绑定要重填。
- 从 `excel_to_tree.py` 导入的五个函数是共享实现，改动会同时影响两种格式。

### scripts/assemble_scenario.py

**功能与作用**

步骤 2 的组装脚本，把步骤 1 的产物组装成可以填写算子绑定的产物目录。命令行 `assemble_scenario.py --scenario "<场景名>" --indicators <源1>,<源2> --out "<OUT>"`，也可用 `--out-base` 或 `--xlsx` 推导目录，`--work` 指定中间目录（默认与 `--out` 相同），`--keep` 保留已填的分析脚本。输入是 `<OUT>/tree/` 下唯一的树索引、同名 Markdown 与可选的 `solutions.json`；输出是 `scripts/` 下的工具链公共文件副本、每个数据源一份空白分析脚本、跨源判据脚本 `cross_analyzer.py`、执行计划 `plan.json`、产物清单 `manifest.json`、首次组装时的 `tree/knowledge.md`，最后以子进程运行 `export_tree_excel.py` 导出调优表。它产出的空白脚本由步骤 2.5 的 `merge_conditions.py` 填写，执行计划由 cs-scot-execute 的 runner 消费。

**实现介绍**

文件开头是几段常量。`PRODUCT_FORMAT_VERSION = 3` 是产物格式版本，注释规定只在改动使旧产物与新执行器不兼容时才加 1，并要同步改 cs-scot-execute 的 `runner.py` 里的 `EXPECTED_PRODUCT_FORMAT`；版本 2 增加了 `filter` 字段，版本 3 增加了执行计划里的 cross 节点。`KNOWLEDGE_TEMPLATE` 是技能级 `knowledge.md` 不存在时写入产物的空白模板。`ANALYZER_SCAFFOLD` 是单源分析脚本的全文模板，`__IND__` 占位替换为源名：脚本导入 `condition_kernels` 的 `load_table` 与 `evaluate_binding`，`CONDITIONS` 留空，`CUSTOM` 为空字典；运行时按条目的 `source` 字段懒加载页签，`decision` 为 `insufficient_data`、`manual`、`not_applicable` 或类别为 J 的条目不读数据直接出事实，`mode` 为 `custom` 的条目调用 `CUSTOM` 里的函数（一个参数传本表，两个参数再传取表函数），其余调用 `evaluate_binding`；输出 JSON 含 `indicator`、`source`、`summary`（`total`、`ok`、`insufficient_data`、`manual` 计数）与 `conditions`（每条带 `id`、`node`、`node_id`、`condition_text`、`class`、`mapping_reason` 和 `fact`）。`CROSS_SCAFFOLD` 是跨源脚本模板：以多个 `--data 源名=文件` 接收全部数据源，`raw` 或 `raw_dir` 作为原始数据目录通道单独挂到 `sources["raw_dir"]`，`cmd` 与 `diag` 指向同一文件时只读一份，默认只加载 `CONDITIONS` 实际引用的源（`_referenced_sources` 递归收集嵌套的 `source`），有 S 类条目时全量加载，再调用 `alias_sources` 让两代源名互认。

`_extract_block` 与 `_upgrade_cross_scaffold` 用 `ast` 升级已填过的跨源脚本：顶层定义比模板多出任何名字（可能是手写的辅助函数）就放弃升级；否则用模板替换引擎部分，`CONDITIONS` 与 `CUSTOM` 两个赋值块原文保留。

`build_plan` 生成执行计划：每个数据源一个 `kind` 为 `script` 的节点（`inputs.data` 为 `$input.<源>`，`outputs.result` 为 `$memory.<源>_result`，`transition.next_on_success` 指向下一个源或 `cross`），然后是 `cross` 节点（`inputs.data` 为 `$input.*`），最后是 `kind` 为 `agent_skill` 的 `rca` 节点，输入含 `fault_tree_md`、各源结果与 `cross` 结果，`transition.end` 为真；顶层 `version` 为 `4.0`，`plan_id` 为 `plan_<短名>`，`entry` 为第一个源，`memory_init.fault_tree_md` 指向树的 Markdown 相对路径。

主流程 `main`：源名含保留字 `cross` 退出码 1。`_resolve_out` 决定产物目录：`--out` 优先，否则由 `--out-base` 或 `--xlsx` 所在目录推导，并无条件调用 `_scan_containers` 扫描 `scot产物/*/tree/*.tree_index.json` 与 `scot技能/*/assets/tree/*.tree_index.json`，按内容的 `scenario` 或 `slug` 认领：恰一个就用它，多个退出码 2，零个沿用推导目录。`_load_tree_index` 核验步骤 1 产物：`tree/` 下没有树索引退出码 1，多于一个退出码 2，`--scenario` 与索引的 `scenario`、`slug` 都不符退出码 1，带判据节点为 0 退出码 1，每条报错都附排查指引。随后复制 Markdown；`_merge_solutions` 以树侧为准把 `solutions.json` 增量并入产物侧（产物侧多出的条目保留）；`_copy_engine` 复制七个脚本（`condition_kernels.py`、`xlsx_reader.py`、`custom_lib.py`、`mo_config.py`、`analyzer.py`、`export_tree_excel.py`、`import_tree_excel.py`）和 `references/condition_binding_schema.md`，`tree/knowledge.md` 不存在时复制技能级 `knowledge.md`，技能级也没有就写模板。`_scaffold_analyzers` 为每个源和 `cross` 铺脚本：不存在就新建；内容与模板一字不差就跳过；`cross` 已填过则走 `_refresh_cross`（能升级就备份为 `cross_analyzer.py.bak` 后写入升级版，不能就原样保留）；`--keep` 保留；否则 `_regen_analyzer` 把旧脚本备份为 `<源>_analyzer.py.bak` 后写入当前版本的空白脚本。最后写 `plan.json` 与 `manifest.json`（`product_format_version`、`generated_at`、`scenario`、`slug`、`indicators`、`tree`），打印下一步指引，`export_tuning_table` 以子进程运行产物里的 `export_tree_excel.py`，失败只提示（通常是缺 openpyxl）。

**修改时要注意**

- `PRODUCT_FORMAT_VERSION` 只在不兼容时递增，并与 runner 的 `EXPECTED_PRODUCT_FORMAT` 同步。
- 模板里的顶层名字 `CONDITIONS`、`CUSTOM`、`INDICATOR` 被 `merge_conditions.write_conditions`、`make_dispatch._claimed_node_ids`、`_upgrade_cross_scaffold` 以及调优表导入导出脚本按名定位，改名要全部跟改。模板文本的任何改动都会让已有的空白脚本不再等于模板，重跑时按「已填过」处理（备份后重铺）。
- 已填过的 `cross_analyzer.py` 绝不能重铺成空白：注释记录此前重新生成曾把 `param_from` 传参链静默清空。
- `_scan_containers` 必须无条件扫描：新旧两代产物目录并存时，只在推导目录不存在才扫会选中过期的树。
- `_merge_solutions` 不能整表覆盖或删除：产物侧可能带有 cs-scot-adopt 并入的方案，审计发现过因此丢失。
- `tree/knowledge.md` 一旦存在永不覆盖，它是业务同事手写的。
- `export_tuning_table` 在 `merge_conditions.py` 里有一份相同实现，`scenario_out_dir` 在 `excel_to_tree.py` 里也有一份，改动要同步。
- 跨源模板依赖 `condition_kernels.alias_sources`；`raw_dir` 通道与 S 类的全量加载规则要和 `custom_lib.py` 的约定一致。

### scripts/make_dispatch.py

**功能与作用**

步骤 2.5 的任务说明生成脚本，把并行绑定前的机械准备从模型转交给脚本。它有三种模式：`--route` 生成分派任务说明，让一个子代理先判断每条判据可能归哪些数据源；默认模式为每个数据源生成按批切分的绑定任务说明；`--cross` 生成跨源补绑任务说明。输入是 `<OUT>/tree/` 下唯一的树索引、`overview/<源>_overview.json` 概览、枚举文件、`tree/knowledge.md`、可选的 `tree/script_digests.json`、`dispatch/route_batch<N>_result.json` 与 `dispatch/cross_candidates.json`；输出写在 `<OUT>/dispatch/` 下：`route_batch<N>.txt`、`<源>_batch<N>.txt`、`cross_batch<N>.txt`、声明完成标记的 `manifest.json`，并把耗时追加到 `<OUT>/timing.json`。注释记录它要防的三类实测问题：判据没读全就派发、EAV 窄表源没带枚举导致子代理自编指标名、不拆批导致回复截断或长时间无输出。

**实现介绍**

文件前半是固定文本：`COMMON_RULES`（十一条公共规约、各类参数速查、参考示例）、`EAV_RULES`、`CROSS_RULES`、`CROSS_OUTPUT_FORMAT`、`OUTPUT_FORMAT`、`ROUTE_RULES`、`ROUTE_OUTPUT`。`COMMON_RULES`、`OUTPUT_FORMAT` 与 `CROSS_RULES` 经 `str.format` 处理，所以文本里的 JSON 花括号都写成双花括号；其余几段不经 `format`，用单花括号。

判据来源 `load_conditions`：从树索引取出 `condition` 非空且 `pass_through` 为假的节点，返回（`node_id`、名字、判据）列表。知识载入：`parse_knowledge` 把 `knowledge.md` 按 `### ` 切成条目，正文之前可选的 `阶段:`、`适用:` 行（全角冒号归一成半角）分别进 `stage` 与 `tags`；`match_knowledge` 排除标了「诊断」且没提「绑定」「生成」的条目，有标签且不含「通用」的条目要有任一标签出现在节点名加判据文本拼成的 `name_pool` 里才选中；`load_knowledge` 读产物级 `tree/knowledge.md`。`load_digests` 读历史流程留下的 `tree/script_digests.json`（节点到平台脚本纪要），`digest_block` 把一条纪要渲染成任务说明里的块，`depends` 字段只在跨源任务说明里附上。

分派三级优先 `_route_conditions`：纪要声明了 `sources` 的判据只进对应源的批；否则看分派结果 `route_map`；都没有就广播进每个源的批：

```python
    for c in conditions:
        srcs = (digests.get(c[0]) or {}).get("sources") or []
        known = [s for s in srcs if s in inds]
        if known:
            if ind in known:
                outc.append(c)
            continue
        rt = route_map.get(c[0])
        if rt and ind not in rt:
            continue
        outc.append(c)
```

`_load_route` 读 `route_batch*_result.json`，坏文件整批作废并警告，源名过滤到本产物的源集合，过滤后为空的条目等同广播。`_load_route_map` 是跳步拦截：分派任务说明已生成、却一条结果都没有，且没有 `--no-route`，就拒绝生成绑定任务说明（退出码 1），注释记录实测模型出完分派说明 19 秒就直接重跑的问题。

切批 `split_batches`：没有纪要时按条数切（默认 `--batch-size` 12）；有纪要时再叠一个 30000 字符的预算，带纪要的判据体积大，按字符封顶自动少装几条。`_source_material` 取一个源的原料：概览缺失拒绝生成并给出 `analyzer.py --lean` 命令；概览列同时含 `DataItem` 与 `DataValue` 判定为 EAV 窄表，`find_enums` 找不到枚举文件（`<源>*枚举*.txt`、`*enum*.txt`、`*dataitem*.txt`、`*DataItem*.txt`）时同样拒绝，并给出抽枚举的命令。`build_briefing` 拼装一份任务说明：标题与【结果文件】路径、角色说明、公共规约、EAV 规则（仅 EAV 源）、知识条目、概览原文、枚举原文、本批判据清单（判据原文逐字列出，带纪要的附纪要块）、输出格式；结果文件约定为 `<源>_batch<N>_result.py`，内容是 Python 列表字面量，末行必须是 `# BATCH_COMPLETE`。`_write_source_briefs` 逐源取原料、按三级优先裁剪、切批、写文件；`_report_written` 写 `dispatch/manifest.json`（`{"complete_marker": true}`）、打印任务说明清单与派发句式、记录耗时，有源拒绝生成时退出码 1。

分派模式 `_run_route`：候选池排除纪要已声明归属的判据，每批 60 条，`_route_source_block` 列出各源最多 30 个列名供快判，结果文件约定为 JSON 数组，每条含 `node_id` 与 `sources`。跨源模式 `_run_cross`：`_cross_candidates` 读 `dispatch/cross_candidates.json`，再用 `_claimed_node_ids`（`ast` 静态读各单源脚本的 `CONDITIONS`）找出没有被任何源认领的诊断节点一并补进候选，注释说明子代理的 `cross_source` 标记不可靠，机械补位保证跨源判据不会静默消失；候选写回同一文件，供 `check_tree_coverage.py` 区分「流程中」与「真漏」；`_cross_shared_sections` 把所有源的概览与枚举都放进每份说明，按 `--batch-size` 切批写 `cross_batch<N>.txt`，结果文件为 `cross_batch<N>_result.py`。

`main`：`--route` 与 `--cross`、`--route` 与 `--no-route` 互斥，退出码 2；树索引不唯一、树上没有判据、找不到任何源（`--indicators` 或按 `scripts/*_analyzer.py` 自动发现，排除 `cross_analyzer.py`）都退出码 2；场景名取树索引文件名去掉 `.tree_index` 后缀。

**修改时要注意**

- 规约文本经 `format` 的那几段必须写双花括号，新增示例时别漏；不经 `format` 的段落则相反，混用会在运行时抛 `KeyError`。
- 结果文件名、`# BATCH_COMPLETE` 标记、`dispatch/manifest.json` 的 `complete_marker` 字段、`route_batch*_result.json` 的格式都由 `merge_conditions.py` 对应读取，两个脚本要一起改。
- 跳步拦截与 EAV 缺枚举拒绝生成都是针对实测问题的防护，不要为了方便放松；广播必须是显式的 `--no-route`。
- 默认批大小 12 的依据写在参数帮助里：批太大时子代理最后一次性写文件要十几分钟且期间无工具输出，会被按 10 分钟无输出终止；批多则并行度高。
- `digest_block` 在单源任务说明里不附 `depends`，注释记录它唯一的实测作用是诱导子代理以「需上游传参」放弃。
- `_claimed_node_ids` 对解析失败的分析脚本直接跳过，坏文件会让其节点被当作无人认领补进跨源候选。
- 知识条目的匹配只看「适用」行与节点名加判据文本，正文不参与；标「诊断」的条目不进任务说明。

### scripts/merge_conditions.py

**功能与作用**

步骤 2.5 的合并脚本，把子代理写的批次结果机械合并进各分析脚本。命令行 `merge_conditions.py --out "<OUT>" [--indicators ...]`。输入是 `dispatch/` 下的任务说明（用于发现有哪些源、哪些批）、各批结果文件 `<源>_batch<N>_result.py`、`dispatch/manifest.json` 与树索引；输出是改写后的 `scripts/<源>_analyzer.py`（只替换 `CONDITIONS` 赋值块）、跨源候选文件 `dispatch/cross_candidates.json`、`timing.json` 里每批的绑定耗时，有写入时还刷新调优表。退出码 0 表示全部批次到齐并合并完成，1 表示有缺批或坏批，2 表示用法或产物错误。注释说明设计动机：几百条算子绑定若经子代理回复、主代理转录、再编辑写入，要三次经过模型上下文，实测超出主会话的上下文上限；改为子代理各写各的结果文件，本脚本串行合并，先到先合，可反复运行。

**实现介绍**

`_discover_plan` 用正则 `^(?P<ind>.+)_batch(?P<n>\d+)\.txt$` 从任务说明文件名发现源与批号，排除 `route`；`_require_marker` 读 `dispatch/manifest.json` 的 `complete_marker` 决定是否启用完成标记检查（老目录没有标记文件就不启用，向后兼容）；`_load_tree_nodes` 读唯一的树索引作为回填依据。

每个源由 `_merge_source` 处理，对每批依次做：`_collect_batch` 收结果，结果写成 `.json` 的改名为 `.py` 后照常接收；`parse_result` 验收，读不了或含 U+FFFD 乱码拒收，启用标记检查时缺 `# BATCH_COMPLETE` 按截断拒收，然后 `ast.literal_eval` 整体解析，失败时截取首个 `[` 到末个 `]` 再试，再失败用 `json.loads` 救回 JSON 风格写法，结果必须是字典列表；`cross` 批缺完成标记时由 `_cross_amnesty` 按内容判断完整性，结果里的 `node_id` 集合覆盖任务说明里全部 `[nN]` 候选则仍然接收；含 `class` 为 S 的条目整批拒收，S 类只接受人工或调优表来源。通过验收后，非 `cross` 源用 `_extract_cross_marks` 抽出 `decision` 为 `cross_source` 的标记汇入 `cross_cands`，不进本源脚本；`_backfill` 以树索引为准回填 `id`（写成 `<源>-<node_id>`）、`node`、`condition_text`，非 `cross` 源回填 `source`，`cross` 的 `source` 是查询落点、由子代理给出、不覆盖，同时点名 `node_id` 不在树里、`cross` 真绑定缺 `source`、`cross` 的 `decision` 没写理由三类问题；`_dedup` 按全部字段排序后的 `repr` 去掉完全重复的条目；`drift_warnings` 点名缺 `node_id` 和 C/G 类既无 `contains` 也无 `equals` 的条目（条目保留，提示修改算子绑定）。合并后有条目就调用 `write_conditions`，用 `ast` 定位 `CONDITIONS` 赋值块整体替换，并在写入后重新解析自检：

```python
    block = "CONDITIONS = [\n" + "".join(f"    {e!r},\n" for e in entries) + "]"
    lines = src.splitlines(keepends=True)
    new_src = "".join(lines[:span[0] - 1]) + block + "\n" + "".join(lines[span[1]:])
    analyzer.write_text(new_src, encoding="utf-8")
```

两条软提示：非 `cross` 源合并条数不少于 10 且 `decision` 占位超过一半，提示可能对全树逐条表态；`cross_source` 标记不少于 10 条且超过总数一半，提示可能把「归别的源答」误标成跨源。`cross` 批不做占位比例检查，注释记录其规约要求每条候选都表态，曾有 8/13 个占位被误点名。

收尾：`_write_cross_candidates` 有标记就写排序后的候选文件并提示下一步跑 `make_dispatch.py --cross`；本轮完整扫过全部单源批次且一个标记都没有时删除陈旧候选文件。`_record_batch_timings` 用结果文件与任务说明文件的修改时间之差记每批耗时，已记过的不重记。`export_tuning_table` 与 `assemble_scenario.py` 里的同名函数相同。`_report` 打印最多 40 条点名，有未完成项时退出码 1，并明确禁止自己写脚本合并结果文件（会丢标记、去重、守卫与回填）。`main` 在没有 `dispatch/` 目录或没有任何任务说明时退出码 2。

**修改时要注意**

- 按内容判断完整性只对 `cross` 批开放，因为跨源规约要求每条候选都表态；单源批允许不认领，无法用「逐条表态」判断完整性，只能靠标记。不要手工往结果文件补标记，那会让截断检查失效。
- `write_conditions` 用 `repr` 生成 Python 字面量，条目里只能有可字面求值的值；写入后的语法自检不能去掉。
- 回填会覆盖子代理写的 `id`、`node`、`condition_text`、`source`（`cross` 的 `source` 除外），这是有意为之，子代理抄的判据常被截断。
- S 类拒收是防线：模型产出的 S 类会绕过全部格式校验。
- 文件名约定与 `make_dispatch.py` 绑定；`export_tuning_table` 两处实现要同步。
- 去重只识别完全相同的条目，字段略有差异的重复会都保留。

### scripts/package_scenario.py

**功能与作用**

场景技能打包脚本，对应 SKILL.md 的步骤 5，只在步骤 1 没能直接生成技能目录、要打包旧产物或在另一台机器重新打包时运行。命令行 `package_scenario.py --out <产物目录> [--dest <根目录>] [--name <英文名>] [--execute-skill <目录>] [--rca-skill <目录>] [--force]`。输出是技能目录 `<dest>/<短名>/`：cs-scot-execute 的执行指引与脚本副本（`SKILL.md` 的 frontmatter 改名并按场景写 description，正文不动），`assets/` 下是本产物加 fault-tree-rca 副本，有 zip2csv 时也内置。它的 `find_material` 与 `scaffold_skill` 两个函数被 `excel_to_tree_v2.py` 导入，用于步骤 1 直接搭技能目录，所以它同时是步骤 1 的依赖。

**实现介绍**

`_SKIP_DIRS` 与 `_ignore` 定义不进技能包的临时文件：`__pycache__`、`overview`、`dispatch`、`runs`、`result`、`.pyc`、`.py.bak`、`result-*`、`runs-*`、`index.jsonl`、`conditions_all.json`、`*_conditions*.py`，与 SKILL.md 步骤 4 的清理清单同源。`find_material(name)` 按两处查找素材：先是本技能目录内与 `SKILL.md` 同层的同名目录（发行包形态），再是同一 skills 目录下的独立技能；都没有时仍返回后者的路径，由调用方判断。`scaffold_skill` 把 cs-scot-execute 整个复制为技能目录，`rewrite_skill_md` 用正则匹配 frontmatter（容忍 CRLF）只替换 `name:` 与 `description:` 两行，缺 frontmatter 退出码 1，然后建 `assets/`，复制 fault-tree-rca 到 `assets/fault-tree-rca`，有 zip2csv 则复制到 `assets/zip2csv`。

`main` 的顺序：产物目录没有 `plan.json` 退出码 1；`_read_identity` 优先从 `manifest.json` 取场景名与短名，老产物没有 manifest 时从树索引取，取不到退出码 1；`_resolve_name` 用 `--name`（经 `excel_to_tree_v2.slugify`，无效退出码 2）或产物的 `slug`，都没有退出码 2；两个素材目录下没有 `SKILL.md` 退出码 2；`_resolve_dest` 决定目标目录：`--dest` 优先，否则产物目录的父目录名是 `scot产物` 时放到与之平级的 `scot技能`，再否则放到父目录下的 `scot技能`；目标与产物目录、两个素材目录重叠退出码 2，目标已存在且没有 `--force` 退出码 2，有 `--force` 先删除。打包前导入 `export_tree_excel.export` 刷新调优表，失败只提示；`scaffold_skill` 搭框架，`_copy_product` 把产物复制进 `assets/`（`--out` 本身是已打包技能的 `assets/` 时剔除其中旧的 `fault-tree-rca` 与 `fault-tree-rca-debate` 目录，避免与新放的副本冲突）；最后打印技能目录、分析脚本数量、是否含 `solutions.json` 以及上传后的使用句式。

**修改时要注意**

- 默认目标目录不能回退成产物目录的父目录：产物目录名与技能名都是短名，目标会与产物目录同路径，加 `--force` 会先删产物再打出空包，注释记录实测过。重叠检查同理不能去掉。
- `_ignore` 的清单要与 SKILL.md 步骤 4 的清理命令保持一致。
- `find_material`、`scaffold_skill` 的签名被 `excel_to_tree_v2.default_out` 依赖。
- 短名规则通过导入 `excel_to_tree_v2.slugify` 共用，不要在本文件另写一套。
- 注释明确不会增加 `--with-debate` 参数，交付的场景技能只带标准根因分析。

### scripts/analyzer.py

**功能与作用**

数据概览工具，被 `assemble_scenario.py` 复制进产物 `scripts/`，步骤 2.5 用它为每个数据源生成概览 `overview/<源>_overview.json`，供填写算子绑定前看清有哪些列、量纲和类别取值；`make_dispatch.py` 读概览的 `summary.columns` 判断 EAV 窄表并把概览原文嵌入任务说明。命令行 `analyzer.py <数据文件> -o <输出.json> [--indicator <标签>] [--fault-tree <树.md>] [--sheet <页签>] [--lean]`。它只统计、不判断，不输出任何命中或结论。读 csv 与 xlsx（经 `xlsx_reader.py`），数据是 MO 配置文本时按需导入 `mo_config.py` 解析，解析失败退回按 csv 读。

**实现介绍**

模块开头把 csv 单字段上限调到最大（命令回显类 CSV 的多行字段可达数 MB，默认 128KB 会抛错；Windows 上溢出时退到 2 的 31 次方减 1）。`_decode` 按候选编码解码。`_sheets` 列出页签：xlsx 用 `xlsx_reader.list_sheets`，MO 配置文本（后缀 `.txt`、`.cfg`、`.mo`、`.moc`）用 `mo_config.mo_sheets` 把每个 MO 类型当一个页签。`_multisheet_brief` 逐页签只取表头与行数。`_load` 读表：MO 文本走 `parse_mo_text`，xlsx 走 `read_xlsx`（指定的页签不存在时落回活动页），其余按 csv。

统计核心 `_stat_values`：先分出空值（`_NULLISH` 含空串、na、n/a、null、none、nil、`-`，NIL 视为缺测而不是一个类别值），计入 `nulls`；非空值里至少 90% 能转成数（`_to_num` 去掉末尾百分号）就按数值列给 `count`、`min`、`max`、`mean` 与十分位 `deciles`；否则按类别列给 `distinct` 与 `top`，不同取值不超过 200 个时全部枚举，超过时只给最高频 20 个并附 `vocab_note` 声明词汇不完整、禁止据此判断某值不存在，注释记录因 top-N 截断把 vrrp 藏起来、绑定代理误判数据里没有而漏诊的问题；`samples` 给前 8 个高频值各附最多 3 个样例标识，标识列从 `_ID_CANDIDATES` 里挑第一个存在的。`_analyze_tabular` 逐列调用它，输出 `indicator`、`kind`、`summary`（`total_records`、`columns`、`id_column`）与 `field_stats`。

故障树投影是可选的压缩：`_auto_tree` 在脚本旁的 `../tree/` 下恰有一个 Markdown 文件时自动启用；`_project` 只保留名字（至少 3 个字符）在树文本里出现过的列，命中少于 3 列或少于 10% 时保留全部列并在 `projection` 里写 `kept_all_fallback`，因为树常用英文或编码名、数据列常是中文，子串几乎不会命中。`main`：多页签且没给 `--sheet` 时只输出各页签的列与行数（`kind` 为 `multisheet_overview`），不做全量统计；否则统计、投影，`--lean` 删掉每列的 `deciles` 与 `samples` 并标记 `lean: true`；写 JSON 并打印一行摘要。`_default_indicator` 从 `--indicator`、脚本名 `<源>_analyzer` 或数据文件名推断标签。

**修改时要注意**

- 不超过 200 个不同值全枚举与超过时的 `vocab_note` 都不能去掉，它们是绑定子代理判断「列的形状」的依据。
- NIL 等缺测标记必须计入 `nulls`，不能当类别值或 0。
- `summary.columns` 被 `make_dispatch._source_material` 与 `_route_source_block` 读取，改输出结构要同步。
- 投影保留全部列的保护不能去掉，否则跨命名的数据会被投成空表。另外 `_auto_tree` 要求 `../tree/` 下恰有一个 `.md` 文件，而组装后的产物 `tree/` 里还有 `knowledge.md`，所以当前产物里自动投影实际不会触发，只有显式传 `--fault-tree` 才投影。
- `--lean` 只删十分位与样例，不删列。`datacom_log_adapter.md` 第四节说精简概览会省略超大枚举列，与当前代码不一致，以代码为准：超大枚举列在概览里是前 20 个高频值加 `vocab_note`。

### scripts/xlsx_reader.py

**功能与作用**

零第三方依赖的 xlsx 读取器，用标准库 `zipfile` 与 `xml.etree` 直接解析 xlsx 内部的 XML。本技能所有读 xlsx 的地方（`analyzer.py`、`condition_kernels.py`、两个建树脚本、`import_tree_excel.py`）都从它读，注释给出理由：同一份数据在各环节必须读出同样的值，否则概览看到的值与逐条件判定用的值会静默不一致，根因分析的结论跟着出错；写 xlsx 仍用 openpyxl，因为写只发生在生成与迁移期。它随 `assemble_scenario.py` 复制进产物 `scripts/`。对外六个函数：`is_xlsx`、`list_sheets`、`sheet_briefs`、`read_xlsx`、`read_rows`、`iter_sheets`。不支持旧版 `.xls`，遇到时抛 `ValueError` 提示另存为 `.xlsx`。

**实现介绍**

单元格层：`_text` 取 `<si>` 或 `<is>` 的文本，兼顾直接的 `<t>` 与富文本的多个 `<r><t>`（单元格局部加粗就是富文本，漏了会丢字）；`_col_index` 把 `BC12` 这类引用转成列下标，必须靠它定位，因为 xlsx 会整个跳过空单元格，按出现顺序枚举会让空值后面的列错位；`_serial_to_text` 把日期序列号还原成 `YYYY-MM-DD [HH:MM:SS]`，范围之外的当普通数值，按秒取整避免 08:30:00 变成 08:29:59；`_num_text` 整数原样返回（大整数编号不过 `float`、不丢精度），小数走 `repr(float(...))` 的最短往返表示，与 openpyxl 转 `float` 再 `str` 的结果对齐；`_cell` 按类型分发：`inlineStr` 内联串、`t="s"` 共享字符串、布尔、错误值与公式字符串原样、样式命中日期格式的转日期文本，其余走数值归一，公式单元格取缓存值 `<v>`。

工作簿层：`_open` 对 `.xls` 与非 zip 文件抛 `ValueError`；`_shared_strings` 读 `xl/sharedStrings.xml`；`_date_styles` 读 `xl/styles.xml`，内置日期格式号集合 `_DATE_FMT_IDS` 加上自定义格式码里含 y、m、d、h、s 的编号，映射到 `cellXfs` 的样式下标，没有它时间列会变成 45292 这种序列号被当普通数值统计；`_sheets` 读 `xl/workbook.xml` 与 `xl/_rels/workbook.xml.rels` 得到页签名与真实文件路径（关系缺失时按顺序推断）以及活动页下标；`_iter_rows` 用 `iterparse` 流式产出每行，按 `r` 属性把空单元格补回原位，逐行 `clear()` 不整张进内存；`_header_of` 去掉表头尾部的空列；`_rows_as_dicts` 按表头建行字典；`_sheet_path` 按页签名选页，不存在抛 `ValueError` 并列出可用页签。

对外函数：`list_sheets` 返回页签名；`sheet_briefs` 每页只取表头与行数；`read_xlsx(path, sheet=None)` 返回（表头、行字典列表），不给页签取活动页；`read_rows` 返回位置形态的全部行（含表头行，值不去空白、重复表头不合并、不建字典），给需要校验重复列的调优表导入脚本用；`iter_sheets` 返回全部页签的（页签名、表头、行字典列表），供多页签建树使用。

**修改时要注意**

- 任何新的读 xlsx 的需求都要走本模块，不要另引 openpyxl 读，以免各环节读出不同的值。
- 列定位必须依赖 `r` 属性；`_rows_as_dicts` 遇到重复表头时后一列会覆盖前一列，这正是 `read_rows` 存在的原因，不要把两者合并。
- `_num_text` 的两条规则（整数不走 `float`、小数取最短表示）是为了与 openpyxl 的历史行为一致，改动会让旧概览与新读数对不上。
- 日期样式识别不能省，否则时间列的量纲全乱。

### scripts/condition_kernels.py

**功能与作用**

内核算子库，是整条诊断流水线里负责确定性统计的一层。各数据源的分析脚本 `<指标>_analyzer.py` 和跨源判据脚本 `cross_analyzer.py`（两者都由 `assemble_scenario.py` 按模板生成）在运行时 `import` 本文件，对 `CONDITIONS` 里的每条算子绑定调用 `evaluate_binding(绑定, {源名: 表}, 源名)`，得到一条「事实」字典，再把事实挂在该条绑定的 `fact` 字段下，连同 `id`、`node`、`node_id`、`condition_text`、`class`、`mapping_reason` 一起写进事实文件（生成期在 `<OUT>/overview/<指标>_facts.json`；执行期由 cs-scot-execute 的 `runner.py` 按执行计划调用分析脚本，事实交给 fault-tree-rca 做根因分析）。分析脚本里标了 `"mode": "custom"` 的条目由脚本自己的 `CUSTOM` 函数计算，不经过本文件。

输入是算子绑定（字典，字段定义见 `references/condition_binding_schema.md`）和数据源字典，表的形态统一为 `(header, rows)`：`header` 是列名列表，`rows` 是每行一个字典。输出是事实字典，带算出的量、来源信息（解析到哪一列、怎么命中、多少空值）和 `predicate_satisfied`（在解析到的数据上做字面比较的真假）。文件说明里写明了本层的分工：列解析不到就输出数据不足，不给 `predicate_satisfied`，不编造值；空值计入 `nulls`，不当作一个类别也不当作 0；成立与否的最终判定、路径回溯、数据不足的传播由 fault-tree-rca 负责。

除算子外，文件还提供读表函数 `load_table`、页签清单 `list_sheets`、源名互认 `alias_sources`、列名解析 `resolve_column`，供分析脚本和 `custom_lib.py` 复用。步骤 2 组装时它被复制进产物 `scripts/` 随场景技能分发；`kernel_selftest.py` 是它的回归测试；`skills/cs-scot-migrate/engine/` 下另有一份副本供单独安装的迁移技能使用。早期版本的 B、E、F、H、I 五类算子已停用，移到 `archive/kernels-v1/`。

**实现介绍**

#### 统一入口与分派

`evaluate_binding(b, sources, source_name, _resolved=False)` 是唯一入口，按下面的顺序处理一条绑定：

1. `_declared_fact`：绑定带 `decision` 时不跑算子。`decision` 为 `cross_source`（绑定阶段留下的跨源标记）时输出数据不足，并提示回到步骤 2.5 跑 `make_dispatch --cross`；为 `insufficient_data`、`manual`、`not_applicable` 时直接输出对应状态（只有 `manual` 的 `status` 是 `manual`，其余是 `insufficient_data`），带 `declared: True`，`reason` 取 `mapping_reason` 或 `reason`。
2. 类别 `J` 或带 `manual` 字段，交 `k_manual`。
3. `_unknown_keys_fact`：顶层字段白名单。允许的字段是公共字段 `_META_KEYS` 加该类别的 `_CLASS_KEYS[cls]`，多出任何字段都输出数据不足。代码注释记录了加这道检查的原因：`require` 忘配 `count_rows`、A 类误写 `contains`、`combine` 拼错，以前都被静默忽略，然后算出语义错误的确定结论。
4. 类别 `S`，交 `k_script`；S 类不接受 `param_from`。
5. 带 `param_from`，交 `_eval_with_params`（见下文「参数化查询与传参链」）。
6. `_resolved` 为 `False` 时用 `_find_stray_params` 查找整串形如 `@名` 的引用；写了 `@xxx` 却没有 `param_from` 就输出数据不足，否则会拿字面的 `@` 去匹配，恒不命中，等于错误地排除。
7. `P` 类带 `pair_from`，交 `k_pair_from`。
8. `D` 类：顶层带 `filter` 直接拒收（提示把 `filter` 写进各 `sub`），否则交 `k_combo`。
9. 其余读表类别：`_source_table` 取表（没有该源或表为空都输出数据不足；源名写成 `cross` 时另有提示，说明 `cross` 不是数据源名），`_kernel_fn` 按 `_KERNELS` 取算子（`A` 对应 `k_threshold`，`C` 和 `G` 对应 `k_match`，`P` 对应 `k_pair`；`_ARCHIVED` 里的 B/E/F/H/I 输出「已停用」的数据不足；其他字母输出未知类别），带 `filter` 的走 `_run_filtered`，否则直接 `fn(table, b)`。

#### 各类算子

阈值比较（A 类，`k_threshold`）。先用 `resolve_column` 解析 `column`；再用 `_shape_err(b, need_op=True)` 做参数合法性检查：`op` 里的全角符号按 `_FW_OPS` 归一为半角，不接受的运算符、非数值的 `threshold`、不在 `_AGGS` 里的 `agg`、不在 `_SCOPES` 里的 `scope`、小于 1 的 `require`，以及 `require` 配在非 `count_rows` 作用域下，都返回错误串并输出数据不足。数值序列由 `_numeric_series` 取得，`_num` 会去掉百分号，把 `_NULLISH` 里的空值写法和 `nan`、`inf` 一律当缺测；`_apply_transforms` 按 `transforms` 词汇依次做 `scale:<f>`、`abs`、`neg`、`round:<n>`、`log10`（非正数取对数按缺测处理）。带 `extract` 时改走 `_threshold_extract_series`，每格先抽值再转数值。一个有效数值都没有时 `_threshold_no_values` 输出数据不足，`extract` 情形附该列样例。`_threshold_verdict` 按作用域出判定：`count_rows` 给 `count_satisfied`、`ratio`，`predicate_satisfied` 为满足比较的行数不少于 `require`（默认 1）；`aggregate` 用 `_agg` 聚合（默认 `mean`）给 `value`，再与阈值比较。`extract` 情形还附 `hits`，即满足比较的那几格的原文片段，至多 3 条。

字段匹配与日志匹配（C 类与 G 类，`k_match`）。两类共用同一个算子，G 类只是把 `cls` 标成 `G`。匹配模式的取法：有 `regex` 用正则，有 `contains` 用子串，否则按 `match` 字段（默认 `exact`，整串相等）；目标值取 `contains`、`equals`、`regex` 之一。`_match_spec_err` 检查：目标值为空拒收（注释说明是防 `op`、`target` 这类清单外字段让 `matched=0` 被当成排除）、`match` 枚举、每条正则逐条编译、`extract` 规格、`require` 不小于 1、`absent` 与 `require` 互斥。大小写处理：`case_insensitive` 在 `contains` 模式默认为真，其他模式默认为假；正则模式不做小写化。`_match_scan` 逐行统计：空值计入 `nulls`；带 `extract` 时抽不到值的行也计入 `nulls`，不算未命中；`dist` 记取值分布（键截 80 字）；`matched` 记命中行数；`by_target` 记每个目标各命中多少行；`samples` 记前 3 条命中行的标识（`_id_col` 在 `_ID_COLS` 里找标识列，找不到用 `row#<序号>`）；`hit_texts` 用 `_hit_snippet` 截取命中位置附近的原文。`_match_fact` 组装事实：`total` 是纳入统计的非空行数；带 `absent` 且 `total` 为 0 时输出数据不足（列全空无法证明「不存在」，缺的可能是导出内容而不是配置）；`predicate_satisfied` 在 `absent` 下为 `matched == 0`，否则为 `matched >= require`；事实带 `match`、`targets`、`matched`、`total`、`ratio`、`nulls`、`top`（前 5 个取值）、`samples`，按需带 `absent`、`hits`、`extract`、`matched_by_target`（多目标时才输出）。

多条件组合（D 类，`k_combo`）。`sub` 为空直接拒收，因为空的 AND 会恒判成立，注释记录实际出过假阳性；`combine` 只接受 `AND`/`OR`，拼错会被静默按 OR 算，所以也拒收。不带 `group_by` 时对每个子绑定递归调用 `evaluate_binding`（子绑定可自带 `source`，缺省继承父绑定的源），再由 `_combine_tri` 汇总：

```python
    if comb == "AND":
        return ((False, "ok") if any(p is False for p in preds)
                else (None, "insufficient_data") if any(s != "ok" for s in sts)
                else (True, "ok"))
    return ((True, "ok") if any(p is True for p in preds)
            else (None, "insufficient_data") if any(s != "ok" for s in sts)
            else (False, "ok"))
```

即 AND 下有假即假，否则有数据不足即数据不足，否则为真；OR 对称。事实带 `sub_facts`。带 `group_by` 时走 `_combo_grouped`：子绑定不得跨源（各源的行没有对齐关系）；用 `_combo_groups` 按分组列切组，分组值为空的行不入组；`_combo_scan_groups` 对每组把本源的表替换成组内行后跑全部子绑定并汇总；`_combo_group_fact` 做组间汇总：有组全真则成立，否则有组数据不足则数据不足，否则不成立。事实带 `groups`、`matched_groups`（前 5 个）、`matched_groups_total`、代表组的 `sample_group` 与 `sub_facts`。注释说明这是为了解决「整列有 A 也有 B，但 A 和 B 不在同一接口上」的错误确证。

成对比较（P 类，`k_pair` 与 `k_pair_from`）。`k_pair` 处理同一张表里本端与对端靠分侧列区分的情形：`side_column` 的值含 `peer_marker`（默认 `*`）的行是本端，其余是对端；取 `value_column` 的值（可加 `extract`）；`fault_when` 为 `not_equal`（默认，两侧不等算故障成立）或 `equal`。任一侧没有取值输出数据不足（注释强调「查不到对端」是无法判断，不是不成立）；一侧出现多个不同的值输出数据不足（有歧义，提示先用 `filter` 缩小行）。事实带 `local`、`peer`、`equal`、`n_local`、`n_peer`。`k_pair_from` 处理两侧在不同文件里的情形：`pair_from` 必须有 `local` 和 `peer` 两侧取值规格，各自用 `_resolve_values` 取值再比较；顶层带 `filter` 拒收（静默忽略会让两侧取全表值）；事实另带 `local_source`、`peer_source`。

人工确认（J 类，`k_manual`）。输出 `status: "manual"`、`predicate_satisfied: None` 和 `note`，不读数据。

交付脚本（S 类，`k_script`）。供人工调优和 cs-scot-migrate 迁入节点交付包使用。`_resolve_script` 要求 `func` 与 `file` 二选一：`func` 在同目录 `custom_lib.py` 的 `REGISTRY` 里查函数；`file` 是 `scripts/` 下的相对路径，解析后必须仍在 `scripts/` 之下，脚本需有入口函数 `check(table, args, load)`。`_load_py` 用 `importlib` 加载模块并缓存在 `_SCRIPT_CACHE`，同一路径只加载一次。调用 `fn(table, args, load)`，`load(名)` 返回 `sources` 里对应的表。异常处理分两层：`SystemExit` 单独捕获（命令行型脚本常用 `sys.exit` 报错，它不是 `Exception` 的子类，不拦会中止整个跨源脚本），其他异常附 `scripts/` 下最后三帧的位置信息，两者都按数据不足处理，不当作不成立。返回值校验：`status` 为 `insufficient_data` 时补齐 `class`、`script`、`predicate_satisfied`、`reason`；`status` 为 `ok` 且 `predicate_satisfied` 为布尔时补齐 `class`、`script`、`explain`；其他形状输出「返回不符合约定格式」的数据不足。

#### 行级过滤与格内取值

行级过滤由 `_apply_filter(table, flt)` 实现，服务于键值形态的窄表（一行一个量，如 `DataItem`/`DataValue`/`Unit`）和「先选中某条命令的行」的情形。`filter` 可以是一个子句或子句列表（列表为 AND）。`_filter_clause` 解析一条子句：必须是字典；列名用 `resolve_column` 解析；`equals`、`contains`、`excludes` 必须恰给一个；目标值归一为小写字符串列表，全空拒收。`_filter_row_keep` 判断一行是否保留：`equals` 要整格相等，`contains` 要含子串，`excludes` 要不含才保留，而且 `excludes` 模式下过滤列为空的行不保留（注释记录：对端行选择混入残缺的续行、汇总行后，实际比出过错误的值）。`_FILTERABLE` 限定作用于 A、C、G、P 四类。`_run_filtered` 在过滤后调用算子：过滤后 0 行输出数据不足（该指标项本次未采集）并带 `filter`；否则事实附 `filter` 与 `filter_rows`。

格内取值由 `_extract_spec_err` 与 `_extract_val` 实现。规格只接受 `{"kv": 键名}` 或 `{"regex": 正则}` 之一，正则编译失败拒收（注释提醒判据里的 `%u`、`%d` 是 printf 占位符，不是正则）。`kv` 匹配「键 + 冒号或等号 + 值」，值读到空白、逗号或分号为止，但逗号后恰好跟 3 位数字时当作千分位分隔符继续读，`_decomma` 再把 `1,234` 这类形态去掉逗号转数值；`regex` 取第 1 个捕获组，没有捕获组取整个命中。抽不到返回 `None`，由调用方按缺测处理。`_hit_snippet` 负责证据片段：从命中位置前 30 字起截取 120 字，连续空白压成一个空格，截断处加省略号；注释说明整格回显可达数 KB，证据引用的是代码截取的原文而不是模型转述。

#### 参数化查询与传参链

`_eval_with_params(b, sources, source_name)` 处理带 `param_from` 的绑定。`param_from` 必须是非空字典，每个键是参数名，值是取值规格 `{source?, column, filter?, extract?}`，可加 `expand`。`_resolve_params` 按声明顺序逐个解析：`_param_values` 先校验参数名只含字母、数字、下划线，再用 `_subst` 把前面已解析的参数代入本参数的规格（引用了排在后面的参数会报「只能引用已解析的」，所以不会出现循环），弹出 `expand` 后用 `_resolve_values` 抽值。不带 `expand` 的参数抽到多个不同值输出数据不足（歧义）；`expand` 只接受 `all`/`any`，一条绑定只允许一个参数带 `expand`（两个一起展开会组合爆炸），展开值数不得超过 `_EXPAND_CAP`（32）。

`_run_substituted` 用 `_subst` 把参数代入去掉 `param_from` 后的查询体，引用了未声明的参数报数据不足，然后以 `_resolved=True` 调用 `evaluate_binding`，跳过残留 `@` 检查；注释说明抽出的参数值本身恰好形如 `@xxx` 时必须按字面参与匹配，评审时实际出过这种误报。没有 `expand` 时事实附 `params`；有 `expand` 时 `_expand_fact` 逐值评估，`all` 按 AND、`any` 按 OR 汇总，事实带 `params`、`expand`（参数名、模式、`n`、`n_true`、`n_false`、`n_insuf`），`_expand_evidence` 再附 `fail_values`（`all` 下不满足的值）或 `hit_values`（`any` 下命中的值），各取前 5 个，以及一个代表性的 `sample_value` 与 `sub_facts`。

`_subst` 的替换规则：只有整串匹配 `_PARAM_RE`（`^@名$`）的字符串才替换，句子里夹的 `@` 不动；`_NO_SUBST_KEYS` 列出的文案字段（`mapping_reason`、`condition_text`、`node`、`id`、`note`、`reason`）不替换；tuple 和 set 按 list 处理并统一成 list；自带 `param_from` 的子字典整体深拷贝后跳过，由它自己评估时解析，这就是「D 类子绑定自成作用域」的实现。`_find_stray_params` 用同一套遍历规则找未解析的引用。

`_resolve_values(spec, sources, parent_source)` 是 `param_from` 与 `pair_from` 共用的取值函数：规格键只允许 `_SPEC_KEYS` 里的 `source`、`column`、`filter`、`extract`，拼错拒收（注释记录 `extract` 拼成 `extrct` 被静默忽略后，参数变成整格混杂文本，曾把 `absent` 判成假阳性）；`_spec_table` 取表并先过滤，源缺失、空表、过滤后 0 行都返回错误；`_distinct_values` 收集非空取值并去重保序；一条值也没抽到时返回错误并附该列样例。

#### 事实的统一格式与数据不足的各种情形

所有算子的事实都是字典，公共字段有 `class`、`status`（`ok`、`insufficient_data`、`manual` 三种）、`predicate_satisfied`（`True`、`False`、`None`）、`resolved_column`、`resolve`（命中方式），非 `ok` 时带 `reason`。数据不足事实统一由 `_insuf(cls, reason, **extra)` 构造。各类算子的专有字段见上文。注释提到 `matched_by_target` 这类只供校验审计的字段不在 cs-scot-execute `tree_prune.py` 的 `_FACT_KEEP` 白名单里，不会进入根因分析上下文。

列名解析 `resolve_column(header, wanted)` 的顺序是：精确匹配；去掉组前缀（`_strip_prefix` 按首个冒号切，如 `Common:`）后精确匹配；归一化（去前缀、去空格、忽略大小写）后唯一匹配，多个候选返回 `ambiguous`；按 `_COL_ALIASES` 查同义列（`ObjContent` 与 `CmdResult` 互认）；含 `->` 的嵌套路径取末段重试；都不中返回 `not_found`。解析不到或有歧义交上层判数据不足，不挑相近的列替代。`alias_sources(sources)` 按 `_SOURCE_ALIASES` 让 `cmd` 与 `diag` 互认：只给了其中一个时补另一个键指向同一张表，两个都给了则各归各。注释说明这是 2026 年 7 月平台把 `diag` 改名 `cmd` 后的兼容处理。

读表函数 `load_table(path, sheet=None)`：后缀为 `.txt`、`.cfg`、`.mo`、`.moc` 时交 `mo_config.parse_mo_text` 解析成表，`sheet` 指定 MO 类型，解析失败改按 csv 读；xlsx 走同目录 `xlsx_reader.read_xlsx`；csv 用标准库 `csv.DictReader`，`_decode` 依次尝试 `_ENCODINGS` 里的 utf-8-sig、utf-8、gbk、gb18030、latin-1。模块加载时把 `csv.field_size_limit` 放到 `sys.maxsize`（Windows 上溢出时用 `2**31 - 1`），因为命令回显表的多行字段可达数 MB，默认 128KB 上限会直接抛错。`list_sheets` 对 xlsx 返回页签名，对 MO 配置文本返回 MO 类型清单，csv 返回空列表。

数据不足的来源可以归为五类，便于排查时定位：

- 数据层面：没有该数据源（提示用 `--data <源>=<文件>` 提供）；表为空（仅表头）；`filter` 命中 0 行；列解析不到或有歧义；该列没有有效数值；`extract` 一条也抽不到；`absent` 判据遇到列全空；成对比较一侧无值或一侧多值；参数抽不到或多值而没有 `expand`。
- 绑定写法层面：顶层出现清单外字段；`op`、`threshold`、`agg`、`scope`、`require`、`match`、`combine`、`fault_when`、`extract` 规格、`filter` 子句不合法；D 类缺 `sub` 或顶层带 `filter`；`pair_from` 顶层带 `filter`；S 类带 `param_from`；`@` 引用未声明、顺序错误或没有 `param_from`；两个参数都带 `expand` 或展开值过多。
- 类别层面：已停用的 B/E/F/H/I，或未知类别。
- 模型声明：绑定自带 `decision`。
- S 类脚本：加载失败、入口缺失、抛异常、调用 `sys.exit`、返回格式不符。

**修改时要注意**

- 绑定写错一律明确拒收，不静默降级，这是文件注释反复记录的原则：`_shape_err` 的注释说实测里全角运算符、`agg` 拼错、`scope` 拼错、`require: 0`、字符串阈值都曾被旧版无声地算出方向相反的结论。新增参数时要同时加进 `_CLASS_KEYS` 白名单（否则会被当作清单外字段拒收），并在对应的参数检查函数里补校验。
- `_combine_tri` 的判断顺序决定数据不足的传播语义：AND 先看有没有假，再看有没有非 `ok`；调整顺序会改变「有假即假」与「有缺即缺」的优先级。
- `_subst` 必须保留对 tuple 和 set 的处理（模型写绑定时会把列表写成圆括号或花括号），`_NO_SUBST_KEYS` 决定哪些文案字段不被代入，新增文案字段要加进去。`_run_substituted` 传 `_resolved=True` 是为了让形如 `@xxx` 的参数值能按字面匹配，不能去掉。
- `excludes` 模式丢弃过滤列为空的行、`absent` 遇到列全空判数据不足、空表和 `filter` 命中 0 行不当作排除、`nan`/`inf` 当缺测，这几处都是针对实际出过的错误结论加的，改动前先看对应注释。
- `_hit_snippet` 的 120 字窗口和 `dist` 键截 80 字是为了防止整格回显（可达数 KB）让事实过大。
- `_load_py` 用 `_SCRIPT_CACHE` 按路径缓存模块，同一进程内改了脚本不会重新加载。
- `k_script` 里 `SystemExit` 必须与 `Exception` 分开捕获；只报 `scripts/` 下的栈帧是为了定位到脚本的出错行。
- 新增事实字段若要进入根因分析上下文，需同步修改 cs-scot-execute 的 `tree_prune.py` 中的 `_FACT_KEEP` 白名单，否则只进校验层。
- 改完必须跑 `kernel_selftest.py`。产物里的副本不会自动更新，要重跑 `assemble_scenario.py`（每次重跑都刷新公共脚本）或用 cs-scot-migrate 刷新工具链公共文件；`skills/cs-scot-migrate/engine/` 下的副本要运行 `cs-scot-migrate/scripts/sync_engine.py` 同步。

### scripts/custom_lib.py

**功能与作用**

S 类（代码脚本类）预制函数库，人工调优专用。算子绑定写成 `{"class": "S", "source": "<源>", "func": "<函数名>", "args": {…}}` 时，`condition_kernels.k_script` 在本文件的 `REGISTRY` 里按名字取函数并调用。每个函数统一签名 `fn(table, args, load)`：`table` 是绑定 `source` 那个源的表 `(header, rows)`，`args` 是绑定里的 `args` 原样传入，`load("<源名>")` 取其他源的表（该源没有提供时返回 `None`）。返回与内核算子同形的事实：`{"status": "ok", "predicate_satisfied": 布尔, …, "explain": "…"}` 或 `{"status": "insufficient_data", "reason": "…"}`。模块说明要求函数不打印、不写文件、不自己读 Excel。

目前注册了三个函数：`sum_extracts_vs_threshold`（两处取值相加比阈值）、`fault_check`（运行 datacom-fault-tree-tester 交付的 `fault_check_*.py` 的通用适配函数，cs-scot-migrate 迁入节点交付包时写的 S 类绑定就调用它）、`latest_by_seq_then_match`（先取最新一轮采集再匹配）。步骤 2 组装时本文件被复制进产物 `scripts/`。按格式约定，S 类绑定只能写在 `cross_analyzer.py`（调优表的`绑定:cross`列），因为跨源脚本运行时全部数据源都已加载，`load` 才取得到。

**实现介绍**

`register(name)` 是装饰器，把函数登记进 `REGISTRY`。`pick_values(table, spec)` 是公用工具，按取值规格 `{filter?, column, extract?}` 从表里抽出值列表，直接复用 `condition_kernels` 的 `_apply_filter`、`resolve_column`、`_is_null`、`_extract_val`，抽不到返回空列表。

`sum_extracts_vs_threshold(table, args, load)`：`args` 里 `a`、`b` 两个取值规格各抽第一个值（去掉逗号转浮点数）相加，与 `threshold` 按 `op`（默认 `>=`）比较；缺规格、抽不到、抽到的不是数、阈值缺失、`op` 不受支持都返回数据不足。事实带 `value`、`op`、`threshold`、`parts`、`explain`。

`fault_check(table, args, load)` 是文件的主体，按下面的顺序运行：

1. `_fc_locate_script`：`args.script` 必填，是 `scripts/` 下的相对路径，解析后必须仍在 `scripts/` 之下且存在。`entry` 缺省为 `diagnose`，`entry_kwargs` 必须是字典。
2. 在临时目录里 `_fc_prepare_data(load, tmp_root)` 备出交付脚本要读的数据根目录。优先取 `load("raw_dir")`：runner 以 `--data raw=<原始数据目录>` 传入的路径，由 `assemble_scenario.py` 生成的跨源脚本以 `raw_dir` 键放进数据源字典。`_fc_layout_from_raw` 把它整理成 `<根>/<网元>/CONFIG|CMD|LOG` 布局：结构已合格（有网元子目录，且 `DIAG` 与 `CMD` 同在或同缺）直接用原目录；平铺的单网元目录或只有 `DIAG`、`CMD` 之一时，拷贝到临时目录 `raw_mirror`，并把缺的那个目录名作为别名补上，原目录名全部保留。注释记录了原因：旧逻辑把 `DIAG` 改名成 `CMD`，认 `DIAG` 的脚本找不到自己的诊断文件，节点 n4 直接跑是真、经适配层整链跑却无法判断。`raw` 不可用时退到备用做法，从 `config`、`log`、`cmd`（或 `diag`）三张表重建目录：`_fc_rebuild_config` 每行取最深的非空 `levelN` 作一行并按深度缩进，`level1` 换块时补 `#`；`_fc_rebuild_cmd` 把连续同名命令的行拼回一段，每段前置 `<网元>命令` 提示行；`_fc_rebuild_log` 优先用 `Log` 列，缺它时用 `Time`、`Module`、`Level`、`Summary`、`Content` 拼一行。两路都不通时返回的原因必须说清 `raw` 的下落（未提供、路径不存在、层级错），注释说明以前静默转入重建后，操作者只能看到重建的错误，定位不到其实是 `raw` 没传对。临时目录写文件是本库明确的例外，用完即删。
3. `_fc_run_script` 载入并调用交付脚本：把脚本目录加入 `sys.path`（交付脚本会 `import` 同目录的共享模块）；调用前后各执行一次 `_fc_purge_shared`，清掉 `sys.modules` 里来自脚本目录的模块，保证每个脚本冷启动（注释记录：多个交付包共用同名模块时，前一个包载入的模块状态会传给后一个，n4 直接跑是真、整链跑变成假）；用 `sys.argv = [脚本路径]` 隔离命令行参数（命令行型脚本在库调用时仍会读取 `sys.argv`，把跨源脚本的 `-o` 路径当成自己的参数，实测路径被当作正则而出错）；标准输出和标准错误全部收进 `StringIO`，不进执行日志。入口函数不存在时返回数据不足并列出文件顶层的可调用名。`per_ne` 为真时 `_fc_per_ne_map` 对根目录下每个网元目录各调一次；否则传整个根目录，结果经 `_fc_normalize` 识别形状，若 `_fc_no_judgment` 发现没有任何网元给出判定（脚本没做多网元探测，在根目录下找不到 `LOG/` 就整体无法判断），逐网元重调一次，重调仍无判定就维持原判。传给入口的路径是 `_StrPath`：`str` 的子类，同时支持 `/` 拼接和 `exists`、`glob` 等 `Path` 操作，因为交付脚本两种写法都常见，注释记录按 `Path` 写的入口收到纯字符串曾直接报 `TypeError`。
4. `_fc_normalize` 识别四种返回形状：`details.devices` 包裹；`results` 信封；顶层直接带 `matched` 或 `error` 的全局结果（记为 `{"(整体)": result}`）；网元名直挂顶层。
5. `_fc_collect` 逐网元归类，核心是 `_fc_ne_state`：`_fc_tristate` 把 `matched` 映射成真、假、无法判断，字符串 `"true"`/`"false"` 显式映射（注释说明按真值判断时非空字符串恒真，会把 `"false"` 反转成真），`"undetermined"`、`"insufficient_data"` 和认不出的值都是无法判断；无法判断时 `_fc_gap_kind` 细分为 `no_data`（`insufficient_data`，缺数据，补采可解）和 `cannot_judge`（数据在但判不出）；`matched` 为假却带 `error` 的按无法判断处理（缺数据报成阴性的老问题）。命中网元的 `evidence` 经 `_fc_ev_label` 转成文字（新格式条目是 `{type, id, data: {source_files, matched_items}}`），`matched_items` 里的原文经 `_fc_item_str` 拼成 `k=v` 并附 `@文件:行`，至多 3 条进 `hits_raw`；脚本给的命中、未命中理由分别收进 `hit_why`、`miss_why`。
6. 结果汇总：全部网元都无法判断时 `_fc_all_unjudged` 返回数据不足，`gap_kind` 按细分集合归为 `no_data`、`cannot_judge` 或 `mixed`；否则 `predicate_satisfied` 为「至少一个网元命中」，事实带 `matched_nes`、`checked_nes`、`data_via`、`script`、`evidence`（前 8 条）、`explain`（`_fc_note` 附未判网元清单和脚本说明）、`hits`。注释里写明责任边界：脚本内部的判断逻辑原样接受，对错归脚本；适配层只在一个网元都没备出来时报数据不足。

`latest_by_seq_then_match(table, args, load)`：可选 `filter` 先选行，按 `seq_column`（默认 `CmdSeq`）取最大值那一轮的行，再在 `column` 里找 `contains` 子串；事实带 `matched`、`total`、`seq`、`hits`、`explain`。

**修改时要注意**

- 新函数必须用 `@register` 登记，不得打印、写文件或自己读 Excel；临时目录是 `fault_check` 专有的例外。
- `_fc_tristate` 的字符串映射、`_fc_purge_shared` 的前后清理、`sys.argv` 隔离、`DIAG`/`CMD` 只补别名不改名、`raw` 失败原因的完整说明，都对应注释里记录的具体问题，去掉任何一处都会重现。
- `_fc_normalize` 与 cs-scot-migrate 的 `migrate_fault_check.py` 用同一套形状规则校验交付脚本的返回值，改形状识别要两边同步。
- cs-scot-migrate 刷新工具链公共文件时会识别产物里 `custom_lib.py` 的手工注册函数：能力齐全时不整体覆盖，否则覆盖前备份为 `.bak` 并提示拷回；在产物副本里手写的函数要自行保管。

### scripts/mo_config.py

**功能与作用**

MO 配置文本的适配器。接入类场景的配置常是 `MO类型:键=值,键=值;` 每实例一行的文本，不是表；本模块把它解析成与 csv、xlsx 同形的 `(header, rows)`，让阈值比较、字段匹配等算子直接可用，并提供 custom 函数常用的几个原语（IP 网段判断、多值拆分、键值提取、MO 间追溯、从回显抽数值）。纯标准库。调用方：`condition_kernels.load_table` 和 `list_sheets` 在数据文件后缀为 `.txt`、`.cfg`、`.mo`、`.moc` 时按需 `import` 它；`analyzer.py` 做概览时同样按需 `import`；步骤 2 组装时复制进产物 `scripts/`。

**实现介绍**

解析部分：`_split_top(s, sep)` 按分隔符切分但不切花括号、方括号里的内容（值里可能是 `{k=v,k=v}`）。`parse_mo_lines(text)` 逐行按 `;` 切块，跳过空块、`#` 开头的注释和没有冒号的块，`MO类型:其余` 里的 `键=值` 按逗号切，返回 `[(MO类型, {键: 值})]`。`parse_mo_text(text_or_path, sheet=None)` 把它组成表：`header` 是 `MO` 加按首次出现顺序排列的全部键，每行补齐缺列为空串；`sheet` 给 MO 类型时只取该类的行，避免不同 MO 的同名列互相污染。`mo_sheets` 返回 MO 类型清单，供 `list_sheets` 把 MO 类型当作页签。`parse_mo_grouped` 返回 `{MO类型: [实例字典]}`。`_read` 对单行且路径存在的参数读文件，否则当作文本。

原语部分：`split_multi(value, seps=";|,")` 拆多值并去重；`extract_kv(blob, key)` 从 `{k=v,...}` 引用串里取某键的全部值；`extract_metric(text, pattern, group=1)` 按正则（忽略大小写）抽出全部命中的浮点数并去掉千分位逗号，用于「标签恒在、数值才是信号」的回显计数器（注释说明 G 类子串匹配对这类判据必然假阳，值为 0 的回显也含 `CRC:`）；`ip_to_int`、`mask_to_cidr`、`ipv4_in_subnet(ip, dst_ip, mask_or_cidr)` 做 IPv4 网段判断，掩码可传点分或 CIDR 整数，非法输入返回 `None` 或 `False`；`group_rows(table)` 把扁平表按 `MO` 列重组并去掉空值键；`mo_find(grouped, motype, **eq)` 按字段等值找实例；`mo_get(grouped, motype, match_field, match_val, get_field)` 取匹配实例的某字段值列表，跨 MO 追溯就是把它串起来；`find_header_row(rows, must_have)` 处理表头不在首行的核查表。`fact_ok` 与 `fact_insufficient` 构造带 `"mode": "custom"` 的事实。

`__main__` 自测用脱敏样例验证解析成表、按 MO 分组、花括号完整、链式追溯到对端 IP、网段判断、掩码转换、多值拆分，全过退出 0，否则 1。

**修改时要注意**

- `load_table` 调用 `parse_mo_text` 时把异常吞掉并改按 csv 读，所以解析规则改坏了不会以报错形式暴露，要靠本文件的自测发现。
- `_split_top` 只识别 `{[` 与 `}]` 两组括号，深度不会降到负数；引号内的分隔符不做保护。
- `ipv4_in_subnet` 对非法 IP 返回 `False`，调用方无法由返回值区分「不在网段」和「输入无效」。

### scripts/kernel_selftest.py

**功能与作用**

内核算子的回归测试。文件说明写明了它存在的原因：`condition_kernels.py` 是整条流水线的基础，而此前没有任何回归测试，改坏一行不会有任何报警。用法 `python kernel_selftest.py`，逐条打印结果，全部通过退出 0，有失败退出 1；也可 `from kernel_selftest import run_battery` 被其他脚本调用。用例是确定性的：用合成小表，不调模型，不需要真实数据。`cs-scot-generate-optimize/scripts/package_skill.py` 把它列入随技能打包的文件。

**实现介绍**

`CASES` 列表共 103 条用例，每条是一个字典：`name` 用例名；`table` 单源表（`None` 表示没有数据源）或 `sources`（多源用例，`{源名: 表}`）；`binding` 算子绑定；`expect` 期望字段及其值（浮点数按 1e-9 容差比较；值为 `None` 表示该字段不应出现，例如单目标时不输出 `matched_by_target`）；`present` 必须出现的字段；`reason_has` 是 `reason` 里必须含的子串。`_t(header, *rows)` 把列名和若干行组成 `(header, [字典])` 形态的表。

`_check(case)` 组出数据源字典（单源时键为 `_t`），以 `binding.source` 或 `_t` 为源名调用 `evaluate_binding`，算子抛异常直接记为失败，再逐项比对 `expect`、`present`、`reason_has`，返回错误列表与事实。`run_battery()` 跑全量，返回 `(通过数, 总数, 文本行)`；`main()` 打印并给出退出码。

用例按文件注释分组覆盖：A 类聚合与计数、百分号、`require`、`log10` 变换、缺列、全空；C 类命中与分量字段；C/G 负存在 `absent`（含列全空判数据不足、与 `require` 互斥、配了 A 没配 B 的组合）；D 类 AND/OR、数据不足传播、`group_by` 的同组同现、跨组各现、组内负存在、分组列全空、跨源子绑定拒收；G 类告警码、多目标分解、空目标拒收；正则匹配（大小写敏感、坏正则拒收、`match` 取值不合法）；`extract` 的键值与正则取值、千分位、抽不到计空值、规格错误；P 类不一致成立、一致不成立、对端缺失、一侧多值、`fault_when` 取值不合法、加 `extract`、加 `filter`；`filter` 的 `excludes`、子句冲突；`param_from` 单跳、多跳、抽不到、多值歧义、未声明引用、无 `param_from` 的 `@`、引用顺序、`expand` 的 `all`/`any`、取值不合法、两个 `expand`；命中原文 `hits`；评审整改回归（tuple/set 写法、规格键拼错、`excludes` 空行、子绑定作用域、字面 `@` 值、`pair_from` 顶层 `filter`、源名误写 `cross`）；顶层白名单（`require` 忘配 `count_rows`、A 类误写 `contains`、`combine` 拼错、P 类清单外字段）；`pair_from` 两文件比较与对端源缺失；`cross_source` 标记漏到运行时；J 类与 `decision`；停用类别 F、H 与未知类别 Z；空表、`filter` 选行、命中 0 行、子句缺模式、子句非对象、D 顶层 `filter`；参数形状守卫（空 `sub`、全角运算符、`op`、缺 `op`、字符串阈值、`agg`、`scope`、`require: 0`、`nan`/`inf`）。现有用例不含 S 类。

**修改时要注意**

- 改 `condition_kernels.py` 之后必须跑它；新增算子参数、守卫或事实字段时同步补用例，拒收类用例要带 `reason_has` 锁定提示语的关键词，否则提示语改了也测不出。
- `expect` 里用 `None` 断言字段不存在依赖 `fact.get` 的语义，字段值本身为 `None` 的情形与字段缺失无法区分。
- 保持用例确定性，不要加依赖真实数据文件或外部脚本的用例；S 类涉及文件系统和 `custom_lib.py`，目前没有用例，补充时要自带临时脚本。

### scripts/validate_conditions.py

**功能与作用**

步骤 3 的第一道检查：读分析脚本输出的事实文件（结构为 `{"indicator", "source", "summary", "conditions": [...]}`，每条 `conditions` 元素带绑定元信息和 `fact`），在事实进入根因分析之前机械地查出「静默编造」。用法 `python validate_conditions.py <事实文件>`。退出码：发现硬问题返回 1；缺参数或文件读不了返回 2；通过返回 0。硬问题之外还打印若干软告警，只提示不拦。

**实现介绍**

`main()` 读文件后对每条事实调用 `_condition_problems`，它把两组检查合在一起。

字面层 `_fact_problems`：类别缺失（没有 `class`）；J 类越权（J 类 `status` 不是 `manual`）；无中生有，有两种情形，一是 `status` 为 `insufficient_data` 却给了 `predicate_satisfied`，二是 `status` 为 `ok` 且给了判断，但 `resolved_column`、`sub_facts`、`group_by` 三个「解析到了什么」的痕迹字段一个都没有；缺理由（数据不足却没有 `reason`）；越权判定（`predicate_satisfied` 不是布尔）。

代理层 `_proxy_problems`，只针对 `status` 为 `ok` 且绑到率列（列名含 `rate` 或「率」，`_is_rate_col`）的事实：`_counter_proc` 从条件正文里的 `N.<流程>.`、`L.<流程>.` 取流程段并经 `_PROC_ALIAS` 规范化，`_col_proc` 从列名里找 `_KNOWN_PROCS` 的流程词，两者都能识别且不同记「流程不匹配」；`_is_setup_cond`（建立类条件）绑到 `_is_drop_col`（掉线、异常释放列）记「建立配掉线」；`_is_subcause`（计数器含 `.FailEst.` 或 `.Fail<原因>`）绑到总率列记「子因配总率」。另有「丢了区分量」：条件正文提到 rsrp、sinr、rsrq、rssi，绑定却是单一 A 或 C 类、列名不含这些词且没有 `sub`。文件注释写明代理层只查「任何语义下都错」的三类，不判语义是否相等，那是模型的事，写在 `mapping_reason` 里供审核。

软告警有三组，都从事实本身计算，不写死任何列名或取值：

- `_proxy_warnings` 对 `ok` 事实做三路采集（`_proxy_collect`）：命中占比 `ratio` 不低于 0.9 且有命中的记「恒成立」；同一（列，`filter`，取值集）挂到不少于 2 个 `node_id` 的记「一值多挂」；同一（列，`filter`，`op`，阈值）出现不少于 5 次的记「共用阈值」。聚合键带 `filter` 的原因在注释里：窄表同列不同指标子集是不同的量，不带会把正常绑法误报。
- `_target_warnings` 只看 `status` 为 `ok`、`match` 为 `contains` 的 C/G 事实（`_contains_fact`），给四种提示：`_short_target_warning`（`_SHORT_ASCII`，1 到 3 个字符的 ASCII 目标值，如 `NP` 是 `snmp` 的子串）；`_cmd_signal_warning`（列名含 `cmd` 或 `command`，目标值以 `display`、`show` 开头，注释记录实测一次 19 条全部假阳）；`_polarity_warning`（判据含「缺失」「未配置」等字样，绑定及其 `sub_facts` 都没有 `absent`）；`_neg_trend_warning`（判据含 `_NEG_TREND` 里的否定或趋势词，目标值又没有 `_ALARM_SHAPE` 的告警码形状）。事实带 `matched_by_target` 时把各目标的命中分解附在提示后。
- `_coverage_warnings` 做覆盖度对账：`_coverage_by_col` 按列汇总 `ok` 的 C/G 事实（带 `filter` 的跳过，因为同列各看不同行子集，取值分布不可合并），记认领集 `(match, targets)`、`top` 取值分布和 `total`；`_uncovered_values` 列出列里出现过却没被任何绑定目标值认领的取值，占比不低于一半的取值视为正常值跳过。

输出顺序：先打印 `contains` 质量告警和覆盖度告警；有硬问题则列出问题清单和修法并返回 1；没有硬问题再打印代理软告警（`_print_proxy_warnings`）和通过摘要（`summary` 里的总数、`ok`、数据不足、人工数）。

**修改时要注意**

- 无中生有的判定依赖 `resolved_column`、`sub_facts`、`group_by` 三个痕迹字段；新增算子若事实不带其中任何一个，`ok` 事实会被误报，需同步扩充 `_prov`。
- 「共用阈值」只数有真实 `op` 和阈值的数值绑定；注释记录旧版按（列，`op`，阈值）把 `op`、阈值都为 `None` 的 C 类算成共用一列而误伤，多条 C 类绑不同取值到同一列是正常的逐码区分。
- `equals` 精确匹配不检查超短目标值，状态枚举列的短值（`UP`、`Down`）本身携带极性，对它们报噪声是错的。
- `_ALARM_SHAPE` 的全大写分支含 `\-`，`ALM-26106` 这类连字符码必须豁免，改正则时不要去掉。
- 带 `filter` 的事实不参与覆盖度对账，且聚合键要带 `filter`，改采集逻辑时保持。

### scripts/check_tree_coverage.py

**功能与作用**

步骤 3 的第三道检查，树驱动的叶子覆盖度检查。文件说明解释了与 `validate_conditions.py` 的分工：后者的覆盖检查是取值级的，只能看出已绑的列里哪些值没被认领；一整支故障的叶子没人绑、它的判别码这次数据里又没出现时，取值级检查看不见。本检查从树索引枚举全部需要绑定的诊断节点，对照各分析脚本的 `CONDITIONS` 和事实文件里出现过的 `node_id`，把没有任何绑定认领的节点全部列出来，与数据取值无关。

用法：`check_tree_coverage.py <tree_index.json> --analyzer <ind>_analyzer.py ... --facts <ind>_facts.json ... [--columns 列名,...]`，至少给一个 `--analyzer` 或 `--facts`。退出码：0 无漏绑；1 有漏绑（条件正文提到已提供的列却没绑）；2 无法判定（树里没有带条件节点，或参数缺失、文件读不了）。

**实现介绍**

`main()` 的处理顺序：

1. 读树索引取 `nodes`；没有 `nodes`，或没给任何 `--analyzer`/`--facts`，返回 2。
2. `_warn_cross_not_passed`：按产物布局推出 `scripts/cross_analyzer.py`，它存在、带绑定单元却没传给本检查时提示补传，注释记录漏传会让已绑数虚低、整支未触达虚高，实测误报过。
3. `_collect_claims` 建认领表 `{源标签: {node_id: 绑定单元数}}`。`_claims_from_analyzer` 用 `ast` 静态解析脚本里 `CONDITIONS` 的字面量列表：普通绑定记 1 个单元，D 类按 `sub` 条数记（`_dict_unit`），这样能看出「一个节点的判据有多个子条件却只绑了一条」；`CONDITIONS` 不是字面量列表时退到 `_units_from_walk`，遍历所有字典按 `node_id` 计次。`_claims_from_facts` 从事实文件按 `sub` 或 `fact.sub_facts` 条数记。`_renorm` 把以 `code` 写的 `node_id` 归一回短 id。合并后得到 `claimed` 全集。
4. `_resolve_columns`：`--columns` 显式给了以给的为准；否则按每个 `--analyzer` 的路径推出 `../overview/<指标>_overview.json`，读 `summary.columns` 自动取列名（`cross` 没有概览，跳过）。注释说明这是为了不让「本源可答」的触发条件依赖模型手抄，传空或传错会让检查永远不会失败。
5. `_pick_targets`：诊断节点是带非空 `condition` 且非 `pass_through` 的节点（`_diagnostic`）；优先取其中没有 `children` 的叶子，树把判据挂在中间节点时退而用全部带条件节点；一个都没有返回 2 并提醒核对建树产物。
6. `_load_cross_ids` 读 `dispatch/cross_candidates.json` 里的 `node_id`，用来给未绑节点加「跨源候选中」注记，把仍在跨源补绑流程中的节点与真漏分开。
7. `_classify` 逐个归类：在 `claimed` 里为已绑；未绑且条件正文按词边界提到已提供的某个列名（`_mentions`）为漏绑；否则祖先链（`_ancestors`，防环）上有节点被绑为「分支已入未细绑」；否则为「整支未触达」。
8. 打印：总览行（需绑数、已绑数、三类未绑数）；候选中的未绑数提示（中途跑会虚高，应先完成跨源补绑再跑）；`_report_missing` 漏绑清单（节点、层级、是否分支已入、数据来源摘要 `_ds`、判据摘要 `_snip`）；`_report_branches` 把整支未触达按「未触达区域的入口」聚合，入口取路径上未绑且非根的最高祖先，最多列 40 个子树；`_report_leaf_only` 分支已入的计数；`_report_claims` 跨源认领对账，包括各源认领分布、`_report_under`（判据用 `_CLAUSE_SPLIT` 按换行、分号、带圈数字、「且」「并且」「以及」切出不少于 2 段，全部源加起来却只有 1 个绑定单元）、`_report_lonely`（`_ALARMISH` 识别的告警类判据在多源在场时只有单源认领）。
9. 返回值只由漏绑决定：有漏绑返回 1，缺源与分支级未细绑不阻断。

**修改时要注意**

- `_mentions` 必须按词边界匹配，注释记录不按词边界时 `Cause` 会命中 `DCInnerCause` 内部造成假漏绑。
- `_CLAUSE_SPLIT` 特意不切「或」：「A 或 B」常是同列多取值，一条 `equals`/`contains` 列表就是正确绑法，切了会误报。
- `--columns` 只应填本次已提供源的列名，填入没提供的源的列会把缺源误判成漏绑。
- `decision: insufficient_data` 的绑定也算认领（它带 `node_id`）；本检查是节点级的，一个节点第 2、3 条判据有没有漏只能靠 `_report_under` 的启发式提示。
- 本检查按产物目录布局推 `cross_analyzer.py`、`overview/`、`dispatch/` 的位置（树索引所在目录的上一级），产物布局改动要同步这里。

### scripts/import_tree_excel.py

**功能与作用**

调优表导入：把 `tree/<短名>.tuning.xlsx` 编译回场景产物，更新树索引、故障树 Markdown、`solutions.json`、各 `<指标>_analyzer.py` 与 `cross_analyzer.py` 的 `CONDITIONS`、`plan.json`、`manifest.json`。全程零模型参与，确定性、可重复。用法 `import_tree_excel.py --out <产物目录或场景技能的 assets/>`。SKILL.md 步骤 6 由人工调优后运行；cs-scot-migrate 迁入节点交付包时也调用它（先导入现有调优表，再导出、改表、导入编译）。文件随产物分发，只依赖同目录 `xlsx_reader.py`，不依赖 generate 技能的其他模块。退出码：校验错误汇总后返回 2 且不改动任何文件；定位产物、找页签、表头缺失等致命错误用带消息的 `SystemExit` 退出（退出码 1）；成功返回 0 并打印变更摘要。

**实现介绍**

处理分成「定位与读表、全量校验、备料、统一写入」几段，文件说明强调先全量校验、全量备料，最后统一写入，写入阶段不再有任何可失败的计算。

1. `_locate_product(out)`：`tree/` 下必须恰有一份调优表（跳过 Excel 打开时的 `~$` 锁文件）和一份树索引；读入旧索引、旧 `solutions.json`；收集 `scripts/` 下现有的 `*_analyzer.py`（排除 `cross_analyzer.py`，它的脚本框架形态不同）作为新增数据源时的克隆模板，一个都没有则退出。
2. `_read_tuning_table`：`_pick_sheet` 通过 `xlsx_reader.sheet_briefs` 按表头找同时含 `node_id` 和层级列的页签，必须恰一页（业务同事复制页签留底是常态，不能取活动页）；`xlsx_reader.read_rows` 读整页，格值统一转字符串并去首尾空白。
3. `_parse_header`：表头的全角冒号归一为半角；固定列按 `_FIXED_COLS` 识别（`node_id`、`Judgment Condition(English)`、`Recommend Solution(Chinese)`、`Recommend Solution(English)`、`meta(勿改)`）；`L<n>(Chinese)` 或 `L<n>` 是层级列，`L<n>(English)` 是英文层级列；`判据:<标签>`列中标签为「原文」的单独记为原文列（`_parse_cond_header`）；`绑定:<源名>`列的源名只许字母、数字、下划线（`_parse_bind_header`）；重复列、非法源名、像判据或绑定列但格式不对的表头都记错。缺 `node_id` 列或层级列直接退出。
4. `_check_deleted_columns`：旧产物有的判据标签、解决方案、`meta`、英文名、英文判据，表里必须仍有对应列，否则记错；注释标注这是评审 M2 的「删列即清数据」防护。
5. `_parse_rows` 逐行解析（Excel 行号为下标加 2）：填了值的层级列里最深一层是本节点名；`_row_parent` 核对上级各层名字与上文栈 `stack`，对不上记错；`_check_node_id` 要求 `node_id` 格式为 `n` 加数字、不重复、必须在旧树里存在，手编一个树里从没有过的 id 直接拒绝（评审 M1，防静默断开外部引用），新增行留空并以 `__new__<行下标>` 为临时键；`_row_condition` 取判据，`判据:原文`列优先且逐字保真，否则把各标签列按旧 `data_source` 顺序拼成 `[标签] 文本`（评审 M6，防往返抖动），两种都填记错；`meta` 列解析 JSON；`_row_node` 组节点记录，`code` 从旧树按 `node_id` 继承，`combine` 由 `_infer_combine` 从判据文字推断 AND/OR，`pass_through` 在无判据时为真、既有节点保留原值、新增节点只豁免根，英文名取本行所在层的英文格，英文判据随中文判据走（中文清空则英文清空）；解决方案中英文；`_row_bindings` 解析各绑定格，`_parse_cell_json` 用 `json.loads` 并拒收 NaN、Infinity，中文引号自动替换后能解析的算通过但提醒；`_check_binding` 校验每条绑定：须是对象，`decision` 只接受 `_DECISIONS` 三种，`class` 须在 `_CLASSES` 内（含已停用字母以兼容老产物，加 P 与 S），S 类只许写在`绑定:cross`列且 `func`、`file` 二选一，`绑定:cross`列的非 S 绑定必须带真实源名的 `source`。
6. 根节点必须恰一个。有任何错误则把全部错误按行号打印到标准错误并以 2 退出，不改动任何文件。
7. `_assign_new_ids`：新 id 从表内与旧索引的最大编号加 1 起分配，历史编号不复用；把临时键换成正式 id，组出新索引（保留 `scenario`、`slug`）。
8. `_prepare_analyzers`：指标列表是有绑定的非 `cross` 源，按表列顺序；每条绑定补默认的 `id`（`<源>-<node_id>`）、`node`、`node_id`、`condition_text`、`source`，绑定自带的同名字段覆盖默认值；现有脚本用 `_replace_conditions` 以 `ast` 定位 `CONDITIONS` 赋值块整体替换，替换后再用 `ast` 自检语法；有`绑定:cross`列但产物没有 `cross_analyzer.py` 时退出；新增源克隆第一份现有脚本并替换 `INDICATOR = ` 行。
9. `_prepare_plan`：重建执行计划的节点链，各指标依次相连，产物有 `cross_analyzer.py` 时接 `cross` 节点，最后接 `rca`；`rca` 的 `inputs` 按指标重写；`entry` 取第一个指标；`manifest.json` 的 `indicators` 同步。注释标注评审 M3：绑定全部清空也要重写，防执行计划与 `manifest.json` 不一致。
10. `_node_changes` 与 `_content_changes` 算变更摘要（新增、删除、改名、移动、判据改动、英文改动、解决方案改动）；`_write_outputs` 统一写入（树索引、由 `_index_to_markdown` 生成的 Markdown、`solutions.json` 有则写无则删、各脚本、执行计划、`manifest.json`）；`_print_summary` 打印提醒与摘要；`_check_cross_drift` 在调优表没有`绑定:cross`列时，核对判据改动或删除是否波及 `cross_analyzer.py` 里认领的节点并点名。

**修改时要注意**

- 保持「校验、备料、写入」的分段：任何新校验都要放在写入之前，写入阶段不能新增可失败的计算，否则会出现只写了一半的产物。
- `node_id` 只读不改的三条规则（格式、不重复、必须已存在）和历史编号不复用，都对应评审记录，不要放松。
- `pass_through` 对既有节点保留原值，注释说明调优表的列号是相对深度，语义与建树时的母表层级不同，按列号重算会误伤。
- 默认字段的补齐规则（`id`、`source`、`condition_text`）与 `export_tree_excel.py` 的省略规则互为逆操作，两边必须同步，否则调优表往返不保真。
- `_CLASSES` 含 A 到 J 的全部字母是为了老产物兼容，不代表这些类别可用；运行时仍由内核算子拒收已停用类别。
- 本文件随产物分发，除 `xlsx_reader.py` 外不要引入对 generate 其他脚本的依赖。

### scripts/export_tree_excel.py

**功能与作用**

调优表导出：把场景产物的树、判据、各源算子绑定和解决方案汇成单页签的 `tree/<短名>.tuning.xlsx`，一行一个节点，供业务同事编辑；改完由 `import_tree_excel.py` 编译回产物。用法 `export_tree_excel.py --out <产物目录>`。调用方：`assemble_scenario.py` 组装后自动导出；`merge_conditions.py` 每次合并绑定后刷新；`package_scenario.py` 打包时 `from export_tree_excel import export` 刷新；cs-scot-migrate 迁入后回导同步；也可手动运行。写 xlsx 依赖 openpyxl，读表走同目录 `xlsx_reader.py`。

**实现介绍**

`export(out_dir)` 的处理顺序：

1. `_read_index`：`tree/` 下恰一份树索引且有根，取短名、`nodes`、`root`；读 `solutions.json`（可选）。
2. `_collect_bindings`：遍历 `scripts/*_analyzer.py`，源名取文件名去掉 `_analyzer.py`；`read_conditions` 用 `ast.literal_eval` 读出 `CONDITIONS` 字面量；建 `node_id` 别名表，把老产物以 `code` 写的 `node_id` 解析回 `nN`；解析不到节点的绑定收进 `dropped`，在标准错误里逐条提示该绑定指向的节点在树里已不存在、请先修产物，不无声丢弃；`_binding_cell` 去掉 `node`、`node_id`，与默认值一致的 `id`（`<源>-<nN>`）、`source`、`condition_text` 省略，不一致的保留（自定义 id、页签路由、逐字子句往返不丢）；`cross` 源放到最后一列。
3. `_dfs_order`：深度优先、父先子后排出行序，并由 `parent` 链推出相对深度（根为 1）。注释记录不用节点存的层级号的原因：多页签合并的树层级号可能内部不自洽（父子同号、兄弟异号），按它摆列会同列互相覆盖，导入按列重建父链必然失败，实测零改动纯往返曾报 73 处「上级和上文对不上」和 83 个根。
4. `_condition_cells`：节点的 `condition` 能由 `data_source` 逐字拼回 `[标签] 值` 的按标签拆到各`判据:<标签>`列；拼不回的（老格式 v1 树）整段进`判据:原文`列，不丢判据。
5. `_build_header` 定列序：`node_id`、`L<n>(Chinese)`、树里有英文名时 `L<n>(English)`、判据列、有英文判据时 `Judgment Condition(English)`、`绑定:<源>`、`Recommend Solution(Chinese)`、`Recommend Solution(English)`、`meta(勿改)`。`_column_layout` 把表头名映射成列号。
6. `_node_row` 生成一行：祖先各层中文名逐行照抄、英文名、英文判据、判据、各源绑定（单条为 JSON 对象，多条为 JSON 数组）、解决方案、`meta`。
7. `_new_workbook` 建单页签「树」并加粗表头；`_style_columns` 按列类型设列宽（`_col_width`），所有格顶端对齐、自动换行。
8. `_save_with_backup`：目标调优表已存在且修改时间比树索引新（可能有尚未导入的人工修改）时先备份为 `.xlsx.bak` 再覆盖并提示；保存后用 `os.utime` 把表的修改时间对齐树索引，注释说明这是为了消除迁移、打包每次都触发的「表比索引新」假警报，此后人工真改过表，时间变新，检测照常生效。
9. 打印跨源绑定涉及的节点数，提醒每条必须带真实源名的 `source`。

**修改时要注意**

- 列位必须用相对深度，不能改回节点存的层级号。
- `_save_with_backup` 的时间对齐若去掉，迁移和打包流程会每次误报并产生多余的 `.bak`。
- 默认字段省略规则与 `import_tree_excel.py` 的补齐规则必须一致；列名常量 `META_COL`、`RAW_TAG`、`COND_EN_COL` 在导入脚本和 cs-scot-migrate 的 `migrate_fault_check.py` 里各有一份，改名要三处同步。
- openpyxl 只在写 xlsx 时需要；缺它时组装与合并只提示不阻断（SKILL.md 步骤 6），不要在模块顶层 `import` 它。
- 指向不存在节点的绑定只提示不导出，之后导入时这些绑定会丢失，提示信息不能去掉。

### SKILL.md

技能的操作手册，给模型执行用，也是开发者了解全流程的入口：定义术语（场景、判据、绑定、判断类别、`node_id`、短名等），列出执行规矩，按步骤 1 到步骤 6 给出每步的命令、完成标志与注意事项，画出产物目录结构，规定最终汇报必须附技能目录路径和当场查文件得到的数字。frontmatter 的 `description` 决定技能何时被触发。涉及本目录全部脚本。

### knowledge.md

技能级出厂知识库，跨场景积累，与 SKILL.md 平级随技能分发。每条知识以 `### 标题` 开头，正文前可写 `阶段:绑定` 或 `阶段:诊断` 与 `适用:标签` 两行。`assemble_scenario.py` 首次组装时把它复制为产物 `tree/knowledge.md`；`make_dispatch.py` 的 `parse_knowledge`、`match_knowledge` 按标签把条目载入任务说明；根因分析也按同一格式读产物级那份。

### references/binding_dispatch.md

步骤 2.5 的操作细则：为什么并行、为什么由脚本生成任务说明，分派、生成任务说明、派发、合并、跨源补绑各环节的规矩与失败处理。内容与 `make_dispatch.py`、`merge_conditions.py` 一一对应，模型在派发或处理缺批、坏批时读，开发者修改这两个脚本前也应先读。

### references/binding_rules.md

说明怎样算把一条判据绑对：一条算子绑定的五个要素、逐条模型判断而禁止脚本批量生成、按树遍历全覆盖、多源多子条件都要绑、不用替代指标、先确认数据写法，以及步骤 3 校验警告「必须处理」与「记录即可」两级的处理方式和步骤 3.5 假阳性检验。与 `validate_conditions.py`、`check_tree_coverage.py` 配合使用。

### references/condition_binding_schema.md

算子绑定的格式约定，也是产物 `scripts/condition_binding_schema.md` 副本的来源：公共字段、A/C/G/D/J/P 各类参数与产出的事实、`filter` 与 `extract`、`param_from` 传参链与 `pair_from`、`绑定:cross` 列与 S 类写法、`contains` 的三条规则、极性、变换词汇、列解析、EAV 窄表与多页签。`condition_kernels.py` 按它实现，绑定子代理与人工调优都按它填写。

### references/datacom_log_adapter.md

数通「诊断文件包」场景的接入说明：7 类 CSV（cmd、diag、config、dblg、log、opr、bas）的形态与对应的内核算子、cmd 回显里存在类与计数器类判据的不同绑法、EAV 窄表的 `filter` 绑法与抽枚举命令、日志类绑 `Summary` 列的要点、常见问题速查。绑定子代理与开发者读，关联 `excel_to_tree_v2.py`、`condition_kernels.py`、`mo_config.py`。

### references/plan_schema.md

执行计划 `plan.json` 的格式说明，本项目只用 SCoT v4.0 的子集：顶层字段、`script` 与 `agent_skill` 两种节点、`cross_analyzer` 节点的 `$input.*` 写法、`$input`、`$memory`、`$step`、`$output` 四个引用命名空间。由 `assemble_scenario.py` 的 `build_plan` 产出，cs-scot-execute 的 `runner.py` 消费。文末列出 SCoT 里暂未用到的节点类型与字段，并说明本项目是直线诊断，暂不实现它们。
