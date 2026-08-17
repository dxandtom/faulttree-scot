#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_contract.py — 交付脚本契约体检(《判断方法_规范.md》附录的机械化检查)

用法:
    python check_contract.py <交付包目录|交付包.zip|fault_check_*.py> [--strict] [--json]
                             [--data <数据目录>] [--timeout N]

体检对象:
  - 交付包(目录/zip):包内每个 fault_check_*.py 逐个体检 + 判断方法.md 必填字段 +
    包自包含(脚本 import 的 search_*.py 必须在包里);包里带 测试数据/ 时试跑一次,
    把 matched 打出来当回归基线(不评对错;崩了/形态坏了才算 FAIL)
  - 单个 .py:只做脚本级检查(生成阶段自查用)

分级(体检项与规范附录逐条对应,FAIL/WARN 行自带修法):
  FAIL = 契约红线且执行/结论必受影响,下游转接头兜不住 —— 必须修
  WARN = 契约不达标,但下游转接头运行时可兜、或静态无法确证 —— 新脚本必须修(--strict);
         存量收编可放行,但要记台账
退出码: 0=通过(默认允许 WARN;--strict 时 WARN 也算不过) 1=不通过 2=用法/输入错误

只用标准库;体检不改任何被检文件。
"""
import argparse
import ast
import io
import importlib.util
import json
import re
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

# 入口候选:与 cs-scot-migrate 的探测口径一致,但不含 check_ne/diagnose_ne——
# 那俩是"内部帮手"命名,撞上过入口误对号的实测事故,本体检只认全局入口。
ENTRY_OK = ("fault_check", "diagnose", "main", "check")
TRI = ("true", "false", "undetermined", "insufficient_data")   # 四态契约(规范 v1.2);变量名沿用
MD_REQUIRED = ("节点名称", "故障描述", "判断条件", "命中条件", "输出", "数据来源")

# py<3.10 没有 sys.stdlib_module_names 时的兜底清单(常用标准库)
_STDLIB_FALLBACK = {
    "abc", "argparse", "array", "ast", "atexit", "base64", "binascii", "bisect", "bz2",
    "calendar", "cmath", "codecs", "collections", "configparser", "contextlib", "copy",
    "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib", "enum", "errno",
    "fnmatch", "fractions", "functools", "gc", "getopt", "getpass", "glob", "gzip",
    "hashlib", "heapq", "html", "http", "importlib", "inspect", "io", "itertools",
    "json", "keyword", "locale", "logging", "lzma", "math", "multiprocessing", "numbers",
    "operator", "os", "pathlib", "pickle", "platform", "pprint", "queue", "random", "re",
    "reprlib", "secrets", "select", "shelve", "shutil", "signal", "socket", "sqlite3",
    "stat", "statistics", "string", "struct", "subprocess", "sys", "tarfile", "tempfile",
    "textwrap", "threading", "time", "tokenize", "traceback", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref", "xml", "zipfile",
    "zlib",
}


def _is_stdlib(top: str) -> bool:
    names = getattr(sys, "stdlib_module_names", None)
    return top in names if names else top in _STDLIB_FALLBACK


class Item:
    def __init__(self, iid, tier, ok, msg, fix=""):
        self.id, self.tier, self.ok, self.msg, self.fix = iid, tier, ok, msg, fix

    def to_dict(self):
        return {"id": self.id, "tier": self.tier, "ok": self.ok,
                "msg": self.msg, "fix": self.fix}


def _fail(iid, msg, fix):
    return Item(iid, "FAIL", False, msg, fix)


def _warn(iid, msg, fix):
    return Item(iid, "WARN", False, msg, fix)


def _pass(iid, msg):
    return Item(iid, "PASS", True, msg)


# ---------------- 静态检查(AST) ----------------

def _guarded_nodes(tree):
    """模块级 `if __name__ == '__main__'` 分支里的全部节点 id 集合。"""
    out = set()
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        t = node.test
        names = [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        consts = [c.value for c in ast.walk(t) if isinstance(c, ast.Constant)]
        if "__name__" in names and "__main__" in consts:
            for sub in ast.walk(node):
                out.add(id(sub))
    return out


def _reachable_funcs(tree, entry_name):
    """从入口函数出发、按模块内同名调用可达的函数集合(粗粒度调用图)。"""
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
    seen, todo = set(), [entry_name]
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        for sub in ast.walk(funcs[name]):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                todo.append(sub.func.id)
    return {funcs[n] for n in seen}


def _find_entry(tree):
    """(入口名, FunctionDef) — 只在模块顶层找;认不出返回 (None, None)。"""
    tops = {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for cand in ENTRY_OK:
        if cand in tops:
            return cand, tops[cand]
    return None, None


def _static_check(script: Path, local_names, pkg_files, entry_hint=None):
    """返回 (items, 烟测入口名)。local_names=同包可 import 的模块名集合;
    entry_hint=下游仲裁选定的入口(给了就按它烟测/扫可达路径——S1 仍按契约命名评判)。"""
    items = []
    src = script.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [_fail("S0", f"脚本语法错误:{e}", "先把脚本改到能通过 python 编译")], None

    # S1 入口
    entry_name, entry_def = _find_entry(tree)
    tops = {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if entry_name is None:
        helper = tops & {"check_ne", "diagnose_ne"}
        hint = (f"(只有内部帮手 {'/'.join(sorted(helper))},帮手不算入口)" if helper else "")
        items.append(_fail("S1", f"没有可识别的入口函数{hint}",
                           "补顶层 def fault_check(data_dir):做全局诊断入口(规范附录5)"))
    else:
        args = entry_def.args
        pos = list(args.posonlyargs) + list(args.args)
        n_default = len(args.defaults)
        required = pos[: len(pos) - n_default] if len(pos) >= n_default else pos
        extra_required = [a.arg for a in required[1:]]
        if not pos:
            items.append(_fail("S1", f"入口 {entry_name}() 没有位置参数",
                               "入口签名必须是 fault_check(data_dir)(规范附录5)"))
        elif extra_required:
            items.append(_fail(
                "S1", f"入口 {entry_name} 的额外参数 {extra_required} 没有默认值",
                "额外参数一律具名+字面量默认值——下游只传 data_dir,缺省必须能跑(规范附录5)"))
        elif entry_name != "fault_check":
            items.append(_warn(
                "S1", f"入口名是 {entry_name},不是契约钉死的 fault_check(转接头认旧名,兜得住)",
                "改名为 fault_check(data_dir),与文件名前缀同名(规范附录5)"))
        else:
            note = "" if pos[0].arg == "data_dir" else f"(首参名是 {pos[0].arg},下游按位置传,能跑)"
            items.append(_pass("S1", f"入口 fault_check({pos[0].arg}) 签名合规{note}"))

    # S2 argv/print/exit 纪律:入口可达函数 + 模块级(守卫分支外)
    smoke_entry = entry_hint if entry_hint and entry_hint in tops else entry_name
    guarded = _guarded_nodes(tree)
    scan_funcs = _reachable_funcs(tree, smoke_entry) if smoke_entry else set()
    scan_nodes = []
    for f in scan_funcs:
        scan_nodes.extend(ast.walk(f))
    for node in tree.body:
        if id(node) in guarded or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scan_nodes.extend(ast.walk(node))
    argv_aliases = set()                                 # from sys import argv [as x] 的别名也要认
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sys" and not node.level:
            argv_aliases.update(al.asname or "argv" for al in node.names if al.name == "argv")
    hits = {"argv": 0, "print": 0, "exit": 0}
    for node in scan_nodes:
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Attribute) and node.attr == "argv" and \
                isinstance(node.value, ast.Name) and node.value.id == "sys":
            hits["argv"] += 1
        elif isinstance(node, ast.Name) and node.id in argv_aliases \
                and isinstance(node.ctx, ast.Load):
            hits["argv"] += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "print":
            hits["print"] += 1
        elif isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "exit"
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "sys")
                or (isinstance(node.func, ast.Name) and node.func.id == "exit")):
            hits["exit"] += 1
    bads = [f"{k}×{v}" for k, v in hits.items() if v]
    if bads:
        items.append(_warn(
            "S2", f"__main__ 守卫外发现 {'、'.join(bads)}(入口可达路径/模块级;转接头隔离 argv、"
                  "收走 stdout、拦 SystemExit,兜得住)",
            "sys.argv/print/sys.exit 全部挪进 if __name__ == '__main__' 分支;"
            "库调用出错走 undetermined(规范附录5)"))
    else:
        items.append(_pass("S2", "argv/print/exit 纪律合规(守卫外无)"))

    # S3 文件发现 .txt/.log 双吃
    low = src.lower()
    if ".log" in low:
        items.append(_pass("S3", "文件发现见 .log 处理(与 .txt 双吃)"))
    elif ".txt" in low:
        items.append(_fail(
            "S3", "文件发现只认 .txt,未见 .log 处理——现场常见 log.log,整目录漏掉(实测事故)",
            "目录扫描改成 .txt/.log 双后缀、大小写不敏感收(规范附录5)"))
    else:
        items.append(_warn(
            "S3", "源码里未见 .txt/.log 后缀常量,无法静态确认文件发现方式",
            "确认数据文件发现对 .txt/.log 双吃、大小写不敏感(规范附录5);以烟测为准"))

    # S4 多网元探测痕迹
    if re.search(r"iterdir|listdir|scandir|os\.walk|glob\(", src):
        items.append(_pass("S4", "见目录遍历调用(多网元子目录探测有痕迹)"))
    else:
        items.append(_warn(
            "S4", "未见目录遍历调用(iterdir/listdir/scandir/walk/glob)——多网元根目录喂进来"
                  "可能零判定(转接头会逐网元重调兜底)",
            "入口自带多网元/单网元自动探测:data_dir 下有网元子目录→逐网元;"
            "直接是 CONFIG/CMD/LOG→单网元(规范附录5)"))

    # S5/S6 依赖:三方件红线 + 自包含
    third, missing_local = [], []
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods = [node.module]
        for m in mods:
            top = m.split(".")[0]
            if _is_stdlib(top) or top in local_names:
                continue
            if top.startswith("search_"):
                missing_local.append(top)
            else:
                third.append(top)
    if third:
        items.append(_fail(
            "S5", f"import 了第三方库/包外模块:{sorted(set(third))}——三方件平台装不上,"
                  "包外模块下游收不进,落地即 ImportError",
            "只许标准库+包内共享模块;解析用 re/csv/json/zipfile/xml.etree 等标准库实现,"
            "自写的帮手模块要么并进主脚本要么按 search_*.py 命名随包(规范附录7/8)"))
    else:
        items.append(_pass("S5", "依赖合规(只用标准库+包内模块)"))
    if missing_local:
        where = "交付包" if pkg_files else "脚本同目录"
        items.append(_fail(
            "S6", f"import 的共享模块 {sorted(set(missing_local))} 不在{where}里——落地后 import 崩",
            "把脚本 import 的每个 search_*.py 打进交付包(不管改没改;规范附录8);"
            "cs-scot-migrate 收编时可自动补库,但正本必须自包含"))
    elif pkg_files is not None:
        items.append(_pass("S6", "自包含合规(本地 import 的模块都在包里)"))

    return items, smoke_entry


# ---------------- 烟测(动态) ----------------

class _Shield:
    """护现场:argv 换掉、stdout/stderr 收走(烟测期间脚本的输出不许污染体检报告)。"""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.bak = (sys.argv, sys.stdout, sys.stderr)
        sys.argv = [self.name]
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()

    def __exit__(self, *a):
        sys.argv, sys.stdout, sys.stderr = self.bak
        return False


def _run_boxed(fn, timeout):
    box = {}

    def run():
        try:
            box["r"] = fn()
        except BaseException as e:      # SystemExit 也要接
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "timeout", None
    if "e" in box:
        return "error", box["e"]
    return "ok", box.get("r")


def _import_script(script: Path, timeout):
    sdir = str(script.parent)
    inserted = sdir not in sys.path
    if inserted:
        sys.path.insert(0, sdir)

    def do():
        spec = importlib.util.spec_from_file_location(f"cc_{script.stem}", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    with _Shield(script.name):
        status, val = _run_boxed(do, timeout)
    if inserted and status != "ok":
        # 导入成功时留着 sdir——后续调用里脚本可能再做延迟 import
        sys.path.remove(sdir)
    return status, val


def _tri_of(result):
    """把四形态结果压成一个三态字符串 + 形态标签(与转接头 _fc_normalize 同口径:
    details.devices 包裹 → results 信封 → 全局 matched → 按网元字典;只看不改)。"""
    if not isinstance(result, dict):
        return None, "non-dict"
    det = result.get("details")
    if isinstance(det, dict) and isinstance(det.get("devices"), dict):
        vals = [v.get("matched") for v in det["devices"].values() if isinstance(v, dict)]
        shape = "devices"
    elif isinstance(result.get("results"), dict):
        vals = [v.get("matched") for v in result["results"].values() if isinstance(v, dict)]
        shape = "wrapped"
    elif "matched" in result:
        vals, shape = [result.get("matched")], "global"
    elif result and all(isinstance(v, dict) for v in result.values()):
        vals = [v.get("matched") for v in result.values()]
        shape = "per-ne"
    else:
        vals, shape = [], "empty" if not result else "unknown"
    norm = []
    for v in vals:
        if isinstance(v, bool):
            norm.append("true" if v else "false")
            shape += "+bool"
        elif isinstance(v, str) and v.lower() in TRI:
            norm.append(v.lower())
        else:
            norm.append("undetermined")
    if "true" in norm:
        return "true", shape
    if "false" in norm:
        return "false", shape
    if "insufficient_data" in norm and "undetermined" not in norm:
        return "insufficient_data", shape                # 纯缺数据;混"判不动"时按 undetermined 归并
    return "undetermined", shape


def _shape_items(iid, result, expect_undetermined, label):
    """按输出契约给一次调用结果打分。"""
    items = []
    tri, shape = _tri_of(result)
    if tri is None:
        return [_fail(iid, f"{label}:返回 {type(result).__name__},不是 dict",
                      "入口必须返回全局结果 dict(matched+evidence;规范附录6)")]
    if shape == "unknown":
        return [_fail(iid, f"{label}:返回 dict 形态认不出(无 matched/results,值也不是网元结果)",
                      "按规范附录6 的全局形态返回")]
    if shape == "empty":
        return [_warn(iid, f"{label}:返回空 dict(没有 matched 键;转接头按无判定处理)",
                      '哪怕无数据也要返回 {"matched":"insufficient_data","details":{"reason":…}}(规范附录6)')]
    if shape.startswith(("per-ne", "wrapped", "devices")):
        kind = {"p": "按网元字典", "w": "results 信封", "d": "details.devices 包裹"}[shape[0]]
        items.append(_warn(iid + "s", f"{label}:返回{kind}形态(契约要求全局形态;转接头兜得住)",
                           "顶层改成一个 matched+evidence,网元写进 evidence 的 type/id(规范附录6)"))
    if "+bool" in shape:
        items.append(_warn(iid + "b", f"{label}:matched 是布尔值(契约要求字符串四态;转接头兜得住)",
                           'matched 用 "true"/"false"/"undetermined"/"insufficient_data" 字符串(规范附录6)'))
    if expect_undetermined and tri in ("true", "false"):
        items.append(_fail(
            iid, f'{label}:空数据判成 "{tri}"——缺数据冒充结论,下游会把"没数据"当"排除/坐实"(实测事故)',
            '判定所需数据缺失时必须 matched:"insufficient_data"(旧三态包 undetermined 也认)'
            '+details.reason 写明缺什么(规范附录4)'))
    elif not items:
        reason = ""
        if tri in ("undetermined", "insufficient_data") and shape == "global":
            r = (result.get("details") or {}).get("reason") if isinstance(result.get("details"), dict) else None
            if not r:
                items.append(_warn(iid + "r", f"{label}:{tri} 但 details.reason 没写原因",
                                   f"{tri} 必须带 details.reason("
                                   "insufficient_data 写缺哪条命令/文件;undetermined 写为什么判不动;规范附录6)"))
                return items
            reason = f'(reason:{str(r)[:60]})'
        items.append(_pass(iid, f'{label}:matched="{tri}" 形态合规{reason}'))
    return items


def _smoke_check(script: Path, entry_name, timeout, per_ne=False, entry_kwargs=None):
    """空目录 + 假多网元目录两连喂,返回 items。per_ne=入口按单网元驱动(下游逐网元喂):
    多网元根目录那一喂不适用,跳过。"""
    kw = entry_kwargs or {}
    items = []
    status, mod = _import_script(script, timeout)
    if status == "timeout":
        return [_fail("D1", f"模块导入超时(>{timeout}s)——模块级別有重活/死循环",
                      "模块级只放 def/常量,重活挪进函数")], None
    if status == "error":
        return [_fail("D1", f"模块导入即崩:{type(mod).__name__}: {mod}",
                      "模块级代码(守卫外)不许执行诊断/读参;查报错行")], None
    fn = getattr(mod, entry_name, None)
    if not callable(fn):
        return [_fail("D1", f"导入后取不到入口 {entry_name}(被条件定义/覆盖?)",
                      "入口必须无条件定义在模块顶层")], None
    items.append(_pass("D1", "模块导入干净(无守卫外副作用)"))

    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty"
        empty.mkdir()
        with _Shield(script.name):
            status, val = _run_boxed(lambda: fn(str(empty), **kw), timeout)
        if status == "timeout":
            items.append(_fail("D2t", f"空目录调用超时(>{timeout}s)", "检查死循环/大 IO"))
        elif status == "error":
            hint = "先 data_path = Path(data_dir) 显式包装——下游按字符串传参(规范附录5)" \
                if isinstance(val, TypeError) and "str" in str(val) else \
                "缺数据必须返回 undetermined,不许抛异常/退出(规范附录4)"
            items.append(_fail("D2x", f"空目录(str 入参)调用崩了:{type(val).__name__}: {val}", hint))
        else:
            items.extend(_shape_items("D2", val, True, "空目录喂 str"))

        if per_ne:
            return items, fn                             # 单网元驱动:多网元根目录那一喂不适用
        root = Path(td) / "root"
        for sub in ("CONFIG", "CMD", "LOG"):
            (root / "网元A" / sub).mkdir(parents=True)
        with _Shield(script.name):
            status, val = _run_boxed(lambda: fn(str(root), **kw), timeout)
        if status == "timeout":
            items.append(_fail("D3t", f"多网元空目录调用超时(>{timeout}s)", "检查死循环"))
        elif status == "error":
            items.append(_fail("D3x", f"多网元根目录调用崩了:{type(val).__name__}: {val}",
                               "多网元结构必须能吃(规范附录5);空数据走 undetermined"))
        else:
            items.extend(_shape_items("D3", val, True, "多网元空目录"))
    return items, fn


def _baseline_run(fn, data_dir: Path, timeout):
    """对真实数据试跑,记录基线(matched/证据数);只有崩/形态坏才 FAIL。"""
    with _Shield("baseline"):
        status, val = _run_boxed(lambda: fn(str(data_dir)), timeout)
    if status == "timeout":
        return [_fail("D4t", f"测试数据试跑超时(>{timeout}s)", "检查性能/死循环")], None
    if status == "error":
        return [_fail("D4x", f"测试数据试跑崩了:{type(val).__name__}: {val}",
                      "有数据都跑不动,交付不出去;按报错修")], None
    items = _shape_items("D4", val, False, f"测试数据({data_dir.name})试跑")
    tri, _ = _tri_of(val)
    ev = len(val.get("evidence") or []) if isinstance(val, dict) else 0
    baseline = {"matched": tri, "evidence_count": ev}
    items.append(Item("D4i", "INFO", True,
                      f"回归基线:matched={tri} evidence={ev}(翻新/修复前后比对用,不评对错)"))
    return items, baseline


# ---------------- 包级检查 ----------------

def _md_check(md: Path):
    text = md.read_text(encoding="utf-8", errors="replace")
    heads = [ln for ln in text.splitlines() if re.match(r"\s*#{1,6}\s", ln)]
    missing = [k for k in MD_REQUIRED if not any(k in h for h in heads)]
    if missing:
        return [_fail("M1", f"{md.name} 缺必填字段:{'、'.join(missing)}",
                      "按《判断方法_规范.md》第四节模板补齐必填章节")]
    return [_pass("M1", f"{md.name} 必填字段齐({'/'.join(MD_REQUIRED)})")]


def check_package(pkg: Path, data_dir=None, timeout=120):
    """体检一个交付包目录。返回 report dict。"""
    report = {"target": str(pkg), "mode": "package", "scripts": [], "md": [],
              "notes": [], "baseline": None}
    fcs = sorted(set(pkg.rglob("fault_check_*.py")))
    other_py = [p for p in pkg.rglob("*.py")
                if not p.name.startswith(("fault_check_", "search_", "verify_"))]
    # 可 import 的"包内模块"只认契约允许随包的两类(fault_check_*/search_*)——别的 .py
    # (自写帮手/调试遗留/测试数据里的杂项)下游迁移不收,这里就得当包外模块拦,两端口径一致
    pkg_mods = {p.stem for p in pkg.rglob("*.py")
                if p.name.startswith(("fault_check_", "search_"))}
    mds = [p for p in sorted(pkg.rglob("判断方法*.md")) if "规范" not in p.name]

    if not mds:
        report["md"].append(_fail("M0", "包里没有 判断方法*.md",
                                  "交付包必须带判断方法文档(SKILL.md 打包清单)").to_dict())
    for md in mds:
        report["md"].extend(i.to_dict() for i in _md_check(md))

    if not fcs:
        stray = [p.name for p in other_py
                 if "def fault_check" in p.read_text(encoding="utf-8", errors="replace")]
        if stray:
            report["md"].append(_fail(
                "P1", f"没有 fault_check_*.py 命名的脚本,但 {stray} 里有入口——命名不合契约,下游收不到",
                "脚本改名为 fault_check_<节点名>.py(SKILL.md 步骤5)").to_dict())
        else:
            report["notes"].append("包里没有诊断脚本(纯文档包):只体检判断方法文档")
    for p in sorted(pkg.rglob("verify_*.py")):
        report["notes"].append(f"调试遗留 {p.name} 混在包里(下游忽略它,建议打包时清掉)")

    test_data = data_dir
    if test_data is None:
        cands = [p for p in pkg.rglob("测试数据") if p.is_dir()]
        test_data = cands[0] if cands else None

    for fc in fcs:
        items, entry = _static_check(fc, pkg_mods, pkg_files=pkg_mods)
        fatal_static = any(i.tier == "FAIL" and i.id in ("S0", "S5", "S6") for i in items)
        if entry and not fatal_static:
            smoke, fn = _smoke_check(fc, entry, timeout)
            items.extend(smoke)
            if fn and test_data is not None:
                bitems, baseline = _baseline_run(fn, test_data, timeout)
                items.extend(bitems)
                if baseline:
                    report["baseline"] = dict(baseline, script=fc.name)
        elif entry:
            report["notes"].append(f"{fc.name}:依赖检查未过,烟测跳过(先修 S5/S6)")
        report["scripts"].append({"script": fc.name, "items": [i.to_dict() for i in items]})
    return report


def check_script(script: Path, data_dir=None, timeout=120, entry=None, per_ne=False,
                 entry_kwargs=None):
    """体检单个脚本(生成阶段自查/收编闸用):同目录当作共享库所在地。
    entry/per_ne/entry_kwargs=下游烟测仲裁的裁决——给了就按裁决口径烟测(S1 命名仍照契约评)。"""
    report = {"target": str(script), "mode": "script", "scripts": [], "md": [],
              "notes": [], "baseline": None}
    local = {p.stem for p in script.parent.glob("*.py")
             if p.name.startswith(("fault_check_", "search_"))}
    items, smoke_entry = _static_check(script, local, pkg_files=None, entry_hint=entry)
    fatal_static = any(i.tier == "FAIL" and i.id in ("S0", "S5", "S6") for i in items)
    if smoke_entry and not fatal_static:
        smoke, fn = _smoke_check(script, smoke_entry, timeout, per_ne=per_ne,
                                 entry_kwargs=entry_kwargs)
        items.extend(smoke)
        if fn and data_dir is not None and not per_ne:
            bitems, baseline = _baseline_run(fn, Path(data_dir), timeout)
            items.extend(bitems)
            if baseline:
                report["baseline"] = dict(baseline, script=script.name)
    elif smoke_entry:
        report["notes"].append("依赖检查未过,烟测跳过(先修 S5/S6)")
    report["scripts"].append({"script": script.name, "items": [i.to_dict() for i in items]})
    return report


def _summarize(report):
    all_items = [i for s in report["scripts"] for i in s["items"]] + report["md"]
    n_fail = sum(1 for i in all_items if i["tier"] == "FAIL")
    n_warn = sum(1 for i in all_items if i["tier"] == "WARN")
    report["fail"], report["warn"] = n_fail, n_warn
    report["pass"] = n_fail == 0
    report["strict_pass"] = n_fail == 0 and n_warn == 0
    return report


def _print_report(report):
    print(f"契约体检:{report['target']}")
    for blk in report["scripts"]:
        print(f"\n== {blk['script']} ==")
        for i in blk["items"]:
            mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[info]"}[i["tier"]]
            print(f"{mark} {i['id']} {i['msg']}")
            if i["fix"] and i["tier"] in ("FAIL", "WARN"):
                print(f"       修法:{i['fix']}")
    if report["md"]:
        print("\n== 文档/包 ==")
        for i in report["md"]:
            mark = "[PASS]" if i["tier"] == "PASS" else f"[{i['tier']}]"
            print(f"{mark} {i['id']} {i['msg']}")
            if i.get("fix") and i["tier"] != "PASS":
                print(f"       修法:{i['fix']}")
    for n in report["notes"]:
        print(f"[note] {n}")
    verdict = "通过" if report["pass"] else "不通过"
    strict = "" if report["strict_pass"] else "(--strict 口径:不通过)" if report["pass"] else ""
    print(f"\n== 汇总 == FAIL {report['fail']} 项 / WARN {report['warn']} 项 → {verdict}{strict}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付脚本契约体检(详见文件头)")
    ap.add_argument("target", help="交付包目录 | 交付包.zip | 单个 fault_check_*.py")
    ap.add_argument("--strict", action="store_true", help="WARN 也算不过(新脚本/打包前用)")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告(机器读)")
    ap.add_argument("--data", default=None, help="额外指定数据目录做试跑基线(默认用包内 测试数据/)")
    ap.add_argument("--timeout", type=int, default=120, help="单次动态调用超时秒数(默认 120)")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    target = Path(args.target)
    if not target.exists():
        print(f"[error] 目标不存在:{target}")
        return 2
    data_dir = Path(args.data) if args.data else None
    if data_dir and not data_dir.is_dir():
        print(f"[error] --data 目录不存在:{data_dir}")
        return 2

    tmp = None
    try:
        if target.is_file() and target.suffix.lower() == ".zip":
            tmp = tempfile.TemporaryDirectory(prefix="contract_")
            with zipfile.ZipFile(target) as zf:
                zf.extractall(tmp.name)
            report = check_package(Path(tmp.name), data_dir, args.timeout)
            report["target"] = str(target)
        elif target.is_dir():
            report = check_package(target, data_dir, args.timeout)
        elif target.suffix == ".py":
            report = check_script(target, data_dir, args.timeout)
        else:
            print(f"[error] 认不出的目标类型:{target}(要目录/zip/.py)")
            return 2
    finally:
        if tmp:
            tmp.cleanup()

    _summarize(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_report(report)
    ok = report["strict_pass"] if args.strict else report["pass"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
