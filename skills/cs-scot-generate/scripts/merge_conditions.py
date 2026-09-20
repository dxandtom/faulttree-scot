#!/usr/bin/env python3
"""把子代理写的批次结果机械合并进 <ind>_analyzer.py:验收、去重、格式守卫、写入文件一次完成。

为什么要它:绑定结果(几百条)若走"子代理回复 → 主代理转录 → Edit 重打"要三过模型上下文——
实测撑爆主会话(自动压缩)、写入代理 600s 流挂。改为:子代理把结果写成
<OUT>/dispatch/<ind>_batch<N>_result.py(各写各的、零竞态),本脚本串行合并进产物:
  · 逐批验收:UTF-8 可读、无 U+FFFD 乱码、ast 可解析为 list[dict];
  · 格式守卫:点名「C/G 既无 contains 也无 equals」(op/target 之类野字段内核不读,
    实测整源假排除)与「缺 node_id」的条目——条目保留、修绑定去,内核对空 targets 也会明确拒绝;
  · 完全重复的条目去重(实测子代理会把同一段重复两遍);
  · 用 ast 定位 <ind>_analyzer.py 里的 CONDITIONS 赋值块整体替换(空白脚本/已填过的旧内容都识别),幂等。
**先到先合**:哪批结果文件在就合哪批,可反复跑——"谁先回来就先写谁"天然成立,不必等全齐。

用法:
    merge_conditions.py --out "<OUT>" [--indicators cmd,dblg,...]
退出码:0=全部批次到齐且合并完成;1=有缺批/坏批(照打印重派或等结果到齐后重跑);2=用法/产物错误。
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

_BRIEF = re.compile(r"^(?P<ind>.+)_batch(?P<n>\d+)\.txt$")


def _record_batch_timings(out: Path, ddir: Path, plan: dict) -> None:
    """每批绑定耗时 = 结果文件 mtime − 任务说明 mtime(纯文件时间,零模型参与),幂等追加进
    <OUT>/timing.json(重复合并不重记)。提效汇报的数据从这里拿,不靠人掐表。"""
    import datetime as _dt
    import json as _json
    p = Path(out) / "timing.json"
    try:
        recs = _json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:  # noqa: BLE001
        recs = []
    seen = {(r.get("step"), r.get("ind"), r.get("batch")) for r in recs if isinstance(r, dict)}
    added = []
    for ind in sorted(plan):
        for n in sorted(plan[ind]):
            bp, rp = ddir / f"{ind}_batch{n}.txt", ddir / f"{ind}_batch{n}_result.py"
            if ("bind_batch", ind, n) in seen or not (bp.exists() and rp.exists()):
                continue
            dt = rp.stat().st_mtime - bp.stat().st_mtime
            if dt <= 0:                                  # 任务说明比结果还新(重生成过)→ 算不出,跳过
                continue
            recs.append({"step": "bind_batch", "ind": ind, "batch": n, "seconds": round(dt, 1),
                         "at": _dt.datetime.fromtimestamp(rp.stat().st_mtime).isoformat(timespec="seconds")})
            added.append((ind, n, dt))
    if added:
        p.write_text(_json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
        print("绑定批次耗时(任务说明生成→结果写入,取自文件时间):"
              + "、".join(f"{i} 批{n} {d / 60:.1f}分" for i, n, d in added) + " → timing.json")


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
            warns.append(f"  ⚠ [{tag}] 「{eid}」缺 node_id——挂不回树,覆盖度/RCA 都找不到它")
        if (cls in ("C", "G") and not e.get("contains") and not e.get("equals")
                and not e.get("decision") and not e.get("sub")):
            warns.append(f"  ⚠ [{tag}] 「{eid}」C/G 缺 contains/equals(op/target 等野字段内核不读)——运行时会被内核拒为数据不足,修绑定格式")
    return warns


def write_conditions(analyzer: Path, entries) -> str | None:
    """用 ast 定位 CONDITIONS 赋值块并整体替换(空白脚本/旧内容都识别)。返回错误串或 None。"""
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
        return "找不到 CONDITIONS 赋值块(不是 assemble 生成的脚本框架?)"
    block = "CONDITIONS = [\n" + "".join(f"    {e!r},\n" for e in entries) + "]"
    lines = src.splitlines(keepends=True)
    new_src = "".join(lines[:span[0] - 1]) + block + "\n" + "".join(lines[span[1]:])
    analyzer.write_text(new_src, encoding="utf-8")
    try:
        ast.parse(new_src)                              # 写入后自检,绝不留语法坏文件
    except SyntaxError as e:
        return f"写入后语法自检失败:{e}"
    return None


def _discover_plan(ddir: Path, indicators: str) -> dict:
    """按 dispatch/ 里的任务说明发现 ind -> [批号...];--indicators 给了则只留这些源。"""
    plan = {}                                           # ind -> [批号...](按任务说明发现)
    for p in sorted(ddir.iterdir()):
        m = _BRIEF.match(p.name)
        if m and m.group("ind") != "route":             # route_batch*.txt 是路由任务说明(make_dispatch --route),
            plan.setdefault(m.group("ind"), []).append(int(m.group("n")))  # 结果是 JSON 不是绑定,不归这里收
    if indicators:
        keep = {x.strip() for x in indicators.split(",") if x.strip()}
        plan = {k: v for k, v in plan.items() if k in keep}
    return plan


def _require_marker(ddir: Path) -> bool:
    """dispatch/manifest.json 声明了 complete_marker 才启用完成标记检查。"""
    require_marker = False                              # 老 dispatch 目录没有派发标记文件 → 不启用标记检查(向后兼容)
    mf = ddir / "manifest.json"
    if mf.exists():
        try:
            import json as _json
            require_marker = _json.loads(mf.read_text(encoding="utf-8")).get("complete_marker") is True
        except Exception:  # noqa: BLE001
            pass
    return require_marker


def _load_tree_nodes(out: Path) -> dict:
    """树权威节点表(唯一的 tree/*.tree_index.json 的 nodes);读不到 → {} 并提示按原样合并。"""
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
    return tree_nodes


def _cross_amnesty(ddir: Path, ind: str, n: int, rp: Path, entries, err: str):
    """cross 批缺完成标记的机械补证:结果的 node_id 集覆盖任务说明的候选集 = 内容齐全,代收。返回 (entries, err)。"""
    # cross 特赦通道:cross 规约要求本批**每条候选都表态**(单源批允许不认领,享受不了这条),
    # 所以"缺完成标记"可以机械补证——结果里的 node_id 集合覆盖任务说明的候选集合 = 内容齐全,代收。
    # 实测弱模型重派后仍丢标记行、然后主会话手工往文件里补标记(让截断检查失效的危险动作),这里堵住。
    e2, err2 = parse_result(rp, False)
    bp_ = ddir / f"{ind}_batch{n}.txt"
    exp = set(re.findall(r"^\[(\w+)\]", bp_.read_text(encoding="utf-8"), re.M)) \
        if bp_.exists() else set()
    if not err2 and exp and exp <= {str(x.get("node_id")) for x in e2 if isinstance(x, dict)}:
        print(f"⚠ cross 批{n}:缺完成标记,但本批 {len(exp)} 条候选已逐条表态(机械核对齐全)——代收。"
              "禁止手工往结果文件补 # BATCH_COMPLETE(那会让截断检查失效)")
        return e2, None
    return entries, err


def _collect_batch(ddir: Path, ind: str, n: int, require_marker: bool):
    """收一批结果:.json 代收改名 → 验收解析(cross 缺标记特赦)→ 拒收 class:S。返回 (entries, 未完成原因或 None)。"""
    rp = ddir / f"{ind}_batch{n}_result.py"
    jp = ddir / f"{ind}_batch{n}_result.json"
    if not rp.exists() and jp.exists():         # 子代理把扩展名写成 .json(路由批惯性,实测一轮 7 份)
        jp.rename(rp)                           # → 机械代收改名,验收流程一字不松
        print(f"⚠ {ind} 批{n}:结果写成了 .json,已代收为 {rp.name}(规约是 _result.py,验收照常)")
    if not rp.exists():
        return None, f"{ind} 批{n}:结果文件未到({rp.name})——等子代理/用同一份任务说明重派"
    entries, err = parse_result(rp, require_marker)
    if err and ind == "cross" and "BATCH_COMPLETE" in err:
        entries, err = _cross_amnesty(ddir, ind, n, rp, entries, err)
    if err:
        return None, f"{ind} 批{n}:{err}——用同一份任务说明重派该批"
    s_ids = [str(e.get("node_id") or "?") for e in entries
             if isinstance(e, dict) and str(e.get("class") or "").upper() == "S"]
    if s_ids:                                    # S(代码类)只接受人工/调优表来源:模型产出=绕过全部 DSL 检查
        return None, (f"{ind} 批{n}:含 class:S 条目({', '.join(s_ids[:5])})——代码类只许人工/调优表"
                      "来源,绑定子代理不得产出;从结果文件删掉这些条目再重跑(确需脚本逻辑请人工补)")
    return entries, None


def _extract_cross_marks(ind: str, entries, cross_cands: dict):
    """抽出子代理的「涉及多源」标记汇入补绑候选(不进本源脚本)。返回 (剩余条目, 标记数)。"""
    marks = [e for e in entries            # 不进本源脚本(它不是可执行绑定)
             if e.get("decision") == "cross_source"]
    entries = [e for e in entries if e.get("decision") != "cross_source"]
    for m in marks:
        nid = m.get("node_id") or "?"
        c = cross_cands.setdefault(nid, {"node_id": nid, "node": m.get("node"),
                                         "sources": [], "reasons": []})
        if ind not in c["sources"]:
            c["sources"].append(ind)
        r = str(m.get("mapping_reason") or "").strip()
        if r and r not in c["reasons"]:
            c["reasons"].append(r)
    return entries, len(marks)


def _backfill(ind: str, tag: str, entries, tree_nodes: dict):
    """机械回填 id/node/condition_text/source(tree_index 为权威),顺带点名不在树里/缺 source/缺理由。
    返回 (decision 占位数, 点名列表)。"""
    # 机械回填:id/node/condition_text/source 以 tree_index 为权威(子代理只交判定;
    # 它抄的判据常被截断,一律覆盖)。cross 的 source=查询落点,由补绑代理给,不覆盖。
    n_dec, warns = 0, []
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
                warns.append(f"  ⚠ [{tag}] 「{nid}」cross 真绑定缺顶层 source(最终查询落点的真实源名;"
                             "D 组合写第一个 sub 的源)——运行时按顶层 source 取数,缺了整条数据不足,补上")
        elif nid and tree_nodes:
            warns.append(f"  ⚠ [{tag}] 「{nid}」不在树里(node_id 抄错?)——该条挂不回任何节点")
        if e.get("decision") == "insufficient_data":
            n_dec += 1
            if ind == "cross" and not str(e.get("mapping_reason") or "").strip():
                warns.append(f"  ⚠ [{tag}] 「{nid}」decision 占位没写 mapping_reason——"
                             "cross 规约:绑不了必须写清缺哪一跳,空理由等于没表态")
    return n_dec, warns


def _dedup(entries, seen: set, merged: list) -> int:
    """完全重复的条目去重(实测子代理会把同一段重复两遍):新条目并入 merged,返回去掉的重复数。"""
    dup = 0
    for e in entries:
        key = repr(sorted(e.items(), key=lambda kv: kv[0]))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        merged.append(e)
    return dup


def _merge_source(ind: str, batches, ddir: Path, out: Path, require_marker: bool, tree_nodes: dict,
                  cross_cands: dict):
    """合并一个源的全部批次:逐批收/验/抽标记/回填/去重 → 写进 <ind>_analyzer.py。
    返回 (未完成项, 漂移点名, 是否已写入)。"""
    pending, warns = [], []
    merged, seen = [], set()
    got = []
    n_dec = n_marks = 0                             # 检查滥填 decision 与滥标 cross_source 用
    for n in sorted(batches):
        tag = f"{ind}_batch{n}_result"
        entries, err = _collect_batch(ddir, ind, n, require_marker)
        if err:
            pending.append(err)
            continue
        if ind != "cross":                          # 子代理的「涉及多源」标记:抽出来汇入补绑候选,
            entries, k = _extract_cross_marks(ind, entries, cross_cands)
            n_marks += k
        n_d, ws = _backfill(ind, tag, entries, tree_nodes)
        n_dec += n_d
        warns += ws
        dup = _dedup(entries, seen, merged)
        warns += drift_warnings(entries, tag)
        got.append(f"批{n}={len(entries)}条" + (f"(去重-{dup})" if dup else ""))
    if not merged:
        if got:                                     # 结果到了、验收也过了,只是没有可写入本源的条目
            print(f"{ind}:{' / '.join(got)}——到货但无直接绑定可写入"
                  + (f"({n_marks} 条跨源标记已汇入候选,走 --cross 补绑)" if n_marks
                     else "(子代理判定这些判据都不归本源答)"))
        else:
            print(f"{ind}:0 批到货,未写入。")
        return pending, warns, False
    analyzer = out / "scripts" / f"{ind}_analyzer.py"
    err = write_conditions(analyzer, merged)
    if err:
        pending.append(f"{ind}:{err}")
        return pending, warns, False
    print(f"{ind}:{' / '.join(got)} → 合并 {len(merged)} 条,已写入 {analyzer.name}"
          + (f"(缺 {len(batches) - len(got)} 批,到货后重跑本脚本即可)" if len(got) < len(batches) else ""))
    # 滥填/滥标检查(软提示):decision 占比畸高=对全树逐条表态(规约明令"不归本源答的整条不写",
    # 实测 log 源 92 条全 decision 让覆盖度检查失去意义);cross_source 占比畸高=把"不归我答"误标跨源。
    # cross 批不适用:CROSS_RULES 要求每条候选都表态,decision 占位是义务不是灌水(实测 8/13 占位
    # 被误点名,弱模型照"删多余占位"的建议琢磨删条目)——cross 的检查是上面的"占位必须带理由"。
    if ind != "cross" and len(merged) >= 10 and n_dec > len(merged) * 0.5:
        warns.append(f"  ⚠ [{ind}] 灌水嫌疑:{n_dec}/{len(merged)} 条是 decision 占位——"
                     "「明显不归本源答的判据整条不写」,占位只给「确属本源该答却答不了」的;复核并删多余占位")
    if n_marks >= 10 and n_marks > (len(merged) + n_marks) * 0.5:
        warns.append(f"  ⚠ [{ind}] cross_source 滥标嫌疑:{n_marks} 条标记——跨源=「单靠任何一个源都答不完」;"
                     "「归别的源单独答」不是跨源,整条不写;复核候选清单")
    return pending, warns, True


def _write_cross_candidates(ddir: Path, cross_cands: dict, plan: dict, pending_inds: set) -> None:
    """有 cross_source 标记 → 写 dispatch/cross_candidates.json;单源批次完整扫过且零标记 → 清掉陈旧候选。"""
    cp = ddir / "cross_candidates.json"
    if cross_cands:
        import json as _json
        cp.write_text(_json.dumps(sorted(cross_cands.values(), key=lambda c: c["node_id"]),
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n跨源判据候选 {len(cross_cands)} 条(子代理标记 cross_source)→ {cp}")
        print("  下一步:python make_dispatch.py --out \"<OUT>\" --cross 生成跨源补绑任务说明,"
              "派一个子代理填 param_from 绑定,再跑本脚本合并进 cross_analyzer.py。")
    else:
        noncross = [i for i in plan if i != "cross"]
        # 本轮完整扫过全部单源批次且一个标记都没有 → 陈旧候选必须清掉,防按旧候选补绑早已改判的节点
        if noncross and not (pending_inds - {"cross"}) and cp.exists():
            cp.unlink()
            print("\n单源批次里已无 cross_source 标记 → 陈旧的 cross_candidates.json 已清除。")


def export_tuning_table(out: Path, reason: str) -> None:
    """自动导出调优表 tree/<短名>.tuning.xlsx:用产物 scripts/ 里的 export_tree_excel.py 以子进程跑一遍。
    调优表是树与绑定的可编辑事实源,组装后、每次合并绑定后都要与产物保持一致;导出失败(如缺 openpyxl)
    只提示不阻断,随时可手动重跑。表比 tree_index 新(可能有未导入的人工修改)时导出器自己会备份 .bak 并提示。"""
    exp = out / "scripts" / "export_tree_excel.py"
    if not exp.exists():
        print(f"  调优表未导出:产物里没有 scripts/export_tree_excel.py(重跑组装可补齐)")
        return
    r = subprocess.run([sys.executable, str(exp), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    notes = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    if r.returncode == 0:
        xls = sorted((out / "tree").glob("*.tuning.xlsx"))
        print(f"  调优表已{reason}: {xls[0] if xls else out / 'tree'}")
        for ln in notes:                                 # 转发导出器的提示(如备份了未导入的人工修改)
            print("  " + ln)
        return
    tail = (notes or (r.stdout or "").strip().splitlines() or ["无输出"])[-1]
    print(f"  ⚠ 调优表未导出({tail})——需要时手动跑: python {exp} --out {out}(写 xlsx 需要 openpyxl)")


def _report(all_warns: list, pending: list, wrote: list) -> int:
    """收尾打印:漂移点名(最多 40 条)、未完成项(退出码 1)或全部完成(0)。"""
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
              "处置只有两种:用同一份任务说明重派该批;或主会话自己按任务说明绑定、把结果写成该批 _result.py 再重跑本脚本。",
              file=sys.stderr)
        return 1
    print(f"\n✓ 全部批次到齐并合并完成({', '.join(wrote)})。接着跑步骤 3 三件套。")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="机械合并批次绑定结果进 <ind>_analyzer.py(验收/去重/格式守卫/写入)")
    ap.add_argument("--out", required=True, help="场景产物目录 <OUT>")
    ap.add_argument("--indicators", default="", help="逗号分隔;缺省=按 dispatch/ 里的任务说明自动发现")
    args = ap.parse_args()
    out = Path(args.out)
    ddir = out / "dispatch"
    if not ddir.is_dir():
        print(f"✗ 没有 {ddir}——先跑 make_dispatch.py", file=sys.stderr)
        return 2

    plan = _discover_plan(ddir, args.indicators)
    if not plan:
        print("✗ dispatch/ 里没有任何任务说明(或 --indicators 全滤掉了)", file=sys.stderr)
        return 2
    require_marker = _require_marker(ddir)
    tree_nodes = _load_tree_nodes(out)

    pending, all_warns, wrote = [], [], []
    cross_cands = {}                                    # node_id -> {node_id, sources:[…], reasons:[…]}
    pending_inds = set()                                # 有缺批/坏批的源(候选清理要避开不完整的轮次)
    for ind in sorted(plan):
        pend, warns, ok = _merge_source(ind, plan[ind], ddir, out, require_marker, tree_nodes, cross_cands)
        pending += pend
        all_warns += warns
        if pend:
            pending_inds.add(ind)
        if ok:
            wrote.append(ind)

    _write_cross_candidates(ddir, cross_cands, plan, pending_inds)
    _record_batch_timings(out, ddir, plan)
    if wrote:                                            # 绑定写进了 analyzer:调优表随之刷新,保持事实源一致
        export_tuning_table(out, "按最新绑定刷新")
    return _report(all_warns, pending, wrote)


if __name__ == "__main__":
    sys.exit(main())
