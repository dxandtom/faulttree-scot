# zip2csv 代码说明

## 技能概述

zip2csv 是一个辅助部件，不在七个技能的分工表里单列，职责只有一件：把现场采集的华为设备原始文本（日志、配置、命令回显）转成后续分析脚本能读的 CSV 表。它在两个阶段被用到。生成阶段，cs-scot-generate 只接受带表头的表，没有表、只有原始文本时需要先用它转表；`package_scenario.py` 搭建场景技能目录时，找得到 zip2csv 就把整个目录复制进场景技能的 `assets/zip2csv/`，之后由 cs-scot-migrate 的 `_refresh_zip2csv` 当作工具链公共文件刷新。执行阶段，cs-scot-execute 的数据入口检查 `check_data.py` 识别到 zip 包或原始日志目录时，指引调用 `assets/zip2csv/scripts/zip2csv.py` 转成三类表；datacom-fault-tree-tester 也用它为节点判断脚本准备测试数据，用的是它归类出的原始目录而不是 CSV。发行包由 cs-scot-generate-optimize 的 `package_skill.py` 把本目录平铺在包根。

输入是三种形态之一：zip 包；已按「网元目录/分类目录」整理好的目录；没有分类目录的原始散文件（单个文件，或含零散 log、txt、zip 的目录）。网元指一台设备，分类目录指网元目录下的 `LOG/`、`CONFIG/`、`DIAG/`、`CMD/` 四个子目录，里面是原始文本；`CMD` 是 `DIAG` 的新名字，两者存放的都是命令回显，处理方式相同。输出有三项：`<输出目录>/csv/<网元>/<LOG|CONFIG|DIAG|CMD>/*.csv`（三类目录、四种表头，列定义见 `references/csv_columns.md`）；全部 CSV 打包成的 `<输入名>_csv.zip`；脚本结束时打印的「原始日志目录建议」。最后一项供执行器 `runner.py` 的 `--data raw=<目录>` 参数使用，S 类节点判断脚本（由 cs-scot-migrate 从节点交付包迁入、直接读原始回显文本的判断脚本）通过它找到原始文件，必须指向网元目录的父目录。

文件之间的配合：`scripts/zip2csv.py` 是唯一入口，负责识别输入形态、解压、发现分类目录、逐文件调用三个解析器、生成派生汇总表并打包；`logbuffer2csv.py`、`config2csv.py`、`parse_diag_to_csv.py` 各负责一类文本，彼此独立，只被 `zip2csv.py` 以 `import` 方式调用；`vendor/ifilter.py` 只在输入没有分类目录时由 `zip2csv.py` 以子进程方式运行，把散文件归类成 `formated_result/<网元>/<CMD|CONFIG|LOG>/` 后再按常规转换。调用关系：

```
zip2csv.py main()
  ├─ _resolve_input          zip → 解压到 <out>/extracted/；目录 → 就地扫描；单个散文件 → 复制到 <out>/raw/
  ├─ find_data_dirs          任意深度收集名为 LOG/CONFIG/DIAG/CMD 的目录
  ├─ _ensure_category_dirs   一个都没有 → run_ifilter → 子进程 vendor/ifilter.py <目录> → formated_result/ → 重扫
  ├─ _report_other_types     KPI/LSDB/MOP 等不转换的类别逐个提示
  ├─ process_log_dirs        每个 .txt/.log → logbuffer2csv.convert_file          → csv/<网元>/LOG/
  ├─ process_config_dirs     每个 .txt/.log → config2csv.parse_config_to_csv      → csv/<网元>/CONFIG/
  ├─ process_diag_dirs       每个 .txt/.log → parse_diag_to_csv.parse_diag_to_csv → csv/<网元>/DIAG/
  ├─ process_cmd_dirs        每个 .txt/.log → parse_diag_to_csv.parse_diag_to_csv → csv/<网元>/CMD/
  ├─ generate_cmd_csv        每张 DIAG 表 → 派生汇总表 → csv/<网元>/CMD/（同名已存在则跳过）
  └─ pack_csv_to_zip         csv/ 下全部 CSV → <out>/<输入名>_csv.zip
```

## 文件清单

