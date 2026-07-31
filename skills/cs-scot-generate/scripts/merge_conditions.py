#!/usr/bin/env python3
"""把子代理写的批次结果机械合并进 <ind>_analyzer.py:验收、去重、格式守卫、落盘一条龙。

为什么要它:绑定结果(几百条)若走"子代理回复 → 主代理转录 → Edit 重打"要三过模型上下文——
实测撑爆主会话(自动压缩)、写入代理 600s 流挂。改为:子代理把结果写成
<OUT>/dispatch/<ind>_batch<N>_result.py(各写各的、零竞态),本脚本串行合并进产物:
  · 逐批验收:UTF-8 可读、无 U+FFFD 乱码、ast 可解析为 list[dict];
  · 格式守卫:点名「C/G 既无 contains 也无 equals」(op/target 之类野字段内核不读,
    实测整源假排除)与「缺 node_id」的条目——条目保留、修绑定去,内核对空 targets 也会响亮拒绝;
  · 完全重复的条目去重(实测子代理会把同一段重复两遍);
  · 用 ast 定位 <ind>_analyzer.py 里的 CONDITIONS 赋值块整体替换(骨架/已填过的旧内容都认),幂等。
**先到先合**:哪批结果文件在就合哪批,可反复跑——"谁先回来就先写谁"天然成立,不必等全齐。

用法:
    merge_conditions.py --out "<OUT>" [--indicators cmd,dblg,...]
退出码:0=全部批次到齐且合并完成;1=有缺批/坏批(照打印重派或等到货后重跑);2=用法/产物错误。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

_BRIEF = re.compile(r"^(?P<ind>.+)_batch(?P<n>\d+)\.txt$")


def parse_result(path: Path, require_marker: bool = False):
    """结果文件 → (entries, 错误串或None)。认裸列表和 `CONDITIONS = [...]` 两种写法。
    require_marker=True(dispatch/manifest.json 声明)时,缺尾部完成标记按截断拒收——
    弱模型半途而废但输出恰好是合法短列表时,唯有这个机械信号能逮住。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, f"读不了:{e}"
    if "�" in text:
        return None, "含 U+FFFD 乱码(编码错配,重写时显式 encoding='utf-8')"
    if require_marker and "# BATCH_COMPLETE" not in text:
        return None, "缺完成标记 # BATCH_COMPLETE(疑似半路截断——列表虽可解析,但可能不全)"
    body = text.strip()
    try:
        val = ast.literal_eval(body)
    except (ValueError, SyntaxError):
        lo, hi = body.find("["), body.rfind("]")
        if lo < 0 or hi <= lo:
            return None, "解析不出列表(没有 [...])"
        seg = body[lo:hi + 1]
        try:
            val = ast.literal_eval(seg)
        except (ValueError, SyntaxError) as e:
            try:
                import json as _json                    # 子代理常写 JSON 风格(true/false/null 小写),
                val = _json.loads(seg)                  # 机械救回——别逼模型"解析失败就自己写脚本硬合"
            except Exception:  # noqa: BLE001
                return None, f"ast/json 都解析失败:{e}"
    if not isinstance(val, list) or not all(isinstance(x, dict) for x in val):
        return None, "内容不是 list[dict]"
    return val, None


def drift_warnings(entries, tag):
    """格式漂移点名(条目保留,修绑定去):C/G 无 contains/equals、缺 node_id。"""
    warns = []
    for e in entries:
        eid = e.get("id") or e.get("node_id") or "?"
        cls = str(e.get("class") or "").upper()
        if not e.get("node_id"):
            warns.append(f"  ⚠ [{tag}] 「{eid}」缺 node_id——挂不回树,覆盖度/RCA 都认不到")
        if (cls in ("C", "G") and not e.get("contains") and not e.get("equals")
                and not e.get("decision") and not e.get("sub")):
            warns.append(f"  ⚠ [{tag}] 「{eid}」C/G 缺 contains/equals(op/target 等野字段内核不读)——运行时会被内核拒为数据不足,修绑定格式")
    return warns


