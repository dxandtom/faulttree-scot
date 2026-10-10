# cs-scot-generate-optimize 代码说明

## 技能概述

cs-scot-generate-optimize 是生成与优化两类工作的统一入口，对应 `docs/design.md` 第 2 节分工表里的「入口」一行和第 3 节流程图的第 ⑦ 步。它本身不实现任何一步处理：模型读 `SKILL.md` 里的判定表，按输入文件的客观特征（有没有层级列、zip 列、node_id 列，有没有判断方法文档，有没有 `meta.verdict` 等字段）识别输入属于哪一种，再按对应的处理流程执行。`SKILL.md` 把四条处理流程称为「线」：生成线交给 cs-scot-generate 的手册，优化线交给 cs-scot-migrate 的手册，增删节点线用场景产物自带的调优表导出、导入脚本完成，反馈处理线则由本目录的 `scripts/route_feedback.py` 为评估结果里的每条问题给出处理方式。本目录只有两个脚本：`route_feedback.py` 服务反馈处理线；`package_skill.py` 在仓库开发态把入口、两条线的技能和公共素材目录合成一个自包含的发行包，发行包里不含它。

输入方面，生成线收故障树母表 xlsx 和各指标数据，优化线收节点交付包、合集包或对应表，增删节点线收用户明确的增删要求，反馈处理线收 cs-scot-reflect 产出的评估报告 `evaluation_report.json` 或反馈文件 `feedback_to_generate.json`；除生成线外，其余三条线都需要一个既有的场景产物目录。输出方面，前三条线的产物由各自的手册和脚本负责；反馈处理线产出处理清单 `feedback_tickets.json`（每条问题一个处理项，分自动档与人工档），处理项执行完后把记录追加到产物的 `tree/feedback_log.json`；发行打包产出 `dist/cs-scot-generate-optimize/` 目录、同名 zip 和包内的 `MANIFEST.txt`。

反馈处理线上各文件的配合关系如下。自动档的处理动作由模型按处理项调用既有工具完成，本目录的脚本不改任何资产：

```
cs-scot-reflect/scripts/eval_run.py
    │ 写 evaluation_report.json、feedback_to_generate.json
    ▼
scripts/route_feedback.py --feedback <评估输出> --product <产物目录>
    │ 读 产物/tree/*.tree_index.json（树索引）
    │ 读 产物/tree/migrations.json（迁移记录）
    │ 查 产物/scripts/*.py（定位绑定所在的脚本）
    ▼
feedback_tickets.json（处理清单）
    ├─ 自动档：模型调用 migrate、调优表导出导入、case_gen 执行
    └─ 人工档：汇总交人
    │
    ▼
scripts/route_feedback.py --record <处理清单> --done all --product <产物目录>
    │ 追加 产物/tree/feedback_log.json
    ▼
cs-scot-reflect/scripts/regress.py 全库回归，进入下一轮评估
```

发行打包与上面的流程无关，只在开发时运行：`package_skill.py` 从仓库 `skills/` 下读七个技能目录，按 `LAYOUT` 复制到输出目录，改写两份线手册副本的开头信息，核验关键文件清单，写 `MANIFEST.txt`，最后打 zip。

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 入口的判定表、四条处理流程的入参与手册位置、反馈处理线和发行打包的操作步骤 |
| `references/feedback_routing.md` | 参考文档 | 反馈处理线每类问题的处理方式对照表，以及评估报告 `verdict` 各取值的处理 |
| `scripts/route_feedback.py` | 脚本 | 读评估输出，为每条问题判定原因分支并给出处理项，写处理清单；处理项执行后记录到产物的 `tree/feedback_log.json` |
| `scripts/package_skill.py` | 脚本 | 开发态打发行包：把入口、两条线的技能和公共素材目录合成自包含目录与 zip，逐项核验并写清单 |

## 各文件说明

### scripts/route_feedback.py

**功能与作用**

