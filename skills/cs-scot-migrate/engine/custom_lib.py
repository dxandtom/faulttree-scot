#!/usr/bin/env python3
"""S 类(代码类)预制函数库——人手调优专用。

绑定 JSON 用 `{"class":"S","source":"<源>","func":"<函数名>","args":{…}}` 引用这里的函数。
每个函数统一签名 `fn(table, args, load)`:
  table = 绑定 source 那个源的表,形态 (header, rows):header=列名列表,rows=一行一个 dict;
  args  = 绑定 JSON 里的 args 原样传入(dict);
  load  = 函数:load("config") 取其他源的表(同样 (header, rows)),该源没喂返回 None。
返回与内核同形的事实(二选一,内核 wrapper 会校验形状、兜异常):
  {"status": "ok", "predicate_satisfied": True/False, …证据字段…, "explain": "一句人话"}
  {"status": "insufficient_data", "reason": "缺什么"}
新函数照下面的样子写 + @register("名字") 挂进注册表;别 print、别写文件、别自己读 excel。
"""
from __future__ import annotations

from pathlib import Path

REGISTRY = {}


def register(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def pick_values(table, spec):
    """工具:按取值子句 {filter?, column, extract?} 从表抽出值列表(复用内核的 filter/extract 语义;
    抽不到返回 [])。spec 与绑定手册第二节的取值子句同形。"""
    import condition_kernels as _k
    if not table:
        return []                                        # 没声明 source(table=None)/该源没喂:抽不到
    header, rows = table
    flt = spec.get("filter")
    if flt:
        t, err = _k._apply_filter((header, rows), flt)
        if err:
            return []
        header, rows = t
    col, _how = _k.resolve_column(header, spec.get("column", ""))
    if not col:
        return []
    out = []
    for r in rows:
        v = r.get(col, "")
        if _k._is_null(v):
            continue
        ex = spec.get("extract")
        if ex:
            v = _k._extract_val(v, ex)
            if v is None:
                continue
        v = str(v).strip()
        if v:
            out.append(v)
    return out


@register("sum_extracts_vs_threshold")
def sum_extracts_vs_threshold(table, args, load):
    """两处各抽一个数,相加比阈值(DSL 无加法时的正牌解,如 BGP+ASBR 路由数)。
    args: {"a": 取值子句, "b": 取值子句, "op": ">="(< <= > >= == 之一), "threshold": 数}"""
    nums = []
    for key in ("a", "b"):
        spec = args.get(key)
        if not isinstance(spec, dict):
            return {"status": "insufficient_data", "reason": f"args 缺取值子句「{key}」"}
        vals = pick_values(table, spec)
        if not vals:
            return {"status": "insufficient_data",
                    "reason": f"args[{key}] 一个值也没抽到(filter/extract 与数据对不上,或对应命令未采集)"}
        try:
            nums.append(float(vals[0].replace(",", "")))
        except ValueError:
            return {"status": "insufficient_data",
                    "reason": f"args[{key}] 抽到的不是数:「{vals[0][:40]}」"}
    op = str(args.get("op", ">="))
    try:
        thr = float(args["threshold"])
    except (KeyError, TypeError, ValueError):
        return {"status": "insufficient_data", "reason": "args.threshold 缺失或不是数"}
    cmp = {"<": nums[0] + nums[1] < thr, "<=": nums[0] + nums[1] <= thr,
           ">": nums[0] + nums[1] > thr, ">=": nums[0] + nums[1] >= thr,
           "==": nums[0] + nums[1] == thr}
    if op not in cmp:
        return {"status": "insufficient_data", "reason": f"op「{op}」不认识(只认 < <= > >= ==)"}
    total, ok = nums[0] + nums[1], cmp[op]
    return {"status": "ok", "predicate_satisfied": ok,
            "value": total, "op": op, "threshold": thr, "parts": nums,
            "explain": f"{nums[0]:g}+{nums[1]:g}={total:g} {op} {thr:g} → {'成立' if ok else '不成立'}"}


# ---------- 收编:datacom-fault-tree-tester 交付的 fault_check_*.py 直接当判据跑 ----------
# 这些脚本吃「网元目录」(<网元>/CONFIG|CMD|LOG/*.txt 裸文本),吐 {matched, evidence, details}。
# 下面的通用转接头负责:①备数据(优先 --data raw=<extracted目录> 原始 txt 直读;没有就从
# config/log/cmd 三表兜底重建临时目录——临时目录写文件是本库的**特批例外**,用完即删,
# 节点脚本自己仍然不许写文件)②调脚本入口 ③把结果翻译成内核三态事实。
# 责任边界(用户拍板):脚本内部判断逻辑照单全收,对错归脚本;本壳只在「数据整体缺失」
# (一个网元都没备出来)时报数据不足——那是我们自己的边界,不是替脚本兜。

def _fc_ne_names(*tables):
    """从各表的 NEName 列收集网元名(保持出现顺序);一个都没有 → ["NE"]。"""
    seen = []
    for t in tables:
        if not t:
            continue
        header, rows = t
        if "NEName" not in header:
            continue
        for r in rows:
            ne = str(r.get("NEName", "")).strip() or "NE"
            if ne not in seen:
                seen.append(ne)
    return seen or ["NE"]


def _fc_rebuild_config(table, root):
    """config 表(NEName/CfgType/Module/level1..level5)→ 各网元 CONFIG/<网元>_cfg.txt。
    逆 zip2csv/config2csv:每行取最深非空 levelN 当一行,缩进 N-1 个空格;level1 换块时补 # 分隔。"""
    if not table:
        return
    header, rows = table
    lv = [c for c in ("level1", "level2", "level3", "level4", "level5") if c in header]
    if not lv or "NEName" not in header:
        return
    per = {}
    for r in rows:
        ne = str(r.get("NEName", "")).strip() or "NE"
        buf = per.setdefault(ne, {"lines": [], "prev_l1": None})
        depth, text = 0, ""
        for i, c in enumerate(lv, start=1):
            v = str(r.get(c, "")).strip()
            if v:
                depth, text = i, v
        if not text:
            continue
        l1 = str(r.get("level1", "")).strip()
        if depth == 1 or l1 != buf["prev_l1"]:
            if depth == 1 or buf["prev_l1"] is not None:
                buf["lines"].append("#")
            buf["prev_l1"] = l1
        buf["lines"].append(" " * (depth - 1) + text)
    for ne, buf in per.items():
        d = root / ne / "CONFIG"
        d.mkdir(parents=True, exist_ok=True)
        content = f"<{ne}>\n" + "\n".join(buf["lines"]) + "\n#\n"
        (d / f"{ne}_cfg.txt").write_text(content, encoding="utf-8")


def _fc_rebuild_cmd(table, root):
    """cmd/diag 表(NEName/CmdName/ObjContent 或 CmdResult)→ 各网元 CMD/<网元>_cmd.txt。
    连续同名命令行(超长回显被 zip2csv 拆行)拼回一段;每段前置 <网元>命令 提示符行。"""
    if not table:
        return
    header, rows = table
    ccol = "ObjContent" if "ObjContent" in header else ("CmdResult" if "CmdResult" in header else None)
    if ccol is None or "CmdName" not in header:
        return
    per = {}
    for r in rows:
        ne = str(r.get("NEName", "")).strip() or "NE"
        cmd = str(r.get("CmdName", "")).strip()
        body = str(r.get(ccol, ""))
        segs = per.setdefault(ne, [])
        if segs and segs[-1][0] == cmd:                  # 拆行续段:拼回同一命令
            segs[-1][1].append(body)
        else:
            segs.append((cmd, [body]))
    for ne, segs in per.items():
        d = root / ne / "CMD"
        d.mkdir(parents=True, exist_ok=True)
        parts = [f"<{ne}>{cmd}\n" + "\n".join(bodies).rstrip("\n") for cmd, bodies in segs]
        (d / f"{ne}_cmd.txt").write_text("\n".join(parts) + "\n", encoding="utf-8")


def _fc_rebuild_log(table, root):
    """log 表 → 各网元 LOG/<网元>_logbuffer.txt:优先 Log 列(原始整行);缺 Log 列时用
    Time/Module/Level/Summary/Content 拼一行近似。"""
    if not table:
        return
    header, rows = table
    if "NEName" not in header:
        return
    per = {}
    for r in rows:
        ne = str(r.get("NEName", "")).strip() or "NE"
        line = str(r.get("Log", "")).strip()
        if not line:
            parts = [str(r.get(c, "")).strip() for c in ("Time", "Module", "Level", "Summary", "Content")]
            line = " ".join(p for p in parts if p)
        if line:
            per.setdefault(ne, []).append(line)
    for ne, lines in per.items():
        d = root / ne / "LOG"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{ne}_logbuffer.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fc_layout_from_raw(raw, tmp_root):
    """原始数据目录(zip2csv 的 extracted/)整形成脚本期望的 <根>/<网元>/CONFIG|CMD|LOG 布局。
    结构已合格(网元子目录齐、无 DIAG→CMD 改名需要)→ 直接用原目录零拷贝;
    需要整形(DIAG 要当 CMD 用 / 平铺单网元)→ 拷到临时镜像(txt 诊断文件,量可控)。"""
    import shutil
    raw = Path(raw)
    kinds = ("CONFIG", "CMD", "LOG", "DIAG")
    if any((raw / k).is_dir() for k in kinds):           # 平铺:raw 本身就是单网元 → 镜像成 <root>/<网元名>/
        nes = [raw]
        flat = True
    else:
        nes = [d for d in sorted(raw.iterdir()) if d.is_dir()
               and any((d / k).is_dir() for k in kinds)]
        flat = False
    if not nes:
        return None
    need_mirror = flat or any((d / "DIAG").is_dir() and not (d / "CMD").is_dir() for d in nes)
    if not need_mirror:
        return raw
    root = tmp_root / "raw_mirror"
    for d in nes:
        ne_dst = root / (raw.name if flat else d.name)
        for k in ("CONFIG", "CMD", "LOG"):
            if (d / k).is_dir():
                shutil.copytree(d / k, ne_dst / k, dirs_exist_ok=True)
        if (d / "DIAG").is_dir() and not (d / "CMD").is_dir():
            shutil.copytree(d / "DIAG", ne_dst / "CMD", dirs_exist_ok=True)   # 平台 DIAG 已更名 CMD:目录别名
    return root


def _fc_prepare_data(load, tmp_root):
    """备出脚本要吃的数据根目录。返回 (根路径|None, 说明)。"""
    raw = load("raw_dir")
    if isinstance(raw, str) and raw.strip() and Path(raw).is_dir():
        root = _fc_layout_from_raw(raw.strip(), tmp_root)
        if root is not None:
            return root, "原始数据目录直读"
    tables = [load("config"), load("log"), load("cmd") or load("diag")]
    if not any(tables):
        return None, "既无 --data raw=<原始数据目录>,也无 config/log/cmd 任何一张表"
    root = tmp_root / "rebuilt"
    root.mkdir(parents=True, exist_ok=True)
    _fc_rebuild_config(tables[0], root)
    _fc_rebuild_log(tables[1], root)
    _fc_rebuild_cmd(tables[2], root)
    if not any(d.is_dir() for d in root.iterdir()):
        return None, "三表兜底重建失败:表里没有 NEName/level/CmdName 等预期列,重建不出网元目录"
    return root, "无原始目录,已从数据表兜底重建"


def _fc_normalize(result):
    """脚本产出 → {网元: 单元结果} 映射。认三种形状:全局包裹(details.devices)/
    网元名直挂顶层 / 单网元裸结果。认不出 → None。"""
    if not isinstance(result, dict):
        return None
    det = result.get("details")
    if isinstance(det, dict) and isinstance(det.get("devices"), dict):
        return det["devices"]
    if "matched" in result or "error" in result:         # 全局裸结果(脚本不分网元、只给一个总结论)
        return {"(整体)": result}
    if result and all(isinstance(v, dict) and ("matched" in v or "error" in v or "status" in v)
                      for v in result.values()):
        return result
    return None


@register("fault_check")
def fault_check(table, args, load):
    """通用转接头:跑一个 datacom-fault-tree-tester 交付的 fault_check_*.py 判本节点。
    args: {"script": "custom/fault_check_xxx.py"(scripts/ 下相对路径,必填),
           "entry": "diagnose"(入口函数名,缺省 diagnose),
           "per_ne": false(true=入口吃单网元目录,本壳逐网元调用汇总),
           "entry_kwargs": {}(入口额外关键字参数,如 judgment_conditions)}
    数据:优先 load("raw_dir")(runner --data raw=<extracted目录>);没有则三表兜底重建。
    翻译:任一网元 matched → 成立;全网元 error/无网元 → 数据不足;否则不成立(网元级 error 如实注记)。"""
    import contextlib
    import importlib.util
    import io as _io
    import sys as _sys
    import tempfile

    rel = str(args.get("script") or "").strip()
    if not rel:
        return {"status": "insufficient_data", "reason": "args.script 缺失(scripts/ 下相对路径,如 custom/fault_check_xxx.py)"}
    here = Path(__file__).resolve().parent
    script = (here / rel).resolve()
    if not str(script).startswith(str(here)):
        return {"status": "insufficient_data", "reason": f"args.script 路径越界(必须在 scripts/ 之下):{rel}"}
    if not script.exists():
        return {"status": "insufficient_data", "reason": f"脚本不存在:{rel}(先把交付包脚本拷进 scripts/custom/)"}
    entry_name = str(args.get("entry") or "diagnose")
    kwargs = args.get("entry_kwargs") or {}
    if not isinstance(kwargs, dict):
        return {"status": "insufficient_data", "reason": "args.entry_kwargs 须是 dict"}

    with tempfile.TemporaryDirectory(prefix="fc_") as td:
        root, how = _fc_prepare_data(load, Path(td))
        if root is None:
            return {"status": "insufficient_data", "reason": f"数据整体缺失,脚本未运行:{how}"}
        sdir = str(script.parent)
        if sdir not in _sys.path:
            _sys.path.insert(0, sdir)                    # 交付脚本 import search_config 等同目录共享模块
        spec = importlib.util.spec_from_file_location(f"fc_{script.stem}", script)
        mod = importlib.util.module_from_spec(spec)
        sink = _io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            spec.loader.exec_module(mod)                 # 脚本运行期的 print 全部收走,不进执行日志
            fn = getattr(mod, entry_name, None)
            if not callable(fn):
                cands = [n for n in dir(mod) if callable(getattr(mod, n)) and not n.startswith("_")]
                return {"status": "insufficient_data",
                        "reason": f"脚本没有入口函数「{entry_name}」;文件顶层可调用:{cands[:8]}"}
            if args.get("per_ne"):
                ne_map = {}
                for ne_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                    r = fn(str(ne_dir), **kwargs)
                    ne_map[ne_dir.name] = r if isinstance(r, dict) else {"error": f"入口返回非 dict:{type(r).__name__}"}
            else:
                ne_map = _fc_normalize(fn(str(root), **kwargs))

    if ne_map is None:
        return {"status": "insufficient_data",
                "reason": "脚本返回的形状认不出(既非 {matched,...} 也非 {网元:{matched,...}} "
                          "也非 details.devices 包裹)——核对 entry/per_ne 配置"}
    if not ne_map:
        return {"status": "insufficient_data", "reason": "脚本一个网元都没读到(目录结构与脚本预期对不上,或数据缺失)"}
    hit, miss, bad = [], [], []
    evidence = []
    for ne, r in ne_map.items():
        if not isinstance(r, dict) or ("matched" not in r):
            bad.append((ne, str((r or {}).get("error") or r)[:120] if isinstance(r, dict) else str(r)[:120]))
        elif r.get("matched"):
            hit.append(ne)
            for ev in (r.get("evidence") or [])[:5]:
                evidence.append(f"{ne}: {str(ev)[:200]}" if not str(ev).startswith(f"{ne}:") else str(ev)[:200])
        else:
            miss.append(ne)
    if not hit and not miss:                             # 全部网元都没给出 matched(error/坏形状)
        return {"status": "insufficient_data",
                "reason": "所有网元都未产出判定:" + "; ".join(f"{n}: {e}" for n, e in bad[:5])}
    sat = bool(hit)
    note = f";另有 {len(bad)} 个网元未判({'; '.join(f'{n}: {e}' for n, e in bad[:3])})" if bad else ""
    return {"status": "ok", "predicate_satisfied": sat,
            "matched_nes": hit, "checked_nes": len(hit) + len(miss), "data_via": how,
            "evidence": evidence[:8],
            "explain": (f"脚本判定:{len(hit)}/{len(hit) + len(miss)} 个网元命中"
                        f"({'、'.join(hit[:5])})" if sat
                        else f"脚本判定:{len(hit) + len(miss)} 个网元均未命中") + note}


@register("latest_by_seq_then_match")
def latest_by_seq_then_match(table, args, load):
    """先按 seq 列取"最新一轮采集"的行,再在目标列里找特征串("取最新一次再判"的口径,DSL 表达不了)。
    args: {"filter": 选行(可选,同 filter 子句), "seq_column": "CmdSeq",
           "column": "DataValue", "contains": "Down"}"""
    import condition_kernels as _k
    if not table:
        return {"status": "insufficient_data",
                "reason": "本函数要主表:绑定里声明 source=<要判的源>(或该源本轮没喂数据)"}
    header, rows = table
    flt = args.get("filter")
    if flt:
        t, err = _k._apply_filter((header, rows), flt)
        if err:
            return {"status": "insufficient_data", "reason": f"filter 失败:{err}"}
        header, rows = t
    if not rows:
        return {"status": "insufficient_data", "reason": "filter 后 0 行(该指标项本次未采集)"}
    scol, _ = _k.resolve_column(header, str(args.get("seq_column", "CmdSeq")))
    vcol, _ = _k.resolve_column(header, str(args.get("column", "")))
    if not scol or not vcol:
        return {"status": "insufficient_data",
                "reason": f"找不到列(seq_column={args.get('seq_column')}/column={args.get('column')})"}

    def _seq_key(r):
        s = str(r.get(scol, "")).strip()
        try:
            return (1, float(s))
        except ValueError:
            return (0, s)
    latest_seq = max((str(r.get(scol, "")).strip() for r in rows), key=lambda s: _seq_key({scol: s}))
    latest = [r for r in rows if str(r.get(scol, "")).strip() == latest_seq]
    target = str(args.get("contains", ""))
    if not target:
        return {"status": "insufficient_data", "reason": "args.contains 为空(要找的特征串)"}
    bad = [r for r in latest if target in str(r.get(vcol, ""))]
    return {"status": "ok", "predicate_satisfied": bool(bad),
            "matched": len(bad), "total": len(latest), "seq": latest_seq,
            "hits": [str(r.get(vcol, ""))[:80] for r in bad[:3]],
            "explain": f"最新一轮采集({scol}={latest_seq})共 {len(latest)} 行,"
                       f"{vcol} 含「{target}」的 {len(bad)} 行 → {'成立' if bad else '不成立'}"}
