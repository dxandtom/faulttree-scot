#!/usr/bin/env python3
"""S 类(代码类)预制函数库——人手调优专用。

绑定 JSON 用 `{"class":"S","source":"<源>","func":"<函数名>","args":{…}}` 引用这里的函数。
每个函数统一签名 `fn(table, args, load)`:
  table = 绑定 source 那个源的表,形态 (header, rows):header=列名列表,rows=一行一个 dict;
  args  = 绑定 JSON 里的 args 原样传入(dict);
  load  = 函数:load("config") 取其他源的表(同样 (header, rows)),该源没喂返回 None。
返回与内核同形的事实(二选一,内核 wrapper 会校验形状、捕获异常):
  {"status": "ok", "predicate_satisfied": True/False, …证据字段…, "explain": "一句易懂的说明"}
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
    抽不到返回 [])。spec 与 condition_binding_schema.md 的取值子句(extract/filter)同形。"""
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


# ---------- 导入:datacom-fault-tree-tester 交付的 fault_check_*.py 直接当判据跑 ----------
# 这些脚本吃「网元目录」(<网元>/CONFIG|CMD|LOG/*.txt 裸文本),吐 {matched, evidence, details}。
# 下面的通用适配函数负责:①备数据(优先 --data raw=<extracted目录> 直接读原始 txt;没有就从
# config/log/cmd 三张表重建临时目录作为备用做法——临时目录写文件是本库的**特批例外**,用完即删,
# 节点脚本自己仍然不许写文件)②调脚本入口 ③把结果翻译成内核三态事实。
# 责任边界(用户拍板):脚本内部判断逻辑照单全收,对错归脚本;本适配层只在「数据整体缺失」
# (一个网元都没备出来)时报数据不足——那是我们自己的边界,不是替脚本兜。

class _StrPath(str):
    """路径 str/Path 双兼容:是 str(老脚本 os.path.join/字符串拼接照旧零影响),又能当 pathlib 用
    (/ 拼接、exists/glob/iterdir 等)。交付脚本两种写法都常见,且规范此前没钉死类型——实测按 Path
    写的入口(`ne_dir / "CMD"`)吃到纯 str 直接 TypeError(fault_check_LDP定时器超时.py:185)。"""

    def __truediv__(self, other):
        import os as _os
        return _StrPath(_os.path.join(str(self), str(other)))

    def __getattr__(self, name):                         # str 没有的属性(exists/glob/name…)转给 Path
        return getattr(Path(str(self)), name)


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
    need_mirror = flat or any((d / "DIAG").is_dir() != (d / "CMD").is_dir() for d in nes)
    if not need_mirror:
        return raw
    root = tmp_root / "raw_mirror"
    for d in nes:
        ne_dst = root / (raw.name if flat else d.name)
        for k in kinds:                                  # 原名全部保留:脚本按交付时的目录名找数据
            if (d / k).is_dir():
                shutil.copytree(d / k, ne_dst / k, dirs_exist_ok=True)
        # DIAG/CMD 是同一类数据的两代目录名,脚本两种都可能认:缺哪个补哪个当别名,**绝不改名**。
        # 实测(n4):旧逻辑把 DIAG 改名成 CMD,认 DIAG 的脚本找不到自己的诊断文件——直跑 true、
        # 经本适配层整链跑变成判不动,就是这一处砍掉了原名。
        if (d / "DIAG").is_dir() and not (d / "CMD").is_dir():
            shutil.copytree(d / "DIAG", ne_dst / "CMD", dirs_exist_ok=True)
        if (d / "CMD").is_dir() and not (d / "DIAG").is_dir():
            shutil.copytree(d / "CMD", ne_dst / "DIAG", dirs_exist_ok=True)
    return root