| 文件 | 类型 | 作用 |
|---|---|---|
| `SKILL.md` | 技能说明 | 给模型执行的操作手册：输入形态、命令参数、输出目录、异常处理 |
| `references/csv_columns.md` | 参考文档 | 四种 CSV 表头的列定义，下游核对表头时读 |
| `scripts/zip2csv.py` | 脚本 | 唯一入口：识别输入形态、解压、发现分类目录、调度三个解析器、生成派生汇总表、打包 |
| `scripts/logbuffer2csv.py` | 脚本 | 把 `display logbuffer` 一类的日志文本解析成 LOG 表 |
| `scripts/config2csv.py` | 脚本 | 把 `display current-configuration` 一类的配置文本按缩进层级解析成 CONFIG 表 |
| `scripts/parse_diag_to_csv.py` | 脚本 | 把诊断命令回显文本按四种版式切成「命令、回显」记录，产出 DIAG 表与 CMD 目录的转换结果 |
| `vendor/ifilter.py` | 第三方件 | 业务同事维护的归类脚本，原样引入不改；把散文件按网元和类别归入 `formated_result/` |
| `../cs-scot-migrate/engine/zip2csv/`（本目录之外） | 副本 | 本目录的整目录副本，由 `cs-scot-migrate/scripts/sync_engine.py` 生成，供 cs-scot-migrate 单独安装时使用，不手改 |

## 各文件说明

### scripts/zip2csv.py

**功能与作用**：整条转换流水线的入口和调度者，523 行。命令行运行：

```bash
python skills/zip2csv/scripts/zip2csv.py <zip 包、原始数据目录或单个散文件> [output_dir]
python skills/zip2csv/scripts/zip2csv.py <同上> -o <output_dir>
```

它把三种输入形态统一成「某个根目录下任意深度有分类目录」，遍历每个分类目录里的 `.txt`、`.log` 文件，分别交给同目录的三个解析器转换，再基于 DIAG 表生成派生汇总表，最后把全部 CSV 打包成一个 zip。调用方是生成阶段与执行阶段的人或模型，按 cs-scot-generate 的 SKILL.md「输入」节、cs-scot-execute 的 `check_data.py` 与 `references/data_entry.md` 的指引运行。输出目录结构：

```
<output_dir>/
├── extracted/            zip 输入：解压出的原始文件（散文件时 ifilter 的 formated_result/ 也在这里）
├── raw/                  单个散文件输入：暂存区（formated_result/ 在它下面）
├── csv/<网元>/LOG/*.csv
├── csv/<网元>/CONFIG/*.csv
├── csv/<网元>/DIAG/*.csv
├── csv/<网元>/CMD/*.csv  CMD 目录的转换结果，加由 DIAG 表派生的汇总表
└── <输入名>_csv.zip
```

目录输入没有 `extracted/`，就地扫描；散文件目录输入时，ifilter 的 `formated_result/` 写在输入目录内。

**实现介绍**：`main()` 先把标准输出和标准错误改成 utf-8（注释：Windows 的 GBK 控制台下中文输出会乱码），然后按下面的顺序调用各步，步骤编号 `[1/7]` 到 `[7/7]` 随进度打印。

1. `_parse_cli()`：用 `argparse` 解析参数。`src` 必填；输出目录可以用位置参数 `out_pos` 或 `-o/--out` 给，两个都给时打印错误并以退出码 2 结束。注释记录了改用 `argparse` 的原因：直接解析 `sys.argv` 时，`-o <目录>` 曾被当成第二个位置参数，输出静默写进了名字就叫 `-o` 的目录。

2. `_resolve_input(src, out_arg)`：按输入形态定出 `(extract_dir, output_dir, csv_dir, zip_basename)`。`extract_dir` 是后面扫描分类目录的根，`csv_dir` 固定是 `<output_dir>/csv`，`zip_basename` 用来命名输出的 zip。三个分支：
   - 目录：不解压，`extract_dir` 就是输入目录；默认输出目录是 `<目录>_csv`；全局变量 `DEFAULT_NE` 从 `'NE'` 改成目录名，平铺的单网元目录（分类目录直接在根下）就用目录名当网元名。注释说明此前只接受 zip，现场不得不先压包再转，是多余步骤。
   - 单个非 zip 文件（`.log`、`.txt` 等）：复制到 `<output_dir>/raw/` 作为暂存区，`extract_dir` 指向它；默认输出目录是 `<文件路径>_csv`，`DEFAULT_NE` 改成文件名去掉扩展名。注释说明了不直接把文件所在目录交给 ifilter 的原因：ifilter 会把整个父目录连同无关文件全部处理进去。
   - zip 文件：默认输出目录是去掉 `.zip` 后缀的同名目录，`extract_dir` 是 `<output_dir>/extracted`，由 `unzip_file()` 用 `zipfile` 全部解压并打印文件数（`[1/7]`）。
   - 路径不存在：打印错误，退出码 1。

