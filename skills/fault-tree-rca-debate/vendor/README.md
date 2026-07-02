# vendor/ —— 同事的辩论式 RCA 引擎原件(本目录内容**永不修改**)

把合作方交付的两个文件**原样**放进本目录:

```
vendor/
├── fault_tree_evaluator_bfs_advanced.py   # 主算法(核心算法,不许改)
└── config.yaml                            # 其配置(LLM api_key/base_url/model + 算法参数)
```

约定:
- **本目录 = 上游交付物的落点**。任何适配(输入/输出/格式转换)都写在 `../scripts/` 的壳里,
  不改这里的文件——同事发新版直接覆盖即可,不用合代码。
- `config.yaml` 含 API 密钥,已被本目录 `.gitignore` 挡住、**不会入库**;仓库里只留本 README。
- 壳(`../scripts/run_debate_rca.py`)按其文档接口调用:
  `FaultTreeProcessor(cfg).load_fault_tree(xlsx)` → `build_tree_structure(df)` →
  `find_top_k_paths(tree, question, k)` → `(top_k_original, top_k_softmax, top_k_ratio, debate_records)`。
  若真实签名与此不符,只需改壳里的 `_run_real_vendor()` 一处(见 ../INTEGRATION.md 核对清单)。
