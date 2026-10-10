# datacom-fault-tree-tester 代码说明

## 技能概述

datacom-fault-tree-tester 是七个技能里面向业务同事的那一个，处在生成、执行、评估、优化这个闭环的优化环节（`docs/design.md` 第 3 节流程图的第 ④ 步）。它的职责是把业务同事口头描述的一个故障树节点的判断方法，整理成符合 `判断方法_规范.md` 的判断方法文档，生成能在采集数据上运行的判断脚本，用测试数据验证后打成节点交付包；节点交付包随后由 cs-scot-migrate 迁入场景技能，替换该节点原有的判据与绑定。按设计原则「判断逻辑归交付方」，判断逻辑由业务同事负责，工具链只检查格式、做输入输出适配，不合格的包退回交付方修改。另有一条批量翻新存量节点交付包的流程，只修输入输出格式，不动判断逻辑。

输入有五项：故障树节点名（必填）、节点路径（可选，树上有同名节点时供下游按路径消歧）、自然语言描述的判断方法（必填）、按「网元目录/CONFIG、CMD、LOG」组织的测试数据目录（必填；原始散文件先用同级的 zip2csv 技能归类）、解决方案（可选）。输出是一个 zip 形式的节点交付包，写到 `skills/datacom-fault-tree-tester/output/<节点名>_<yyyyMMdd-HHmmss>.zip`，包内有判断方法文档 `判断方法_<节点名>.md`、`判断方法_规范.md`、`测试数据/`、判断脚本 `fault_check_<节点名>.py`、脚本 import 到的全部 `search_*.py`、`执行记录.md` 与 `SKILL.md`。同一节点重新打包产生新时间戳的文件，旧包不删，作为版本历史。

本技能目录里没有驱动整个流程的程序，流程由模型按 `SKILL.md` 的十一个步骤执行，脚本只承担确定性的工作：四个 `search_*.py` 是判断脚本可以 import 的共享解析库，分别处理设备配置、日志、命令回显；`check_contract.py` 是格式检查，对单个判断脚本或整个节点交付包做静态检查、试运行检查并记录回归基线。判断脚本在生成与验证阶段放在 `scripts/` 下，与共享解析库同目录，`from search_config import …` 才能成立；打包后移入节点交付包，`scripts/` 只常驻共享解析库与格式检查。五个脚本的正本都在这里：`cs-scot-migrate/engine/` 下的同名文件是 `cs-scot-migrate/scripts/sync_engine.py` 复制出来的副本，只在迁移技能单独安装时使用，当前与正本逐字节一致；发行包由 `cs-scot-generate-optimize/scripts/package_skill.py` 打出，收录本技能的 `SKILL.md`、`判断方法_规范.md`、两份 references 与五个脚本，`output/` 不进发行包。调用关系如下：

```
业务同事：自然语言的判断方法 + 测试数据目录
    │  模型按 SKILL.md 步骤 1 到 11 执行；向用户输出的固定格式见 references/templates.md
    ▼
scripts/fault_check_<节点名>.py        写法遵守 references/script_contract.md 的十条要求
    │  from search_config import …     import 同目录的 search_config / search_log / search_cmd / search_diag_txt
    │  python scripts/check_contract.py <脚本路径> --strict        步骤 5，脚本级检查
    │  python scripts/check_contract.py <打包目录> --strict        步骤 10，包级检查 + 回归基线
    ▼
output/<节点名>_<时间戳>.zip
    │  cs-scot-migrate 迁入前：migrate_fault_check.py 以模块方式调用 check_contract.check_script
    │  运行期：通用适配函数 fault_check（custom_lib.py）加载判断脚本并调用其入口
    ▼
场景技能（判据与绑定更新）
```

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 模型执行本技能的操作手册：术语、输入参数、测试数据目录的组织方式、十一个步骤、批量翻新流程、打包清单 |
| `判断方法_规范.md` | 参考文档 | 判断方法文档的命名、六个必填章节与三个选填章节、模板与正反示例；附录是判断脚本的八条通用要求。始终随节点交付包 |
| `references/script_contract.md` | 参考文档 | 判断脚本的十条格式要求与返回结果的完整 JSON 格式，写脚本前读 |
| `references/templates.md` | 参考文档 | 各步骤向用户输出的固定格式、执行记录模板、翻新记录模板 |
| `scripts/check_contract.py` | 脚本 | 格式检查：对判断脚本或节点交付包做静态检查、试运行检查、回归基线记录，每项给出 PASS、WARN 或 FAIL 与修法 |
| `scripts/search_config.py` | 脚本 | 共享解析库：解析以 `#` 分块的华为设备配置，按关键字、正则或路径查找配置项 |
| `scripts/search_log.py` | 脚本 | 共享解析库：解析 `display logbuffer`、`display trapbuffer` 的输出，按关键字、模块、级别、时间过滤，统计端口事件 |
| `scripts/search_cmd.py` | 脚本 | 共享解析库：在命令回显文件里按四种排版定位某条 `display` 命令的回显，或列出全部命令 |
| `scripts/search_diag_txt.py` | 脚本 | 共享解析库：与 `search_cmd.py` 相同规则的回显查找，只接受行列表，不做编码检测 |

`output/` 目录在打包时创建，不在仓库里。四个共享解析库在仓库内没有调用方（仓库里没有判断脚本样例），它们的调用方是各节点交付包里的 `fault_check_*.py`。

## 各文件说明

### scripts/check_contract.py

**功能与作用**

