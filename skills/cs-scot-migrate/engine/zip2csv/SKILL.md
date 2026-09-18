---
name: zip2csv
description: 把华为设备的诊断采集数据转换成结构化 CSV。输入可以是 zip 包、已按网元和类别分好目录的原始数据目录，或者一堆散的 log/txt/rar 文件；散文件会先由内置的 ifilter 归类成"网元/CMD、CONFIG、LOG"目录结构，再把每个 txt 转成表，并生成命令回显的汇总表。用户提到"解压 zip 转 csv""批量转换诊断文件""处理 zip 包""原始 log 解析"时使用。
disable-model-invocation: false
---

# zip2csv：诊断数据批量转换

## 任务

把现场采集的原始数据转成后续解析脚本能读的三类表：日志表（LOG）、配置表（CONFIG）、命令回显表（DIAG 或 CMD）。整条流水线由一个脚本完成，不需要人工介入：

1. 识别输入形态：没有分类目录的散文件先调用 `vendor/ifilter.py` 归类；已有分类目录的直接进入下一步。
2. zip 输入解压到本地目录；目录输入就地扫描，不解压。
3. 处理 `LOG/`：遍历全部 `.txt` 和 `.log` 文件（大小写不敏感，现场常见 log.log），用 `logbuffer2csv.convert_file()` 转成 CSV。
4. 处理 `CONFIG/`：遍历全部 `.txt`，用 `config2csv.parse_config_to_csv()` 转成 CSV。
5. 处理 `DIAG/`：遍历全部 `.txt`，用 `parse_diag_to_csv.parse_diag_to_csv()` 转成 CSV。
6. 处理 `CMD/`：CMD 是 DIAG 的新名字，两个名字都认，处理方式相同；输出到 `csv/<网元>/CMD/`，列为 NEName、CmdName、ObjContent（下游把 ObjContent 与 CmdResult 视为同义）。
7. 生成命令汇总表：基于 DIAG 转出的 CSV，在 `csv/<网元>/CMD/` 下生成汇总 CSV，列为 NEName、CmdName、OriginCmd、CmdResult。
8. 把全部 CSV 打成一个 zip。

## 术语

| 术语 | 含义 |
|---|---|
| 分类目录 | 网元目录下的 `LOG/`、`CONFIG/`、`DIAG/`、`CMD/` 子目录，里面是命令回显的原始文本 |
| 原始散文件 | 单个或一堆 log、txt、rar、混杂的 zip，没有分类目录 |
| ifilter | 同事维护的归类脚本，放在 `vendor/` 下原样引入，不改内部逻辑；把散文件归类成 `formated_result/<网元>/<CMD、CONFIG、LOG>/` |
| 网元 | 一台设备；输出按网元分目录 |
| 原始日志目录 | S 类节点脚本直接读的原始回显所在目录，执行器用 `--data raw=<目录>` 接收；脚本结束时会打印建议填哪个路径 |

## 输入的三种形态

数据到手先看形态，脚本会自动识别并从对应环节接入：

| 形态 | 特征 | 处理 |
|---|---|---|
| 原始散文件 | 单个或一堆 log/txt、rar、混杂 zip，没有分类目录 | 脚本自动调用 ifilter 归类出 `formated_result/<网元>/<CMD、CONFIG、LOG>/`，再按分类目录处理 |
| 分类目录 | 任意深度存在 LOG、CONFIG、DIAG、CMD 目录，里面是 txt 或 log 文件 | 本技能常规转换成三类表 |
| 已是 csv | 表头带 NEName、CmdName 等列 | 不需要本技能，直接交给绑定或执行器 |

关于 ifilter 归类，有五点要知道：

- 混装输入（既有分类目录又有散文件）以分类目录为准，散文件不再另行归类。
- ifilter 依赖第三方库（rarfile、chardet、olefile、oletools、pandas），首次运行会自动 pip 安装。受控环境装不上时，散文件形态走不了；脚本会明确报错并给出替代做法（在能装依赖的机器上先跑 ifilter）。另外两种形态不受影响。
- 走过 ifilter 后，原始日志目录应指向 `formated_result/`，脚本结束时会打印建议值。
- 副作用提醒：ifilter 会在输入目录里写 `formated_result/`，并清删自己的旧产物；这个清理还会波及任何名字以 `_result` 结尾的目录，包括输入目录的同名 `_result` 兄弟目录。zip2csv 在调用前已做避让检查，发现可能误删用户目录时会停下并点名，按提示移走或改名后重跑。
- 对同一目录重跑时，已有的 `formated_result/` 直接复用，不重新归类（脚本会打 ⚠ 提醒）。原始数据有更新时，先删掉 `formated_result/` 再跑。