3. `find_data_dirs(extract_dir)`：用 `os.walk` 遍历整个根目录，目录名转大写后等于 `LOG`、`CONFIG`、`DIAG`、`CMD` 之一的，按类别收进字典 `{'LOG': [...], 'CONFIG': [...], 'DIAG': [...], 'CMD': [...]}`。因为是全量遍历、只看目录名，分类目录在任意深度都能被发现：`xxx/xxx/网元/CMD`、`网元/CMD`、根下直接 `CMD` 都可以；同一类别会收到多个目录（多个网元）。打印 `[2/7]` 各类别的目录数。

4. `_ensure_category_dirs(extract_dir, data_dirs)`：决定是否调用 ifilter，并算出「原始日志目录建议」`raw_hint`（初值是 `extract_dir`）。
   - 一个分类目录都没有，判定为原始散文件形态，调用 `run_ifilter(extract_dir)`。返回 `None` 时以退出码 3 结束；成功则把 `extract_dir` 与 `raw_hint` 都换成返回的 `formated_result` 路径，重新 `find_data_dirs` 并打印 `[2c/7]`；仍一个都没有，提示检查数据是否为设备采集回显或日志，退出码 3。
   - 已有分类目录就按分类目录处理，零散的杂文件不再另行归类（注释：两种混在一起时以分类目录为准）。此时用 `_formated_root(p)` 逐级向上找每个分类目录是否位于某个 `formated_result` 之下，找到就打印带 ⚠ 的提醒：检测到已有 ifilter 归类产物，本次直接复用、不重新归类，原始数据有更新时删除 `formated_result` 目录后重新运行。若只有一处 `formated_result` 且全部分类目录都在它下面，`raw_hint` 改成它。

5. `run_ifilter(raw_dir)`：先确认 `VENDOR_IFILTER`（脚本目录上一级的 `vendor/ifilter.py`）存在，不存在就提示改用分类目录形态或已转好的 csv，返回 `None`。然后做风险检查，针对的是 ifilter 自带的清理行为（递归删除输入目录下所有名字以 `_result` 结尾、以 `_merged_temp_` 开头的目录，Linux 下还会波及输入目录旁同名的 `_result` 兄弟目录，原因见 `vendor/ifilter.py` 一节）：

   ```python
   hazards = []
   for r, dirs, _fs in os.walk(raw_dir):
       for d in dirs:
           if d != 'formated_result' and (d.endswith('_result') or d.startswith('_merged_temp_')):
               hazards.append(os.path.join(r, d))
   sib = raw_dir.rstrip('/\\') + '_result'
   if os.path.isdir(sib):
       hazards.append(sib)
   ```

   `hazards` 非空就打印前 10 个并返回 `None`，由用户移走或改名后重新运行，脚本不替用户删除。检查通过后打印 `[2b/7]`，用 `subprocess.run([sys.executable, VENDOR_IFILTER, raw_dir])` 运行 ifilter，要求退出码为 0 且 `<raw_dir>/formated_result` 存在，否则打印原因（常见原因是受控环境禁 pip、第三方库装不上）和替代做法（在能装依赖的机器上先运行 ifilter 得到 `formated_result/`），返回 `None`。注释强调失败要明确报出原因，不能静默转出 0 个文件。

6. `_report_other_types(data_dirs)`：看每个分类目录的父目录下还有哪些兄弟目录，既不是四个类别、也不是 `extracted`、`csv`、`raw`、`formated_result` 的，例如 ifilter 归出的 `KPI`（性能统计文件）、`LSDB`（链路状态数据库回显）、`MOP`（变更操作脚本），逐个点名提示不在三表转换范围、已跳过，避免被当成全部转换完成。