def _fc_prepare_data(load, tmp_root):
    """备出脚本要吃的数据根目录。返回 (根路径|None, 说明)。
    失败时 reason 必须把 **raw 的下落**说清(未提供/路径不存在/错层)——实测:raw 没读到时静默
    转入三表重建的备用做法,备用做法再失败,报出来的只有备用做法的错,操作者根本定位不到"其实是 raw 没传对"。"""
    raw = load("raw_dir")
    if isinstance(raw, str) and raw.strip() and Path(raw).is_dir():
        root = _fc_layout_from_raw(raw.strip(), tmp_root)
        if root is not None:
            return root, "原始数据目录直读"
        raw_note = (f"raw 给了但不合布局({raw.strip()}):底下找不到 <网元>/CONFIG|CMD|LOG 结构"
                    "——常见错:给了顶层目录,应给**网元层的父目录**(check_data.py 会告诉你正确的层)")
    elif isinstance(raw, str) and raw.strip():
        raw_note = f"raw 给了但路径不存在/不是目录:{raw.strip()}"
    else:
        raw_note = "raw 未提供(runner 没带 --data raw=<原始数据目录>)"
    tables = [load("config"), load("log"), load("cmd") or load("diag")]
    if not any(tables):
        return None, f"数据两路都不通:{raw_note};config/log/cmd 三表也一张都没有"
    root = tmp_root / "rebuilt"
    root.mkdir(parents=True, exist_ok=True)
    _fc_rebuild_config(tables[0], root)
    _fc_rebuild_log(tables[1], root)
    _fc_rebuild_cmd(tables[2], root)
    if not any(d.is_dir() for d in root.iterdir()):
        detail = []
        for nm, t in (("config", tables[0]), ("log", tables[1]), ("cmd/diag", tables[2])):
            if not t:
                continue
            header, rows = t
            miss = "缺 NEName 列" if "NEName" not in (header or []) else ("0 行数据" if not rows else "列不合预期")
            detail.append(f"{nm} 表 {len(rows)} 行({miss})")
        return None, (f"数据两路都不通:{raw_note};从三张表重建的备用做法也失败"
                      f"({';'.join(detail) or '表载入异常'}——缺 NEName 列常见于**裸日志文件被当表喂**,"
                      "先用 zip2csv 转)")
    return root, f"从三张表重建({raw_note})"


def _fc_no_judgment(ne_map) -> bool:
    """整张网元 map 一个能用的判定都没有(空/全 error/全 undetermined/形状怪)→ True。
    用于识别"单网元风格脚本吃不了多网元根目录"的空转(实测:脚本没做规范要求的多网元探测,
    直接在根目录下找 LOG/,找不到就整体 undetermined),识别到就改成逐网元调用。"""
    if not isinstance(ne_map, dict) or not ne_map:
        return True
    return not any(isinstance(r, dict) and _fc_tristate(r.get("matched")) is not None
                   for r in ne_map.values())