反馈处理线的第一个动作。它读 cs-scot-reflect 的评估输出，对其中每条没通过的问题判定真因分支（问题的真正原因属于哪一种情形），给出「用哪个工具、做什么」的处理项，分成自动档（优化循环内由模型直接按处理项执行）和人工档（自动化做不了的情况，汇总交人），把全部处理项写成处理清单 `feedback_tickets.json`。它只给出处理方式，不执行：真正改资产的是 cs-scot-migrate、调优表导出导入脚本、cs-scot-reflect 的 `case_gen.py`，或者人。脚本有两种运行方式，都需要 `--product` 指向场景产物目录（或带 `assets/` 的场景技能目录）：

```bash
python route_feedback.py --feedback <evaluation_report.json 或 feedback_to_generate.json> --product <产物目录> [--out <处理清单路径>]
python route_feedback.py --record <处理清单> --done all|t1,t2,... --product <产物目录> [--by <确认人>]
```

第一种读评估输出并写处理清单，默认写在评估输出文件旁边；第二种在处理项执行后把执行记录追加到产物的 `tree/feedback_log.json`，这是反馈处理线唯一写进产物的文件，供事后与评估目录里各轮报告对账。

**实现介绍**

`main()` 先把标准输出设为 UTF-8（Windows 控制台打印中文用），解析参数，调用 `locate_product()` 确定产物目录：目录下有 `tree/` 就是产物本身，有 `assets/tree/` 就取 `assets/`，两者都没有则退出。随后按有无 `--record` 分成两条路径。

分类路径依次做五件事。第一，`load_json()` 读评估输出，`load_tree()` 读 `tree/` 下按文件名排序的第一个 `*.tree_index.json`，取其 `nodes`；`nodes` 是列表时按每项的 `node_id`（或 `id`）转成字典，键是节点编号，值含 `name`、`condition`、`data_source` 等字段。迁移记录 `tree/migrations.json` 存在就读入，否则当作空字典；它的键是节点编号，值里本脚本用到 `package_node`（来源交付包的节点名）和 `script`（交付脚本路径，形如 `custom/<文件名>`）。第二，`extract_issues()` 把两种输入统一成问题条目列表：顶层是列表就逐项递归；字典里有 `reconciliation` 或 `verdict_detail` 键的是评估报告，`meta.verdict` 为 `PASS` 或 `PASS_WITH_GAPS` 时返回空列表，其他取值（包括 `EVAL_ERROR`，没有特殊处理）取 `failure_signatures`；其余字典按反馈文件处理，取 `issues`。两种情况都给缺 `case_id` 的条目补上 `meta.case_id`。条目为空时打印「没有需要处理的条目」后正常退出。第三，`build_tickets()` 逐条处理：`issue_category()` 依次从 `error_category`、`error_class` 两个字段取类别，字面是六个类别名之一就直接用，是 0 到 5 的整数就经 `CLASS_NAMES` 换成名字；之后把条目序列化成字符串 `blob`，取节点编号（`node_id`，没有则 `expected_node`），连同树索引、迁移记录、产物路径放进上下文字典 `ctx`。第四，选分支：序列化文本里含「熔断」或 `previous_attempts` 大于等于 3 时，不看类别，直接走 `_route_fused()`；否则按类别名在 `_ROUTES` 字典里查对应的 `_route_*` 函数；类别名不在六类里走 `_route_unknown()`，类别记为 `UNKNOWN`。每个 `_route_*` 函数返回四元组（分支名、档、工具、动作说明），`_ticket()` 把它和条目拼成处理项。第五，处理清单写成 JSON（字段 `generated_at`、`feedback_file`、`product`、`tickets`），同时在终端逐条打印，并汇总两档数量和下一步的记录命令。

这里的「熔断」指同一用例、同一节点、同一类错误连续三轮没有修好，评估侧（cs-scot-reflect 的 `eval_run.py`）据此在反馈条目的 `suggestion.detail` 末尾加「【熔断警告】」，本脚本看到后停止自动处理，转人工。

