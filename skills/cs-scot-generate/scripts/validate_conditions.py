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
import re
import sys
from pathlib import Path

# ---- 代理"必要条件"自检:只逮"任何语义下都错"的三类;绝不判"语义是否相等"(那是模型的活、写在 mapping_reason 可审) ----
_PROC_ALIAS = {"uecntx": "uecontext", "uecontext": "uecontext", "rrc": "rrc",
               "ngsig": "ngsig", "ngsignaling": "ngsig", "qosflow": "qosflow",
               "pdusession": "pdusession", "paging": "paging"}
_KNOWN_PROCS = ("uecontext", "rrc", "ngsig", "qosflow", "pdusession", "paging")


def _counter_proc(text):
    """条件正文里 N.<Proc>. / L.<Proc>. 的流程段 → 规范名(认得才返回,认不得返回 None,绝不误伤)。"""
    for m in re.finditer(r"\b[NL]\.([A-Za-z]+)\.", text or ""):
        p = _PROC_ALIAS.get(re.sub(r"[^a-z0-9]", "", m.group(1).lower()))
        if p:
            return p
    return None


def _col_proc(col):
    """列名里出现的已知流程关键词 → 规范名(认不得返回 None)。"""
    norm = re.sub(r"[^a-z0-9]", "", (col or "").lower())
    return next((p for p in _KNOWN_PROCS if p in norm), None)


def _is_rate_col(col):
    return bool(col) and ("rate" in col.lower() or "率" in col)


def _is_subcause(text):
    """计数器含 .FailEst.<原因> / .Fail<原因> = 特定失败子因。"""
    return bool(re.search(r"\.Fail(Est)?\.[A-Za-z]", text or ""))


def _is_setup_cond(text):
    return bool(text and (re.search(r"\.Est\.|\.ConnEst\.|Setup", text) or "建立" in text))


def _is_drop_col(col):
    c = (col or "").lower()
    return "drop" in c or "abnorm" in c or "release" in c or "掉线" in (col or "")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
        # 各内核"解析到了什么"的痕迹字段(A/F/I:resolved_column;B:resolved_columns;D:sub_facts;
        # H:msg_column;E:group_by)。一个都没有却给了判断,才算无中生有。
        _prov = ("resolved_column", "resolved_columns", "sub_facts", "msg_column", "group_by")
        if st == "ok" and pred is not None and not any(f.get(k) is not None for k in _prov):
            problems.append(f"[无中生有]「{cid}」没解析到任何列/痕迹却给了 predicate_satisfied={pred!r}")
        if st == "insufficient_data" and not f.get("reason"):
            problems.append(f"[缺理由]「{cid}」数据不足但没写 reason")
        if pred is not None and not isinstance(pred, bool):
            problems.append(f"[越权判定]「{cid}」predicate_satisfied 应为布尔,实际 {pred!r}")

        # 代理三类必要条件(只逮"任何语义下都错"的):流程不匹配 / 建立配掉线 / 子因配总率
        ct, col = c.get("condition_text") or "", f.get("resolved_column")
        if st == "ok" and _is_rate_col(col):
            cp, lp = _counter_proc(ct), _col_proc(col)
            if cp and lp and cp != lp:
                problems.append(f"[流程不匹配]「{cid}」条件计数器流程={cp},却绑到 {lp} 流程的率列「{col}」——不同流程不能互代,改数据不足或换源")
            if _is_setup_cond(ct) and _is_drop_col(col):
                problems.append(f"[建立配掉线]「{cid}」建立类条件绑到掉线/异常释放率列「{col}」——建立≠掉线,数据不足")
            if _is_subcause(ct):
                problems.append(f"[子因配总率]「{cid}」特定失败子因(.FailEst.)绑到总率列「{col}」——总率分不出子因,改 C 类查错误码字段或数据不足")

    # 软告警(不致命):多条不同判据绑到同一(列,op,阈值)→ 疑似"通用代理",损泛化
    from collections import Counter
    key_count = Counter()
    for c in doc.get("conditions", []):
        f = c.get("fact") or {}
        col = f.get("resolved_column")
        if col and f.get("status") == "ok":
            key_count[(col, f.get("op"), f.get("threshold"))] += 1
    proxies = [(k, n) for k, n in key_count.items() if n >= 5]

    if problems:
        print(f"不合格:发现 {len(problems)} 处问题:")
        for p in problems:
            print("  - " + p)
        print("\n修法:列解析不到/数据不足就只给 status+reason,绝不给 predicate_satisfied;"
              "J 类用 manual;成立/不成立的最终裁决留给 fault-tree-rca。")
        return 1

    for (col, op, thr), n in proxies:
        print(f"⚠ 告警:{n} 条绑定共用「列={col} {op} {thr}」——疑似把多条不同判据塞给同一通用指标"
              f"(语义错+恒成立+丢泛化)。对不上专属量的应 decision:insufficient_data。")
    s = doc.get("summary", {})
    print(f"通过:{s.get('total','?')} 条事实无静默编造(ok={s.get('ok')} "
          f"数据不足={s.get('insufficient_data')} 人工={s.get('manual')})。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
