# SCOT 引用 & 条件表达式语法

下面这两套语法在 SCOT Plan 各处都会用到：节点的 `inputs`、`outputs`、
`memory_init`，以及 `transition.conditions[].expr`。

实现位于 `scot-shared/ref_expr.py` 和 `scot-shared/condition_expr.py`。

---

## 1. 引用表达式（在 inputs / outputs / memory_init 中使用）

### 文法

```
ref_expr := "$input." path
          | "$memory." path
          | "$output." path
          | "$step." node_id "." path
          | literal_value

path := identifier ("." identifier | "[" integer "]")*
```

### 命名空间

| 前缀 | 含义 | 读 | 写 |
|------|------|----|-----|
| `$input` | 执行启动时传入的参数 | 可 | 否 |
| `$memory` | 工作记忆存储 | 可 | 可 |
| `$output` | 最终输出缓冲（返回给调用方） | 否 | 可 |
| `$step.<id>` | 前序某节点的输出数据 | 可 | 否 |

### 解析规则

- 递归按 `.` / `[index]` 语法走 JSON 路径
- 路径不存在 → 返回 `null`（不报错）
- 索引越界 → 返回 `null`
- 无隐式类型转换

### 示例

| 表达式 | 含义 |
|--------|------|
| `$input.user_request` | 用户原始请求 |
| `$memory.alarms` | 工作记忆中的 `alarms` 字段 |
| `$memory.alarms[0].severity` | 第一条告警的 severity |
| `$step.n2.alarms` | 节点 `n2` 输出中的 `alarms` 字段 |
| `"hello"` | 字符串字面量 |
| `42` | 数字字面量 |

---

## 2. 条件表达式（在 `transition.conditions[].expr` 中使用）

### 文法

```
condition := ref_expr comparator value
           | ref_expr "exists"
           | ref_expr "empty"
           | "not" condition
           | condition "and" condition
           | condition "or" condition
           | "(" condition ")"

comparator := "==" | "!=" | ">" | ">=" | "<" | "<=" | "in" | "contains"
```

### 求值

由运行时的安全解析器处理 —— **绝不使用 `eval()`**。比较两侧的引用会针对工作
记忆 / 前序步骤结果做解析。类型不兼容的比较（如 `null > 0`）返回 false，
不抛异常。

### 示例

```
$memory.alarm_count > 0
$memory.alarms exists and not $memory.alarms empty
$step.n2.status == "success"
$memory.device_type in ["router", "switch"]
$memory.summary contains "error"
```

### 裸引用的真值规则

裸 `$memory.flag`（不带比较运算符）按 Python 真值规则求值：

- `null` / `false` / `0` / `""` / `[]` / `{}` → false
- 其余 → true
