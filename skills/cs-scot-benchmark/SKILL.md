---
name: cs-scot-benchmark
description: 对一份 cs-scot-generate 产物跑 6 项可量化基准(压缩有效 / 稳定一致 / 可复现 / 诚实护栏有效 / 转换质量 / 内核回归),结果按条分类写进一个 txt。不需要 ground truth、不调模型,纯确定性、可反复跑出同一份数据。用于给"故障树转 SCoT"这套能力做对外说明的实验数据支撑。
---

# cs-scot-benchmark —— 产物基准(省 / 稳 / 可复现 / 诚实)

**本文件是给你照做的操作手册,已加载,不要再次加载。** 任务:对一份 generate 产物 + 它的数据,跑基准脚本,产出一份分类报告 txt。

## 它测什么(6 条,都不需要标准答案、不调模型)
1. **压缩有效**:局部子树 vs 整树+全量事实,省了多少字符;保留几个节点。
2. **稳定一致**:确定性链路(各 `<指标>_analyzer.py` + 剪枝)重跑 K 次,是否全部跑通、有无空/崩。
3. **可复现**:K 次重跑,事实 + 剪枝结果是否逐字节一致(SHA 比对)。
4. **诚实护栏有效**:① 真产物过 `validate_conditions`;② 一组**已知好/坏例**喂给护栏,必须"拦坏放好"(护栏自测)。
5. **转换质量(故障树→子树)**:结构一致性 + 整洁度(残留 HTML/图片/空条件/测试节点名);给了 `--excel` 还会和原表**逐节点对账**(漏/多)。**转换有 ground truth(Excel 本身),所以这项能算真·准确率。**
6. **内核回归(A–J 地基)**:跑 `cs-scot-generate/scripts/kernel_selftest.py` 的 30 例合成用例(每类的 成立/不成立/数据不足/边界:空表·缺列·%值·transform·require·三态组合),证明改动不会**静默改坏**统计内核。报告头还会打**产物铭牌**(格式契约版本+生成时间),旧产物会标 ⚠。

> 它**不**测诊断准确率(没有 ground truth)。准确率要另做"合成故障注入"评测,不在本 skill 范围。

## 怎么跑(就一条命令)
```bash
python "<本目录>/scripts/benchmark.py" \
  --produce "<generate产物目录>" \
  --data <指标1>=<数据文件1> --data <指标2>=<数据文件2> ... \
  --runs 3 \
  -o "<产物目录>/benchmark_report.txt"
```
- `--produce`:`cs-scot-generate` 出的产物目录(里面有 `scripts/<指标>_analyzer.py`、`tree/<场景>.tree_index.json`、`tree/<场景>.md`)。
- `--data <指标>=<文件>`:**指标名要和脚本名对上**——`chr_analyzer.py` → `--data chr=...`;每个 `*_analyzer.py` 都要给一份数据。
- `--excel <原始故障树.xlsx>`(可选):给了才做第 5 项的"与原表逐节点对账";不给只做结构+整洁度。
- `--runs`:重跑次数(默认 3,用于稳定/可复现两项)。
- `-o`:报告路径(默认写到 `<产物目录>/benchmark_report.txt`)。
- 路径有空格/中文/括号 → **整条加引号**。

跑完:终端打印报告、同时落盘到 `-o`。把那份 txt 直接贴进你的实验/汇报材料即可。

## 注意
- 自带定位同级 skill 脚本(`cs-scot-execute/tree_prune.py`、`cs-scot-generate/validate_conditions.py`、`fault-tree-rca/validate_rca.py`);若装的位置特殊,用 `--tree-prune / --validate-conditions / --validate-rca` 显式指定。
- 纯确定性、不调模型 → 同一产物+数据反复跑,报告里的"压缩/可复现"数字应稳定一致(这本身就是"可复现"的一部分)。
- Python 3.11;数据 xlsx/csv 都行(xlsx 需 `pip install openpyxl`)。
