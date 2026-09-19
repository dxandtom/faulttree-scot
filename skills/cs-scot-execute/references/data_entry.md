# 数据入口：三种数据形态与转换

场景技能收到用户的数据后，第一件事是弄清数据长什么样，再决定怎么提供给执行器。本文说明三种形态各怎么处理、什么情况停下问用户、什么情况继续跑。正文 SKILL.md 只给顺序，数据入口检查报出结果后按本文处理。

## 1. 先跑数据入口检查

```bash
python "<本目录>/scripts/check_data.py" <数据路径>
```

检查脚本判定数据形态（原始采集的网元目录在哪一层、哪些是数据表、哪张只是采集清单），并给出建议的传入方式，照抄即可。

- 退出码 0：能识别形态，按它打印的建议传入方式继续。
- 退出码 2：结构无法识别，或只有采集清单。把报告原样给用户，让用户改目录或确认，不要强行继续。唯一的例外：报告自己指路"疑似原始散文件，先跑 zip2csv"时，照它说的转换，转完重跑检查，仍是 2 才停。

两起真实事故说明为什么必须先检查：`--data raw=` 传错了一层目录，全程"数据不足"；采集清单 xlsx 被当成数据表传了进去，42 个判据一起报"找不到列"。

## 2. 三种形态

### 已解析的表

csv 或 xlsx，表头带 NEName、CmdName、Summary、level1 这类列。按正文第 2 条匹配指标后直接传给执行器。检查脚本按表头认表：CmdName、ObjContent、CmdResult 认作 cmd，Summary、Log、Level、Content 认作 log，level1、CfgType、L2Module 认作 config；EAV 窄表（DataItem、DataValue，见 `cs-scot-generate/references/datacom_log_adapter.md`）不在它的识别清单里，报告不会把它列为数据表，这类文件按文件名与指标名匹配后直接传入。

### 零散的原始文件

单个或一堆 log/txt、混杂的 zip，没有 CONFIG、CMD、LOG 分类目录（rar 需先手工解压）。检查退出码 2 且连分类目录都找不到，就是这种。同样交给 zip2csv：它会调用内置的 ifilter 先归类成 `formated_result/<网元>/<CMD|CONFIG|LOG>/`，再转成三张表。此时 `--data raw=` 指向 `formated_result/`（zip2csv 结束时会打印建议值）。ifilter 只依赖 chardet 和 pandas，装不上时 zip2csv 会明确报错并给替代做法，把报错原样给用户，不要强行继续。

### 原始日志目录（txt/log）

特征：网元目录下有 `CONFIG/`、`CMD/`（或 `DIAG/`）、`LOG/` 子目录，里面是命令回显的 `.txt`；`.log` 也算原始文本（如 log.log）。先转换再传入，三步都不能省：

1. **转成三张表**，用本技能 `assets/` 里内置的转换脚本：

   ```bash
   python "<本目录>/assets/zip2csv/scripts/zip2csv.py" <zip或目录> -o <输出目录>
   ```

   zip 和目录都可以直接作为输入，目录不用先压成 zip（只有旧版转换脚本只接受 zip，那时才需要 `shutil.make_archive`）。转出的 csv 在 `<输出>/csv/<网元>/LOG|CONFIG|DIAG|CMD/*.csv`，按子目录名对应指标（log、config、cmd；DIAG 是 CMD 目录的旧名字，执行器把 `diag` 和 `cmd` 当同一类数据的两代源名互认：计划要 `diag` 而只给了 `cmd`（或反之）时，把同一份文件登记到另一个名字下；两个名字都给了就各归各）。多个网元产出多份同类 csv 时，只合并表头完全相同的文件：保留第一份的表头，其余逐文件追加成一份再传入。`CMD/` 下可能有两种表头：CMD 目录的转换结果（NEName、CmdName、ObjContent）和由 DIAG 目录派生的汇总表（NEName、CmdName、OriginCmd、CmdResult），两种都能作为 cmd 指标的数据（分析脚本把 ObjContent 与 CmdResult 视为同义列），但不能追加进同一个文件。另外要分清：有的场景（数通诊断文件包，见 `cs-scot-generate/references/datacom_log_adapter.md`）的 `diag` 指标指的是平台导出的 EAV 窄表 `*_CmdResult.csv`（DataItem、DataValue、Unit），与 zip2csv 的 DIAG 表结构完全不同；哪种表配哪个指标名由生成时的绑定决定（分析脚本里 `filter.column` 为 `DataItem` 的就是 EAV 表），两种表不能互换，计划同时需要 `cmd` 与 `diag` 时两份都要传，不能只传一份靠别名补上。

2. **无论转换成功与否，把原始目录用 `--data raw=<原始目录>` 一并传给执行器。** 原始目录一律指网元目录的父目录：zip 输入时是 zip2csv 输出里的 `extracted/`，经 ifilter 归类的散文件是 `formated_result/`，目录输入时就是那个目录本身（前提是它下面直接是网元目录；检查脚本会打印该传哪一层）。S 类脚本（`scripts/custom/` 下的节点判断脚本）靠它直接读原始回显；不传，这类节点只能从三张表重建输入，数据不齐时会报"数据不足，脚本未运行"。

3. **转换失败、转出 0 个文件、找不到转换脚本时，明说，但不是停手。** 仍然带着 `--data raw=<原始目录>` 把诊断跑起来：S 类脚本直接读原始文件照常输出事实，执行器对"只有 raw"专门放行；转不出的数据源如实进缺口汇总，结论只代表能看到的部分。只有检查连结构都无法识别（退出码 2）才停下问用户。

## 3. 两条禁令

- 不要把原始 txt 文件的路径当作某个指标的表提供。txt 不是表，解析出来是没有预期列的假表，全部判据会错报"数据不足"。
- 数据转换只用本技能 `assets/zip2csv/` 里的脚本。不要去环境里寻找或调用其他技能做转换；找不到脚本就把检查报告给用户。

## 4. 缺口怎么向用户说

带 `--data raw=` 运行时，某一类数据为空，意味着采集数据里不含该类内容。CMD、CONFIG、LOG 的分类是转换器的内部概念，用户上传的是完整的原始包，既不感知分类，多半也补不出来。缺口汇总按"知会"的方式报（执行器已在 run.json 里记 `missing_kind`），不要对这类用户说"缺数据源，请补传"。只有用户直接给了三张表却少了一张，才是"缺数据源，建议补传"。