格式检查脚本。它把 `判断方法_规范.md` 附录与 `references/script_contract.md` 十条要求里能机械判断的部分写成检查项，对三类目标运行：单个 `fault_check_*.py`（生成阶段自查，SKILL.md 步骤 5）、节点交付包目录、节点交付包 zip（打包前检查，步骤 10；批量翻新时逐包检查）。它也是 cs-scot-migrate 迁入前的检查组件：`cs-scot-migrate/scripts/migrate_fault_check.py` 的 `_gate_run_check` 以模块方式加载它并调用 `check_script`，迁移侧怎样处理两档结果写在 `cs-scot-migrate/references/script_check.md`。

命令行：`python check_contract.py <交付包目录|交付包.zip|fault_check_*.py> [--strict] [--json] [--data <数据目录>] [--timeout N]`。输出是逐项的检查报告，`--json` 时输出机器可读的 JSON；退出码 0 表示通过（默认允许 WARN，`--strict` 时 WARN 也算不通过），1 表示不通过，2 表示用法或输入错误。检查项分四档：FAIL 是违反格式要求且执行或结论必受影响、下游通用适配函数处理不了的，必须修；WARN 是格式不达标但通用适配函数运行时可以处理、或者静态无法确证的，新脚本必须修，存量导入可以放行但要写入迁移记录；INFO 只有回归基线一项；其余为 PASS。文件头声明只用标准库，且不修改任何被检文件。

**实现介绍**

代码分三段：静态检查（AST 分析，编号 S0 到 S6）、试运行检查（动态加载并调用入口，编号 D1 到 D4）、包级检查（文档与包布局，编号 M0、M1、P1），由 `check_package` 与 `check_script` 两个入口组装，`main` 负责命令行与退出码。每条结果是一个 `Item` 对象，字段 `id`（编号）、`tier`（PASS、WARN、FAIL、INFO）、`ok`、`msg`、`fix`（修法）；三个构造函数 `_fail`、`_warn`、`_pass`。报告是一个 dict：`target`、`mode`（`package` 或 `script`）、`scripts`（每个脚本一个 `{script, items}`）、`md`（文档与包布局项）、`notes`（提示性文字）、`baseline`，经 `_summarize` 补上 `fail`、`warn`（计数）、`pass`（没有 FAIL）、`strict_pass`（没有 FAIL 也没有 WARN）。三个常量决定了检查的依据：

```python
ENTRY_OK = ("fault_check", "diagnose", "main", "check")
TRI = ("true", "false", "undetermined", "insufficient_data")   # 四态约定(规范 v1.2);变量名沿用
MD_REQUIRED = ("节点名称", "故障描述", "判断条件", "命中条件", "输出", "数据来源")
```

静态检查。`_static_check(script, local_names, pkg_files, entry_hint=None)` 读源码并 `ast.parse`，语法错误记 S0 FAIL 并直接返回。S1 由 `_find_entry` 在模块顶层函数里按 `ENTRY_OK` 的顺序找入口，`_check_entry` 评判签名。S2 由 `_check_main_guard` 完成：`_guarded_nodes` 收集模块级 `if __name__ == '__main__'` 分支内的全部节点；`_reachable_funcs` 从入口出发、沿模块内按名字的直接调用建一张粗粒度调用图；`_scan_nodes` 把可达函数的节点与守卫分支外的模块级节点合为扫描范围；`_rule_hits` 统计其中 `sys.argv` 的引用（含 `from sys import argv as x` 的别名，由 `_argv_aliases` 收集）、`print(...)` 调用、`sys.exit()` 或 `exit()` 调用的次数，有任一项就记一条 WARN。S3 `_check_file_discovery` 与 S4 `_check_ne_probe` 都是源码文本检查：前者看小写后的源码里有没有 `.log` 与 `.txt` 字样，后者用正则找 `iterdir`、`listdir`、`scandir`、`os.walk`、`glob(` 之一。S5、S6 由 `_check_deps` 完成：`_collect_imports` 遍历 `import` 与绝对的 `from … import`，取顶层模块名，标准库（`sys.stdlib_module_names`，Python 3.10 以下用 `_STDLIB_FALLBACK` 清单）与 `local_names` 里的名字放过，以 `search_` 开头但不在本地的归入缺失的共享模块（S6），其余归入第三方或包外模块（S5）。函数返回 `(items, 试运行用的入口名)`，入口名优先取 `entry_hint`（它必须是模块顶层函数），否则取 S1 认出的名字。

试运行检查。`_smoke_check(script, entry_name, timeout, per_ne=False, entry_kwargs=None)` 先用 `_import_script` 加载脚本：把脚本所在目录插到 `sys.path` 最前，使脚本的 `from search_xxx import …` 成立，再用 `importlib` 以 `cc_<文件名>` 为模块名执行模块；加载与之后每次调用都包在 `_Shield` 与 `_run_boxed` 里。`_Shield` 进入时把 `sys.argv` 换成只有脚本名、把 `sys.stdout`、`sys.stderr` 换成内存缓冲，退出时恢复，试运行期间脚本的输出不会混进检查报告；`_run_boxed(fn, timeout)` 在守护线程里调用并 `join(timeout)`，返回 `("timeout", None)`、`("error", 异常)` 或 `("ok", 返回值)`，捕获的是 `BaseException`，所以脚本里的 `sys.exit()` 也算错误。超时秒数来自 `--timeout`，默认 120 秒。D1 评判加载：超时、异常、取不到入口都是 FAIL。D2 用 `tempfile` 建一个空目录，以字符串路径调用 `fn(str(empty), **kw)`：超时记 D2t FAIL；抛异常记 D2x FAIL，若异常是 `TypeError` 且消息含 `str`，修法提示先 `Path(data_dir)` 显式包装，否则提示缺数据应返回 `insufficient_data` 而不是抛异常或退出；正常返回则交给 `_shape_items("D2", 返回值, True, …)`。`per_ne` 为真（入口按单网元目录驱动）时到此为止；否则再建 `root/网元A/CONFIG、CMD、LOG` 三个空目录，以 `fn(str(root), **kw)` 做 D3，评判方式与 D2 相同。