处理项的字段：`id`（`t1`、`t2` 顺序编号）、`case_id`、`error_category`、`branch`（真因分支名）、`level`（`auto` 自动档或 `fallback` 人工档）、`channel`（用哪个工具）、`node_id`、`reason`（评估侧给的原因原文）、`action`（动作说明）。各类错误对应的分支、判定依据与处理方式见下表，判定依据里的「文本」指条目序列化后的字符串：

| 类别 | `branch` | `level` | 判定依据 | `channel` 与 `action` 要点 |
|---|---|---|---|---|
| 任一类别 | `熔断` | `fallback` 人工档 | 文本含「熔断」，或 `previous_attempts` 大于等于 3；优先于类别判断 | `人工处理(迭代上限)`。停止自动迭代，先由模型定性：判据描述不足、脚本怎么写都判不对的，转为 KNOWLEDGE_GAP，向 tester 提重写判断方法的需求；判据没问题但用例期望可疑的，修用例并反馈测试数据质量；两者都不是的，按结论等级规则争议出争议说明交人 |
| ENV_FAILURE | `脚本运行崩溃` | `auto` 自动档 | `failed_steps` 的 JSON 文本与 `reason` 里含 `custom` 或 `fault_check` | `migrate(重迁+限定范围修复)`。在迁移记录里找到交付脚本所属节点时给出 `migrate --only "<包节点名>" --node <节点编号>`，格式检查与限定范围的修复随迁移自动进行；找不到时按 traceback 定位 `custom/` 下的脚本再重新迁入其交付包，清单之外的实现问题记退回单交 tester |
| ENV_FAILURE | `环境故障` | `fallback` | 其余执行失败 | `人工处理`。依赖、权限、数据损坏等修好后重跑 execute，报错原文在 `reason` |
| INPUT_GAP | `数据缺失` | `fallback` | 一律 | `人工处理(使用方补数据)`。补齐数据后重跑 execute，不改任何资产；动作说明附每个缺失数据源涉及的节点数 |
| BINDING_GAP | `绑定形态不匹配` | `auto` | `binding_form_mismatch` 为真，或文本含「绑定形态不匹配」 | `migrate(重迁换 S 绑定)`。现有绑定要表类数据，而用例只有原始日志形态；有交付包就重新迁入，换成直接运行交付脚本、由脚本读原始日志的 S 类绑定（迁移记录里有包名时附 `--only`）；确实没有包就用 zip2csv 转成表后在用例的 `data_map.json` 里映射，或向 tester 提脚本需求 |
| BINDING_GAP | `孤儿绑定`（代码里的字面值；含义是绑定指向的节点在故障树里已不存在，通常是删了节点没清绑定） | `auto` | `orphan_nodes` 非空，或文本含 `孤儿绑定` 或 `孤立节点`（前者是评估侧 `classify.py` 写此类 `reason` 时的开头词） | `调优表+import(产物自带)`。导出调优表，删掉这些节点的绑定引用，再导回；动作说明附 `grep_binding_files()` 找到的绑定所在脚本 |
| BINDING_GAP | `绑定清除冲突` | `fallback` | 节点在迁移记录里有条目 | `人工处理(二选一,无默认)`。节点曾由交付包迁入而现在没有绑定，说明是人工清掉的，但用例又期望它是根因；要么用 `migrate --only --node` 重新迁入恢复绑定，要么改用例期望 |
| BINDING_GAP | `绑定缺失` | `auto` | 以上三个分支都不是 | `migrate 或 调优表+import`。节点从未绑定过；有交付包就由 migrate 迁入，没有就在调优表该节点的绑定列按判据文档写匹配规则后导回；写不出绑定的，三轮内熔断后向 tester 提脚本需求 |
| KNOWLEDGE_GAP | `用例库过期` | `auto` | 条目 `node_name` 与树索引里某节点的 `name` 完全相同 | `case_gen 重新生成`。节点仍在树上只是编号重排，重跑 `case_gen` 刷新用例库 |
| KNOWLEDGE_GAP | `故障树缺节点` | `fallback` | 树上没有同名节点 | `人工处理(tester 需求单)`。向 tester 提增加节点的需求（节点名、建议父路径、来源用例、期望证据），回包后走优化线导入，或走增删节点线 |
| CRITERIA_GAP | `判定偏差修复` | `auto` | 一律 | `调优表 或 migrate(按证据自动修复)`。动作说明带期望与实测的 `predicate` 和判据原文前 160 字；绑定或列映射错就改调优表导回，脚本实现偏离判断方法文档就由 migrate 做限定范围修复；判定标准本身不改，疑为用例期望标错的留下证据，连续修不好由熔断转人工 |
| VERDICT_GAP | `结论归档争议` | `fallback` | 一律 | `人工处理`。前五类都排除后仍漏判或误报，属于结论等级判定规则的争议，出争议说明交人，不自动改根因分析的判定规则 |
| 其他 | `未识别` | `fallback` | 类别名不在六类里 | `人工处理`。原样转人工，附条目前 300 字；`error_category` 记为 `UNKNOWN` |

