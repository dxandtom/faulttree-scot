---
name: zip2csv
description: 把华为设备诊断数据(ZIP 包或原始目录)批量转换为结构化 CSV——LOG/CONFIG/DIAG/CMD 目录下的 txt 逐个转表,并生成 cmd 汇总 CSV。当用户提到"解压zip转csv"、"zip转csv"、"批量转换诊断文件"、"处理zip包"时使用此技能。
disable-model-invocation: false
---

# ZIP2CSV — 华为交换机诊断数据批量转换技能

## 功能概述

接收一个 ZIP 压缩包**或原始数据目录**（目录直接给，无需先压 zip），自动完成以下流水线：

1. **解压 ZIP** 到本地目录（目录输入则跳过解压、就地扫描）
2. **LOG/** — 遍历所有 `.txt` 文件，调用 `logbuffer2csv.convert_file()` 转换为 CSV
   (命名迁移:**DIAG 更名 CMD**——两名并存兼容,新包 CMD、老包 DIAG 都认,同款回显解析)
3. **CONFIG/** — 遍历所有 `.txt` 文件，调用 `config2csv.parse_config_to_csv()` 转换为 CSV
4. **DIAG/** — 遍历所有 `.txt` 文件，调用 `parse_diag_to_csv.parse_diag_to_csv()` 转换为 CSV
5. **CMD/** — DIAG 的新名字,同款处理(parse_diag_to_csv),输出到 `csv/<网元>/CMD/`(列 NEName/CmdName/ObjContent,下游认 ObjContent↔CmdResult 同义)
5. **cmd/** — 基于 DIAG 输出的 CSV，生成汇总 CSV

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
- `zip或目录`（必填）：ZIP 压缩包路径,或原始数据目录(内含 LOG/CONFIG/DIAG/CMD 子目录,可带网元层;平铺单网元目录的网元名取目录名)
- `output_dir`（可选）：输出目录。zip 默认=zip 同名目录(去掉 `.zip` 后缀);目录默认=`<目录>_csv`

## 输出目录结构

```
<output_dir>/
├── extracted/          # 解压的原始文件
│   ├── LOG/
│   ├── CONFIG/
│   └── DIAG/
├── LOG/                # logbuffer CSV 输出
│   └── *.csv
├── CONFIG/             # config CSV 输出
│   └── *.csv
├── DIAG/               # diag CSV 输出
│   └── *.csv
└── cmd/                # cmd 汇总 CSV 输出
    └── *.csv
```

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
