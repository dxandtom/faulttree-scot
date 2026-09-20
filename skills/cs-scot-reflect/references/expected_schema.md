# Expected.json 结构定义

> 本文件定义评估所需的用例期望文件结构，是评估反思技能的输入格式约定。

## 从哪来

**通常不由人手写**：阳性用例由 `scripts/case_gen.py` 从 migrate 迁移台账（产物
`tree/migrations.json`）+ tester 交付包（内含 测试数据/）机械生成，故障树结构调整后
重跑即全库刷新。自动生成的文件会多带 `generated_by` / `data_source` 字段（评估不读，
仅溯源用）。阴性用例（健康数据）无机械来源，按本格式人工编写。

## 目录形态

```
cases/
└── <case_id>/
    ├── data/                    # （可选）该用例的测试数据
    └── expected.json            # 期望结果定义
```

## expected.json 字段说明

```json
{
  "case_id": "ldp-lsp-001",      // 【必须】用例唯一标识，全局唯一
  "scenario": "LDP LSP未建立",   // 【必须】场景名称，用于日志和报告
  "negative": false,             // 【必须】是否为阴性用例
                                 //   true = 期望无根因（用于假阳率测量）
                                 //   false = 期望有根因（正常阳性用例）
  "expected_root_causes": [       // 阳性用例【必须】，阴性用例【禁止】
    {
      "node_id": "n6",           // 【必须】节点ID，必须与tree_index中的node_id一致
      "node_name": "接口DOWN",   // 【推荐】节点中文名，用于报告可读性
      "note": "log里约80行OperStatus=DOWN"  // 【可选】备注，说明如何得出此期望
    }
  ],
  "expected_facts": {             // 【可选】期望事实，用于CRITERIA_GAP判断
    "n6": {
      "predicate_satisfied": true,  // 该节点的条件期望是否满足
      "evidence_source": "log"      // 【可选】期望的证据来源
    }
  },
  "expected_excluded": [],        // 【可选】期望被排除的节点ID列表
  "expected_undetermined": []     // 【可选】期望待定的节点ID列表
}
```

## 字段约束

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| case_id | string | 是 | 唯一标识，建议格式：`<场景>-<序号>` |
| scenario | string | 是 | 场景描述，用于报告 |
| negative | boolean | 是 | true=阴性用例（无根因），false=阳性用例（有根因） |
| expected_root_causes | array | negative=false时必须 | 期望的根因节点列表 |
| expected_facts | object | 否 | 节点→期望事实映射，用于判断predicate_satisfied是否正确 |
| expected_excluded | array | 否 | 期望被明确排除的节点（用于校验VERDICT_GAP） |
| expected_undetermined | array | 否 | 期望状态为待定的节点 |

## 验证规则

1. **互斥性**：`negative=true` 时 `expected_root_causes` 必须为空数组或不存在
2. **一致性**：`expected_root_causes` 中的 `node_id` 必须在对应 `tree_index.json` 中存在
3. **完整性**：阳性用例至少要有一个期望根因

## 防止泄露答案的规则

1. `expected.json` 只允许存放在 `cases/` 目录下
2. 不得拷贝到 `assets/`、`result-*/` 或任何执行链目录
3. 评估会话读过 `expected.json` 后，该会话不得执行手工 RCA 诊断

## 示例

### 阳性用例
```json
{
  "case_id": "ldp-lsp-001",
  "scenario": "LDP LSP未建立",
  "negative": false,
  "expected_root_causes": [
    { "node_id": "n6", "node_name": "接口DOWN", "note": "log里约80行OperStatus=DOWN" }
  ],
  "expected_facts": {
    "n6": { "predicate_satisfied": true }
  }
}
```

### 阴性用例
```json
{
  "case_id": "ldp-lsp-healthy-001",
  "scenario": "正常健康设备",
  "negative": true,
  "expected_root_causes": [],
  "expected_facts": {}
}
```