def write_conditions(analyzer: Path, entries) -> str | None:
    """用 ast 定位 CONDITIONS 赋值块并整体替换(骨架/旧内容都认)。返回错误串或 None。"""
    try:
        src = analyzer.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as e:
        return f"读/解析 analyzer 失败:{e}"
    span = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            span = (node.lineno, node.end_lineno)
            break
    if span is None:
        return "找不到 CONDITIONS 赋值块(不是 assemble 产的骨架?)"
    block = "CONDITIONS = [\n" + "".join(f"    {e!r},\n" for e in entries) + "]"
    lines = src.splitlines(keepends=True)
    new_src = "".join(lines[:span[0] - 1]) + block + "\n" + "".join(lines[span[1]:])
    analyzer.write_text(new_src, encoding="utf-8")
    try:
        ast.parse(new_src)                              # 落盘后自检,绝不留语法坏文件
    except SyntaxError as e:
        return f"写入后语法自检失败:{e}"
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="机械合并批次绑定结果进 <ind>_analyzer.py(验收/去重/格式守卫/落盘)")
    ap.add_argument("--out", required=True, help="场景产物目录 <OUT>")
    ap.add_argument("--indicators", default="", help="逗号分隔;缺省=按 dispatch/ 里的简报自动发现")
    args = ap.parse_args()
    out = Path(args.out)
    ddir = out / "dispatch"
    if not ddir.is_dir():
        print(f"✗ 没有 {ddir}——先跑 make_dispatch.py", file=sys.stderr)
        return 2

    plan = {}                                           # ind -> [批号...](按简报发现)
    for p in sorted(ddir.iterdir()):
        m = _BRIEF.match(p.name)
        if m:
            plan.setdefault(m.group("ind"), []).append(int(m.group("n")))
    if args.indicators:
        keep = {x.strip() for x in args.indicators.split(",") if x.strip()}
        plan = {k: v for k, v in plan.items() if k in keep}
    if not plan:
        print("✗ dispatch/ 里没有任何简报(或 --indicators 全滤掉了)", file=sys.stderr)
        return 2
    require_marker = False                              # 老 dispatch 目录没铭牌 → 不启用标记检查(向后兼容)
    mf = ddir / "manifest.json"
    if mf.exists():
        try:
            import json as _json
            require_marker = _json.loads(mf.read_text(encoding="utf-8")).get("complete_marker") is True
        except Exception:  # noqa: BLE001
            pass

    # 树权威节点表:机械回填 id/node/condition_text 用(子代理只交"判定",判据逐字由这里补——
    # 防截断/抄错;模型写了也一律被权威值覆盖)。
    tree_nodes = {}
    tis = sorted(out.glob("tree/*.tree_index.json"))
    if len(tis) == 1:
        try:
            import json as _json
            tree_nodes = _json.loads(tis[0].read_text(encoding="utf-8")).get("nodes") or {}
        except Exception:  # noqa: BLE001
            tree_nodes = {}
    if not tree_nodes:
        print("⚠ 读不到唯一的 tree/*.tree_index.json——无法机械回填 condition_text 等字段,按子代理原样合并")

    pending, all_warns, wrote = [], [], []
    cross_cands = {}                                    # node_id -> {node_id, sources:[…], reasons:[…]}
    pending_inds = set()                                # 有缺批/坏批的源(候选清理要避开不完整的轮次)
    for ind in sorted(plan):
        merged, seen = [], set()
        got = []
        n_dec = n_marks = 0                             # 灌水/滥标体检用
        for n in sorted(plan[ind]):
            rp = ddir / f"{ind}_batch{n}_result.py"
            tag = rp.stem
            if not rp.exists():
                pending.append(f"{ind} 批{n}:结果文件未到({rp.name})——等子代理/同简报重派")
                pending_inds.add(ind)
                continue
            entries, err = parse_result(rp, require_marker)
            if err:
                pending.append(f"{ind} 批{n}:{err}——同简报重派该批")
                pending_inds.add(ind)
                continue
            s_ids = [str(e.get("node_id") or "?") for e in entries
                     if isinstance(e, dict) and str(e.get("class") or "").upper() == "S"]
            if s_ids:                                    # S(代码类)只认人工/调优表来源:模型产出=绕过全部 DSL 闸门
                pending.append(f"{ind} 批{n}:含 class:S 条目({', '.join(s_ids[:5])})——代码类只许人工/调优表"
                               "来源,绑定子代理不得产出;从结果文件删掉这些条目再重跑(确需脚本逻辑请人工补)")
                pending_inds.add(ind)
                continue
            if ind != "cross":                          # 子代理的「涉及多源」标记:抽出来汇入补绑候选,
                marks = [e for e in entries            # 不进本源脚本(它不是可执行绑定)
                         if e.get("decision") == "cross_source"]
                entries = [e for e in entries if e.get("decision") != "cross_source"]
                n_marks += len(marks)
                for m in marks:
                    nid = m.get("node_id") or "?"
                    c = cross_cands.setdefault(nid, {"node_id": nid, "node": m.get("node"),
                                                     "sources": [], "reasons": []})
                    if ind not in c["sources"]:
                        c["sources"].append(ind)
                    r = str(m.get("mapping_reason") or "").strip()
                    if r and r not in c["reasons"]:
                        c["reasons"].append(r)
            # 机械回填:id/node/condition_text/source 以 tree_index 为权威(子代理只交判定;
            # 它抄的判据常被截断,一律覆盖)。cross 的 source=查询落点,由补绑代理给,不覆盖。
            for e in entries:
                nid = str(e.get("node_id") or "")
                nd = tree_nodes.get(nid)
                if nd:
                    e["id"] = f"{ind}-{nid}"
                    e["node"] = nd.get("name")
                    e["condition_text"] = nd.get("condition")
                    if ind != "cross":
                        e["source"] = ind
                    elif e.get("class") and not e.get("source"):   # decision 占位本就不带 source(规约示例即三字段),只查真绑定
                        all_warns.append(f"  ⚠ [{tag}] 「{nid}」cross 真绑定缺顶层 source(最终查询落点的真实源名;"
                                         "D 组合写第一个 sub 的源)——运行时按顶层 source 取数,缺了整条数据不足,补上")
                elif nid and tree_nodes:
                    all_warns.append(f"  ⚠ [{tag}] 「{nid}」不在树里(node_id 抄错?)——该条挂不回任何节点")
                if e.get("decision") == "insufficient_data":
                    n_dec += 1
                    if ind == "cross" and not str(e.get("mapping_reason") or "").strip():
                        all_warns.append(f"  ⚠ [{tag}] 「{nid}」decision 占位没写 mapping_reason——"
                                         "cross 规约:绑不了必须写清缺哪一跳,空理由等于没表态")
            dup = 0
            for e in entries:
                key = repr(sorted(e.items(), key=lambda kv: kv[0]))
                if key in seen:
                    dup += 1
                    continue
                seen.add(key)
                merged.append(e)
            all_warns += drift_warnings(entries, tag)
            got.append(f"批{n}={len(entries)}条" + (f"(去重-{dup})" if dup else ""))
        if not merged:
            print(f"{ind}:0 批到货,未写入。")
            continue
        analyzer = out / "scripts" / f"{ind}_analyzer.py"
        err = write_conditions(analyzer, merged)
        if err:
            pending.append(f"{ind}:{err}")
            pending_inds.add(ind)
            continue
        wrote.append(ind)
        print(f"{ind}:{' / '.join(got)} → 合并 {len(merged)} 条,已写入 {analyzer.name}"
              + (f"(缺 {len(plan[ind]) - len(got)} 批,到货后重跑本脚本即可)" if len(got) < len(plan[ind]) else ""))
        # 灌水/滥标体检(软点名):decision 占比畸高=对全树逐条表态(规约明令"不归本源答的整条不写",
        # 实测 log 源 92 条全 decision 把闸门糊瞎);cross_source 占比畸高=把"不归我答"误标跨源。
        # cross 批不适用:CROSS_RULES 要求每条候选都表态,decision 占位是义务不是灌水(实测 8/13 占位
        # 被误点名,弱模型照"删多余占位"的建议琢磨删条目)——cross 的体检是上面的"占位必须带理由"。
        if ind != "cross" and len(merged) >= 10 and n_dec > len(merged) * 0.5:
            all_warns.append(f"  ⚠ [{ind}] 灌水嫌疑:{n_dec}/{len(merged)} 条是 decision 占位——"
                             "「明显不归本源答的判据整条不写」,占位只给「确属本源该答却答不了」的;复核并删多余占位")
        if n_marks >= 10 and n_marks > (len(merged) + n_marks) * 0.5:
            all_warns.append(f"  ⚠ [{ind}] cross_source 滥标嫌疑:{n_marks} 条标记——跨源=「单靠任何一个源都答不完」;"
                             "「归别的源单独答」不是跨源,整条不写;复核候选清单")

    cp = ddir / "cross_candidates.json"
    if cross_cands:
        import json as _json
        cp.write_text(_json.dumps(sorted(cross_cands.values(), key=lambda c: c["node_id"]),
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n跨源判据候选 {len(cross_cands)} 条(子代理标记 cross_source)→ {cp}")
        print("  下一步:python make_dispatch.py --out \"<OUT>\" --cross 生成跨源补绑简报,"
              "派一个子代理填 param_from 绑定,再跑本脚本合并进 cross_analyzer.py。")
    else:
        noncross = [i for i in plan if i != "cross"]
        # 本轮完整扫过全部单源批次且一个标记都没有 → 陈旧候选必须清掉,防按旧候选补绑早已改判的节点
        if noncross and not (pending_inds - {"cross"}) and cp.exists():
            cp.unlink()
            print("\n单源批次里已无 cross_source 标记 → 陈旧的 cross_candidates.json 已清除。")

    if all_warns:
        print(f"\n格式漂移点名({len(all_warns)} 处,条目已保留,修绑定后重写该批结果并重跑):")
        for w in all_warns[:40]:
            print(w)
        if len(all_warns) > 40:
            print(f"  …(还有 {len(all_warns) - 40} 处,略)")
    if pending:
        print(f"\n✗ 未完成 {len(pending)} 项:", file=sys.stderr)
        for x in pending:
            print("  - " + x, file=sys.stderr)
        print("⛔ 禁止自己写脚本硬合批次结果(会丢 cross_source 标记/去重/守卫/机械回填,实测产出孤儿绑定)。"
              "处置只有两种:照单同简报重派该批;或主会话自己按简报绑、把结果写成该批 _result.py 再重跑本脚本。",
              file=sys.stderr)
        return 1
    print(f"\n✓ 全部批次到齐并合并完成({', '.join(wrote)})。接着跑步骤 3 三件套。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