表里几处依据需要补充。类 0 查找交付脚本的归属，是在迁移记录里找 `script` 文件名出现在文本中的第一个节点。类 1 的影响统计，是数树索引里 `data_source` 字段的字符串形式含该数据源名的节点个数，属于粗略估计。类 2 残留绑定的定位靠 `grep_binding_files()`，它用正则在 `scripts/*.py` 里找 `"node_id": "<编号>"` 字样，返回命中的脚本文件名。类 3 的判断是拿条目的 `node_name` 与树索引各节点的 `name` 做完全相等比较。

记录路径由 `record_execution()` 完成：读处理清单，`--done` 为 `all`（不分大小写）时取全部处理项编号，否则按逗号拆分；有编号不在清单里就退出。然后读已有的 `tree/feedback_log.json`（没有则新建 `{"entries": []}`），追加一条记录，含时间 `at`、清单文件名 `tickets_file`、被执行的处理项 `executed`（每项只留 `id`、`error_category`、`branch`、`node_id`、`channel` 五个字段）和确认人 `approved_by`，再写回。

异常处理统一用 `SystemExit` 带中文说明退出，退出码为 1：JSON 读不出来、`--product` 下没有 `tree/`、`--record` 没配 `--done`、`--done` 里有未知编号、既没给 `--feedback` 也没给 `--record`。正常结束退出码为 0，包括没有待处理条目的情况。脚本不捕获其他异常。

**修改时要注意**

