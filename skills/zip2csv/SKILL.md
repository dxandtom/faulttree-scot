---
name: zip2csv
description: 把华为设备诊断数据(原始散包 log/txt/rar、ZIP 包或分类目录)批量转换为结构化 CSV——原始散包自动先经内置 ifilter 归类成网元/CMD|CONFIG|LOG 结构,再把各目录下的 txt 逐个转表,并生成 cmd 汇总 CSV。当用户提到"解压zip转csv"、"zip转csv"、"批量转换诊断文件"、"处理zip包"、"原始log解析"时使用此技能。
disable-model-invocation: false
---

# ZIP2CSV — 华为交换机诊断数据批量转换技能

## 功能概述

接收一个 ZIP 压缩包**或原始数据目录**（目录直接给，无需先压 zip）**或单个/一堆原始散文件**（log/txt/rar），自动完成以下流水线：

0. **形态识别**：没有 LOG/CONFIG/DIAG/CMD 分类目录的原始散包 → 先调 `vendor/ifilter.py` 归类（见下节），有分类目录则直接进入下一步
1. **解压 ZIP** 到本地目录（目录输入则跳过解压、就地扫描）
2. **LOG/** — 遍历所有 `.txt`/`.log` 文件（现场常见 log.log,两种都认,大小写不敏),调用 `logbuffer2csv.convert_file()` 转换为 CSV
   (命名迁移:**DIAG 更名 CMD**——两名并存兼容,新包 CMD、老包 DIAG 都认,同款回显解析)
3. **CONFIG/** — 遍历所有 `.txt` 文件，调用 `config2csv.parse_config_to_csv()` 转换为 CSV
4. **DIAG/** — 遍历所有 `.txt` 文件，调用 `parse_diag_to_csv.parse_diag_to_csv()` 转换为 CSV
5. **CMD/** — DIAG 的新名字,同款处理(parse_diag_to_csv),输出到 `csv/<网元>/CMD/`(列 NEName/CmdName/ObjContent,下游认 ObjContent↔CmdResult 同义)
6. **cmd 汇总** — 基于 DIAG 输出的 CSV，在 `csv/<网元>/CMD/` 下生成汇总 CSV(列 NEName/CmdName/OriginCmd/CmdResult)

## 进料三形态自动识别（数据解析全链路 ①→②→③）

数据到手先分形态,本技能自动识别并从对应环节接入,**无论进来的是哪一段都能处理**:

| 形态 | 特征 | 处理 |
|---|---|---|
| ① 原始散包 | 单个/一堆 log/txt、rar、混杂 zip,**没有** LOG/CONFIG/DIAG/CMD 分类目录 | 脚本自动调 `vendor/ifilter.py` 归类出 `formated_result/<网元>/<CMD\|CONFIG\|LOG>/`,再走② |
| ② 分类目录 | 任意深度存在 LOG/CONFIG/DIAG/CMD 目录,内是裸 txt/log | 本技能常规转换为三表 csv |
| ③ 已是 csv | 表头带 NEName/CmdName 等列 | **不需要本技能**,直接喂绑定/runner |

- 混装(既有分类目录又有散文件)以分类目录为准,散文件不再另行归类;
- `ifilter.py` 是同事维护的**原样引入件(vendor,不改内部逻辑)**,依赖三方件
  (rarfile/chardet/olefile/oletools/pandas),首跑会自动 pip 安装——**受控环境装不上时
  形态①走不了**(脚本会响亮报错并给出替代做法:在能装依赖的机器上先跑 ifilter),②③不受影响;
- 走过 ifilter 后,S 类脚本直读的 raw 通道应指 `formated_result/`(脚本结束时会打印建议值);
- **副作用告警**:ifilter 会在**输入目录里**写 `formated_result/` 并清删自己的旧产物,其清场
  还会波及任何名字以 `_result` 结尾的目录(含输入目录的同名 `_result` 兄弟目录)——zip2csv
  调用前已做避让检查,撞上疑似用户目录会**停手并点名**,按提示移走/改名后重跑;
- 对同一目录**重跑**时,已有的 `formated_result/` 会被直接复用(不重新归类)——原始数据有更新,
  先删掉 `formated_result/` 再跑(脚本发现复用会打 ⚠ 提醒)。

## 执行前检查

1. 确认用户提供了 ZIP 文件或原始数据目录的路径
2. 确认该路径存在且可读
3. 确认 Python 环境可用

## 执行方式

```bash
python "skills/zip2csv/scripts/zip2csv.py" <zip或目录> [output_dir]
python "skills/zip2csv/scripts/zip2csv.py" <zip或目录> -o <output_dir>
```

参数说明：
- `zip或目录`（必填）：ZIP 压缩包路径,或原始数据目录。**结构宽容**:LOG/CONFIG/DIAG/CMD 目录在**任意深度**都能被发现(`xxx/xxx/网元/CMD`、`网元/CMD`、根下直接 `CMD` 皆可);网元名=类型目录的父目录名,没有网元层取输入目录名(zip 平铺取 `NE`);类型目录**内部再嵌子目录**(按日期存放等)的文件递归收,输出名带子路径前缀防重名;多外层同名网元的同名文件自动 `_2` 顺延防覆盖(有 ⚠ 播报)
- `output_dir`（可选）：输出目录。zip 默认=zip 同名目录(去掉 `.zip` 后缀);目录默认=`<目录>_csv`

## 输出目录结构

```
<output_dir>/
├── extracted/          # zip 输入:解压的原始文件(散包时 ifilter 的 formated_result/ 也在这里面)
├── raw/                # 单个散文件输入:暂存区(formated_result/ 在其下)
├── csv/                # 转换产物(按网元分目录)
│   └── <网元>/
│       ├── LOG/*.csv
│       ├── CONFIG/*.csv
│       ├── DIAG/*.csv
│       └── CMD/*.csv   # 真 CMD 转换 + DIAG 派生汇总
└── <输入名>_csv.zip     # 全部 csv 打包
```
目录输入时没有 extracted/(就地扫描);散包目录输入时 ifilter 的 `formated_result/` 落在**输入目录内**。
脚本结束打印「raw 通道建议」——S 类脚本直读裸 txt 时 `--data raw=` 就填它。

## cmd CSV 列映射

| 列名      | 来源                    |
|----------|------------------------|
| NEName   | DIAG CSV 的 NEName 列   |
| CmdName  | DIAG CSV 的 CmdName 列  |
| OriginCmd| DIAG CSV 的 CmdName 列  |
| CmdResult| DIAG CSV 的 ObjContent 列 |

## 各模块 CSV 输出格式

### LOG CSV 列
NEName, Time, Module, Level, Summary, Content, Log, Para1, Para1Name, ..., Para20, Para20Name

### CONFIG CSV 列
NEName, CfgType, Module, level1, L2Module, level2, L3Module, level3, L4Module, level4, L5Module, level5

### DIAG CSV 列
NEName, CmdName, ObjContent

### cmd CSV 列
NEName, CmdName, OriginCmd, CmdResult

## 工作流程

1. 向用户确认 ZIP 文件/原始目录路径
2. 执行 `python "skills/zip2csv/scripts/zip2csv.py" <zip或目录> [output_dir]`
3. 检查输出结果，报告各步骤处理情况：
   - 解压了多少文件
   - LOG/CONFIG/DIAG 各转换了多少 txt 文件
   - cmd 目录生成了多少条记录
4. 如有错误，诊断并报告

## 异常处理

- 输入路径(zip/目录)不存在：提示用户检查路径（目录输入时不产生 `extracted/`，原始文件就在输入目录里）
- LOG/CONFIG/DIAG 目录不存在：对应步骤自动跳过，不影响其他步骤
- txt 文件解析失败：跳过该文件，继续处理其他文件
- 编码问题：脚本内部使用 `utf-8-sig` 编码，兼容 Excel 打开