返回值形态的识别集中在 `_tri_of` 与 `_shape_items`。`_tri_values` 按固定顺序认四种形态：`details.devices` 是 dict（标签 `devices`）、顶层 `results` 是 dict（`wrapped`）、顶层有 `matched`（`global`）、所有值都是 dict 的按网元字典（`per-ne`），认不出的记 `unknown`，空 dict 记 `empty`；`_tri_of` 的文档字符串写明这一顺序与通用适配函数 `_fc_normalize`（`custom_lib.py`）相同。`_tri_normalize` 把各 `matched` 原值归一成四个取值之一的小写字符串：布尔值转成 `true` 或 `false` 并在标签上追加 `+bool`，不认识的值归入 `undetermined`。多个值由 `_tri_merge` 合并：

```python
    if "true" in norm:
        return "true"
    if "false" in norm:
        return "false"
    if "insufficient_data" in norm and "undetermined" not in norm:
        return "insufficient_data"                      # 纯缺数据;混"判不动"时按 undetermined 归并
    return "undetermined"
```

`_shape_items(iid, result, expect_undetermined, label)` 据此打分：不是 dict、形态认不出都是 FAIL；空 dict 是 WARN；按网元字典、`results` 信封、`details.devices` 包裹记 WARN（编号加后缀 `s`）；布尔值记 WARN（后缀 `b`）；`expect_undetermined` 为真（D2、D3 传入的是空数据）而合并结果是 `true` 或 `false`，记 FAIL，因为缺数据被当成了结论；前面没有记任何项时，若合并结果是 `undetermined` 或 `insufficient_data`、形态是全局形态且 `details.reason` 为空，记 WARN（后缀 `r`），否则记 PASS 并附 `reason` 的前 60 字。

回归基线。`_baseline_run(fn, data_dir, timeout)` 用真实测试数据调用 `fn(str(data_dir))`（不传额外参数）：超时记 D4t FAIL，异常记 D4x FAIL；返回值用 `_shape_items("D4", 返回值, False, …)` 评形态，此时判成 `true` 或 `false` 都允许；最后记一条 INFO `D4i`，内容是 `matched` 的合并值与 `evidence` 的条数，作为基线 `{"matched", "evidence_count"}` 写进报告的 `baseline`（附脚本名）。基线不评对错，用途是修改前后比对：SKILL.md 的翻新流程要求修完重跑后 `matched` 与修前一致，唯一允许的变化是 `undetermined` 变成 `insufficient_data`。数据目录由 `_find_test_data` 决定：`--data` 指定的优先，否则取包内用 `rglob` 找到的第一个 `测试数据` 目录；脚本模式下只有给了 `--data` 且不是 `per_ne` 时才做基线。

包级检查与两个入口。`check_package(pkg, data_dir=None, timeout=120)` 用 `rglob` 收集 `fault_check_*.py`、其他 `.py`（排除 `fault_check_`、`search_`、`verify_` 前缀）、可 import 的包内模块名 `pkg_mods`（只收 `fault_check_*` 与 `search_*` 的文件名，注释说明这是为了与迁移侧的收取规则一致）、判断方法文档（`判断方法*.md` 且文件名不含「规范」）。`_pkg_doc_items` 在没有判断方法文档时记 M0 FAIL，有则逐个用 `_md_check` 查 M1：取所有 Markdown 标题行，`MD_REQUIRED` 六个名字中任何一个没有出现在任一标题行里就 FAIL。`_pkg_layout_items` 处理两种布局问题：没有 `fault_check_*.py` 但别的 `.py` 里有 `def fault_check`，记 P1 FAIL（命名不符合约定，下游收不到）；完全没有脚本记一条「纯文档包」提示；包里混有 `verify_*.py` 记一条调试遗留提示。随后对每个判断脚本调用 `_check_pkg_script`：S0 FAIL 时没有入口可试运行，S5、S6 任一 FAIL 则跳过试运行并记提示「依赖检查未过」，其余情况做试运行检查，有测试数据再做基线。`check_script(script, data_dir=None, timeout=120, entry=None, per_ne=False, entry_kwargs=None)` 检查单个脚本，把同目录下 `fault_check_*`、`search_*` 文件当作可 import 的本地模块；`entry`、`per_ne`、`entry_kwargs` 是迁移侧试运行后选定的调用方式（入口名、是否逐网元调用、额外的关键字参数），给了就按它试运行并扫描可达路径，S1 仍按约定的命名评判。`main(argv=None)` 解析参数后按目标类型分派：zip 解压到 `tempfile.TemporaryDirectory(prefix="contract_")` 后按目录检查并在 `finally` 里删除，目录走 `check_package`，`.py` 走 `check_script`，其他类型返回 2；之后 `_summarize`，按 `--json` 决定输出 JSON 还是由 `_print_report` 打印文本报告（每项一行 `[PASS]`、`[WARN]`、`[FAIL]`、`[info]` 加编号与消息，FAIL 与 WARN 下一行是 `修法:`，随后是「文档/包」块、`[note]` 行与汇总行）。

检查项与十条格式要求的对应如下。「判定」列是该检查项不满足时的等级，修法按代码里 `fix` 字段的意思概括，括号里的「附录」指 `判断方法_规范.md` 的附录章节。