- 两种输入的类别字段含义不同，由评估侧 `eval_run.py` 的写法决定：评估报告的 `failure_signatures` 条目里 `error_class` 是 0 到 5 的整数、`error_category` 是类别名、节点编号在 `expected_node`；反馈文件的 `issues` 条目里 `error_class` 存类别名、节点编号在 `node_id`。`issue_category()` 和 `build_tickets()` 对两套字段都兼容，改动时两套都要保留。
- 分支判定有一部分靠评估侧的文字：类 0 看 `failed_steps` 和 `reason` 里是否含 `custom` 或 `fault_check`；类 2 的前两个分支看 `binding_form_mismatch` 标志或 `reason` 的固定开头；熔断看条目里是否含「熔断」。评估侧改了这些提示语，这里不会报错，只会落到别的分支。另外按 `eval_run.py` 现在的写法，两种条目都不带 `failed_steps`、`missing_inputs`、`orphan_nodes`、`binding_form_mismatch`、`expected_predicate`、`actual_predicate` 字段，相关分支实际上是靠 `reason` 文字命中的，动作说明里的影响统计、绑定定位会是空，期望与实测值会打印成 `None`；评估报告条目也不带 `suggestion` 和 `previous_attempts`，所以用评估报告做输入时识别不到熔断，要识别熔断应以 `feedback_to_generate.json` 为输入。`previous_attempts` 由评估侧写出时固定为 0，代码里没有递增它的逻辑；熔断实际靠评估侧 `eval_run.py` 按用例、节点、错误类跨轮计数后写进 `suggestion.detail` 的「【熔断警告】」文字触发。
- `SKILL.md` 把 `failure_signatures.json` 也列为可识别输入，但按现在的 `extract_issues()`，这个文件顶层是签名条目数组，逐项递归时每项既没有 `reconciliation` 也没有 `issues`，结果为空列表，脚本会报「没有需要处理的条目」。要支持它需要在 `extract_issues()` 里增加对签名条目的识别。
- 类 3 的自动档分支（`用例库过期`）依赖条目的 `node_name`。按当前评估侧代码，类 3 的条目不带节点名（`check_tree.py` 的缺失项只有 `node_id` 和 `reason`），所以这条分支只在条目另行补上 `node_name` 时才会命中，否则一律走人工档。
- `load_tree()` 只读排序后的第一个树索引文件；产物 `tree/` 下若有多个 `*.tree_index.json`，其余会被忽略。
- 本脚本除记录模式写 `tree/feedback_log.json` 外不改产物，文档字符串把这一点定为职责边界；要增加新的自动处理能力，应放到既有工具里，处理项上只写命令。`record_execution()` 不去重，同一清单重复记录会追加重复条目；记录里的 `tickets_file` 只存文件名不存路径，审计时要配合处理清单本身。
- 新增错误类别要同时改 `CLASS_NAMES` 和 `_ROUTES`，两处不一致时新类别会落到 `_route_unknown()`。

### scripts/package_skill.py

**功能与作用**

开发态的发行打包脚本，在仓库任意位置运行：

```bash
python skills/cs-scot-generate-optimize/scripts/package_skill.py [--out <输出根目录>] [--no-zip]
```

它把本入口、两条线的技能（cs-scot-generate、cs-scot-migrate）、公共素材目录（cs-scot-execute、fault-tree-rca、zip2csv 三个技能目录，场景技能里的工具链公共文件就来自它们）和业务侧技能 datacom-fault-tree-tester 合成一个自包含目录 `dist/cs-scot-generate-optimize/`，再打同名 zip，交付给平台。包的布局由 `LAYOUT` 决定：入口自己的 `SKILL.md`、`references/` 平铺在包根，其余技能各占一个子目录，generate 与 migrate 的目录名就叫 `generate`、`migrate`。包根因此扮演仓库 `skills/` 目录的角色，generate 和 migrate 按「同一 skills 目录下的其他技能」这一级就能找到素材，发行包里的生成线生成的场景技能是完整可执行的。打包是纯复制，不改写任何源文件；包内只改两份线手册副本的开头信息。tester 一起进包的理由写在 `LAYOUT` 的注释里：反馈处理线类 3、类 4 退回 tester 的处理项，在交付环境里也要能找到它。平台接入文档 `integration/platform1/交接说明_开发.md` 第 10 节说明装好这个包后如何接线。本脚本自身不进包（`EXCLUDE_REL`）。

**实现介绍**

`main()` 由脚本自身位置推出 `skills_dir`（上三级目录）和仓库根，输出根目录默认 `<仓库>/dist`，包目录是其下的 `cs-scot-generate-optimize`，然后按编号顺序调用六个步骤函数，任一步返回退出码就立即结束。

`_check_sources()` 做两项检查。一是源目录齐备：对 `LAYOUT` 里的每个名字调用 `_resolve_src()`，先找 `skills/<名字>`，三个公共素材找不到时再找 `skills/cs-scot-generate/<名字>`（generate 单独交付时素材内嵌在技能内的形态），任何一个找不到就打印缺失清单并返回 1。二是重叠防护：`_overlaps()` 判断输出目录与任一源目录相同或互为父子，是则返回 2，因为后面的 `rmtree` 会毁掉源文件。

