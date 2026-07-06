# vendor/ —— 评分引擎核心(上游交付原件,本目录内容**永不修改**)

```
vendor/
├── fault_tree_evaluator_bfs_advanced.py   # 引擎核心:BFS 评估 + 多代理辩论 + 三种得分——已随仓携带
└── config.yaml                            # 引擎配置(LLM api_key/base_url/model + 算法参数)——本地放入,不入库
```

**没配过只差一个文件**:把含密钥的 `config.yaml` 原样复制进本目录即可开跑(引擎 .py 已在)。

约定:
- **本目录 = 引擎核心的落点,算法约定不在本仓修改**。任何输入/输出/格式/传输适配都写在
  `../scripts/` 的 I/O 层里——上游发新版引擎,整文件覆盖即可,不用合代码;
  换版后跑一遍 `../references/engine_ops.md` 第六节的升级核对清单。
- `config.yaml` 含 API 密钥,已被本目录 `.gitignore` 挡住、**不会入库**;引擎 .py 不含密钥,
  随仓携带(换机器/新 clone 开箱即有)。
- I/O 层(`../scripts/run_debate_rca.py`)按引擎接口调用:
  `FaultTreeProcessor(cfg).load_fault_tree(xlsx)` → `build_tree_structure(df)` →
  `find_top_k_paths(tree, question, k)` → `(top_k_original, top_k_softmax, top_k_ratio, debate_records)`。
  若新版签名不符,首选**新建** `vendor/bridge.py` 包一层(壳自动优先加载 bridge),
  或改壳里的 `_load_processor()` 一处(见 ../references/engine_ops.md 升级核对清单)。