7. 四个 `process_*_dirs(dirs, csv_dir, extract_dir)` 结构相同，分别打印 `[3/7]`、`[4/7]`、`[5/7]`、`[5b/7]`，对应类别没有目录就打印「不存在，跳过」。对每个分类目录：用 `get_ne_name()` 取网元名，输出目录为 `csv_dir/<网元>/<类别>/`；对 `collect_data_files()` 收集到的每个文件，用 `uniq_csv()` 定输出路径后调用解析器。LOG 调 `logbuffer_convert`（即 `logbuffer2csv.convert_file`），CONFIG 调 `config_convert`（`config2csv.parse_config_to_csv`），DIAG 与 CMD 都调 `diag_convert`（`parse_diag_to_csv.parse_diag_to_csv`）。`process_diag_dirs` 返回 DIAG 表路径列表供下一步使用；`process_cmd_dirs` 也返回列表，`main()` 未使用。三个辅助函数：
   - `get_ne_name(data_dir, extract_dir)`：取分类目录相对根目录的路径，倒数第二段就是网元名（`Switch01/DIAG` 与 `a/b/Switch01/DIAG` 都得到 `Switch01`）；分类目录直接在根下时返回 `DEFAULT_NE`。
   - `collect_data_files(dir_path)`：递归收集分类目录内扩展名为 `.txt`、`.log` 的文件，扩展名不区分大小写，返回 `[(绝对路径, 输出基名)]`。子目录里的文件，基名带子路径前缀（`2026-08-11/x.log` 得到 `2026-08-11_x`），避免不同子目录的同名文件互相覆盖；不进入与四个类别同名的子目录，那属于另一类别，已由 `find_data_dirs` 单独发现，再收集会重复计算。注释记录了两个问题：只扫一层会把按日期子目录存放的整批文件静默漏掉；Linux 的 `glob` 区分大小写，`.TXT` 会漏掉。
   - `uniq_csv(ne_output_dir, base, used, label)`：输出路径在 `used` 集合里已存在时，依次改成 `<基名>_2.csv`、`<基名>_3.csv`，并打印带 ⚠ 的「同名防覆盖」提示。`used` 在每个 `process_*` 函数内新建，同一类别下所有源目录共享，所以多个外层目录里出现同名网元的同名文件时会顺延编号而不是覆盖；重新运行时覆盖上次运行的产物不受影响。注释记录的问题：静默覆盖等于丢掉整台设备的数据。

8. `generate_cmd_csv(diag_csvs, csv_dir)`（`[6/7]`）：对每张 DIAG 表，从路径 `csv_dir/<网元>/DIAG/<基名>.csv` 取网元名，在 `csv_dir/<网元>/CMD/` 下写同基名的派生汇总表，列 `NEName`、`CmdName`、`OriginCmd`、`CmdResult`，其中 `OriginCmd` 复制 `CmdName`，`CmdResult` 复制 `ObjContent`，用 `csv.QUOTE_ALL` 给每个字段加引号。目标路径已存在则跳过并打印原因：`[5b/7]` 已先处理了 CMD 目录，存在的是真实转换结果，派生表不写入，否则会把列结构一起换掉。读写前把 `csv.field_size_limit` 提到 10 MB。

9. `pack_csv_to_zip(csv_dir, output_dir, zip_basename)`（`[7/7]`）：把 `csv_dir` 下所有 `.csv` 按相对路径写入 `<output_dir>/<zip_basename>_csv.zip`，打印文件数和大小。

最后打印 `原始日志目录建议(填给 runner 的 --data raw=): <raw_hint>`。退出码：0 正常；1 输入路径不存在；2 输出目录给了两次（`argparse` 自身的参数错误也是 2）；3 ifilter 没有产出，或归类后仍无分类目录。