## 命令与参数

```bash
python "skills/zip2csv/scripts/zip2csv.py" <zip或目录> [output_dir]
python "skills/zip2csv/scripts/zip2csv.py" <zip或目录> -o <output_dir>
```

| 参数 | 说明 |
|---|---|
| `<zip或目录>`（必填） | zip 包路径，或原始数据目录 |
| `output_dir`（可选） | 输出目录。zip 输入默认是去掉 `.zip` 后缀的同名目录；目录输入默认是 `<目录>_csv` |

对目录结构的容忍度：

- 分类目录在任意深度都能被发现：`xxx/xxx/网元/CMD`、`网元/CMD`、根下直接 `CMD` 都可以。
- 网元名取分类目录的父目录名；没有网元层时取输入目录名，zip 平铺时取 `NE`。
- 分类目录内部再嵌子目录（如按日期存放）的文件会递归收集，输出文件名带子路径前缀，避免重名。
- 多个外层目录里出现同名网元的同名文件时，输出自动加 `_2`、`_3` 顺延，并打 ⚠ 提醒，不静默覆盖。

## 输出目录

```
<output_dir>/
├── extracted/          # zip 输入：解压出的原始文件（散文件时 ifilter 的 formated_result/ 也在这里）
├── raw/                # 单个散文件输入：暂存区（formated_result/ 在它下面）
├── csv/                # 转换产物，按网元分目录
│   └── <网元>/
│       ├── LOG/*.csv
│       ├── CONFIG/*.csv
│       ├── DIAG/*.csv
│       └── CMD/*.csv   # CMD 目录的转换结果，加 DIAG 派生的汇总表
└── <输入名>_csv.zip     # 全部 csv 打包
```

目录输入时没有 `extracted/`（就地扫描）；散文件目录输入时，ifilter 的 `formated_result/` 落在输入目录内。脚本结束时打印"原始日志目录建议"，执行器的 `--data raw=` 就填它。

各类 CSV 的列定义见 `references/csv_columns.md`；只有下游要核对表头时才需要读。

## 执行步骤

1. 确认用户给了 zip 文件或原始数据目录的路径，且路径存在、可读，Python 环境可用。
2. 运行上面的命令。
3. 检查输出，向用户汇报：解压了多少文件；LOG、CONFIG、DIAG（或 CMD）各转换了多少个文件；汇总表生成了多少条记录；脚本打印的原始日志目录建议值。
4. 有错误时定位原因并报告。

## 异常处理

| 情况 | 处理 |
|---|---|
| 输入路径不存在 | 提示用户检查路径 |
| 某个分类目录不存在 | 对应步骤自动跳过，不影响其他步骤 |
| 某个 txt 解析失败 | 跳过该文件，继续处理其他文件 |
| 编码 | 输出用 `utf-8-sig` 编码，Excel 能直接打开 |
| ifilter 装不上依赖或没有产出 | 脚本明确报错并给替代做法；把报错原样给用户，不要手工归类 |
| ifilter 的清理会波及用户目录 | 脚本停下并点名，按提示移走或改名后重跑 |

## 完整例子

用户说"把 D:\采集\站点A.zip 转成 csv"。

```bash
python "skills/zip2csv/scripts/zip2csv.py" "D:/采集/站点A.zip" -o "D:/采集/站点A_csv"
```

预期输出：`[1/7]` 解压完成并报文件数；`[2/7]` 扫描到各类目录的数量；`[3/7]` 到 `[6/7]` 逐类报转换个数；`[7/7]` 打包完成；最后一行"原始日志目录建议"给出 `D:/采集/站点A_csv/extracted`。向用户汇报这些数字，并告知转出的 csv 在 `D:/采集/站点A_csv/csv/<网元>/` 下。