| 要求 | 检查项 | 检查什么 | 判定 | 修法提示 |
|---|---|---|---|---|
| 1 入口与输入参数 | S1 | 模块顶层按 `ENTRY_OK` 顺序找入口。找不到（消息会点名 `check_ne`、`diagnose_ne` 这类内部辅助函数不算入口）、入口没有位置参数、第二个起的位置参数没有默认值 | FAIL | 补顶层 `def fault_check(data_dir)` 作全局入口；额外参数一律具名并给字面量默认值，下游只传 `data_dir`（附录 5） |
| 1 | S1 | 入口名不是 `fault_check`（是 `diagnose`、`main`、`check` 之一） | WARN | 改名为 `fault_check(data_dir)`，与文件名前缀同名（附录 5）。首参不叫 `data_dir` 只在 PASS 消息里注明 |
| 1 | S2 | 入口可达函数与守卫分支外的模块级代码里出现 `sys.argv`（含别名）、`print(...)`、`sys.exit()`、`exit()` | WARN | 全部移进 `if __name__ == '__main__'` 分支；库调用出错返回 `undetermined`（附录 5） |
| 1 | D2x | 以字符串路径调用入口抛出 `TypeError` 且消息含 `str` | FAIL | 先 `data_path = Path(data_dir)` 显式包装，下游按字符串传参（附录 5） |
| 2 目录遍历 | S3 | 源码（小写后）只出现 `.txt`，没有 `.log` | FAIL | 目录扫描改成 `.txt`、`.log` 双后缀、大小写不敏感（附录 5） |
| 2 | S3 | 源码里 `.txt`、`.log` 都没有出现，无法静态确认 | WARN | 确认文件发现对两种后缀都收且大小写不敏感，以试运行检查结果为准 |
| 2 | S4 | 源码里没有 `iterdir`、`listdir`、`scandir`、`os.walk`、`glob(` 中任何一个 | WARN | 入口自带多网元与单网元自动探测：`data_dir` 下有网元子目录就逐网元，直接是 CONFIG、CMD、LOG 就按单网元（附录 5） |
| 2 | D3t、D3x | 传入只有 `网元A/CONFIG、CMD、LOG` 空目录的多网元根目录时超时或抛异常 | FAIL | 多网元目录必须能处理（附录 5），空数据返回 `insufficient_data` |
| 3 输出格式 | D2、D3、D4 | 返回值不是 dict；或是 dict 但没有 `matched`、`results`，值也不是网元结果 | FAIL | 入口返回全局结果 dict，含 `matched` 与 `evidence`（附录 6） |
| 3 | D2、D3、D4 | 返回空 dict | WARN | 没有数据也要返回 `{"matched":"insufficient_data","details":{"reason":…}}`（附录 6） |
| 3 | D2b、D3b、D4b | `matched` 是布尔值 | WARN | `matched` 用 `"true"`、`"false"`、`"undetermined"`、`"insufficient_data"` 字符串（附录 6） |
| 3 | D2r、D3r、D4r | `matched` 为 `undetermined` 或 `insufficient_data` 但 `details.reason` 为空 | WARN | `insufficient_data` 写缺哪条命令或文件，`undetermined` 写为什么判断不出（附录 6） |
| 3 | 无 | `evidence` 条目内部的 `type`、`id`、`source_files`、`matched_items` 字段 | 代码未检查 | 只统计 `evidence` 条数进基线 |
| 4 文件位置 | P1（包模式） | 包里没有 `fault_check_*.py` 命名的脚本，但别的 `.py` 里有 `def fault_check` | FAIL | 脚本改名为 `fault_check_<节点名>.py`（SKILL.md 步骤 5）。脚本放在 `scripts/` 下、打包后移走这两点代码未检查；`verify_*.py` 只记提示 |
| 5 复用共享解析库 | 无 | 是否调用了 `search_config.py` 的函数 | 代码未检查 | SKILL.md 步骤 10 把它列为打包前四项检查之一，由模型核对。S6 只在脚本 import 了 `search_*` 时检查该模块是否随包 |
| 6 全局形态 | D2s、D3s、D4s | 返回按网元字典、`results` 信封或 `details.devices` 包裹 | WARN | 顶层改成一个 `matched` 加 `evidence`，网元写进 evidence 条目的 `type`、`id`（附录 6） |
| 7 无法判断时如实上报 | D2、D3 | 空目录或空的多网元目录仍判成 `true` 或 `false` | FAIL | 数据缺失时 `matched:"insufficient_data"`（旧版返回 `undetermined` 也认）并在 `details.reason` 写明缺什么（附录 4） |
| 7 | D2x | 空目录调用抛出其他异常 | FAIL | 缺数据必须返回 `insufficient_data`，不抛异常、不退出（附录 4） |
| 8 数据来源表列全 | M0、M1（包模式） | 包里没有 `判断方法*.md`；或文档标题行里缺「节点名称、故障描述、判断条件、命中条件、输出、数据来源」中的任何一个 | FAIL | 节点交付包必须带判断方法文档；按规范第四节模板补齐必填章节。表内命令是否列全代码未检查 |
| 9 依赖限制 | S5 | import 了标准库与包内 `fault_check_*`、`search_*` 之外的模块 | FAIL | 只用标准库加包内共享模块；自写的辅助模块要么并入主脚本，要么按 `search_*.py` 命名随包（附录 7、8） |
| 9 | S6 | import 的 `search_*` 模块不在包里（包模式）或不在脚本同目录（脚本模式） | FAIL | 把脚本 import 的每个 `search_*.py` 打进节点交付包，不论本次改没改（附录 8）；迁移时可自动补库，但正本必须自包含 |
| 10 生成后必须过格式检查 | 无 | 这一条是对运行本脚本的要求 | 无 | `--strict` 下有 WARN 即退出码 1 |