**修改时要注意**：
- 对单个文件的转换调用（`logbuffer_convert`、`config_convert`、`diag_convert`）外面没有 `try/except`。解析器内部对格式不匹配的行会跳过，但文件级异常会中止整个脚本。`config2csv.py` 和 `parse_diag_to_csv.py` 读文件时用严格的 utf-8，非 utf-8 编码的文件会抛 `UnicodeDecodeError`；经 ifilter 归类的文件是它用 utf-8 重写过的，直接给目录或 zip 时没有这层保证。`SKILL.md` 异常处理表里「某个 txt 解析失败则跳过该文件」与这一点不一致，改动时按代码核对。
- `DEFAULT_NE` 是模块级全局变量，`_resolve_input` 用 `global` 改写，`get_ne_name` 在没有网元层时读取。拆分或调整这两个函数时要保持先写后读的顺序。
- 派生汇总表的「同名已存在则跳过」只看 `os.path.exists`，不区分是本次 CMD 目录的转换结果还是上次运行遗留在同一输出目录的派生表。向同一输出目录重新运行时派生表不会刷新，需要刷新就先删 `csv/`。
- `find_data_dirs` 只按目录名匹配，不排除任何目录。默认输出位置都在扫描范围之外（zip 与单文件形态下 `csv/` 与 `extracted/`、`raw/` 是兄弟目录，目录形态默认输出在 `<目录>_csv`）；用 `-o` 把输出目录放进输入目录内部再重新运行，上一次的 `csv/<网元>/LOG` 等目录会被当成分类目录扫到。
- 风险检查和「复用已有 `formated_result`」都只看目录名。用户数据目录恰好以 `_result` 结尾时会被判为风险而停下，这是有意的；放宽之前先看 `vendor/ifilter.py` 一节的清理范围。
- 打包和同步对文件清单有硬编码：`cs-scot-generate-optimize/scripts/package_skill.py` 的 `_Z2C` 列表固定列出了 `scripts/` 下四个文件名并把它们列为发行包必备文件；`cs-scot-migrate/scripts/sync_engine.py` 把本目录整目录同步到 `engine/zip2csv/`。新增或改名脚本文件要同时改 `_Z2C` 并运行 `sync_engine.py` 刷新副本，`tests/test_engine_sync.py` 与 `tests/test_refresh_policy.py` 会核对。
- 步骤编号与提示语是 `SKILL.md`「完整例子」描述预期输出的依据，改提示语要同步改文档。

### scripts/logbuffer2csv.py

**功能与作用**：把设备日志缓存文本（`display logbuffer` 的回显，现场文件名常为 `<网元>_logbuffer.txt` 或 `log.log`）解析成 LOG 表，表头见 `references/csv_columns.md`。被 `zip2csv.py` 以 `from logbuffer2csv import convert_file as logbuffer_convert` 引入，对 `LOG/` 目录下每个文件调用一次 `convert_file(input_path, output_path)`。输入一个文本文件，输出一个 CSV。文件头部的文档字符串记录了 v2 相对 v1 的五处修复，是几个常量取值的依据。

**实现介绍**：模块顶部定义四个正则和一个常量。

- `LINE_PATTERN`：匹配一条日志的起始行，形如 `Jul 20 2020 19:12:19-05:00 Switch02 %%01NTP/2/NTP_TRAP_SYNCHRONIZE_STATUS(l):CID=...`，依次捕获日期、时间、时区、网元名、模块、级别、摘要、剩余内容。摘要后的 `(l)`、`(t)` 这类括号标记是可选的，所以 `xxx_active:`、`xxx_clear:` 这种没有标记的写法也能匹配。
- `NEW_ENTRY_PATTERN`：只判断一行是否是新条目的开头（日期、时间、时区、网元名、`%%` 前缀），用于多行合并。
- `PARAM_PATTERN`：从内容末尾提取最后一个含 `=` 的括号组，允许括号后跟一个句号。
- `KV_PATTERN`：把括号内的内容切成多个 `key=value`，键允许字母、数字、连字符、空格（如 `M-LAG ID`），值允许多词，用正则前瞻定位下一个键，避免多词的值被截断。
- `MAX_PARA = 30`：最多提取 30 组参数。`HEADERS` 由固定七列 `NEName`、`Time`、`Module`、`Level`、`Summary`、`Content`、`Log`，加上 `Para1`、`Para1Name` 到 `Para30`、`Para30Name` 组成。

`convert_file(input_path, output_path, skip_lines=5)` 的处理顺序：
1. 以 utf-8 读入全部行，解码错误用替换字符代替（`errors='replace'`），含非法字节的文件不会中断。
2. 跳过开头 `skip_lines` 行，默认 5（文档字符串：v1 的默认值更大，曾多跳过有效数据行）。
3. `merge_multiline(lines)`：匹配 `NEW_ENTRY_PATTERN` 的行作为新条目；不匹配的非空行用空格连接到上一条末尾；第一条之前的非空行丢弃。
4. 对每条合并后的行调用 `parse_line(line)`：用 `LINE_PATTERN` 匹配，失败返回 `None` 并被跳过；成功则由 `parse_time()` 把 `Jul 20 2020` 加 `19:12:19` 转成 `2020/07/20 19:12:19`，由 `extract_params(content)` 得到参数字典，组装成一行，`Log` 列保存整条原文，缺失的参数位留空。
5. 用 `csv.DictWriter` 按 `HEADERS` 写出，编码 `utf-8-sig`（带字节顺序标记的 utf-8，Excel 能直接打开）；写之前把字段里的换行替换成字面量 `\n`，删除回车，保证 Excel 按一行读。
6. 打印转换条数。