def _fc_tristate(v):
    """matched 值 → True / False / None(判不了)。tester 契约是**字符串**四态
    ("true"/"false"/"undetermined"/"insufficient_data";旧包三态无 insufficient_data,照收),
    旧旧契约是 bool——字符串 "false" 按真值判会极性反转(非空字符串恒真),必须显式映射;
    大小写不敏感;数值当 bool;认不出的值按判不了(诚实)。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None                                          # "undetermined"/"insufficient_data"/空/未知值


def _fc_gap_kind(v) -> str:
    """判不了(_fc_tristate=None)的细分:缺数据还是判不动——四态契约的增量信息。
    "insufficient_data" → no_data(缺数据:没采到命令/文件,补采可解);
    其余("undetermined"/空/认不出) → cannot_judge(数据在,判不出——判据/脚本质量信号)。"""
    return "no_data" if str(v).strip().lower() == "insufficient_data" else "cannot_judge"


def _fc_item_str(x) -> str:
    """matched_items/evidence 条目转成易懂文字:交付契约里条目常是 dict(关键字段+_source_file/
    _source_line),str() 直转是 Python repr 且截断后不成话——机械拼 k=v,溯源元键单独缀
    「@文件:行」,不许把 repr 漏进报告证据栏。"""
    if isinstance(x, dict):
        kv = " ".join(f"{k}={v}" for k, v in x.items() if not str(k).startswith("_"))[:140]
        src = str(x.get("_source_file") or "").replace("\\", "/").rsplit("/", 1)[-1]
        line = str(x.get("_source_line") or "")
        loc = (f"@{src}" if src else "") + (f":{line}" if line else "")
        return (kv + loc) or str(x)
    return str(x)


def _fc_normalize(result):
    """脚本产出 → {网元: 单元结果} 映射。认三种形状:全局包裹(details.devices)/
    网元名直挂顶层 / 单网元裸结果。认不出 → None。"""
    if not isinstance(result, dict):
        return None
    det = result.get("details")
    if isinstance(det, dict) and isinstance(det.get("devices"), dict):
        return det["devices"]
    inner = result.get("results")                        # {"fault_node":…, "results":{网元:{…}}} 信封形态
    if isinstance(inner, dict) and inner and all(
            isinstance(v, dict) and ("matched" in v or "error" in v or "status" in v)
            for v in inner.values()):
        return inner
    if "matched" in result or "error" in result:         # 全局裸结果(脚本不分网元、只给一个总结论)
        return {"(整体)": result}
    if result and all(isinstance(v, dict) and ("matched" in v or "error" in v or "status" in v)
                      for v in result.values()):
        return result
    return None


@register("fault_check")
def fault_check(table, args, load):
    """通用适配函数:跑一个 datacom-fault-tree-tester 交付的 fault_check_*.py 判本节点。
    args: {"script": "custom/fault_check_xxx.py"(scripts/ 下相对路径,必填),
           "entry": "diagnose"(入口函数名,缺省 diagnose),
           "per_ne": false(true=入口接受单网元目录,本适配函数逐网元调用汇总),
           "entry_kwargs": {}(入口额外关键字参数,如 judgment_conditions)}
    数据:优先 load("raw_dir")(runner --data raw=<extracted目录>);没有则从三张表重建(备用做法)。
    结果四种形态都认:旧按网元 map(matched 为 bool)/ results 信封 / details.devices 包裹 /
    交付契约**全局形态**(顶层一个 matched,字符串四态 "true"/"false"/"undetermined"/
    "insufficient_data";旧三态包无 insufficient_data 照收,evidence 为 {type,id,data} 结构体数组)。
    翻译:任一实体判真 → 成立;全部实体判不了/无实体 → status=insufficient_data(不当作"正常"),
    并带 gap_kind 细分 no_data(缺数据,补采可解)/cannot_judge(数据在判不动,判据质量信号)/mixed;
    否则不成立;matched:false 却带 details.error(缺数据却报成阴性的老问题)按判不了(no_data)处理。"""
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
            return {"status": "insufficient_data", "gap_kind": "no_data",
                    "reason": f"数据整体缺失,脚本未运行:{how}"}
        sdir = str(script.parent)
        if sdir not in _sys.path:
            _sys.path.insert(0, sdir)                    # 交付脚本 import search_config 等同目录共享模块

        def _fc_purge_shared():
            # 同一进程连续调多个交付脚本时,它们 import 的同目录共享模块(search_*.py 等)会被
            # sys.modules 全局缓存:前一个包载入的模块连同其模块级状态(缓存、全局变量)直接
            # 喂给后面的脚本——这些脚本在交付方那里本是各自独立进程跑的,串门会污染判定
            # (实测:n4 直跑 true、经本适配层在 cross 整链里跑变 false)。调用前后都清掉脚本目录
            # 下的缓存模块,保证每个脚本冷启动。
            for _n, _m in list(_sys.modules.items()):
                _f = getattr(_m, "__file__", None)
                try:
                    if _f and str(Path(_f).resolve().parent) == str(script.parent.resolve()):
                        del _sys.modules[_n]
                except OSError:
                    continue

        _fc_purge_shared()
        spec = importlib.util.spec_from_file_location(f"fc_{script.stem}", script)
        mod = importlib.util.module_from_spec(spec)
        sink = _io.StringIO()
        convention_note = ""                             # 调用方式仲裁的注记(逐网元重调时写进 explain)
        argv_bak, _sys.argv = _sys.argv, [str(script)]   # argv 隔离:CLI 型脚本库调用时常仍偷读 sys.argv,
        try:                                             # 会吃到 cross_analyzer 的 -o 临时路径当自己的参数
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):     # (实测:路径当正则炸 \U)
                spec.loader.exec_module(mod)             # 脚本运行期的 print 全部收走,不进执行日志
                fn = getattr(mod, entry_name, None)
                if not callable(fn):
                    cands = [n for n in dir(mod) if callable(getattr(mod, n)) and not n.startswith("_")]
                    return {"status": "insufficient_data",
                            "reason": f"脚本没有入口函数「{entry_name}」;文件顶层可调用:{cands[:8]}"}
                def _per_ne_map():
                    m = {}
                    for ne_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                        r = fn(_StrPath(ne_dir), **kwargs)
                        m[ne_dir.name] = r if isinstance(r, dict) else {"error": f"入口返回非 dict:{type(r).__name__}"}
                    return m

                if args.get("per_ne"):
                    ne_map = _per_ne_map()
                else:
                    ne_map = _fc_normalize(fn(_StrPath(root), **kwargs))
                    if _fc_no_judgment(ne_map):          # 调用方式仲裁:单网元风格脚本(没做多网元探测)
                        retry = _per_ne_map()            # 吃不了网元层根目录——逐网元重调一次,
                        if not _fc_no_judgment(retry):   # 不改判断逻辑只换喂法;重调也空转就维持原判(数据不足)
                            ne_map = retry
                            convention_note = ("(调用方式:脚本不认多网元根目录,已按逐网元重调"
                                               "——脚本未做规范要求的多网元探测,请交付方补)")
        finally:
            _sys.argv = argv_bak
            _fc_purge_shared()

    if ne_map is None:
        return {"status": "insufficient_data", "gap_kind": "cannot_judge",
                "reason": "脚本返回的形状认不出(既非 {matched,...} 也非 {网元:{matched,...}} "
                          "也非 details.devices 包裹)——核对 entry/per_ne 配置"}
    if not ne_map:
        return {"status": "insufficient_data", "gap_kind": "no_data",
                "reason": "脚本一个网元都没读到(目录结构与脚本预期对不上,或数据缺失)"}
    hit, miss, bad = [], [], []
    bad_kinds = set()                                    # 判不了的细分:no_data(缺数据)/cannot_judge(判不动)
    evidence, hits_raw, hit_why, miss_why = [], [], [], []
    for ne, r in ne_map.items():
        if not isinstance(r, dict) or ("matched" not in r):
            bad.append((ne, str((r or {}).get("error") or r)[:120] if isinstance(r, dict) else str(r)[:120]))
            bad_kinds.add("cannot_judge")
            continue
        tri = _fc_tristate(r.get("matched"))
        det = r.get("details") if isinstance(r.get("details"), dict) else {}
        err = det.get("error") or r.get("error")
        reason = str(det.get("reason") or "").strip()
        if tri is None:                                  # undetermined/insufficient_data → 该实体判不了
            bad.append((ne, str(reason or err or f"matched={r.get('matched')!r},判不了")[:120]))
            bad_kinds.add(_fc_gap_kind(r.get("matched")))
        elif tri is False and err:                       # 缺数据却报 false+error(报成阴性的老问题)→ 按判不了
            bad.append((ne, str(err)[:120]))
            bad_kinds.add("no_data")
        elif tri:
            hit.append(ne)
            if reason:                                   # 脚本给的命中理由(文字说明)不能丢,进 explain
                hit_why.append((ne, reason[:200]))
            for ev in (r.get("evidence") or [])[:5]:
                if isinstance(ev, dict):                 # 新契约证据条目:{type, id, data:{source_files, matched_items}}
                    label = ":".join(str(ev[k]) for k in ("type", "id") if ev.get(k)) or str(ev)[:200]
                    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    items = data.get("matched_items")
                    if isinstance(items, list) and items:    # 命中原文逐字引用(定长截断防上下文爆炸)
                        label += f" 命中{len(items)}条:" + "、".join(f"「{_fc_item_str(x)[:120]}」" for x in items[:2])
                        for x in items:
                            if len(hits_raw) < 3:
                                hits_raw.append(_fc_item_str(x)[:160])
                    srcs = data.get("source_files")
                    if isinstance(srcs, list) and srcs:
                        names = "、".join(str(s).replace("\\", "/").rsplit("/", 1)[-1] for s in srcs[:2])
                        label += f"(来源:{names}{'…' if len(srcs) > 2 else ''})"
                    evidence.append(((f"{ne}: " if ne != "(整体)" else "") + label)[:400])
                else:
                    evidence.append(f"{ne}: {str(ev)[:200]}" if not str(ev).startswith(f"{ne}:") else str(ev)[:200])
        else:
            miss.append(ne)
            if reason:                                   # 未命中的文字理由同样留痕(如"步骤1成立但步骤2输出非空")
                miss_why.append((ne, reason[:160]))
    if not hit and not miss:                             # 全部网元都没给出 matched(error/坏形状/四态的两种判不了)
        gap = ("no_data" if bad_kinds == {"no_data"}
               else "cannot_judge" if bad_kinds == {"cannot_judge"} else "mixed")
        prefix = {"no_data": "数据不足(缺数据,补采可解)",
                  "cannot_judge": "数据在,无法判断(判据/脚本判不动)",
                  "mixed": "部分缺数据、部分无法判断"}[gap]
        return {"status": "insufficient_data", "gap_kind": gap, "script": rel,
                "reason": f"{prefix}:" + "; ".join(f"{n}: {e}" for n, e in bad[:5])}
    sat = bool(hit)
    note = f";另有 {len(bad)} 个网元未判({'; '.join(f'{n}: {e}' for n, e in bad[:3])})" if bad else ""
    if sat and hit_why:
        note = ";脚本说明:" + "; ".join((f"{n}: " if n != "(整体)" else "") + w for n, w in hit_why[:2]) + note
    elif not sat and miss_why:
        note = ";未命中说明:" + "; ".join((f"{n}: " if n != "(整体)" else "") + w for n, w in miss_why[:2]) + note
    out = {"status": "ok", "predicate_satisfied": sat,
           "matched_nes": hit, "checked_nes": len(hit) + len(miss), "data_via": how,
           "script": rel,                                # 被判脚本真身(相对路径):追问"哪个脚本判的"有据
           "evidence": evidence[:8],
           "explain": (f"脚本判定:{len(hit)}/{len(hit) + len(miss)} 个网元命中"
                       f"({'、'.join(hit[:5])})" if sat
                       else f"脚本判定:{len(hit) + len(miss)} 个网元均未命中") + note + convention_note}
    if hits_raw:
        out["hits"] = hits_raw                           # 命中原文 → draft_rca 的 actual 里"命中原文:「…」"
    return out


@register("latest_by_seq_then_match")
def latest_by_seq_then_match(table, args, load):
    """先按 seq 列取"最新一轮采集"的行,再在目标列里找特征串("取最新一次再判"这种规则,DSL 表达不了)。
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