`_reset_pkg_dir()` 清理旧包：目标目录已存在但没有 `MANIFEST.txt` 时视为不是本工具打的包，拒绝删除并返回 2；有则整目录删除后重建，并立刻写一个占位的 `MANIFEST.txt`，这样中途崩溃留下的半个包，下次运行能被认出并清理。

`_copy_layout()` 按 `LAYOUT` 复制。入口目录用 `_copy_flat()` 逐项复制到已存在的包根：子目录用 `copytree` 并带 `_ignore()` 过滤，文件用 `copy2`，复制完若 `scripts/` 成了空目录就删掉（打包脚本自己被排除后那里可能什么都不剩）。其他技能用 `_copy_tree()` 整目录复制到子目录；源是 cs-scot-generate 时额外排除其顶层与 `MATERIALS` 同名的目录，保证素材在包里只有包根一份；目标已存在时抛 `SystemExit` 并给出处置说明。`_ignore()` 返回 `copytree` 用的过滤函数，排除 `__pycache__`、`.git`、`dist` 目录，`.pyc`、`.pyo` 文件，`.DS_Store`、`Thumbs.db`，入口自己的 `scripts/package_skill.py`，以及 `EXCLUDE_TOP_BY_PKG` 指定的 migrate 顶层 `engine/` 目录。generate 和 migrate 复制完后调用 `_rewrite_line_manual()`：用正则取出 `SKILL.md` 开头的 frontmatter，把 `name:` 行改成包内目录名，`description:` 行改成一段固定文字（说明这是发行包内的生成线或优化线手册、对应仓库里哪个技能、不单独触发、命令里的路径前缀换成本目录的绝对路径），正文里第一处 `# cs-scot-generate：` 或 `# cs-scot-migrate：` 标题的技能名改成目录名（冒号半角全角都认），其余正文不动；没有 frontmatter 则抛 `SystemExit`。

`_verify_required()` 逐项检查 `REQUIRED` 清单里的文件是否存在。清单覆盖入口的 `SKILL.md` 与 `references/feedback_routing.md`，generate 的 `SKILL.md`、`knowledge.md`、`_GEN_SCRIPTS` 列的 16 个脚本和 `_GEN_REFS` 列的 5 份参考文档，migrate 的手册、两个脚本和两份参考文档，zip2csv 的手册、`vendor/ifilter.py` 和 `_Z2C` 列的 4 个脚本，以及 cs-scot-execute、fault-tree-rca、datacom-fault-tree-tester 各自手册点名必用的脚本与参考文档。缺任何一个就打印缺失项，删掉不完整的包目录和旧 zip，返回 1。

`_write_manifest()` 列出包内全部文件，写 `MANIFEST.txt`：开头三行是包名、来源提交（`_git_rev()` 取 `git rev-parse --short HEAD`，工作区有未提交改动时追加 `+dirty`，不是 git 环境时写「(非 git 环境)」）、文件数与总字节数，之后每行一个文件的大小和相对路径。`_write_zip()` 把包目录按路径排序逐个写入 zip，条目名以包名开头，时间戳统一用 `_zip_datetime()` 取的最近一次提交时间（取不到或年份早于 1980 时用 `(2020, 1, 1, 0, 0, 0)`，zip 格式不接受更早的时间），文件属性统一为 `0o644`，所以同一提交两次打包得到字节一致的 zip；最后算 sha256 并打印前 16 位。

退出码：0 成功；1 缺源目录或缺关键文件；2 输出目录与源目录重叠，或目标已存在且不是本工具的包；`_copy_tree()` 与 `_rewrite_line_manual()` 的 `SystemExit` 退出码也是 1。`tests/test_engine_sync.py` 会实际运行本脚本（`--out` 临时目录加 `--no-zip`），检查包里没有 `migrate/engine/`、没有内容重复的文件，并用包内的 migrate 跑一次迁移。