模块里还有五条样例日志 `SAMPLE_LINE_1` 到 `SAMPLE_LINE_5` 和 `test_single_line()`、`_run_test()`，用于手工验证解析规则，正常运行时不执行。`if __name__ == '__main__'` 分支固定写了作者本机的 Windows 路径，只是开发时的调试入口。

**修改时要注意**：
- `parse_time` 用 `datetime.strptime(..., "%b %d %Y %H:%M:%S")`，要求英文月份缩写；格式不满足会抛 `ValueError`，`parse_line` 没有捕获。`LINE_PATTERN` 只限定了 `\w{3}\s+\d{1,2}\s+\d{4}` 的形状，没限定是合法月份名。
- `MAX_PARA` 是 30，`references/csv_columns.md` 的 LOG 表列到 `Para20`，两处不一致，改任一处时同步另一处。下游按列名取值，不受列数影响。
- `merge_multiline` 把所有不像新条目的行并进上一条，文件尾部的无关文本（例如命令提示符）会并入最后一条记录的 `Content` 和 `Log`。
- `skip_lines` 是固定行数，不按内容识别头部。头部行本身不匹配 `NEW_ENTRY_PATTERN`、又位于第一条之前，会被 `merge_multiline` 丢弃，所以这个值偏小一般不影响结果，偏大会丢失记录。

### scripts/config2csv.py

**功能与作用**：把华为设备的配置文本（`display current-configuration` 的回显，以单独一行 `#` 分段、以缩进表示层级）解析成 CONFIG 表，表头见 `references/csv_columns.md`。被 `zip2csv.py` 以 `from config2csv import parse_config_to_csv as config_convert` 引入，对 `CONFIG/` 目录下每个文件调用 `parse_config_to_csv(input_file, output_file)`。

**实现介绍**：只有一个函数 `parse_config_to_csv(input_file, output_file)`。
1. 以严格 utf-8 读入整个文件，按 `\n` 切行。
2. 网元名 `ne_name`：从上到下找第一行含 `<...>` 的文本，取尖括号内的内容（ifilter 抽出的配置文件第一行就是 `<网元名> display current-configuration`）；没有则为空字符串。`CfgType` 固定写 `RUNNING`。
3. 分段：以只含 `#`（允许尾随空白）的行为分隔符切成若干段，第一段视为文件头（含网元名那一行）跳过。
4. 每段先过滤掉空行、以 `!` 开头的注释行和单独的 `#` 行，然后逐行按行首空格数定层级：`level = 前导空格数 + 1`，最多到 5。用列表 `current_path` 记录当前各层的祖先行，遇到第 n 层的行就把它截到前 n-1 项再追加本行。每一行输出一条记录：`NEName`、`CfgType`，再对 1 到 5 层各写两列，`Module`、`L2Module` 到 `L5Module` 是该层那一行的第一个词，`level1` 到 `level5` 是该层那一行去掉前导空格后的完整文本，路径不够深的层留空。
5. 用 `csv.writer` 写出，编码 `utf-8-sig`，打印输出路径和行数。

**修改时要注意**：
- 层级完全由前导空格数决定，一个空格一层，这是华为配置回显的缩进习惯。`lstrip(' ')` 只去空格，tab 缩进的行会被算作第一层且 tab 留在文本里；两个空格缩进的行会被判成第三层。
- 读文件是严格 utf-8，没有 `errors` 参数，非 utf-8 文件会抛异常并中止 `zip2csv.py`。
- `cs-scot-generate/scripts/custom_lib.py` 的 `_fc_rebuild_config` 是本函数的逆变换：每行取最深的非空 `levelN` 还原成一行，缩进 n-1 个空格，`level1` 换块时补 `#`，用于从 CONFIG 表重建配置文本给 S 类节点判断脚本。改列名或层级规则要同步改那里。