十条之外还有几项：S0 语法错误（FAIL，后续检查全部跳过）；D1 加载阶段超时、抛异常或取不到入口（FAIL，修法分别是模块级只放定义与常量、守卫外不执行诊断或读参数、入口无条件定义在顶层）；D4t、D4x 测试数据试运行超时或抛异常（FAIL）；D4i 回归基线（INFO）。

**修改时要注意**

- `ENTRY_OK` 故意不含 `check_ne`、`diagnose_ne`，注释记录了这两个内部辅助函数名被误认为入口的实际问题；迁移侧 `migrate_fault_check.py` 的 `_ENTRY_CANDIDATES` 含这两个名字并以逐网元方式调用。两个清单的差别是有意的，改任一侧前要对照另一侧。
- `check_script` 的 `entry`、`per_ne`、`entry_kwargs` 是迁移侧的调用接口：`_gate_run_check` 先按这个签名调用，遇到 `TypeError` 就退回只传 `data_dir` 的旧签名。改动签名不会让迁移报错，只会让这三个参数被静默丢弃。
- 可 import 的包内模块只认 `fault_check_*`、`search_*` 两种文件名（`check_package` 的 `pkg_mods`、`check_script` 的 `local`），其他 `.py` 一律按包外模块判 S5 FAIL，注释说明这是与迁移侧的收取规则保持一致；要放开必须两侧同改。
- S3、S4 是对源码文本的启发式检查，注释或字符串里出现 `.log` 字样也会让 S3 通过；它们证明不了行为正确，真正的判断靠试运行检查。S2 的扫描范围只跟进按名字的直接调用，经属性调用（如 `obj.method()`）到达的函数与入口不可达的函数都不扫。
- `_run_boxed` 的超时靠守护线程加 `join(timeout)` 实现，超时后线程不会被终止，只是不再等待。`_import_script` 导入成功后把脚本目录留在 `sys.path` 里（注释：脚本可能延迟 import），本脚本自身不清理 `sys.modules`，同一进程连续检查多个包时 `search_*` 模块会沿用第一次导入的版本；迁移侧在 `_gate_run_check` 的 `finally` 里清掉 `search_`、`cc_` 前缀的模块与多出的 `sys.path` 项，命令行单次运行不受影响。
- 包模式有多个判断脚本时，报告的 `baseline` 只保留最后一个有基线的脚本，各脚本自己的 D4i 行仍在 `items` 里；基线试运行不传 `entry_kwargs`。
- `_tri_of` 一组函数的形态识别顺序与 `custom_lib.py` 的 `_fc_normalize` 相同（`_tri_of` 的文档字符串说明），改其一要同步另一个，否则检查通过的形态运行期可能认不出。
- 迁移侧把 S1、D2x、D3x 三项 FAIL 降为 WARN 放行（`migrate_fault_check.py` 的 `_ABSORBED`，前提是它自己的试运行已确认能驱动脚本）；新增 FAIL 项时要一并考虑迁移侧能否弥补。
- `TRI` 名字沿用自只有三个取值的版本，现在是四个取值（注释）。`_md_check` 用子串匹配标题行，标题「输出格式」也能满足「输出」；「节点路径」不在必填之列。

### scripts/search_config.py

**功能与作用**

共享解析库之一，解析 `CONFIG/` 目录下 `display current-configuration` 的输出。`判断方法_规范.md` 附录第 1 条与 `script_contract.md` 第 5 条要求判断脚本的配置解析必须用它的 `parse_config_file()`、`find_config_item()`、`get_group_items()`，不得自行实现，理由是它已经处理了编码自动检测、以 `#` 分隔的配置块、大小写不敏感的搜索。调用方是各节点交付包里的 `fault_check_*.py`（仓库内没有），也可以在命令行运行：`python search_config.py <文件路径> [<配置项>] [--value 期望值] [--regex] [--exact] [--lines N] [--json] [--show-tree]`。脚本 import 了它就必须随包；迁移时包里没带，迁移技能会从本技能或 `cs-scot-migrate/engine/` 补一份。

**实现介绍**

处理顺序是读文件、切块、查询。`read_file_content` 先用 `detect_encoding` 探测编码（`chardet` 可用时用它探测，不可用或读取失败时返回 `utf-8`），再以 `errors='ignore'` 读入全文；`parse_config` 按行切块：空行跳过；去掉首尾空白后以 `#` 开头的行结束上一块、开始新块，块的 `separator` 字段保存这一行原文；第一个 `#` 之前的行不属于任何块，被丢弃；块内每一行生成一个配置项，`level = indent // 1`，即层级等于行首空白字符数，`key` 是第一个空白分隔的词，`value` 是其余部分。两个核心数据结构：配置块 dict 有 `separator`、`start_line`（`#` 行的行号，从 1 起）、`items`、`tree` 四个字段，其中 `tree` 初始化为 `{}` 且 `parse_config` 不填充，层级树由 `extract_config_tree` 按需生成；配置项 dict 有 `raw`、`stripped`、`indent`、`level`、`line_number`、`key`、`value`。

对外函数：

