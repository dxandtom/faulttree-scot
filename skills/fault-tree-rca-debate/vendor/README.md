# vendor/ —— 同事的辩论式 RCA 引擎原件(本目录内容**永不修改**)

```
vendor/
├── fault_tree_evaluator_bfs_advanced.py   # 主算法(核心算法,不许改)——已随仓携带(原样入库)
└── config.yaml                            # 其配置(LLM api_key/base_url/model + 算法参数)——本地放入,不入库
```

**你只差一个文件**:把合作方给的 `config.yaml` 原样复制进本目录即可开跑(引擎 .py 已在)。

约定:
- **本目录 = 上游交付物的落点**。任何适配(输入/输出/格式转换)都写在 `../scripts/` 的壳里,
  不改这里的文件——同事发新版直接覆盖即可,不用合代码。
- `config.yaml` 含 API 密钥,已被本目录 `.gitignore` 挡住、**不会入库**;引擎 .py 不含密钥,
  经确认后入库(换机器/新 clone 不用再找同事要文件)。
- 壳(`../scripts/run_debate_rca.py`)按其文档接口调用:
  `FaultTreeProcessor(cfg).load_fault_tree(xlsx)` → `build_tree_structure(df)` →
  `find_top_k_paths(tree, question, k)` → `(top_k_original, top_k_softmax, top_k_ratio, debate_records)`。
  若真实签名与此不符,首选**新建** `vendor/bridge.py` 包一层(壳自动优先加载 bridge),
  或改壳里的 `_load_processor()` 一处(见 ../INTEGRATION.md 核对清单)。