### scripts/parse_diag_to_csv.py

**功能与作用**：把一次采集里多条 `display` 命令的回显文本切成「命令、回显」记录，输出三列 `NEName`、`CmdName`、`ObjContent`。被 `zip2csv.py` 以 `from parse_diag_to_csv import parse_diag_to_csv as diag_convert` 引入，`DIAG/` 与 `CMD/` 两类目录下的文件都用它转换，输出分别放在 `csv/<网元>/DIAG/` 与 `csv/<网元>/CMD/`。头部注释说明解析逻辑参考了 `parse_umc_data.py` 中 `parse_cmd_segment` 的四种格式，该文件不在本仓库。

**实现介绍**：模块顶部定义版式识别用的正则与过滤规则：
- `sep_pattern`：整行等号的分隔线。
- `cmd_standalone_pattern`：单独一行的 `display xxx`（允许前导空格，不含等号）。
- `inline_cmd_pattern`：等号夹着命令的一行，如 `=====display xxx=====`。
- `prompt_pattern`：命令提示符格式 `<SwitchName>display xxx`，也接受 `dis` 这类缩写。
- `huawei_pattern`：含 `HUAWEI` 字样的等号分隔线。
- `skip_patterns` 与 `should_skip(cmd_name)`：不输出的命令，包括精确的 `display current-configuration`，以及 `display logbuffer`、`display trapbuffer`、`display logfile`、`more` 开头的命令；这些内容由配置表、日志表承载或不属于诊断回显。
- `FIELD_LIMIT = 100000`：`ObjContent` 单字段的最大字节数，略小于 `csv` 模块默认的 131072。

`parse_diag_to_csv(input_file, output_file, ne_name=None)` 的处理顺序：
1. `ne_name` 没传时取输入文件名去掉扩展名。`zip2csv.py` 调用时不传，所以 DIAG 表与 CMD 目录转换结果的 `NEName` 列是原始文件名（去掉扩展名），不是从目录推出的网元名；目录名只决定 CSV 放在哪个网元目录下。
2. 严格 utf-8 读入，按行切分。
3. 依次用四种方法扫全文，每种方法独立遍历所有行，返回 `[(命令, 回显)]`：
   - `_parse_method1`：连续三行「分隔线、命令、分隔线」，回显从第四行起。
   - `_parse_method2`：`=====display xxx=====` 一行，回显从下一行起。
   - `_parse_method3`：`<网元>display xxx` 一行，回显从下一行起。
   - `_parse_method4`：`HUAWEI` 分隔线之后最多 5 行内第一个非空、非分隔线的行当命令（匹配 `cmd_standalone_pattern` 则取命令部分，否则整行），回显由 `_huawei_output()` 收集并去掉空行与各种分隔线。
   前三种方法的回显由 `_collect_output(lines, start, n)` 收集：从起始行向下，直到遇到任意一种段落起始行为止。段落起始行由 `is_section_start()` 判定：分隔线、内嵌命令行、提示符行、`HUAWEI` 分隔线，或以 `display ` 开头的行。
4. 每条结果交给 `_add_result(results, seen, ne_name, cmd_name, obj_content)`：先按 `should_skip` 过滤；回显里 `Sequence    :` 出现超过一次（多条告警）时，按空行或三个以上等号拆成多条记录；以 `(CmdName, 回显前 60 字符)` 为去重键，四种方法重复识别到的同一段只保留一条。
5. `_split_oversized(row)`：`ObjContent` 超过 `FIELD_LIMIT` 字节的记录，按空行、`\r\r`、三个以上的 `-`、`=`、`+`、`#` 拆成多条，单块仍超限的再按换行逐行拆，每条拆出的记录 `NEName`、`CmdName` 相同。
6. `_write_csv(output_file, final_results)`：把 `csv.field_size_limit` 提到 10 MB，建输出目录，用 `csv.DictWriter` 以 `QUOTE_ALL`、`utf-8-sig` 写出三列。然后打印记录数，返回输出路径。