| 函数 | 参数 | 返回值 |
|---|---|---|
| `detect_encoding(file_path)` | 文件路径 | 编码名字符串；没有 `chardet` 或读取失败时为 `utf-8` |
| `read_file_content(file_path)` | 文件路径 | 全文字符串，按探测到的编码读取，解码错误忽略 |
| `get_indent_level(line)` | 一行文本 | 行首空白字符数（`len(line) - len(line.lstrip())`，制表符也按一个字符计） |
| `parse_config(content)` | 配置全文 | 配置块列表 |
| `parse_config_file(file_path)` | 文件路径 | 配置块列表（读文件加解析） |
| `find_config_item(config_groups, search_key, exact_match=False, use_regex=False)` | 配置块列表、查找词、是否精确匹配、是否按正则 | 命中的配置块列表（整块，每块最多出现一次）。`use_regex` 优先：按 `re.IGNORECASE` 编译后在 `key` 与 `stripped` 上 `search`，正则非法时跳过该项不报错；其次 `exact_match`：`key` 或 `stripped` 与查找词完全相等，区分大小写；默认：查找词小写后是 `key` 或 `stripped` 小写后的子串 |
| `find_item_by_path(config_groups, path_parts)` | 配置块列表、路径各段（如 `["interface", "Eth-Trunk1", "description"]`） | `{'group', 'path', 'start_line'}` 列表。第一段用 `startswith` 在任意层级的项上匹配（区分大小写），之后在后续项里顺序找下一段，遇到层级不大于首项层级的项停止；全部段都匹配到才记一条 |
| `check_config_value(config_groups, search_key, expected_value, exact_match=False)` | 配置块列表、查找词、期望值、是否精确匹配 | `(是否匹配, 匹配的配置块列表)`。先用 `find_config_item`（不走正则）找块，再看块内任一项的 `value` 或 `stripped` 是否包含期望值（不区分大小写） |
| `get_group_items(group, level=None)` | 一个配置块、层级 | 配置项列表；给了 `level` 只返回该层级的项 |
| `extract_config_tree(group)` | 一个配置块 | 嵌套 dict：有 `value` 的项是叶子 `key: value`，没有 `value` 的项成为子 dict 并压栈，栈深度按 `while len(stack) > level + 1` 回退；同一父节点下重复的 `key` 后者覆盖前者 |
| `main()` | 命令行参数 | 无；文件不存在、未找到配置项、期望值不匹配时退出码 1 |

**修改时要注意**

- `chardet` 是可选依赖：没有它时一切文件按 UTF-8 读、解码错误忽略，GBK 文本会静默丢字。判断脚本禁止第三方库（格式检查 S5 只检查 `fault_check_*.py`，不检查本文件，所以这个带保护的 import 不会被拦），不要把 `chardet` 改成必需。
- `level` 等于行首空白字符数而不是嵌套深度，`extract_config_tree` 的栈回退按「一个空格一层」计算，与文件头写的格式规则一致；缩进不是一空格一层的文本，树的层级会错位。
- `find_config_item` 返回的是整个配置块，`check_config_value` 只要期望值出现在块内任何一行就算匹配，不限于 `search_key` 所在的那一行。
- `exact_match` 模式与 `find_item_by_path` 区分大小写，与默认模式、正则模式不同；规范要求大小写不敏感，判断脚本应优先用 `use_regex=True` 或默认模式。正则非法时 `use_regex` 模式返回空列表而不报错，调用方拿到空结果要先怀疑正则。
- 第一个 `#` 之前的内容不进任何块；配置文件若没有 `#` 分隔行，`parse_config` 返回空列表。
- 本文件随每个节点交付包分发，改动会影响所有已判断脚本；改完要运行 `cs-scot-migrate/scripts/sync_engine.py` 同步 `engine/` 副本。

### scripts/search_log.py

**功能与作用**

共享解析库之一，解析 `LOG/` 目录下 `display logbuffer`、`display trapbuffer` 的输出：把每条日志拆成时间、设备名、模块、级别、信息名、类型、详情等字段，按关键字、模块、级别、时间区间、日志类型、信息名过滤，并统计各字段的出现次数、端口相关事件与端口 UP、DOWN 次数。调用方是节点交付包里的 `fault_check_*.py`；命令行用法 `python search_log.py <文件路径> [--keyword] [--module] [--info-name] [--severity] [--start-time] [--end-time] [--type l|t] [--count] [--group-by 字段] [--context N] [--port-stats 端口] [--updown 端口] [--json]`。

**实现介绍**

三个模块级正则是解析的依据。`LOG_PATTERN` 匹配形如 `Jul 23 2020 10:13:15-05:00 Switch01 %%01MSTP/4/PORT_STATE_FORWARDING(l):...` 的行，七个捕获组依次是时间戳、设备名、模块、级别、信息名、类型字母、详情；`TRAP_PATTERN` 与之相同，只在详情前多一个可选的 `OID=…;` 前缀不计入详情；`PORT_PATTERN` 从详情里取 `ifName=`、`PortName=`、`Port=`、`Interface=` 之后的值作为端口名。一条日志解析成 dict，字段 `timestamp`、`device`、`module`、`level`（字符串）、`info_name`、`log_type`（`l` 为 logbuffer，`t` 为 trapbuffer）、`detail`、`raw`。`parse_log_file` 先跳过文件头：从第一个含 `Logging buffer configuration` 或 `Trapping buffer configuration` 的行开始解析，都没有就从第一行开始；不匹配正则的行直接丢弃。过滤由 `filter_logs` 完成，内部对每条日志调用 `_log_matches`，按关键字、模块、信息名、级别、类型、时间的顺序短路判断；级别用 `_severity_ok`，时间用 `_time_ok`，时间解析不出的日志放行。

对外函数：