**修改时要注意**

- 三道防护不要删：输出目录与源目录重叠即拒绝；目标存在但没有 `MANIFEST.txt` 即拒绝删除；复制时目标已存在即报错。代码注释记录了实际发生过的情况：遇到目标已存在的报错时，有人用 `dirs_exist_ok=True` 让 `copytree` 静默合并，结果两份来源的文件混进同一个包。正确做法是用仓库最新脚本整文件覆盖本脚本并删掉 `dist/` 重跑。
- 源 cs-scot-generate 若是素材内嵌形态，复制时会剔除其顶层素材目录；注释记录了不剔除时在 Windows 上与后续素材复制冲突的问题。新增公共素材要同时加进 `LAYOUT`、`MATERIALS` 和 `REQUIRED`。
- `migrate/engine/` 是其他技能脚本的备用副本，只在 migrate 单独部署时使用；发行包里这些脚本都在包根，所以通过 `EXCLUDE_TOP_BY_PKG` 排除，注释给的理由是手工副本会与源文件漂移。`tests/test_engine_sync.py` 同时检查这一点和全包无重复文件。
- `REQUIRED` 是防止「不完整的检出打出残包」的清单，与各线手册点名的文件对应。子技能新增手册里必用的脚本或参考文档时要补进来，否则漏打不会报错；反过来，子技能改名或删除清单里的文件而不更新清单，打包会一直失败。
- `_rewrite_line_manual()` 依赖子技能 `SKILL.md` 的固定形态：开头是 `---` 包起的 frontmatter，含 `name:` 和 `description:` 行，正文第一处标题是 `# <技能名>：`。改变这两份手册的开头格式时，frontmatter 缺失会报错，标题形态变了则不报错也不改写，包内手册的自称会与目录名不一致，注释指出这会误导模型去找一个叫 cs-scot-generate 的技能。
- 可复现性依赖三点：zip 条目按路径排序、时间戳取自提交时间、`MANIFEST.txt` 内容只由文件集合和提交号决定。往清单或包里加入当前时间、随机值之类的内容会破坏「同一提交两次打包 sha256 一致」。
- 脚本用自身路径的上三级作为 `skills/` 目录，挪动脚本位置会找不到源目录。

### SKILL.md

入口的技能说明，读者是作为入口工作的模型，开发同事改流程时也要先读它。它定义了母表、对应表、调优表、交付包、处理项、自动档与人工档、熔断等术语，给出四条处理流程的判定表（按标志和特征识别输入）、各线的必需入参与手册查找次序（发行包形态下的 `generate/`、`migrate/` 子目录，或仓库形态下的兄弟目录）、混合输入的执行顺序、增删节点线的调优表操作、反馈处理线的四条规则，以及发行打包的命令。与脚本的对应关系：反馈处理线一节给出 `route_feedback.py` 两种运行方式的命令和 cs-scot-reflect 的 `regress.py` 回归命令，发行打包一节描述的就是 `package_skill.py` 的行为；每类问题的处理方式指向 `references/feedback_routing.md`。

### references/feedback_routing.md

反馈处理线的处理方式对照表，由模型在执行处理项时对照，也是开发同事核对 `route_feedback.py` 各 `_route_*` 函数行为的依据。正文一张表列出每类问题（类 0 到类 5 及其真因分支）属于自动档还是人工档、用哪个工具、做什么，之后给出六类错误名与编号的对照、评估报告 `verdict` 各取值的处理（`PASS`、`PASS_WITH_GAPS` 无事可做；`PARTIAL_PASS`、`FAIL` 按处理清单逐项处理；`EVAL_ERROR` 是评估自身失败，修评估输入后重评），以及 `case_quality` 提示和 tester 随发行包交付的说明。cs-scot-reflect 的 `references/error_classification.md` 明确以本文为处理方式的准绳，改 `_route_*` 函数的分支或工具时，本文与那份文档末尾的摘要表都要同步。
