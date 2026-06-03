#!/usr/bin/env python3
"""逐条件事实自检(机械兜底):在数据进 RCA 之前,先揪"静默编造"。

不依赖运行模型的自觉,直接扫 condition_facts.json:
1. **无中生有**:列没解析到(resolved_column 为空)或 status=insufficient_data,却给了 predicate_satisfied。
2. **缺理由**:status=insufficient_data 但没写 reason(说不清为啥缺,等于黑箱)。
3. **越权判定**:predicate_satisfied 出现了非布尔(本层只做字面比较,True/False/缺省三态)。
4. **类别缺失**:condition 没有 class,或 J 类却不是 manual。

用法:python validate_conditions.py <condition_facts.json>   # 不合格 → 退出码 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("用法:python validate_conditions.py <condition_facts.json>", file=sys.stderr)
        return 2
    try:
        doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取/解析: {e}", file=sys.stderr)
        return 2

    problems = []
    for c in doc.get("conditions", []):
        cid = c.get("id") or c.get("node") or c.get("condition_text", "?")
        f = c.get("fact") or {}
        st = f.get("status")
        pred = f.get("predicate_satisfied")
        cls = c.get("class") or f.get("class")

        if not cls:
            problems.append(f"[类别缺失]「{cid}」没有 class(应为 A–J)")
        if str(cls).upper() == "J" and st != "manual":
            problems.append(f"[J类越权]「{cid}」J 类应 status=manual,实际 {st!r}")

        # 数据不足 / 列没解析到,却给了判断 → 编造
        if st == "insufficient_data" and pred is not None:
            problems.append(f"[无中生有]「{cid}」数据不足却给了 predicate_satisfied={pred!r}")
        if st == "ok" and pred is not None and f.get("resolved_column") is None \
                and "resolved_columns" not in f and "sub_facts" not in f and cls != "E":
            problems.append(f"[无中生有]「{cid}」没解析到列却给了 predicate_satisfied={pred!r}")
        if st == "insufficient_data" and not f.get("reason"):
            problems.append(f"[缺理由]「{cid}」数据不足但没写 reason")
        if pred is not None and not isinstance(pred, bool):
            problems.append(f"[越权判定]「{cid}」predicate_satisfied 应为布尔,实际 {pred!r}")

    if problems:
        print(f"不合格:发现 {len(problems)} 处问题:")
        for p in problems:
            print("  - " + p)
        print("\n修法:列解析不到/数据不足就只给 status+reason,绝不给 predicate_satisfied;"
              "J 类用 manual;成立/不成立的最终裁决留给 fault-tree-rca。")
        return 1

    s = doc.get("summary", {})
    print(f"通过:{s.get('total','?')} 条事实无静默编造(ok={s.get('ok')} "
          f"数据不足={s.get('insufficient_data')} 人工={s.get('manual')})。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
