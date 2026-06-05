#!/usr/bin/env python3
"""RCA 输出诚实性自检(指标无关、机械兜底)。

只兜**结构底线**,不碰强度/排序(那是模型判断)。扫 rca.json:
- **rca_result(确证根因)**:
  1. 自相矛盾——某节点 judgment 判"成立"却又承认"无实测/数据不足/事实中无";
  2. 假根因——路径里含"数据不足/不成立"节点(确证要求路径每个带条件节点都成立)。
     → 修法是**把该条挪进 probable_causes(数据不足)或 excluded(不成立)**,
       **不是去把路径凑完整、更不是造不存在的节点**。
- **probable_causes(疑似)**:只拦"**不成立**"节点(那应 excluded);"数据不足"是疑似的正常特征,放行。

用法:python validate_rca.py <rca.json>   # 不合格 → 退出码 1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# judgment 里"承认拿不到/不够"的标记(出现这些就不该判成立)
MISSING = ("无实测", "未找到", "找不到", "不存在", "事实中无", "无此值", "查不到",
           "未提供", "无法验证", "无法判定", "缺数据", "缺少", "数据不足")
# 确证根因路径里不允许出现的"未完整成立"标记
NOT_PROVEN = ("数据不足", "不成立", "部分成立", "未判定", "无法验证", "无法判定")
# 疑似路径里不允许出现的"判否"标记(数据不足在疑似里是允许的,故不含)
FAIL = ("不成立", "部分成立")


def _is_clean_hold(judgment: str) -> bool:
    j = judgment or ""
    return "成立" in j and not any(m in j for m in NOT_PROVEN)


def _tag(judgment: str, tags) -> str | None:
    j = judgment or ""
    return next((t for t in tags if t in j), None)


def _confirmed(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("rca_result", "root_causes", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def _evidence(rc):
    return rc.get("evidence_path") or rc.get("evidence") or [] if isinstance(rc, dict) else []


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

    confirmed = _confirmed(doc)
    probables = doc.get("probable_causes", []) if isinstance(doc, dict) else []
    undet = doc.get("undetermined", []) if isinstance(doc, dict) else []
    problems = []

    # 空诊断:三档全空 = 啥也没说,不合格(没根因也要在 undetermined 写清卡在哪、缺什么、看到啥)
    if not confirmed and not probables and not undet:
        problems.append("[空诊断] rca_result / probable_causes / undetermined 全空——"
                        "就算没有根因,也必须在 undetermined 写清:卡在哪个节点、缺哪个量、看到哪些观察(标注非根因)。")

    for rc in confirmed:
        if not isinstance(rc, dict):
            continue
        name = rc.get("Root_cause") or rc.get("root_cause") or "(未命名根因)"
        for node in _evidence(rc):
            if not isinstance(node, dict):
                continue
            judg, actual, nname = str(node.get("judgment", "")), str(node.get("actual", "")), node.get("node", "?")
            if _is_clean_hold(judg) and any(m in (judg + actual) for m in MISSING):
                problems.append(f"[自相矛盾] 确证根因「{name}」/节点「{nname}」判'成立'却承认拿不到数据 → {judg!r}")
            tag = _tag(judg, NOT_PROVEN)
            if tag:
                problems.append(
                    f"[假根因] 确证根因「{name}」路径含'{tag}'节点「{nname}」"
                    f"→ 把它挪进 probable_causes(数据不足)或 excluded(不成立),别凑路径、别造节点")

    for rc in probables:
        if not isinstance(rc, dict):
            continue
        name = rc.get("Root_cause") or "(未命名疑似)"
        for node in _evidence(rc):
            if not isinstance(node, dict):
                continue
            tag = _tag(str(node.get("judgment", "")), FAIL)
            if tag:
                problems.append(
                    f"[疑似越界] 疑似「{name}」路径含'{tag}'节点「{node.get('node', '?')}」"
                    f"→ 有不成立节点应归 excluded(不报),不是疑似")

    if problems:
        print(f"不合格:发现 {len(problems)} 处问题:")
        for pr in problems:
            print("  - " + pr)
        print("\n修法:确证根因必须路径全成立;有'数据不足'节点→挪 probable_causes(疑似,带 caveat),"
              "有'不成立'节点→excluded(不报)。**绝不把路径凑完整、绝不造不存在的节点。**")
        return 1

    print(f"通过:确证根因 {len(confirmed)} 条(全成立、无矛盾)、疑似 {len(probables)} 条(无不成立节点)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