| 函数 | 参数 | 返回值 |
|---|---|---|
| `detect_encoding(file_path)` | 文件路径 | 编码名；同 `search_config.py` |
| `read_file_lines(file_path)` | 文件路径 | 行列表（`readlines()`，保留行尾换行） |
| `parse_log_line(line)` | 一行文本 | 日志 dict；两个正则都不匹配时 `None` |
| `parse_log_file(file_path)` | 文件路径 | 日志 dict 列表 |
| `parse_time(time_str)` | 日志时间字符串 | `datetime`；先按第一个 `-` 截断以去掉时区，再按 `%b %d %Y %H:%M:%S` 解析，失败返回 `None` |
| `filter_logs(logs, keyword=None, module=None, severity=None, start_time=None, end_time=None, log_type=None, info_name=None)` | 日志列表与各过滤条件 | 通过全部条件的日志列表。`keyword` 在 `detail` 与 `info_name` 里做不区分大小写的子串匹配；`module`、`info_name` 为不区分大小写的子串匹配；`severity` 为数字字符串时与 `level` 比较；`log_type` 精确比较；`start_time`、`end_time` 经 `parse_time` 后与日志时间比较 |
| `extract_port_name(log)` | 日志 dict | 详情里第一个匹配 `PORT_PATTERN` 的端口名，没有则 `None` |
| `count_events(logs, field='module')` | 日志列表、字段名 | `collections.Counter`，键是字段值，缺字段记 `unknown` |
| `count_port_events(logs, port_name=None)` | 日志列表、端口名 | `{端口名: [日志列表]}`；给了 `port_name` 只统计该端口 |
| `count_port_updown(logs, port_name=None)` | 日志列表、端口名 | `{端口名: {'up', 'down', 'total', 'up_events', 'down_events'}}`。`info_name` 小写后含 `linkdown`、`link_down`、`down_active` 计入 down，否则含 `linkup`、`link_up`、`down_clear` 或 `up` 计入 up；`total` 是两者之和 |
| `main()` 及 `_print_updown`、`_print_port_stats`、`_print_count`、`_print_matches` | 命令行参数 | 无；文件不存在或没有匹配时退出码 1 |

**修改时要注意**

- `_severity_ok` 里 `sev_level = int(severity) if severity.isdigit() else None` 对 `Critical` 这类名称不会抛 `ValueError`（`isdigit()` 为假时直接得到 `None`），所以 `except ValueError` 分支里的名称对照表实际不会执行，按名称给的级别不起过滤作用；命令行帮助与文件头示例却写着接受名称。
- `parse_log_line` 先试 `LOG_PATTERN`，它的详情组 `(.*)$` 接受任何内容，`TRAP_PATTERN` 能匹配的行都已被它匹配，所以 `TRAP_PATTERN` 实际不会用到，`OID=…;` 前缀会留在 `detail` 里。
- `parse_time` 用 `split('-')[0]` 去时区，时区是 `+08:00` 这类正偏移时字符串里没有 `-`，`strptime` 失败返回 `None`，`_time_ok` 随即放行这条日志；时区信息在任何情况下都不参与计算。
- `count_port_updown` 的 up 分支包含「`info_name` 含 `up` 子串」这一宽泛条件；`total` 是事件数之和，不是状态切换次数。
- 多行日志的续行不匹配正则，`parse_log_file` 会丢掉它们。`chardet` 为可选依赖，情况同 `search_config.py`。

### scripts/search_cmd.py

**功能与作用**

共享解析库之一，面向 `CMD/` 目录下把多条命令回显拼在一个文件里的采集文本：按四种常见排版定位某条 `display` 命令及其回显，列出文件里的全部命令，按关键字过滤结果。调用方是节点交付包里的 `fault_check_*.py`；命令行用法 `python search_cmd.py <文件路径> [<命令匹配模式>] [--keyword 关键字] [--max-lines N] [--json] [--all]`。

**实现介绍**

识别逻辑在 `_match_cmd_at(lines, i, n, cmd_regex, patterns)`：在下标 `i` 的行依次尝试四种排版，命中返回 `(命令文本, 命令所在行下标, 回显起始行下标, 遇分隔线是否停止, 遇提示符是否停止)`，否则 `None`。四种排版及其停止规则：排版 1 是上下两行等号分隔线、中间一行命令，回显从下标 `i+3` 起，遇分隔线或 `<` 开头的提示符行停止；排版 2 是 `===display xxx===` 单行，回显从下一行起，停止规则同排版 1；排版 3 是 `Command: display xxx`，回显只在遇到下一个 `Command:` 时停止；排版 4 是 `<Switch>display xxx` 提示符行，遇提示符行停止。`Command:` 行对所有排版都是停止点。`_read_output(lines, start, n, max_lines, sep_pattern, stop_sep, stop_prompt)` 按这些规则收集最多 `max_lines` 行，返回 `(回显行列表, 停止位置)`。四个正则中 `cmd_standalone_pattern`、`inline_cmd_pattern`、`prompt_pattern` 都要求命令以 `display` 开头，所以排版 1、2、4 只认 `display` 命令，排版 3 接受 `Command:` 之后的任何文本。`cmd_pattern` 以 `re.IGNORECASE` 按正则编译，编译失败时转义成字面字符串，再对提取出的命令文本做 `search`。

对外函数：

