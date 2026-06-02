#!/usr/bin/env python3
"""RCA 输出诚实性自检(指标无关、机械兜底)。

不依赖运行时模型的自觉,直接扫 rca.json,揪两类问题:
1. **自相矛盾**:某节点 judgment 自己承认"无实测/未找到/数据不足/事实中无此值",却又判"成立"。
2. **假根因**:被列为根因的路径里,存在"数据不足/不成立/部分成立/未判定"的节点
   (根因要求路径上每个带条件节点都成立)。

用法:python validate_rca.py <rca.json>   # 不合格 → 退出码 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# judgment 里"承认拿不到/不够"的标记(出现这些就不该判成立)
MISSING = ("无实测", "未找到", "找不到", "不存在", "事实中无", "无此值", "查不到",
           "未提供", "无法验证", "无法判定", "缺数据", "缺少", "数据不足")
# "未完整成立"的结论标记(根因路径里不允许出现)
NOT_PROVEN = ("数据不足", "不成立", "部分成立", "未判定", "无法验证", "无法判定")


def _is_clean_hold(judgment: str) -> bool:
    """judgment 是否是"干净的成立"(成立,且不带任何未完整成立的修饰)。"""
    j = judgment or ""
    if "成立" not in j:
        return False
    return not any(m in j for m in NOT_PROVEN)


def _not_proven_tag(judgment: str):
    j = judgment or ""
    return next((m for m in NOT_PROVEN if m in j), None)


def _root_causes(doc):
    """兼容两种落盘:顶层数组,或 {rca_result:[...], undetermined:{...}}。"""
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("rca_result", "root_causes", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def main() -> int:
    if len(sys.argv) < 2:
        print("用法:python validate_rca.py <rca.json>", file=sys.stderr)
        return 2
    p = Path(sys.argv[1])
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取/解析 {p}: {e}", file=sys.stderr)
        return 2

    problems = []
    for rc in _root_causes(doc):
        if not isinstance(rc, dict):
            continue
        name = rc.get("Root_cause") or rc.get("root_cause") or "(未命名根因)"
        ev = rc.get("evidence_path") or rc.get("evidence") or []
        for node in ev:
            if not isinstance(node, dict):
                continue
            judg = str(node.get("judgment", ""))
            actual = str(node.get("actual", ""))
            nname = node.get("node", "?")
            # 1) 自相矛盾:承认拿不到却判成立
            if _is_clean_hold(judg) and any(m in (judg + actual) for m in MISSING):
                problems.append(
                    f"[自相矛盾] 根因「{name}」/ 节点「{nname}」:judgment 判'成立',"
                    f"但同时承认拿不到数据 → {judg!r}")
            # 2) 假根因:根因路径里有未完整成立的节点
            tag = _not_proven_tag(judg)
            if tag:
                problems.append(
                    f"[假根因] 根因「{name}」的路径里,节点「{nname}」其实是'{tag}'"
                    f" → 含未完整成立节点的路径不该列为根因(应归 undetermined)")

    if problems:
        print(f"不合格:发现 {len(problems)} 处诚实性问题:")
        for pr in problems:
            print("  - " + pr)
        print("\n修法:把'承认无数据却成立'的改成'数据不足';把含'数据不足/不成立'节点的"
              "路径从根因里拿掉,放进 undetermined。")
        return 1

    n = len(_root_causes(doc))
    print(f"通过:{n} 条根因路径均无自相矛盾、无'数据不足/不成立'节点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
