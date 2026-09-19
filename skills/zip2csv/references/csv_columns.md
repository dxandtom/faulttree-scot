# 各类 CSV 的列定义

zip2csv 转出的表按三类目录存放，共四种表头（CMD 目录下有转换结果与派生汇总表两种）。下游分析脚本按这些列名取值，核对表头时看这里。

## LOG 表（`csv/<网元>/LOG/*.csv`）

NEName, Time, Module, Level, Summary, Content, Log, Para1, Para1Name, …, Para20, Para20Name

## CONFIG 表（`csv/<网元>/CONFIG/*.csv`）

NEName, CfgType, Module, level1, L2Module, level2, L3Module, level3, L4Module, level4, L5Module, level5

## DIAG 表（`csv/<网元>/DIAG/*.csv`）与 CMD 目录的转换结果（`csv/<网元>/CMD/*.csv`）

NEName, CmdName, ObjContent

下游把 ObjContent 与 CmdResult 视为同义。

## 命令汇总表（`csv/<网元>/CMD/*.csv`，由 DIAG 表派生）

NEName, CmdName, OriginCmd, CmdResult

| 列名 | 来源 |
|---|---|
| NEName | DIAG 表的 NEName 列 |
| CmdName | DIAG 表的 CmdName 列 |
| OriginCmd | DIAG 表的 CmdName 列 |
| CmdResult | DIAG 表的 ObjContent 列 |

同一网元下若 CMD 目录已有真实的转换结果，同名的派生汇总表会跳过，不覆盖。