| 函数 | 参数 | 返回值 |
|---|---|---|
| `detect_encoding(file_path)`、`read_file_lines(file_path)` | 文件路径 | 同 `search_log.py` |
| `find_cmd_output(lines_or_file, cmd_pattern, max_lines=1000)` | 文件路径（`str` 或 `Path`）或行列表、命令匹配模式、单条回显最大行数 | `{'start_line', 'cmd', 'output', 'output_lines'}` 列表：`start_line` 是命令所在行的行号（从 1 起），`cmd` 是命令文本，`output` 是各回显行去掉行尾 `\n` 后直接拼接的字符串，`output_lines` 是回显行数 |
| `find_all_cmds(file_path)` | 文件路径 | `{'start_line', 'cmd', 'output'}` 列表，没有 `output_lines`。用 `_match_any_cmd_at` 识别排版 1、2、4（不含 `Command:` 排版），命令正则更严格（`display\s+[^\s=]+(?:\s+[^\s=]+)*`），回显不限行数 |
| `filter_by_keyword(results, keyword)` | `find_cmd_output` 的结果、关键字 | `output` 含关键字（不区分大小写）的子集；关键字为空返回原列表 |
| `extract_interface_info(cmd_output)` | `display interface` 的回显文本 | 字典列表，设计意图是每个接口一个字典，含 `name` 与各 `key: value` 字段 |
| `main()` | 命令行参数 | 无；文件不存在、未指定模式、无匹配时退出码 1 |

**修改时要注意**

- `output` 由 `''.join(output_lines)` 生成，而每行已经 `rstrip('\n')`，所以回显各行之间没有换行符（文件若是 `\r\n` 换行则留下 `\r`）。需要逐行处理回显的调用方，应按 `start_line` 与 `output_lines` 回到原始行列表切片，而不是对 `output` 按 `\n` 切分。
- `extract_interface_info` 先对每行 `strip()`，再用「行首是否有空格或制表符」区分接口名行与字段行，因此所有非空行都走接口名分支，`key: value` 的字段解析分支不会执行，返回的每个字典只有 `name`；再加上 `output` 不含换行，传入 `find_cmd_output` 的 `output` 时整段只算一行。使用前要先修正这两处。
- 排版 1、2、4 只识别 `display` 开头的命令，`dir`、`ping` 这类命令只有在 `Command:` 排版里才能找到。`find_all_cmds` 与 `find_cmd_output` 用的命令正则和排版集合不同，两者列出的命令可能不一致。
- 排版 3 的回显只在下一个 `Command:` 处停止，排版混用的文件里一段回显可能一直收到 `max_lines`（默认 1000 行）。
- `_read_output`、`_match_cmd_at` 与 `search_diag_txt.py` 里的同名函数逐字相同，修一处要同时修另一处。

### scripts/search_diag_txt.py

**功能与作用**

共享解析库之一，功能与 `search_cmd.py` 的 `find_cmd_output` 相同：在 txt 或 log 文本里按四种排版定位命令回显并按关键字过滤。区别是只接受行列表而不接受文件路径，没有编码探测，命令行以 UTF-8 读文件并忽略解码错误。SKILL.md、`package_skill.py`、`sync_engine.py` 都把它列为四个共享解析库之一，随包分发与同步副本的规则同其他三个。

**实现介绍**

`find_cmd_output(lines, cmd_pattern, max_lines=1000)` 接受行列表、命令匹配模式与单条回显最大行数，返回 `{'start_line', 'cmd', 'output', 'output_lines'}` 列表，含义同 `search_cmd.py`；函数文档字符串写的返回值是 `[(起始行号, 命令行, 输出内容), ...]` 形式的元组列表，与实际返回的 dict 列表不一致，以代码为准。`_read_output`、`_match_cmd_at` 与 `search_cmd.py` 中同名函数逐字相同。`filter_by_keyword(results, keyword)` 返回 `output` 含关键字（不区分大小写）的子集。`main()` 读文件（`encoding='utf-8', errors='ignore'`）、调用上述两个函数、按 `--json` 输出 JSON 或文本（文本模式每条回显只打印前 500 字符），返回 0；文件不存在或无匹配时 `sys.exit(1)`；模块末尾以 `sys.exit(main())` 结束。

**修改时要注意**

- 文档字符串与返回值不一致（见上）；`output` 同样不含换行符。
- 没有编码探测，GBK 文件在命令行模式下会丢字；作为库使用时文件由调用方自行读取，要自己处理编码。
- 与 `search_cmd.py` 重复的识别逻辑要同步修改；新写的判断脚本直接用 `search_cmd.py` 即可，它接受文件路径且带编码探测。

### SKILL.md

模型执行本技能的操作手册：术语表、五项输入参数、测试数据目录的组织方式与用 zip2csv 归类原始散文件的办法，十一个步骤（从格式校验即检查测试数据目录的组织方式，到打包交付），以及批量翻新存量节点交付包的流程（修复必须逐包手改，以回归基线核对）。步骤 5 与步骤 10 运行 `check_contract.py`，各步输出格式见 `references/templates.md`，脚本写法见 `references/script_contract.md`。

### 判断方法_规范.md

判断方法文档 `判断方法_<节点名>.md` 的编写规范：文件命名（不能含「规范」）、六个必填章节、可选的节点路径与三个选填章节、并行条件与顺序步骤两种模板及正反示例；附录是判断脚本的八条通用要求（v1.2）。模型生成文档与脚本时照它写，业务同事也可直接按它提供文档。`check_contract.py` 的 `MD_REQUIRED` 就是六个必填章节名，多数修法引用附录编号。始终随节点交付包。

### references/script_contract.md

判断脚本的十条格式要求与返回结果的完整 JSON 格式，是 `判断方法_规范.md` 附录面向脚本生成的整理版。模型在步骤 5 写脚本前读它；`check_contract.py` 是其中可机械判断部分的实现，每条要求对应的检查项见本文前面的对照表。

### references/templates.md

流程各步向用户展示的固定文字格式（格式校验、数据探索预检、确认判断方法、数据定位确认、故障诊断、确认诊断结果、方案验证、打包前检查、打包完成），以及随包的 `执行记录.md` 模板和翻新时追加到执行记录末尾的「翻新记录」模板。只有模型读它，没有脚本解析它。