**修改时要注意**：
- 去重键只看命令名加回显前 60 字符，同一命令两次执行且开头 60 字符相同的回显会合并成一条，属于有意的取舍，改之前确认下游是否依赖。
- `is_section_start` 把任何以 `display ` 开头的行都当作新段落开始，回显正文里出现这样的行会截断该段。
- 拆行后的多条记录依靠「连续同名 `CmdName`」拼回：`cs-scot-generate/scripts/custom_lib.py` 的 `_fc_rebuild_cmd` 把连续同名命令的记录拼回一段再给 S 类节点判断脚本。改拆分规则或输出顺序要同步看那里。
- 读文件是严格 utf-8，异常会中止 `zip2csv.py`。`if __name__ == '__main__'` 分支固定写了作者本机路径，仅供调试。

### vendor/ifilter.py

**功能与作用**：业务同事维护的归类脚本，3503 行，原样引入、不改内部逻辑。作用是把没有分类目录的华为设备采集文件（日志、命令回显、配置、KPI 等，可含 zip 与嵌套 zip）按网元和类别归类成 `formated_result/<网元>/<CMD|CONFIG|LOG|DIAG|KPI|LSDB|MOP|INFO>/`，并从命令回显里把 `display current-configuration`、`display logbuffer`、`display trapbuffer` 等的回显抽成单独的 CONFIG、LOG 文件。

**实现介绍**：没有 `main()` 函数，也没有 `if __name__ == '__main__'` 保护，入口是模块末尾的脚本代码：`check_and_install_deps()` 用 pip 补装缺失的 `chardet`、`pandas`（`openpyxl` 可选，缺了不生成汇总文件）；读 `sys.argv` 里的路径（可多个，另有 `--rfc`、`--clear-cache` 开关）。对每个路径：输入是文件时根目录取其所在目录，是目录时就是该目录；清理旧产物；把全部文件解压或复制到 `_merged_temp_<时间戳>/`；`compress()` 多线程地对每个文件运行 `get_file_info()` 判定网元和类别，复制到 `<临时目录>_result/<网元>/<类别>/`（同名先比大小再比 MD5 去重）；最后移到根目录下并改名为 `formated_result/`，`save_csv()` 写 `formated_result_summary.xlsx` 与根目录下的 `formated_result.json`，删除临时目录。改写文件的操作都发生在临时目录里的副本上。

副作用是清理旧产物：递归删除根目录下所有名为 `formated_result`、以 `_result` 结尾、以 `_merged_temp_` 开头的目录及 `formated_result.json` 等文件；另有 `path.join(root_dir, root_dir.split('\\')[-1] + '_result')` 一行，Linux 路径没有反斜杠，`split` 的结果是整个绝对路径，`path.join` 直接返回它，于是删除的是根目录旁的同名 `_result` 兄弟目录。

**修改时要注意**：不改本文件。`zip2csv.py` 以 `subprocess.run([sys.executable, VENDOR_IFILTER, raw_dir])` 调用，传入归类对象的根目录，成功后期望 `raw_dir/formated_result` 存在；因为传文件时它会处理整个父目录，单个散文件先复制到 `raw/` 再调用。更新版本时整文件替换并运行 `sync_engine.py` 刷新副本。

### SKILL.md

给模型执行的操作手册，读者是调用本技能的模型与人；cs-scot-generate、cs-scot-execute、datacom-fault-tree-tester 的手册都把原始文本的转换指向本技能。内容依次是任务、术语、输入的三种形态与处理、ifilter 归类的五点须知、命令与参数、目录结构的容忍度、输出目录、执行步骤、异常处理表和完整例子。所述行为对应 `scripts/zip2csv.py`，步骤编号与脚本打印一致；异常处理表里「某个 txt 解析失败则跳过」与代码的文件级行为有出入，见 `scripts/zip2csv.py` 的注意事项。

### references/csv_columns.md

四种 CSV 表头的列定义：LOG 表、CONFIG 表、DIAG 表（与 CMD 目录的转换结果同表头）、由 DIAG 表派生的命令汇总表，并列出汇总表四列各来自 DIAG 表的哪一列。只有下游要核对表头时才读。列名分别由 `logbuffer2csv.py` 的 `HEADERS`、`config2csv.py` 的 `headers`、`parse_diag_to_csv.py` 的 `_write_csv` 与 `zip2csv.py` 的 `cmd_headers` 决定，改列名要同步改这里。LOG 表在文档里列到 `Para20`，代码 `MAX_PARA` 是 30，实际表头到 `Para30`。
