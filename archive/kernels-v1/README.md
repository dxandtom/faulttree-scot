# kernels-v1(A–J 全量内核,封存)

> 2026-10 更新：为重启无线场景，B 差值、E 占比、H 信令流程三个没有替代写法的内核已嫁接回
> `skills/cs-scot-generate/scripts/condition_kernels.py`（连同自测用例），现役类别为 A/B/C/D/E/G/H/J/P。
> 仍封存的只剩 F 配置检查（改用 C 类 equals 或 extract）与 I 特征分析（改用 A 类 count_rows）。
> 本目录的 v1 文件原样保留作存档；下面"怎么复活"一节描述的是整套拷回的老办法，现在不要那样做，
> 那会丢掉 v1 之后新增的 S 代码类、P 成对比较、param_from 传参链、extract、regex、filter 等能力。

无线接入类场景(NSA/SA)时代的完整内核集:A 阈值 / B 差值 / C 字段匹配 / D 组合 /
E 占比 / F 配置检查 / G 日志匹配 / H 信令流程 / I 特征分析 / J 人工,含 72 条自测电池。

- **为什么封存**:数通场景重构内核集(以字段匹配/取值/组合/成对比较为中心),
  B/E/F/H/I 在数通判据普查中零命中,移出主干防误用与文档噪音。
- **对已交付产物零影响**:产物自包含(assemble 时拷贝内核副本进产物 scripts/),
  老产物带着自己的 v1 副本照常运行。
- **怎么复活**:把本目录两个文件拷回 skills/cs-scot-generate/scripts/ 即可
  (注意同时恢复 condition_binding_schema.md 的对应类表与简报规约)。
