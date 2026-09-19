#!/usr/bin/env python3
"""datacom-fault-tree-tester 的节点交付包 → 场景产物:判据替换 + 绑定更换,完整的机械迁移。

链路(用户拍板):a. 把交付包里人确认过的「判断方法」替换到故障树对应节点的判据上;
b. 把该节点旧绑定去掉,换成一条 S 绑定(func=fault_check 通用适配函数),直接跑交付的脚本。

三种模式:
  匹配模式(不带 --node):解析交付包,和产物故障树逐节点算相似度,列出候选——
      python migrate_fault_check.py --package <交付包目录|zip> --product <场景产物目录>
  执行模式(带 --node):人确认节点号后执行迁移——
      python migrate_fault_check.py --package <…> --product <…> --node n17
      可选:--entry <入口函数名> --per-ne --entry-kwargs '<JSON>'(自动探测不准时手工指定)
  批量模式(多节点合集包 + --auto):逐子包循环,精确同名唯一的全部自动迁入(一轮调优表修改,原子生效),
      匹配不上的列候选,绝不猜;之后逐个 --only "<包节点名>" --node nX 补充匹配——
      python migrate_fault_check.py --package <合集包.zip> --product <…> --auto
  对应表模式(--map <excel>):同事提供的对应表(调优表同款层级列 L1..Ln,**无 node_id**,
      新增 zip 列=交付 zip 名)驱动全自动批量——按「名字路径」定位节点(同名节点靠全路径消歧,
      不再要人指定),按 zip 列把合集里的包匹配到节点——
      python migrate_fault_check.py --package <合集目录|zip> --product <…> --map <对应表.xlsx>

迁移动作(全走产物自带的调优表往返,复用其全部校验):
  1. 交付包脚本(fault_check_*.py + search_*.py 共享模块)拷进 产物/scripts/custom/;
  2. export_tree_excel → 改该节点一行:判据落「判据:原文」列(标签列清空)、单源绑定列清空、
     「绑定:cross」列写 S 绑定 → import_tree_excel 机械编译回产物 → 再 export 让调优表同步;
  3. 产物 cross_analyzer.py 脚本框架过旧(不支持 S 类全量传源/原始日志目录)时,借 cs-scot-generate 的
     框架升级函数就地升级(绑定原样保留);借不到就明确提醒;
  4. 迁移台账记进 产物/tree/migrations.json(哪个节点、来自哪个包、入口是什么)。

设计规则:判断逻辑原样接收(对错归交付脚本,不改写、不"顺手优化");调优表已有**未导入**的
人工修改时停止并报错(绝不覆盖别人手里的活)。
"""
from __future__ import annotations

import argparse
import ast
import datetime
import difflib
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAW_TAG = "判据:原文"
COND_EN_COL = "Judgment Condition(English)"           # 调优表英文判据列(与 export/import_tree_excel 一致)
SOL_EN_COL = "Recommend Solution(English)"


# ---------- 交付包解析 ----------
def _unzip(zp: Path, dst: Path) -> None:
    try:
        try:
            zf = zipfile.ZipFile(zp, metadata_encoding="gbk")    # Windows 打的包常见 GBK 文件名
        except TypeError:                                        # 老 Python 无该参数
            zf = zipfile.ZipFile(zp)
    except zipfile.BadZipFile:
        head = zp.read_bytes()[:120]
        raise SystemExit(f"不是合法 zip:{zp}(开头字节:{head!r})——"
                         "开头是 { 或 < 说明是下载服务回的 JSON/HTML 报错体被当文件存了盘,"
                         "删掉该文件重新下载;真损坏则找来源方重传")
    with zf:
        zf.extractall(dst)


def _explode(dst: Path) -> None:
    """就地递归解嵌套 zip,3 层封顶(防 zip 炸弹式无限套娃);解到同名目录,解完删 zip
    (防重复解/误当交付物)。"""
    for _ in range(3):
        inner = sorted(dst.rglob("*.zip"))
        if not inner:
            break
        for z in inner:
            _unzip(z, z.with_suffix(""))
            z.unlink()


def unpack(package: Path, tmp: Path) -> Path:
    """交付包统一消化成"全是目录"的形态:
    - zip → 解压到临时目录 + 递归解嵌套 zip(合集包里每节点一个 zip 直接消化);
    - 目录且里面**没有** zip → 原样返回(零拷贝);
    - 目录且里面躺着 zip("一堆 zip 的合集目录",可混对应表/散子包)→ **拷到临时区**再解
      (绝不动用户目录),对应表等非 zip 文件原样带上。"""
    if package.is_dir():
        if not any(package.rglob("*.zip")):
            return package
        dst = tmp / "package"
        shutil.copytree(package, dst)
        _explode(dst)
        return dst
    if package.suffix.lower() != ".zip":
        raise SystemExit(f"交付包应是目录或 .zip:{package}")
    dst = tmp / "package"
    _unzip(package, dst)
    _explode(dst)
    inner = [d for d in dst.iterdir() if d.is_dir()]
    return inner[0] if len(inner) == 1 and not any(dst.glob("*.md")) else dst


def find_subpackages(pkg: Path):
    """多节点合集包探测:按「判断方法*.md 所在的顶层子目录」分组。
    返回 {子包根: [md 路径…]};单节点包 → {pkg: [md]}。顶层与子目录同时有 md 的异常包 → 停止并报错
    (分不清哪份文档配哪个脚本,机器不猜)。"""
    cands = [p for p in pkg.rglob("判断方法*.md") if "规范" not in p.name]
    groups: dict[Path, list[Path]] = {}
    for p in cands:
        rel = p.relative_to(pkg)
        top = pkg / rel.parts[0] if len(rel.parts) > 1 else pkg
        groups.setdefault(top, []).append(p)
    if pkg in groups and len(groups) > 1:
        raise SystemExit("交付包顶层和子目录**同时**有判断方法.md——分不清哪份文档配哪个脚本,"
                         "人工把顶层那份挪进对应子目录(或删掉多余的)再迁。")
    return groups


def _safe_name(sub: Path):
    """子包的「节点名称」;解析不动(嵌套合集/缺文档)→ None,由调用方决定怎么报。"""
    try:
        return parse_method_md(sub)["name"].strip()
    except SystemExit:
        return None


def _method_md_file(pkg: Path) -> Path:
    """交付包里的判断方法.md(排除规范文件):没有/多节点合集包 → 停止并报错;同目录多份 → 用第一份并提示。"""
    cands = [p for p in pkg.rglob("判断方法*.md") if "规范" not in p.name]
    if not cands:
        raise SystemExit(f"交付包里找不到 判断方法*.md(排除规范文件):{pkg}")
    if len({p.parent for p in cands}) > 1:               # 不同子目录各有一份 = 多节点合集包:决不能闷头拿第一份
        subs = sorted({str(p.parent) for p in cands})
        raise SystemExit("这是**多节点合集包**(发现 {} 份判断方法.md,分居不同子目录)。\n"
                         "批量:对整包用 --auto 自动循环;或把下列子目录各自当交付包(--only 选定)再跑:\n  "
                         .format(len(cands)) + "\n  ".join(subs))
    if len(cands) > 1:
        print(f"[warn] 交付包里有多份判断方法文档,用第一份:{[c.name for c in cands]}")
    return cands[0]


def _md_sections(text: str) -> dict:
    """「## 标题」分段 → {标题: 段落正文}(### 三级标题算正文,不另起段)。"""
    secs, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S.*?)\s*$", line)
        if m and not line.startswith("###"):
            if cur:
                secs[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur:
            buf.append(line)
    if cur:
        secs[cur] = "\n".join(buf).strip()
    return secs


def _md_first_line(secs: dict, key: str) -> str:
    """段落首行(去空白);段落缺失或为空 → ""。"""
    return (secs.get(key) or "").splitlines()[0].strip() if secs.get(key) else ""


def parse_method_md(pkg: Path):
    """判断方法.md → {name, condition(=全文逐字), core(=判断条件段,只用于相似度匹配), file}。
    判据替换用**全文**:故障描述/判断条件/命中条件/输出/数据来源表/注意事项一个不丢——
    树上的判据从此就是整份规范文档(数据来源表是核对采集覆盖的依据,绝不能丢);
    相似度匹配仍用「判断条件」段,免得模板固定段落稀释相似度。"""
    md = _method_md_file(pkg)
    text = md.read_text(encoding="utf-8", errors="replace")
    secs = _md_sections(text)
    name = _md_first_line(secs, "节点名称")
    if not name:
        m = re.search(r"^#\s*判断方法[::]\s*(\S.*?)\s*$", text, re.M)
        name = m.group(1) if m else md.stem.replace("判断方法_", "")
    path_txt = _md_first_line(secs, "节点路径")
    path = [seg.strip() for seg in re.split(r"[/\\>→]+", path_txt) if seg.strip()]
    core = secs.get("判断条件") or ""
    if not core:
        print("[warn] 判断方法.md 里没解析到「## 判断条件」——格式不合规范,相似度匹配退化为全文比对,建议核对")
    return {"name": name, "path": path, "condition": text.strip(),
            "core": (core or text).strip(), "file": md}


def _path_pick(nodes, method, exact):
    """同名多命中时用包内「## 节点路径」消歧(根→节点,/ 分隔;子序列语义与
    resolve_node_by_path 同):唯一命中且在 exact 内 → 返回 nid,否则 None(照旧交人)。"""
    path = [s for s in (method.get("path") or []) if s]
    if not path or len(exact) < 2:
        return None
    name = str(method.get("name") or "").strip()
    full = path if path[-1] == name else path + [name]
    rnid, _w, _d = resolve_node_by_path(nodes, full)
    return rnid if rnid in exact else None


def _pick_fc_by_name(pkg: Path, fcs, node_name):
    """多脚本包:按交付规范 fault_check_<节点名>.py 精确同名唯一命中就自动选(同名多份但逐字节相同取最浅的);
    同名内容不同/无一同名 → 停止并报错,不猜。"""
    want = f"fault_check_{(node_name or '').strip()}.py"
    hit = sorted((p for p in fcs if p.name == want), key=lambda p: (len(p.parts), str(p)))
    if node_name and len(hit) == 1:
        print(f"  [匹配] 多脚本包:按节点名精确命中 {want},其余 {len(fcs) - 1} 个脚本忽略")
        return hit[0]
    if node_name and hit and all(h.read_bytes() == hit[0].read_bytes() for h in hit[1:]):
        # 同名多份但逐字节相同(包顶层一份、scripts/ 共享目录又一份的实况)→ 取最浅的
        print(f"  [匹配] 多脚本包:按节点名精确命中 {want}(包里 {len(hit)} 份同名且内容相同,"
              f"取 {hit[0].relative_to(pkg).as_posix()}),其余脚本忽略")
        return hit[0]
    if node_name and hit:
        raise SystemExit(f"交付包里「{want}」有 {len(hit)} 份且内容不同"
                         f"({[h.relative_to(pkg).as_posix() for h in hit]})——不猜,"
                         f"用 --script 指定正主(建议交付方清掉过期拷贝)")
    raise SystemExit(f"交付包里有多个脚本且无一与节点名「{node_name}」精确同名,"
                     f"用 --script 指定一个:{[p.name for p in fcs]}")


def _pick_fc(pkg: Path, fcs, script_arg, node_name):
    """选定交付脚本:--script 指定 > 包里唯一一个 > 没有脚本报错 > 多脚本按节点名命中。"""
    if script_arg:
        hit = [p for p in fcs if p.name == script_arg] or [pkg / script_arg]
        if not hit[0].exists():
            raise SystemExit(f"--script 指定的脚本在包里找不到:{script_arg}")
        return hit[0]
    if len(fcs) == 1:
        return fcs[0]
    if not fcs:
        raise SystemExit("交付包里没有 fault_check_*.py(无脚本的包只有判断方法文档,走人工改调优表即可)")
    return _pick_fc_by_name(pkg, fcs, node_name)


def find_scripts(pkg: Path, script_arg: str | None, node_name: str | None = None):
    """交付包里的 fault_check 脚本 + 共享模块(search_*.py)。verify_*.py 调试遗留不收。
    多脚本包(平台存包常把整个共享脚本目录都打进去):按交付规范
    fault_check_<节点名>.py 精确同名唯一命中就自动选——按规范约定选不算猜;对不上仍停止并报错。"""
    fcs = sorted(set(pkg.rglob("fault_check_*.py")))
    fc = _pick_fc(pkg, fcs, script_arg, node_name)
    cands = sorted({p for p in pkg.rglob("search_*.py")}, key=lambda p: p.name)
    deps = _needed_deps(fc, cands)
    if len(deps) != len(cands):
        print(f"  [依赖] 包里 {len(cands)} 个共享模块,按 import 闭包只收 {len(deps)} 个,"
              f"其余不相关的不进产物")
    return fc, deps


def _needed_deps(fc: Path, cands):
    """共享模块按 import 闭包收敛:只收选中脚本(及其依赖链)实际 import 的 search_* 模块
    ——平台存包把整个共享脚本目录都打进去时,不相关的共享件不进产物。
    同名多份优先与脚本同目录的那份。"""
    by_name = {}
    for p in sorted(cands, key=lambda p: (p.parent != fc.parent, str(p))):
        by_name.setdefault(p.stem, p)
    need, queue, seen = [], [fc], {fc}
    while queue:
        f = queue.pop()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [al.name for al in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module]
            else:
                continue
            for m in mods:
                p = by_name.get(m.split(".")[0])
                if p is not None and p not in seen:
                    seen.add(p)
                    need.append(p)
                    queue.append(p)
    return sorted(need, key=lambda p: p.name)


# 入口候选(优先级序)。fault_check 是 tester 规范的约定入口名(与文件名前缀同名),排最前——
# 实测漏了它:脚本入口叫 fault_check 时候选名单误选了内部帮手 check_ne(接受 Path、返回 tuple),运行期必出错。
_ENTRY_CANDIDATES = ("fault_check", "diagnose", "main", "check_ne", "diagnose_ne", "check")


def _required_kwargs(fn) -> dict:
    """首参之外的无默认值参数 → entry_kwargs 需求(常见如 judgment_conditions={};其余待人核)。"""
    pos = [a.arg for a in fn.args.args]
    required_extra = pos[1: len(pos) - len(fn.args.defaults)] if len(pos) > 1 else []
    return {p: {} for p in required_extra}


def detect_entry(script: Path):
    """静态探测入口函数(候选见 _ENTRY_CANDIDATES;选中后还要过 arbitrate_entry 的空目录试跑)。
    入口契约=至少接收 1 个位置参数(数据目录);0 参函数(从 sys.argv 自取路径的老脚本)一律跳过
    并记入 note——运行期适配函数必传目录参数,选了 0 参入口就是必然的 TypeError,在迁移时拦下。
    单网元入口(check_ne/diagnose_ne)→ per_ne。返回 (entry, per_ne, kwargs, 顶层函数清单, argc 表, note)。"""
    tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    argc = {n: len(f.args.args) for n, f in defs.items()}
    entry, skipped = None, []
    for c in _ENTRY_CANDIDATES:
        if c not in defs:
            continue
        if argc[c] < 1:
            skipped.append(c)
            continue
        entry = c
        break
    note = (f"({'/'.join(skipped)}() 不接收数据目录参数——脚本从 sys.argv 自取路径,不符合入口格式约定,已跳过)"
            if skipped else "")
    if entry is None:
        return None, False, {}, sorted(defs), argc, note
    return entry, entry in ("check_ne", "diagnose_ne"), _required_kwargs(defs[entry]), sorted(defs), argc, note


class _StrPathLite(str):
    """试跑入参:与引擎 custom_lib._StrPath 同款的 str/Path 双兼容路径(str 子类,支持 / 拼接与
    Path 方法)——试跑传参必须和运行期适配函数同型,否则试跑放行的脚本运行期照样出错(或反之)。"""
    def __truediv__(self, other):
        import os as _os
        return _StrPathLite(_os.path.join(str(self), str(other)))

    def __getattr__(self, name):
        return getattr(Path(str(self)), name)


def _shape_ok(r):
    """返回值是否是适配函数认识的形状(与 custom_lib._fc_normalize 同一规则,只验形不验语义)。"""
    if not isinstance(r, dict):
        return False, f"返回 {type(r).__name__},不是 dict"
    if "matched" in r or "error" in r or "status" in r:
        return True, ""
    det = r.get("details")
    if isinstance(det, dict) and isinstance(det.get("devices"), dict):
        return True, ""
    inner = r.get("results")
    if isinstance(inner, dict) and inner and all(
            isinstance(v, dict) and ("matched" in v or "error" in v or "status" in v)
            for v in inner.values()):
        return True, ""
    if r and all(isinstance(v, dict) and ("matched" in v or "error" in v or "status" in v)
                 for v in r.values()):
        return True, ""
    return False, "是 dict 但既无 matched/error/status,也不是 {网元:{matched,…}} 映射"


def _smoke_call(stage: Path, script_name: str, entry: str, kwargs: dict):
    """空目录试跑:以库方式(argv 隔离/stdout 收走/异常拦截,与运行期适配函数同样的现场保护)调一次
    入口,验返回形状。空数据下合契约的脚本该返回 undetermined——至少得是个认得出的 dict。"""
    import contextlib
    import importlib.util
    import io
    script = stage / script_name
    sdir = str(stage)
    added = sdir not in sys.path
    if added:
        sys.path.insert(0, sdir)                         # 交付脚本 import search_* 同目录共享模块
    sink = io.StringIO()
    argv_bak, sys.argv = sys.argv, [str(script)]
    mods_bak = set(sys.modules)                          # 交付脚本 import 的 search_* 会进模块缓存:
    try:                                                 # 批量时不清会让后一个包用上前一个包的库
        with tempfile.TemporaryDirectory(prefix="smoke_data_") as td, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            spec = importlib.util.spec_from_file_location(f"smoke_{abs(hash(str(script)))}", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, entry, None)
            if not callable(fn):
                return False, f"没有可调用的「{entry}」"
            r = fn(_StrPathLite(td), **(kwargs or {}))
    except SystemExit as e:
        return False, f"调用了 sys.exit({e.code})——库调用不该退出进程"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        sys.argv = argv_bak
        if added:
            sys.path.remove(sdir)
        for m in set(sys.modules) - mods_bak:
            if m.startswith(("search_", "smoke_")):
                sys.modules.pop(m, None)
    return _shape_ok(r)


def arbitrate_entry(fc: Path, deps, entry: str, per_ne: bool, kwargs: dict, explicit: bool):
    """入口试跑仲裁:选中的入口先真调一次;不合契约时——auto 模式按候选序换下一个(谁返回契约
    形状谁当入口,机械裁决不猜名字),全不行则停止并报错、逐个报原因;人工 --entry 指定的只警告不换(人>名单)。
    返回 (entry, per_ne, kwargs, 仲裁说明)。"""
    with tempfile.TemporaryDirectory(prefix="stage_") as sd:
        stage = Path(sd)
        for p in [fc] + list(deps):
            shutil.copy2(p, stage / p.name)              # 依赖凑齐再测,和迁入后 custom/ 同布局
        ok, why = _smoke_call(stage, fc.name, entry, kwargs)
        if ok:
            return entry, per_ne, kwargs, ""
        if explicit:
            print(f"  ⚠ 试跑:--entry 指定的「{entry}」空目录调用不符合约定格式({why})——按人工指定继续;"
                  "运行期该节点若判不了,先查这里")
            return entry, per_ne, kwargs, ""
        tree = ast.parse(fc.read_text(encoding="utf-8", errors="replace"))
        defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        tried = [(entry, why)]
        for c in _ENTRY_CANDIDATES:
            if c == entry or c not in defs or len(defs[c].args.args) < 1:
                continue
            k2 = _required_kwargs(defs[c])
            ok2, why2 = _smoke_call(stage, fc.name, c, k2)
            if ok2:
                note = f"试跑仲裁:「{entry}」不符合约定格式({why}),换用「{c}」(空目录调用返回约定的形态)"
                print(f"  {note}")
                return c, c in ("check_ne", "diagnose_ne"), k2, note
            tried.append((c, why2))
        raise SystemExit("试跑:所有候选入口空目录调用都不符合约定格式:\n  "
                         + "\n  ".join(f"{n}(): {w}" for n, w in tried)
                         + "\n——约定的格式:入口接收数据根目录,返回 {matched,evidence,details}"
                           "(空数据=insufficient_data,旧三态 undetermined 也认)。确认真入口后用 --entry 指定强推(只警告不拦),"
                           "或把问题反馈交付方按规范改脚本。")


# ---------- 节点匹配 ----------
def load_index(product: Path):
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    if len(tis) != 1:
        raise SystemExit(f"产物 tree/ 下应恰有一份 tree_index,现有 {len(tis)} 份——核对 --product 路径")
    return json.loads(tis[0].read_text(encoding="utf-8"))


def rank_nodes(index, pkg_name: str, pkg_cond: str, top=5):
    """相似度=0.6*名字 + 0.4*判据正文;另给包含关系加成。返回 [(score, nid, name)]。"""
    def ratio(a, b):
        return difflib.SequenceMatcher(None, a or "", b or "").ratio()
    out = []
    for nid, nd in (index.get("nodes") or {}).items():
        name = str(nd.get("name") or "")
        score = 0.6 * ratio(pkg_name, name) + 0.4 * ratio(pkg_cond[:800], str(nd.get("condition") or "")[:800])
        if pkg_name and (pkg_name in name or name in pkg_name):
            score = max(score, 0.85)
        out.append((round(score, 3), nid, name))
    out.sort(reverse=True)
    return out[:top]


# ---------- 调优表修改 ----------
def _open_tuning(product: Path):
    """打开产物调优表(锁文件/唯一性/node_id 列检查),返回 (路径, wb, ws, header, id_col)。"""
    from openpyxl import load_workbook
    locks = sorted((product / "tree").glob("~$*.xlsx"))
    if locks:                                            # Excel 锁文件:表多半正被 Excel 打开,写盘会坏/被拒
        raise SystemExit(f"检测到 Excel 锁文件 {locks[0].name}——调优表可能正被 Excel 打开。"
                         "关掉 Excel 再重跑;若确认没开(Excel 崩溃残留的锁),删掉该文件再来。")
    xls = sorted(p for p in (product / "tree").glob("*.tuning.xlsx")
                 if not p.name.startswith("~$"))
    if len(xls) != 1:
        raise SystemExit(f"tree/ 下应恰有一份调优表,现有 {len(xls)} 份")
    wb = load_workbook(xls[0])
    ws = wb.active
    header = [str(c.value or "").strip() for c in ws[1]]
    if "node_id" not in header:
        raise SystemExit("调优表第一行没有 node_id 列——不是本工具链导出的调优表?")
    return xls[0], wb, ws, header, header.index("node_id") + 1


def _ensure_col(ws, header, title) -> int:
    """表头找列;没有就在最右补一列(导入按表头名识别,位置无关)。返回 1 起列号。"""
    if title in header:
        return header.index(title) + 1
    header.append(title)
    ws.cell(row=1, column=len(header), value=title)
    return len(header)


def edit_tuning_excel(product: Path, nid: str, condition: str, binding: dict):
    path, wb, ws, header, id_col = _open_tuning(product)
    raw_col = _ensure_col(ws, header, RAW_TAG)
    cross_col = _ensure_col(ws, header, "绑定:cross")
    tag_cols = [i + 1 for i, h in enumerate(header) if h.startswith("判据:") and h != RAW_TAG]
    bind_cols = {h[len("绑定:"):]: i + 1 for i, h in enumerate(header) if h.startswith("绑定:")}
    bind_cols["cross"] = cross_col
    row = next((r for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=id_col).value or "").strip() == nid), None)
    if row is None:
        raise SystemExit(f"调优表里找不到节点 {nid} 的行(表刚由 export 生成,不该缺——核对节点号)")
    # 注意:openpyxl 的 ws.cell(..., value=None) 是"不赋值"而非"清空"——清格必须走 .value = None
    ws.cell(row=row, column=raw_col).value = condition or None   # 判据整段进原文列(清除判据的行传空=清格)
    for c in tag_cols:
        ws.cell(row=row, column=c).value = None          # 标签列清空(原文/标签二选一)
    for src, c in bind_cols.items():                     # binding 为空(清除判据的行)= 全部绑定列清空
        ws.cell(row=row, column=c).value = (json.dumps(binding, ensure_ascii=False)
                                            if src == "cross" and binding else None)
    wb.save(path)
    return path


def _lvl_cols(header) -> dict:
    """调优表中文层级列 → {层级: 1 起列号}(裸 L<n> 与 L<n>(Chinese) 都认)。"""
    lvl_re = re.compile(r"^L(\d+)(?:\(Chinese\))?$")
    lvl_cols = {}
    for i, h in enumerate(header):
        m = lvl_re.match(h.replace("：", ":").strip())
        if m:
            lvl_cols[int(m.group(1))] = i + 1
    return lvl_cols


def _subtree_end(ws, prow: int, lvl_cols: dict, parent_depth: int) -> int:
    """父节点子树最后一行的行号(比父深的连续行;没有子节点就是父行本身)。"""
    end = prow
    for r in range(prow + 1, ws.max_row + 1):            # 父节点子树最后一行(比父深的连续行)
        depth_r = max((l for l, c in lvl_cols.items()
                       if str(ws.cell(row=r, column=c).value or "").strip()), default=0)
        if depth_r <= parent_depth:
            break
        end = r
    return end


def _write_add_en(ws, header, new: int, child_depth: int, condition: str, en: dict) -> None:
    """新增行的英文格:表里给了英文就随新行一起写进英文列(英文判据只在有中文判据时写)。"""
    if en.get("name_en"):
        ws.cell(row=new, column=_ensure_col(ws, header, f"L{child_depth}(English)")).value = en["name_en"]
    if en.get("condition_en") and condition:
        ws.cell(row=new, column=_ensure_col(ws, header, COND_EN_COL)).value = en["condition_en"]
    if en.get("sol_en"):
        ws.cell(row=new, column=_ensure_col(ws, header, SOL_EN_COL)).value = en["sol_en"]


def edit_tuning_add(product: Path, parent_nid: str, parent_chain, name: str,
                    condition: str, binding: dict, en: dict | None = None):
    """调优表**新增行**(增节点):node_id 留空(import 自动分配 nN,历史 id 不复用),
    祖先各层名字照抄、自己的名字落在父层+1 的层级列,判据进原文列、绑定只写 cross(S)。
    插在父节点子树的最后一行之后——保持 DFS 连续,import 按"上级和上文对得上"重建父链才稳。
    en={"name_en","condition_en","sol_en"}(可缺省):表里给了英文就随新行一起写进英文列。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    lvl_cols = _lvl_cols(header)
    child_depth = len(parent_chain) + 1                  # 相对深度:根=L1(与 export 同一规则)
    for d in range(1, child_depth + 1):
        if d not in lvl_cols:                            # 树长深了一层:补层级列(导入按表头识别)
            lvl_cols[d] = _ensure_col(ws, header, f"L{d}(Chinese)")
    raw_col = _ensure_col(ws, header, RAW_TAG)
    cross_col = _ensure_col(ws, header, "绑定:cross")
    prow = next((r for r in range(2, ws.max_row + 1)
                 if str(ws.cell(row=r, column=id_col).value or "").strip() == parent_nid), None)
    if prow is None:
        raise SystemExit(f"调优表里找不到父节点 {parent_nid} 的行——核对产物")
    end = _subtree_end(ws, prow, lvl_cols, len(parent_chain))
    ws.insert_rows(end + 1)
    new = end + 1
    for d, nm in enumerate(parent_chain, start=1):
        ws.cell(row=new, column=lvl_cols[d]).value = nm  # 祖先各层逐格照抄(导入按它挂父链)
    ws.cell(row=new, column=lvl_cols[child_depth]).value = name
    ws.cell(row=new, column=raw_col).value = condition
    ws.cell(row=new, column=cross_col).value = (json.dumps(binding, ensure_ascii=False)
                                                if binding else None)   # 纯判据结构节点:无绑定
    _write_add_en(ws, header, new, child_depth, condition, en or {})
    wb.save(path)
    return path


def edit_tuning_condition(product: Path, nid: str, condition: str):
    """调优表**只改判据**(判据同步行):原文列写入、标签列清空(原文/标签二选一),
    **绑定列一格不动**——判据文字变了但没有新交付包,现有绑定(脚本/判断方式)继续生效;
    脚本可能落后于新判据的事实由退回清单点名,交付方补包后走正常迁移行换血。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    raw_col = _ensure_col(ws, header, RAW_TAG)
    tag_cols = [i + 1 for i, h in enumerate(header) if h.startswith("判据:") and h != RAW_TAG]
    row = next((r for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=id_col).value or "").strip() == nid), None)
    if row is None:
        raise SystemExit(f"调优表里找不到节点 {nid} 的行(表刚由 export 生成,不该缺——核对节点号)")
    ws.cell(row=row, column=raw_col).value = condition or None
    for c in tag_cols:
        ws.cell(row=row, column=c).value = None
    wb.save(path)
    return path


def edit_tuning_solution(product: Path, nid: str, text: str):
    """调优表**只改解决方案**(中文列;English 列与判据/绑定一格不碰):以表为准同步。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    col = _ensure_col(ws, header, "Recommend Solution(Chinese)")
    row = next((r for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=id_col).value or "").strip() == nid), None)
    if row is None:
        raise SystemExit(f"调优表里找不到节点 {nid} 的行(表刚由 export 生成,不该缺——核对节点号)")
    ws.cell(row=row, column=col).value = text or None
    wb.save(path)
    return path


def edit_tuning_en(product: Path, nid: str, name_en=None, condition_en=None, sol_en=None):
    """调优表**只改英文格**:英文名(本节点所在层的 L<d>(English) 列)、英文判据、英文解决方案。
    每个参数 None=不动,""=清格,其他=写入;列不存在就在最右补一列(导入按表头名识别,位置无关)。
    本节点所在层 = 本行有值的最深中文层级列。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    row = next((r for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=id_col).value or "").strip() == nid), None)
    if row is None:
        raise SystemExit(f"调优表里找不到节点 {nid} 的行(表刚由 export 生成,不该缺——核对节点号)")
    if name_en is not None:
        lvl_re = re.compile(r"^L(\d+)(?:\(Chinese\))?$")
        depth_r = 0
        for i, h in enumerate(header):
            m = lvl_re.match(h.replace("：", ":").strip())
            if m and str(ws.cell(row=row, column=i + 1).value or "").strip():
                depth_r = max(depth_r, int(m.group(1)))
        if depth_r:
            c = _ensure_col(ws, header, f"L{depth_r}(English)")
            ws.cell(row=row, column=c).value = name_en or None
    if condition_en is not None:
        c = _ensure_col(ws, header, COND_EN_COL)
        ws.cell(row=row, column=c).value = condition_en or None
    if sol_en is not None:
        c = _ensure_col(ws, header, SOL_EN_COL)
        ws.cell(row=row, column=c).value = sol_en or None
    wb.save(path)
    return path


def edit_tuning_del(product: Path, nid: str):
    """调优表**删行**(全量表模式的删节点):按 node_id 定位整行删除,import 重建后节点消失。
    只应由 --map-mode all 的全量对账调用(候选集天然含整棵子树,见 _delete_targets)。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    row = next((r for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=id_col).value or "").strip() == nid), None)
    if row is None:
        raise SystemExit(f"调优表里找不到节点 {nid} 的行,删不了——核对产物")
    ws.delete_rows(row, 1)
    wb.save(path)
    return path


def run_step(product: Path, script_name: str, desc: str) -> None:
    sp = product / "scripts" / script_name
    if not sp.exists():
        raise SystemExit(f"产物缺 scripts/{script_name}(旧产物?先用最新 cs-scot-generate 重跑 assemble 补齐)")
    r = subprocess.run([sys.executable, str(sp), "--out", str(product)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0:
        print(r.stdout[-1500:], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit(f"{desc} 失败(exit {r.returncode})——上面是它的报错,产物未写入,修正后重跑迁移")
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]
    for l in tail:
        print(f"    {l}")


# 产物里属于"引擎"的自包含副本(assemble 拷进去的,工具链所有,人不该手改)。
# 同步规则(refresh_engine):与本机 skills 目录下的源文件逐文件比对,内容不同就刷新、旧文件留 .bak——以本机
# 安装的工具链为准。下面的能力标记用来**核验源**(缺标记的源不可信、不用它刷)和识别"整代过旧"
# 的副本;custom_lib.py 含手工 @register 函数时退回"按标记只补缺"以免顶掉人工代码。
# 引擎源两级:本机 skills 目录下的 cs-scot-generate / cs-scot-execute(更新)→ 本技能内置 engine/
# (自包含的备用副本;维护:引擎侧改动后跑 scripts/sync_engine.py 对齐)。
ENGINE_CAPS = {
    "condition_kernels.py": ("def k_script", "def alias_sources", "【位置 ",
                             "except SystemExit", "xlsx_reader",
                             'setdefault("script"'),    # 末项=script 字段不覆盖 fault_check 写的脚本真身
    "xlsx_reader.py": ("def read_rows",),                # 零依赖 xlsx 读取器(旧产物没有此文件→直接补)
    # 注意:引擎文件行为语义有变时要**升级这里的标记**(加一个只在新版存在的串),
    # 否则旧产物副本因"标记齐全"永远不被刷新——results 信封识别就是靠新增标记下发的。
    "custom_lib.py": ('@register("fault_check")', 'result.get("results")', "argv_bak",
                      "_fc_tristate", "matched_items", "_StrPath",
                      "raw_note", "_fc_no_judgment", "_fc_gap_kind",
                      "_fc_item_str", "_fc_purge_shared",
                      'ne_dst / "DIAG"'),               # 末项=raw 镜像两代目录名互为别名(DIAG 不再被改名弄丢)
    "import_tree_excel.py": ('{"P", "S"}', "xlsx_reader"),   # S/P 放行 + cross 列 + 读表零依赖同代
    "export_tree_excel.py": ('!= "cross"', "os.utime"),  # cross 列/相对深度 + 导出后表 mtime 对齐 index(消假警报)
}
RUNNER_CAP = "execution_status"                          # 逐节点执行状态写入文件(评估反思归因信号);
                                                         # 兼含前代:raw-only 放行 / diag↔cmd 互认
TREE_PRUNE_CAP = "matched_nes"                           # 剪枝放行 S 证据(evidence/matched_nes/
                                                         # explain 作为默认)这一代——旧版精简时丢证据字段


def _mat(name: str) -> Path:
    """公共素材目录查找:①同一 skills 目录下的 skills/<name>(仓库开发态)②发行包形态——素材收进生成线
    目录下(generate/<name>;cs-scot-generate 本身在发行包里就叫 generate)③本技能单独部署、
    旁边没有其他技能时,用内置 engine/<name> 副本(fault-tree-rca、zip2csv 整目录,cs-scot-execute
    只有 SKILL.md;副本由 sync_engine.py 生成)。都没有时返回 skills 目录下的路径。"""
    base = Path(__file__).resolve().parents[2]
    engine = Path(__file__).resolve().parents[1] / "engine"
    cands = (base / name, base / "generate", engine / name) if name == "cs-scot-generate" \
        else (base / name, base / "generate" / name, engine / name)
    for c in cands:
        if c.is_dir():
            return c
    return base / name


def _engine_src(name: str, caps, sibling: Path):
    """引擎文件取源:skills 目录下的技能优先,其次内置 engine/;两处都必须带齐能力标记才可信。"""
    for d in (sibling, Path(__file__).resolve().parents[1] / "engine"):
        p = d / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            if all(c in text for c in caps):
                return p
    return None


def _norm_text(p: Path) -> str:
    """比对用文本:统一行尾(Windows 检出的 CRLF 与 Linux 打包的 LF 不算差异)。"""
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _differs(dst: Path, src: Path) -> bool:
    return not dst.exists() or _norm_text(dst) != _norm_text(src)


def _sync_file(dst: Path, src: Path, label: str, touched: list) -> bool:
    """工具链公共文件同步:与已核验的源内容不同就覆盖,旧文件留 <原名>.bak(x.py → x.py.bak);
    记入 touched 并打印一行。返回是否动了。"""
    if not _differs(dst, src):
        return False
    was = dst.exists()
    if was:
        shutil.copy2(dst, dst.with_name(dst.name + ".bak"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    note = f"已刷新,旧版备份 {dst.name}.bak" if was else "补齐"
    touched.append(f"{label}({note})")
    print(f"  {'刷新' if was else '补齐'} {label}" + (f"(旧版备份 {dst.name}.bak)" if was else ""))
    return True


def _tracked(src_dir: Path, ignore=("__pycache__", "*.pyc", "*.bak", "runs", "result*")):
    """源目录里应同步的文件(相对路径列表),按 ignore 排除。"""
    out = []
    for q in sorted(src_dir.rglob("*")):
        rel = q.relative_to(src_dir)
        if q.is_file() and not any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in ignore):
            out.append(rel)
    return out


def _manual_registered_skip(dst: Path, src: Path, old_text: str, caps_ok: bool) -> bool:
    """custom_lib.py 里手工 @register 的函数:别静默丢——能力齐全就不整体覆盖(返回 True=跳过同步,
    与源不同时点名);能力不齐则警告后照刷(返回 False)。"""
    extra = set(re.findall(r'@register\("([^"]+)"\)', old_text)) \
        - set(re.findall(r'@register\("([^"]+)"\)', src.read_text(encoding="utf-8")))
    if extra and caps_ok:
        if _differs(dst, src):
            print(f"  [info] scripts/custom_lib.py 与 skills 目录下的源不同,但含手工注册的函数 {sorted(extra)}"
                  " 且能力齐全——不整体覆盖;要同步引擎版,先把这些函数挪出再重跑")
        return True
    if extra:
        print(f"  ⚠ 旧 custom_lib.py 里有手工注册的函数 {sorted(extra)} 将被引擎版顶掉——"
              "从 custom_lib.py.bak 把它们拷回文件末尾,否则对应 S 绑定会报「未注册」")
    return False


def _refresh_engine_scripts(product: Path, touched: list) -> None:
    """① assets/scripts 引擎文件:逐文件取可信源比对刷新;拿不出可信源时能力齐全的副本照旧、否则停止并报错。"""
    sdir = product / "scripts"
    gen = _mat("cs-scot-generate") / "scripts"
    for name, caps in ENGINE_CAPS.items():
        dst = sdir / name
        old_text = dst.read_text(encoding="utf-8", errors="replace") if dst.exists() else ""
        caps_ok = bool(old_text) and all(c in old_text for c in caps)
        src = _engine_src(name, caps, gen)
        if src is None:
            if caps_ok:
                continue                                 # 本机拿不出可信源:能力齐全的副本照旧
            raise SystemExit(f"产物 scripts/{name} 缺 S 迁移所需能力,且本机 skills 目录下的技能与内置 engine/ "
                             "都拿不出可信版本——迁移技能安装不完整(engine/ 目录丢了?),重装后再迁。")
        if name == "custom_lib.py" and old_text and _manual_registered_skip(dst, src, old_text, caps_ok):
            continue                                     # 手工 @register 的函数:别静默丢
        _sync_file(dst, src, f"scripts/{name}", touched)


def _refresh_skill_scripts(skill_root: Path, exe: Path, touched: list) -> None:
    """② 打包技能根 scripts/ 的 runner/check_data/tree_prune 与 references/:与执行技能的源比对刷新。"""
    for name, caps, why in (("runner.py", (RUNNER_CAP,), "原始日志目录/两代源名/执行状态写入"),
                            ("check_data.py", ("def main",), "数据入口检查"),
                            ("tree_prune.py", (TREE_PRUNE_CAP,), "剪枝保留 S 脚本证据")):
        dst = skill_root / "scripts" / name
        src = _engine_src(name, caps, exe / "scripts")
        if src is None:
            cur = dst.read_text(encoding="utf-8", errors="replace") if dst.exists() else ""
            if not (cur and all(c in cur for c in caps)):
                print(f"  [warn] 打包技能的 scripts/{name} 缺失或是旧版({why}),且本机无处取可信版本")
            continue
        _sync_file(dst, src, f"scripts/{name}", touched)
    if (exe / "references").is_dir():                    # 执行技能随包的参考文档(plan_schema 等)
        for rel in _tracked(exe / "references"):
            _sync_file(skill_root / "references" / rel, exe / "references" / rel,
                       f"references/{rel.as_posix()}", touched)


def _refresh_zip2csv(product: Path, touched: list) -> None:
    """③ assets/zip2csv(原始日志文件→三表转换器,含 ifilter 归类器):缺→补齐;源是旧代只补不刷;否则逐文件同步。"""
    z2c_dst, z2c_src = product / "zip2csv", _mat("zip2csv")
    z2c_main_src = z2c_src / "scripts" / "zip2csv.py"
    z2c_new = z2c_main_src.exists() \
        and "run_ifilter" in z2c_main_src.read_text(encoding="utf-8", errors="replace") \
        and (z2c_src / "vendor" / "ifilter.py").exists()   # 源自身按代核验:主脚本新代+归类器在
    _z2c_ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak")
    if not z2c_main_src.exists():
        if not z2c_dst.is_dir():
            print("  [warn] 打包技能缺 assets/zip2csv,且本机 skills 目录下没有 zip2csv 技能可复制——"
                  "原始日志数据现场转不了三张表,原有单源绑定会整体数据不足")
    elif not z2c_new:
        if not z2c_dst.is_dir():
            shutil.copytree(z2c_src, z2c_dst, ignore=_z2c_ignore)
            touched.append("assets/zip2csv(补齐,本机的是旧版)")
            print("  补齐 assets/zip2csv;[warn] 本机 skills 目录下的 zip2csv 是旧版(无 ifilter 散文件归类/缺 vendor)"
                  "——分类目录转表可用,原始散文件的解析能力没带上")
        else:
            print("  [warn] 本机 skills 目录下的 zip2csv 是旧版(无 ifilter 散文件归类/缺 vendor)——不用它刷新产物副本")
    elif not z2c_dst.is_dir():
        shutil.copytree(z2c_src, z2c_dst, ignore=_z2c_ignore)
        touched.append("assets/zip2csv(补齐)")
        print("  补齐 assets/zip2csv(原始日志文件→三表转换器,含原始散文件归类)")
    else:
        for rel in _tracked(z2c_src):
            _sync_file(z2c_dst / rel, z2c_src / rel, f"assets/zip2csv/{rel.as_posix()}", touched)


def _refresh_rca_existing(rca_dst: Path, rca_src: Path, rca_ok: bool, rca_caps, ignore, touched: list) -> None:
    """④ 已有 assets/fault-tree-rca:旧代或与源不同 → 整目录刷新(旧副本备份 .bak);旧代又无可信源 → 只提醒。"""
    cur_draft = rca_dst / "scripts" / "draft_rca.py"
    cur = cur_draft.read_text(encoding="utf-8", errors="replace") if cur_draft.exists() else ""
    old_gen = any(c not in cur for c in rca_caps) \
        or not (rca_dst / "scripts" / "render_report.py").exists() \
        or not (rca_dst / "scripts" / "render_tree.py").exists()
    stale = rca_ok and (old_gen or any(_differs(rca_dst / rel, rca_src / rel) for rel in _tracked(rca_src)))
    if stale:
        bak = rca_dst.with_name("fault-tree-rca.bak")
        if bak.exists():
            shutil.rmtree(bak)
        rca_dst.rename(bak)
        shutil.copytree(rca_src, rca_dst, ignore=ignore)
        touched.append("assets/fault-tree-rca(整目录已刷新,旧副本备份 fault-tree-rca.bak)")
        print("  打包技能 assets/fault-tree-rca 已刷新(与本机 skills 目录下的版本不同;"
              "旧副本整目录备份 fault-tree-rca.bak)")
    elif old_gen:
        print("  [warn] 打包技能的 assets/fault-tree-rca 是旧版(无自动定档/机械报告/树视图),"
              "且本机 skills 目录下没有 fault-tree-rca 技能可刷新——RCA 仍按旧规则手工裁决")


def _refresh_rca(product: Path, touched: list) -> None:
    """④ assets/fault-tree-rca(根因规则副本):缺→补齐;旧或与源不同→整目录刷新。"""
    rca_dst, rca_src = product / "fault-tree-rca", _mat("fault-tree-rca")
    rca_src_draft = rca_src / "scripts" / "draft_rca.py"
    _rca_caps = ("gap_kind", 'f.get("evidence")')    # 四态分型 + S 证据标签引用这一代
    rca_ok = rca_src_draft.exists() and all(c in rca_src_draft.read_text(encoding="utf-8", errors="replace")
                                            for c in _rca_caps)
    _rca_ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.py.bak", "runs", "result*")
    if not rca_dst.is_dir():
        if rca_ok:
            shutil.copytree(rca_src, rca_dst, ignore=_rca_ignore)
            touched.append("assets/fault-tree-rca(补齐)")
            print("  打包技能补齐 assets/fault-tree-rca(根因规则副本:自动定档+机械报告这一代)")
        else:
            print("  [warn] 打包技能缺 assets/fault-tree-rca,且本机 skills 目录下没有 fault-tree-rca 技能可复制——"
                  "RCA 步骤没有规则手册与候选清单/合成脚本,诊断走不到根因")
    else:
        _refresh_rca_existing(rca_dst, rca_src, rca_ok, _rca_caps, _rca_ignore, touched)


def _refresh_skill_md(skill_root: Path, exe: Path, touched: list) -> None:
    """⑤ SKILL.md 正文(执行指引):换正文保身份——frontmatter(技能名/描述)原样,正文取 skills 目录下技能的当前版。"""
    sk = skill_root / "SKILL.md"
    sk_text = sk.read_text(encoding="utf-8", errors="replace")
    _sk_caps = ("check_data.py", "draft_rca.py", "rca_reply.md")   # 执行指引正文须带齐的三处引用:数据检查、候选清单、回复稿
    exe_sk = exe / "SKILL.md"
    m_old = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", sk_text, re.S)
    new_txt = exe_sk.read_text(encoding="utf-8", errors="replace") if exe_sk.exists() else ""
    m_new = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", new_txt, re.S)
    src_ok = bool(m_new) and all(m in new_txt for m in _sk_caps)
    body_old = (sk_text[m_old.end():] if m_old else sk_text).replace("\r\n", "\n")
    body_new = (new_txt[m_new.end():] if m_new else "").replace("\r\n", "\n")
    if src_ok and m_old and body_old != body_new:
        shutil.copy2(sk, sk.with_name("SKILL.md.bak"))
        sk.write_text(f"---\n{m_old.group(1)}\n---\n" + new_txt[m_new.end():], encoding="utf-8")
        touched.append("SKILL.md 正文(已刷新到当前执行指引,旧文备份 SKILL.md.bak)")
        print("  打包技能 SKILL.md 正文已刷新到当前执行指引(与本机 skills 目录下的版本不同;"
              "技能名与描述保留原样,旧文备份 SKILL.md.bak)")
    elif any(m not in sk_text for m in _sk_caps):
        print("  [warn] 打包技能 SKILL.md 正文是旧版(缺机械渲染报告指引),但无法自动刷新"
              "(frontmatter 异常或本机 skills 目录下的 cs-scot-execute 版本不够新)——建议重新 package")


def refresh_engine(product: Path) -> list:
    """产物工具链公共文件检查+同步。工具链公共文件 = 工具链所有、与场景无关的文件:assets/scripts 引擎脚本、
    技能根 scripts/ 的 runner/check_data/tree_prune 与 references/、assets/zip2csv、
    assets/fault-tree-rca 规则副本、SKILL.md 执行指引正文。逐文件与本机 skills 目录下的源比对(行尾差异
    不算),内容不同就刷新,旧文件留 .bak;源须先通过自身能力标记核验,坏源不用(本机技能 →
    本技能内置 engine/ 备用)。场景件(树、绑定、cross_analyzer、各指标脚本、节点脚本、knowledge)
    一概不碰;custom_lib.py 含手工 @register 的函数时只按能力标记补缺、不整体覆盖,并点名。
    返回本次动过的清单(逐项文字),--refresh-only 原样打印。
    某引擎文件缺能力又找不到可信源 → 停止并报错(迁过去也是全线'未知类别')。"""
    touched = []
    # ① assets/scripts 引擎文件
    _refresh_engine_scripts(product, touched)
    # ② 打包技能形态:assets/ 的上一级是技能根(有 SKILL.md + scripts/runner.py)
    skill_root = product.parent
    if not ((skill_root / "scripts" / "runner.py").exists() and (skill_root / "SKILL.md").exists()):
        return touched
    exe = _mat("cs-scot-execute")
    _refresh_skill_scripts(skill_root, exe, touched)
    # ③ assets/zip2csv(原始日志文件→三表转换器,含 ifilter 归类器)
    _refresh_zip2csv(product, touched)
    # ④ assets/fault-tree-rca(根因规则副本):缺→补齐;旧或与源不同→整目录刷新
    _refresh_rca(product, touched)
    # ⑤ SKILL.md 正文(执行指引):换正文保身份——frontmatter(技能名/描述)原样,正文取 skills 目录下技能的当前版
    _refresh_skill_md(skill_root, exe, touched)
    return touched


def _refresh_standard(product: Path) -> int:
    """每次收表/收包都先把工具链公共文件与本机 skills 目录下的源对齐(见 refresh_engine),打一行结果;返回动了几处。"""
    touched = refresh_engine(product)
    print(f"  工具链公共文件刷新 {len(touched)} 处" if touched else "  工具链公共文件与本机 skills 目录下的源一致,无需刷新")
    return len(touched)


def upgrade_cross_scaffold(product: Path) -> None:
    """产物 cross_analyzer.py 脚本框架过旧(无 S 类全量传源/原始日志目录)→ 借 generate 的框架升级函数就地升级。"""
    ca = product / "scripts" / "cross_analyzer.py"
    if not ca.exists():
        return
    text = ca.read_text(encoding="utf-8", errors="replace")
    if "raw_dir" in text and "load_all" in text:
        return                                           # 已是新框架
    asm_p = _engine_src("assemble_scenario.py", ("raw_dir", "_upgrade_cross_scaffold"),
                        _mat("cs-scot-generate") / "scripts")          # 发行包里叫 generate/,走 _mat
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("asm_for_migrate", asm_p)
        asm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(asm)
        up, why = asm._upgrade_cross_scaffold(text, asm.CROSS_SCAFFOLD)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] cross_analyzer.py 脚本框架是旧版(S 类会拿不到数据),且自动升级不可用({e})——"
              "用最新 cs-scot-generate 重跑 assemble_scenario 升级脚本框架后再执行诊断")
        return
    if up is None:
        print(f"[warn] cross_analyzer.py 脚本框架无法自动升级:{why}——需人工处理后 S 类才能拿到数据")
    elif up != text:
        shutil.copy2(ca, ca.with_suffix(".py.bak"))
        ca.write_text(up, encoding="utf-8")
        print("  cross_analyzer.py 脚本框架已升级(绑定原样保留,旧文件备份 .py.bak)")


# ---------- 迁移动作(单节点与批量共用) ----------
def _binding(fc_name: str, entry: str, per_ne: bool, kwargs: dict, pkg_node: str) -> dict:
    return {"class": "S", "func": "fault_check",
            "args": {"script": f"custom/{fc_name}", "entry": entry,
                     **({"per_ne": True} if per_ne else {}),
                     **({"entry_kwargs": kwargs} if kwargs else {})},
            "mapping_reason": f"迁移自 tester 交付包「{pkg_node}」:判断逻辑=脚本原样"
                              "(经业务确认+样本数据验证);数据优先 --data raw= 直接读原始目录,否则从三张表重建(备用做法)"}


def _missing_shared(fc: Path, deps):
    """脚本(含共享模块)import 的 search_* 模块里,交付包没带的名单(违反自包含,规范附录8)。"""
    have = {fc.stem} | {p.stem for p in deps}
    missing = set()
    for f in [fc] + list(deps):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [al.name for al in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module]
            else:
                continue
            for m in mods:
                top = m.split(".")[0]
                if top.startswith("search_") and top not in have:
                    missing.add(top)
    return sorted(missing)


def _shared_lib_src(fname: str):
    """共享模块的补齐来源:skills 目录下的 tester 优先,其次内置 engine/。"""
    for d in (_mat("datacom-fault-tree-tester") / "scripts",
              Path(__file__).resolve().parents[1] / "engine"):
        if (d / fname).exists():
            return d / fname
    return None


def _with_supplied(fc: Path, deps):
    """包缺共享模块 → 补齐名单:(supplied[(模块名,源路径)…], unsupplied[模块名…])。"""
    supplied, unsupplied = [], []
    for name in _missing_shared(fc, deps):
        src = _shared_lib_src(name + ".py")
        (supplied.append((name, src)) if src else unsupplied.append(name))
    return supplied, unsupplied


def _effective(product: Path, p: Path) -> Path:
    """脚本的"生效版":custom/ 里同名副本带 .orig 基准、且基准==包里原件(包没变),
    说明副本被修过管道 → 生效的是修复副本;否则就是包里原件。"""
    dst = product / "scripts" / "custom" / p.name
    orig = product / "scripts" / "custom" / (p.name + ".orig")
    if dst.exists() and orig.exists() and orig.read_bytes() == p.read_bytes():
        return dst
    return p


def _copy_scripts(product: Path, fc: Path, deps) -> None:
    """脚本进场,两条附加规矩:
    ①保修复:包没变而 custom/ 副本修过输入输出契约(.orig 基准==包里原件)→ 保留修复副本不覆盖;
      包真变了 → 正常覆盖并清掉过期 .orig(新包重新过检查);
    ②补库:脚本 import 的 search_*.py 包里没带 → 从 skills 目录下的 tester 或 engine/ 拷入(不覆盖已有)。"""
    custom = product / "scripts" / "custom"
    custom.mkdir(parents=True, exist_ok=True)
    preserved = []
    for p in [fc] + list(deps):
        dst, orig = custom / p.name, custom / (p.name + ".orig")
        if dst.exists() and orig.exists():
            if orig.read_bytes() == p.read_bytes():
                preserved.append(p.name)
                continue
            orig.unlink()                                # 包真变了:修复基准作废,正常覆盖
        shutil.copy2(p, dst)
    if preserved:
        print(f"  [保修复] {', '.join(preserved)}:包未变且副本修过管道——保留修复副本(基准 .orig)")
    supplied = []
    for name, src in _with_supplied(fc, deps)[0]:
        if not (custom / (name + ".py")).exists():
            shutil.copy2(src, custom / (name + ".py"))
            supplied.append(name + ".py")
    if supplied:
        print(f"  [补库] {', '.join(supplied)}:包里没带的共享模块已从引擎副本补入"
              "(正本仍须自包含,反馈交付方)")


def _stage_no_entry(product: Path, fc: Path, deps) -> str:
    """入口探测失败:脚本副本+基准+修法 sidecar 落 scripts/custom/,统一进管道修复通道
    ——照 sidecar 修法在副本里补 fault_check(data_dir) 薄封装(判断逻辑一行不动),
    重跑同一条命令时 _effective 认修复副本,入口探到即续迁。返回给失败清单的处置提示。"""
    _copy_scripts(product, fc, deps)                     # 已修过的副本自动保留不覆盖
    custom = product / "scripts" / "custom"
    orig = custom / (fc.name + ".orig")
    if not orig.exists():
        orig.write_bytes(fc.read_bytes())                # 基准:包没变时重跑认修复副本
    side = custom / (fc.name + ".contract.json")
    side.write_text(json.dumps({
        "status": "pending_repair", "reason": "no_entry",
        "fix": "在副本里补一个 fault_check(data_dir) 薄封装入口:接收数据目录参数,"
               "调用原有判断逻辑并返回约定格式的结果(matched/evidence/details);"
               "判断逻辑一行不动,拿不准算不算逻辑就停下问人",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return (f"脚本副本已落 scripts/custom/{fc.name},照 {fc.name}.contract.json 的修法"
            f"补薄封装入口后重跑同一条命令即续迁")


# ---------- 导入检查(契约检查)+ 契约修复循环 ----------
_CC_CACHE = []
# 检查 FAIL 里适配函数运行时能处理的项(能走到这一步=试跑仲裁已确认能驱动):
#   S1 入口名/签名(仲裁选定入口+entry_kwargs 驱动)、D2x/D3x 调用崩(_StrPath/护现场/逐网元重调)
# ——降为 WARN 放行并记台账;其余 FAIL(如 S3 只认 .txt、D2 缺数据当作结论)执行必受影响,拦下。
_ABSORBED = {"S1", "D2x", "D3x"}


def _contract_mod():
    """加载契约检查模块(skills 目录下的 tester 优先,其次 engine/);都没有 → None(老环境,跳过检查)。"""
    if not _CC_CACHE:
        import importlib.util
        p = _engine_src("check_contract.py", ("def check_script",),
                        _mat("datacom-fault-tree-tester") / "scripts")
        mod = None
        if p is not None:
            try:
                spec = importlib.util.spec_from_file_location("cc_gate", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:                            # noqa: BLE001 检查组件坏了不拦迁移,明确说明
                print(f"  [warn] 格式检查组件加载失败({p})——本次跳过检查")
                mod = None
        _CC_CACHE.append(mod)
    return _CC_CACHE[0]


def _gate_run_check(cc, product: Path, sub: Path, fc: Path, deps, supplied, entry, per_ne, kwargs):
    """脚本(生效版)+依赖+补库件搬到临时目录跑契约检查(按试跑仲裁的裁决参数;旧版检查组件退回老签名)。
    检查时导入的脚本/共享库不许留缓存,防止串包。返回检查报告。"""
    data = next((p for p in sorted(sub.rglob("测试数据")) if p.is_dir()), None) if sub else None
    mods_bak, path_bak = set(sys.modules), list(sys.path)
    try:
        with tempfile.TemporaryDirectory(prefix="gate_") as sd:
            stage = Path(sd)
            for p in [fc] + list(deps):
                shutil.copy2(_effective(product, p), stage / p.name)
            for name, src in supplied:
                shutil.copy2(src, stage / (name + ".py"))
            try:                                         # 检查按试跑仲裁的裁决结果跑(入口/单网元/kwargs)
                rep = cc.check_script(stage / fc.name, data_dir=data, entry=entry,
                                      per_ne=per_ne, entry_kwargs=kwargs)
            except TypeError:                            # 旧版检查组件不认裁决参数:退回老签名
                rep = cc.check_script(stage / fc.name, data_dir=data)
    finally:                                             # 检查时导入的脚本/共享库不许留缓存,防止串包
        for m in set(sys.modules) - mods_bak:
            if m.startswith(("search_", "cc_")):
                sys.modules.pop(m, None)
        sys.path[:] = [p for p in sys.path if p in path_bak]
    return rep


def _gate_triage(items, supplied):
    """检查项分档:FAIL 里适配函数能处理的(_ABSORBED)降为 WARN 放行;补库过的追加 S6+ WARN。返回 (fails, warns)。"""
    fails, warns = [], []
    for i in items:
        if i["tier"] == "FAIL" and i["id"] in _ABSORBED:
            warns.append(dict(i, tier="WARN",
                              msg=i["msg"] + "(试跑仲裁已确认适配函数能驱动——可以处理,正本仍须按约定格式改)"))
        elif i["tier"] == "FAIL":
            fails.append(i)
        elif i["tier"] == "WARN":
            warns.append(i)
    if supplied:
        warns.append({"id": "S6+", "tier": "WARN", "fix": "把脚本 import 的每个 search_*.py 打进交付包",
                      "msg": f"包缺共享模块 {[n for n, _s in supplied]}(导入时已补库;正本必须自包含,规范附录8)"})
    return fails, warns


def _gate_prev(side: Path) -> dict:
    """上一次检查的 sidecar 记录(没有/读不动 → {})。"""
    prev = {}
    if side.exists():
        try:
            prev = json.loads(side.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            prev = {}
    return prev


def _gate_fail(product: Path, fc: Path, deps, custom: Path, side: Path, fails, warns, baseline, now) -> str:
    """FAIL 处置:脚本先进场(绑定不落)+ 修复基准 .orig 落 scripts/custom/,sidecar 记 pending_repair;返回修法清单文本。"""
    _copy_scripts(product, fc, deps)                     # 脚本先进场(绑定不落),给修复留工作面
    for p in [fc] + list(deps):
        ob = custom / (p.name + ".orig")
        if not ob.exists():
            shutil.copy2(p, ob)                          # 修复基准=包里原件(回归/保修复都对着它)
    side.write_text(json.dumps(
        {"status": "pending_repair", "fails": fails, "warns": warns,
         "baseline": baseline, "at": now}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"格式检查不过({len(fails)} 项适配函数处理不了,绑定不写入):"]
    for i in fails:
        lines.append(f"    [FAIL] {i['id']} {i['msg']}")
        if i.get("fix"):
            lines.append(f"           修法:{i['fix']}")
    lines.append(f"  处置:脚本副本+修复基准已落 scripts/custom/({fc.name} 与 *.orig),"
                 "照修法**只修管道**(判断逻辑一行不动,拿不准算不算逻辑就停下问人),"
                 "改完重跑同一条迁移命令——包没变时修复副本不会被覆盖,检查通过了绑定才写入;"
                 "正本问题按结尾的退回清单反馈交付方。")
    return "\n".join(lines)


def _bl_norm(seq):
    """回归基线归一:undetermined 与 insufficient_data 同属"判不了",契约升级不算结论改变(四态兼容)。"""
    return [("undetermined" if str(x).strip().lower() in ("undetermined", "insufficient_data")
             else str(x).strip().lower()) for x in (seq or [])]   # 同属"判不了",契约升级不算结论改变


def _gate_regression(fc: Path, prev: dict, baseline, repaired: bool):
    """回归保护:修复后基线 matched 与修前不一致 → 返回拦下的说明文本;否则 None。"""
    if repaired and prev.get("status") == "pending_repair" \
            and (prev.get("baseline") or {}).get("matched") and baseline \
            and _bl_norm(prev["baseline"]["matched"]) != _bl_norm(baseline.get("matched")):
        return (f"  回归不一致:修复前测试数据 matched={prev['baseline']['matched']},"
                f"修复后={baseline.get('matched')}——管道修复改变了判断结论,这就动到逻辑了。"
                f"对照 scripts/custom/{fc.name}.orig 回滚多余改动,或把差异交人定夺。")
    return None


def _contract_gate(product: Path, sub: Path, fc: Path, deps, supplied,
                   entry=None, per_ne=False, kwargs=None, explicit=False):
    """导入时的契约检查(两档处置)。返回 ("skip"|"ok"|"fail", info|修法文本):
    - FAIL(适配函数处理不了)→ 不写绑定:把脚本连同 .orig 修复基准落进 scripts/custom/,返回修法清单
      ——模型照清单只修输入输出契约(副本),重跑同命令续迁(保修复规则保证副本不被包里原件冲掉);
    - WARN(适配函数能处理/静态无法确证)→ 放行,记台账+退回清单;
    - 回归保护:包里带 测试数据/ 时检查自动记基线;修复后基线 matched 与修前不一致 → 拦下
      (契约修复不许改变判断结论)。"""
    cc = _contract_mod()
    if cc is None:
        return "skip", None
    custom = product / "scripts" / "custom"
    rep = _gate_run_check(cc, product, sub, fc, deps, supplied, entry, per_ne, kwargs)
    items = [i for s in rep["scripts"] for i in s["items"]]
    fails, warns = _gate_triage(items, supplied)
    baseline = rep.get("baseline")
    side = custom / f"{fc.stem}.contract.json"
    prev = _gate_prev(side)
    dst, orig = custom / fc.name, custom / (fc.name + ".orig")
    repaired = (dst.exists() and orig.exists() and orig.read_bytes() == fc.read_bytes()
                and dst.read_bytes() != fc.read_bytes())
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if fails and explicit:                               # 人工 --entry 强推:人>名单,只警告不拦(与仲裁同款)
        print(f"  ⚠ 格式检查 {len(fails)} 项 FAIL({','.join(i['id'] for i in fails)})"
              "——按人工 --entry 指定放行;运行期该节点判不了先查这些项")
        warns = warns + [dict(i, tier="WARN") for i in fails]
        fails = []
    if fails:
        return "fail", _gate_fail(product, fc, deps, custom, side, fails, warns, baseline, now)
    regression = _gate_regression(fc, prev, baseline, repaired)
    if regression is not None:
        return "fail", regression
    repairs = [i["id"] for i in prev.get("fails", [])] \
        if repaired and prev.get("status") == "pending_repair" else []
    info = {"warns": [i["id"] for i in warns], "warn_items": warns,
            "repairs": repairs, "baseline": baseline}
    custom.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(
        {"status": "repaired" if repaired else "clean", "warns": info["warns"],
         "repairs": repairs, "baseline": baseline, "at": now},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return "ok", info


def _gate_extra(info) -> dict:
    """契约检查信息 → 台账附加字段(只写非空项)。"""
    if not info:
        return {}
    out = {}
    if info.get("warns"):
        out["contract_warns"] = info["warns"]
    if info.get("repairs"):
        out["repairs"] = info["repairs"]
    return out


def _factory_report(rows):
    """退回清单:rows=[(标签, warn_items, repairs)…] 有内容才打印。副本已处理/已修不影响本次执行,
    但正本的问题必须回到交付方手里,否则每次全量交付都会再出现一遍。"""
    rows = [(lb, wi, rp) for lb, wi, rp in rows if wi or rp]
    if not rows:
        return
    print("\n  退回清单(反馈交付方按规范改正本;副本已处理/已修,不影响本次执行):")
    for label, warn_items, repairs in rows:
        parts = []
        if repairs:
            parts.append(f"管道已修 {','.join(repairs)}(正本照 scripts/custom/ 副本翻新)")
        parts += [f"{i['id']} {i['msg']}" for i in warn_items]
        print(f"    「{label}」:")
        for s in parts:
            print(f"      - {s}")


REBUILD_TUNING = False   # --rebuild-tuning:存量调优表备份为 .bak 后从产物重建(旧版导出器的结构病解法)


def _surgery_prepare(product: Path) -> None:
    """写入前置:--rebuild-tuning 时把存量调优表备份为 .bak 并移除;表比 tree_index 新(可能有未导入的
    人工修改,也可能只是上次迁移的回导)→ 先导入,绝不覆盖。"""
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    xls = sorted(p for p in (product / "tree").glob("*.tuning.xlsx")
                 if not p.name.startswith("~$"))
    if REBUILD_TUNING and xls:
        for p in xls:
            bak = p.with_suffix(p.suffix + ".bak")
            if bak.exists():
                bak.unlink()
            p.rename(bak)
            print(f"  [重建] 存量调优表 {p.name} 已备份为 {bak.name} 并移除——"
                  f"用当前版导出器从产物重建;若里面有未导入的手工修改,从 .bak 找回")
        xls = []
    if xls and tis and xls[0].stat().st_mtime > tis[0].stat().st_mtime:
        print("  [0/3] 调优表比 tree_index 新 → 先导入现有内容(有未导入的人工修改也不会丢)…")
        try:
            run_step(product, "import_tree_excel.py", "预导入(收编现有调优表)")
        except SystemExit as e:
            raise SystemExit(str(e) + "\n提示:预导入失败卡的是**存量调优表**,不是这次的交付包。"
                             "若这份调优表里没有【尚未导入】的手工修改,把 tree/ 下的 *.tuning.xlsx 删掉重跑迁移即可"
                             "(会用当前版导出器从产物重建;「多个顶层节点/层级列错位」这类结构病多为旧版导出器所产);"
                             "或直接加 --rebuild-tuning 重跑——自动备份 .bak 后重建,不用手删。")


def _surgery_apply(product: Path, edits, adds, cond_edits, sol_edits, en_edits, dels) -> None:
    """逐行改调优表,顺序固定:改既有行 → 新增行 → 判据同步 → 解决方案 → 英文格 → 删行(最后执行)。"""
    for nid, cond, b in edits:
        edit_tuning_excel(product, nid, cond, b)
    for pnid, pchain, name, cond, b, *rest in adds:
        edit_tuning_add(product, pnid, pchain, name, cond, b, en=(rest[0] if rest else None))
    for nid, cond in cond_edits:
        edit_tuning_condition(product, nid, cond)
    for nid, sol in sol_edits:
        edit_tuning_solution(product, nid, sol)
    for nid, en in en_edits:
        edit_tuning_en(product, nid, **en)
    for nid in dels:
        edit_tuning_del(product, nid)


def _surgery_summary(edits, adds, cond_edits, sol_edits, en_edits, dels, note) -> str:
    """[2/3] 汇总行文案:各类改动的行数(为 0 的不显示)+ 说明。"""
    return (f"  [2/3] 已改调优表 {len(edits)} 行"
            + (f" + 新增 {len(adds)} 行(node_id 由导入自动分配)" if adds else "")
            + (f" + 判据同步 {len(cond_edits)} 行(绑定不动)" if cond_edits else "")
            + (f" + 解决方案同步 {len(sol_edits)} 行" if sol_edits else "")
            + (f" + 英文同步 {len(en_edits)} 行" if en_edits else "")
            + (f" + 删行 {len(dels)} 个(全量表里已无该节点)" if dels else "")
            + ":" + (note or "判据→原文列,旧绑定清空,绑定:cross→S(fault_check)"))


def _surgery(product: Path, edits, adds=(), cond_edits=(), dels=(), sol_edits=(), note=None,
             en_edits=()) -> None:
    """edits=[(nid, condition, binding)…] 改既有行;adds=[(父nid, 父链名字, 节点名, condition, binding[, 英文dict])…]
    新增行(增节点,node_id 留空由 import 分配);cond_edits=[(nid, condition)…] 只改判据
    绑定不动(判据同步行);dels=[nid…] 删行(全量表模式删节点,最后执行);
    sol_edits=[(nid, 解决方案文本)…] 只改解决方案中文列;
    en_edits=[(nid, {"name_en"/"condition_en"/"sol_en": 文本})…] 只改英文格(""=清格)。
    预导入(如需)→ 一次导出 → 改/增/删 N 行 → 一次导入 → 回导。批量也只走一轮
    export/import:写入是**原子**的——任一行编译不过,整批不生效。
    表比 tree_index 新(可能有未导入的人工修改,也可能只是上次迁移的回导)→ 先导入,绝不覆盖。"""
    _surgery_prepare(product)
    print("  [1/3] 导出调优表…")
    run_step(product, "export_tree_excel.py", "导出调优表")
    _surgery_apply(product, edits, adds, cond_edits, sol_edits, en_edits, dels)
    print(_surgery_summary(edits, adds, cond_edits, sol_edits, en_edits, dels, note))
    print("  [3/3] 编译回产物…")
    run_step(product, "import_tree_excel.py", "导入编译")
    run_step(product, "export_tree_excel.py", "回导调优表(同步)")


def _fingerprint(sub: Path) -> str:
    """子包内容指纹(文件相对路径+字节,顺序稳定):全量交付时判"这包和上次迁的一样不一样"。
    按内容不按 zip 文件哈希——同内容重打的 zip(条目时间戳变了)不该被当成变化;
    共同的外层包裹目录剥掉再算——同一个包"合集里解出"与"单 zip 解出"嵌套深度差一层,
    不归一会把没变的包误判成变化(白重迁一次);
    **只算迁移真正消费的文件**:判断方法*.md(排除规范)+ 全部 .py(脚本与 search 依赖)——
    执行记录.md 每次跑 tester 都变、测试数据也不进产物,算上它们会把"没改"误判成"改了"。"""
    import hashlib

    def _substantive(p: Path) -> bool:
        if p.suffix.lower() == ".py":
            return True
        return p.suffix.lower() == ".md" and p.name.startswith("判断方法") and "规范" not in p.name

    files = [(p.relative_to(sub).parts, p) for p in sorted(sub.rglob("*"))
             if p.is_file() and _substantive(p)]
    if not files:                                        # 防御:奇形包(没有实质文件)退回全量哈希
        files = [(p.relative_to(sub).parts, p) for p in sorted(sub.rglob("*")) if p.is_file()]
    while files and all(len(parts) > 1 for parts, _p in files) \
            and len({parts[0] for parts, _p in files}) == 1:
        files = [(parts[1:], p) for parts, p in files]
    h = hashlib.sha256()
    for parts, p in files:
        h.update("/".join(parts).encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _ledger_read(product: Path) -> dict:
    mig_p = product / "tree" / "migrations.json"
    return json.loads(mig_p.read_text(encoding="utf-8")) if mig_p.exists() else {}


def _ledger(product: Path, entries) -> None:
    """entries 每条 7 元组;可选第 8 元 dict(契约检查信息:contract_warns/repairs)直接并进记录。"""
    mig_p = product / "tree" / "migrations.json"
    mig = _ledger_read(product)
    for e in entries:
        nid, pkg_node, fc_name, entry, per_ne, kwargs, fp = e[:7]
        extra = e[7] if len(e) > 7 and e[7] else {}
        mig[nid] = {"package_node": pkg_node, "script": f"custom/{fc_name}", "entry": entry,
                    "per_ne": per_ne, "entry_kwargs": kwargs or {}, "fingerprint": fp,
                    "migrated_at": datetime.datetime.now().isoformat(timespec="seconds"), **extra}
    mig_p.write_text(json.dumps(mig, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_targets(nodes, clears):
    """清除判据的行 → (要清的 [(nid, 名)], 已是空跳过 [(nid, 名)], 定位失败 [(路径, 原因)])。
    幂等:全量表每次都带清除判据的行,判据已空的节点不再动(天生无判据的结构节点同样落这档)。
    纯函数零副作用,执行(改调优表)由调用方做。"""
    edits, already, misses = [], [], []
    for names, rowno in clears:
        nid, why, _d = resolve_node_by_path(nodes, names)
        if nid is None:
            misses.append((names, f"表第{rowno}行:{why}"))
            continue
        cond = str((nodes.get(nid) or {}).get("condition") or "").strip()
        (edits if cond else already).append((nid, str(nodes[nid].get("name") or nid)))
    return edits, already, misses


def _norm_jd(s) -> str:
    """判据文本归一化比较:压掉全部空白(excel 单元格与 md 多行只是排版差,不算变化)。"""
    return "".join(str(s or "").split())


def _is_subseq(sub, full) -> bool:
    it = iter(full)
    return all(any(s == f for f in it) for s in sub)


def _delete_targets(nodes, all_paths, n_lvls, force=False):
    """--map-mode all 的删除候选:树上有、整张全量表(迁移/结构/清除判据行路径的并集)里
    踪影全无的节点。"在表里"用与 resolve_node_by_path 同一套子序列语义(根→节点的名字链
    按顺序出现在某行路径里;母表高层可以比我们的树多)——由此候选集天然对子树封闭:
    后代在表里则祖先必在,所以"祖先删、后代漏"不会发生。
    防线:①树根名在全表里踪影全无 → 表和树对不上(错表/错树),返回 (None, 原因),一个不删;
    ②深度超出表层级列数的节点表达不了 → 不删,单独点名。
    返回 ([(nid, 名)], [(nid, 名) 超深点名]) 或 (None, 原因)。"""
    chains = {nid: tuple(_name_chain(nodes, nid)) for nid in nodes}
    roots = [nid for nid, ch in chains.items() if len(ch) == 1]
    paths = [tuple(p) for p in all_paths]

    def present(ch):
        return any(_is_subseq(ch, p) for p in paths)

    for r in roots:
        if not present(chains[r]):
            return None, (f"树根「{chains[r][0]}」在对应表所有行的路径里都找不到——"
                          "表和这棵树对不上(错表?错产物?),全量删除一个不执行")
    dels, too_deep = [], []
    for nid, ch in sorted(chains.items(), key=lambda kv: len(kv[1])):
        if len(ch) <= 1 or present(ch):
            continue
        name = str(nodes[nid].get("name") or nid)
        (too_deep if len(ch) > n_lvls else dels).append((nid, name))
    non_root = sum(1 for ch in chains.values() if len(ch) > 1)
    if not force and dels and len(dels) > max(3, non_root // 2):
        # 第三道保险:全量表应包含全部节点——候选过半更像「部分表被标成了 ALL」
        return None, (f"删除候选 {len(dels)}/{non_root} 个,超过全树一半——这更像部分表被标成了"
                      " ALL(全量表应包含全部节点);一个不删。确认这张表确为全量导出、"
                      "就是要大改版,加 --force 重跑以执行删除")
    return dels, too_deep


# ---------- 对应表模式(--map:zip 名 ↔ 树路径) ----------
# 层级列表头:格式没跟交付方钉死,能认的都认——裸 L1 / L1(Chinese) / L1(中文) / 全角括号/
# 大小写/内部空格;唯独「L3 Code」这类编码列(无括号带后缀)不吃。
# 平台1 导出表(L1_Name_CN/L1_Name_EN 成对 + Product_Obs + Product_Name)也认:
# 节点定位取 _Name_CN 列(树节点名是中文),Product_Obs(下载用的 obs 地址,是工装层的事)整列忽略,
# Product_Name 当 zip 列;_Name_EN / Diagnosis_Method_EN / Solution_EN 不参与定位,由
# _parse_map_header_en 单独识别,内容同步进树的 name_en / condition_en 与 solutions.json 的 en。
_MAP_LVL_RE = re.compile(r"^L(\d+)(?:\([^)]*\))?$", re.IGNORECASE)
_MAP_LVL_CN_RE = re.compile(r"^L(\d+)_Name_CN$", re.IGNORECASE)
_MAP_LVL_EN_RE = re.compile(r"^L(\d+)_Name_EN$", re.IGNORECASE)
_MAP_LVL_EN2_RE = re.compile(r"^L(\d+)\(English\)$", re.IGNORECASE)


_MARK_KEYS = ("是否修改", "是否变更", "是否优化", "需要优化", "修改标记", "is_modified", "need_optimize")
_MARK_EXACT = ("modified", "changed", "is_update", "update_flag")
_JD_KEYS = ("判据", "判断方法", "判断条件", "judgment", "judgement", "diagnosis_method", "diagnosismethod")
_TUNING_COL_PREFIXES = ("判据:", "绑定:", "meta")


def _norm_header(h) -> str:
    return str(h).strip().replace("（", "(").replace("）", ")").replace(" ", "")


def _is_marker_col(hn: str) -> bool:
    """标记列(平台正式名 Need_Optimize;老写法 是否修改/is_modified 等)。"""
    low = hn.lower()
    return any(k in low for k in _MARK_KEYS) or low in _MARK_EXACT


def _is_judgment_col(hn: str) -> bool:
    """判据列(平台正式名 Diagnosis_Method_CN/EN;老写法 判据/判断方法/judgement_condition 等)。"""
    low = hn.lower()
    return any(k in low for k in _JD_KEYS)


def _is_tuning_header(names) -> bool:
    """是不是我们自己的调优表:有 node_id 列且带 判据:/绑定:/meta 列。带标记列的一律不算——
    平台表带 Node_ID(节点 UUID)与 Need_Optimize,认对应表靠"层级列+Product_Name",标记列是备用依据。"""
    lows = [n.lower() for n in names]
    if "node_id" not in lows or any(_is_marker_col(n) for n in names):
        return False
    return any(n.startswith(_TUNING_COL_PREFIXES) for n in names)


def _scan_map_header(header, names):
    """表头逐列归类 → (层级列 [(层,原列名)], zip 列, Product_Name 列, 疑似 zip 列, 判据候选, 标记列, 解决方案候选)。
    平台表的 Node_ID / 英文名列 / Product_Obs 整列忽略。"""
    lvls, zip_col, pname_col, zipish, jd_cands, mk_col, sol_cands = \
        [], None, None, [], [], None, []
    for h, hn in zip(header, names):
        if hn.lower() == "node_id":
            continue                                     # 平台表的节点 UUID:整列忽略
        m = _MAP_LVL_RE.match(hn) or _MAP_LVL_CN_RE.match(hn)
        if m:
            lvls.append((int(m.group(1)), h))
        elif _MAP_LVL_EN_RE.match(hn) or hn.lower() == "product_obs":
            continue                                     # 平台1 表:英文名列/obs 地址列,不参与
        elif hn.lower() == "zip":
            zip_col = h
        elif hn.lower() == "product_name":
            pname_col = h                                # 平台1 表:zip 包名列
        elif "zip" in hn.lower():
            zipish.append(h)
        elif _is_marker_col(hn):
            mk_col = h                                   # 标记列:在判据列之前认,防"判据是否修改"误吸
        elif "解决方案" in hn or "solution" in hn.lower():
            sol_cands.append(h)                          # 解决方案列(平台名或调优表 Recommend Solution)
        elif _is_judgment_col(hn):
            jd_cands.append(h)
    return lvls, zip_col, pname_col, zipish, jd_cands, mk_col, sol_cands


def _pick_cn(cands):
    """成对的中/英列取中文那列(列名不带 _en/English 的优先);没有候选 → None。"""
    non_en = [c for c in cands if not str(c).strip().lower().endswith("_en")
              and "english" not in str(c).lower()]
    return (non_en or cands)[0] if cands else None


def _parse_map_header(header):
    """表头 → (层级列升序 [(层,原列名)…], zip 列名, 判据列名|None, 标记列名|None, 解决方案列名|None);
    不像对应表(缺层级列/缺 zip 列)或是我们自己的调优表 → 五个 None。
    平台表的 Node_ID 列(节点 UUID)整列忽略:节点定位靠路径名字,不依赖平台编号。
    zip 列:先认整格就叫 zip 的,没有再认 Product_Name(平台1 表:值=交付 zip 包名),
    再认唯一含 zip 的列名。
    平台1 表的 L{n}_Name_CN 当层级列(定位靠中文名),Product_Obs 整列忽略;L{n}_Name_EN 不参与
    定位,由 _parse_map_header_en 识别后同步进树。
    判据列(可选):表头含 判据/判断方法/判断条件/judgment/judgement 的列——**在场即启用
    "判据与包名都为空=清除"与"判据以表为准"语义**(老表没有此列,行为一切照旧,零兼容风险)。
    平台1 增强表判据列成对(judgement_condition_cn/en):取 cn(树节点判据是中文),en 由
    _parse_map_header_en 识别、同步进树的 condition_en。
    标记列(可选,第四返回值):表头含 是否修改/是否变更/需要优化/is_modified/modified 等
    ——在场即启用**标记契约**:结构(增删)仍看行的出现/消失,内容(迁移/判据同步/清除/
    解决方案同步)只对标记行执行;未标记行只当结构参照(zip 不迁、判据不同也不动只提示)。
    解决方案列(可选,第五返回值):表头含 解决方案/solution 的列(成对取 cn 弃 en)——
    在场即启用"解决方案以表为准"同步(空不清除:很多节点本无解决方案,空≠删除)。"""
    names = [_norm_header(h) for h in header]
    if _is_tuning_header(names):
        return None, None, None, None, None
    lvls, zip_col, pname_col, zipish, jd_cands, mk_col, sol_cands = _scan_map_header(header, names)
    if zip_col is None:
        zip_col = pname_col
    if zip_col is None and len(zipish) == 1:
        zip_col = zipish[0]
    if not lvls or zip_col is None:
        return None, None, None, None, None
    return sorted(lvls), zip_col, _pick_cn(jd_cands), mk_col, _pick_cn(sol_cands)


def _parse_map_header_en(header):
    """表头 → (英文层级列 {层级: 原列名}, 英文判据列名|None, 英文解决方案列名|None)。
    识别平台全量表的 L{n}_Name_EN / Diagnosis_Method_EN / Solution_EN,以及 L{n}(English) 写法;
    没有英文列就全空。英文列只做内容同步,不参与节点定位。"""
    lvls, jd_en, sol_en = {}, None, None
    for h in header:
        hn = _norm_header(h)
        low = hn.lower()
        m = _MAP_LVL_EN_RE.match(hn) or _MAP_LVL_EN2_RE.match(hn)
        is_en = low.endswith("_en") or "english" in low
        if m:
            lvls.setdefault(int(m.group(1)), h)
        elif is_en and ("解决方案" in hn or "solution" in low):
            sol_en = sol_en or h
        elif is_en and _is_judgment_col(hn):
            jd_en = jd_en or h
    return lvls, jd_en, sol_en


def _mk_true(v) -> bool:
    """标记列取值:是/要/需要/Y/yes/1/true(不分大小写)算"要改",其余(含空)算"不改"。"""
    return str(v or "").strip().lower() in ("y", "yes", "1", "true", "是", "要", "需要", "修改")


def _read_table(path: Path):
    """读对应表 → (header, rows[dict])。xlsx 走零依赖 xlsx_reader(skills 目录/engine 两级取路径);
    csv 标准库+编码自测(同事给 csv 也认)。"""
    if path.suffix.lower() == ".csv":
        import csv as _csv
        import io as _io
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "ignore")
        reader = _csv.DictReader(_io.StringIO(text))
        header = list(reader.fieldnames or [])
        return header, [{(k or ""): ("" if v is None else str(v)) for k, v in r.items()} for r in reader]
    for cand in (_mat("cs-scot-generate") / "scripts",            # 发行包里叫 generate/,走 _mat
                 Path(__file__).resolve().parents[1] / "engine"):
        if (cand / "xlsx_reader.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    import xlsx_reader
    return xlsx_reader.read_xlsx(path)


def _map_row_cells(r: dict, lvls, zip_col, jd_col, mk_col):
    """一行的基本格 → (zip 名, 判据文本, 标记, 有值的层级格 [(层, 名字)…] 按层升序)。
    没有判据列判据为 "";没有标记列一律视同已标记。"""
    z = str(r.get(zip_col) or "").strip()
    jd = str(r.get(jd_col) or "").strip() if jd_col is not None else ""
    marked = _mk_true(r.get(mk_col)) if mk_col is not None else True
    filled = [(l, v) for l, c in lvls for v in [str(r.get(c) or "").strip()] if v]
    return z, jd, marked, filled


def _map_en_cell(r: dict, own_lvl: int, en_lvls: dict, jd_en_col, sol_en_col, marked: bool) -> dict:
    """一行的英文格:英文名(本行最深层的英文格)、英文判据、英文解决方案;判据列/解决方案列表里没有时为 None,空格为 ""。"""
    return {
        "name_en": str(r.get(en_lvls[own_lvl]) or "").strip() if own_lvl in en_lvls else "",
        "condition_en": str(r.get(jd_en_col) or "").strip() if jd_en_col is not None else None,
        "sol_en": str(r.get(sol_en_col) or "").strip() if sol_en_col is not None else None,
        "marked": marked}


def _map_sort_row(i: int, names, z: str, jd: str, marked: bool, jd_col,
                  out, zipless, clears, unmarked_zip) -> None:
    """按 zip/判据/标记把一行归入:清除判据的行 / 结构行 / 未标记的 zip 行(只当结构参照)/ 迁移行。"""
    if not z:
        if names and jd_col is not None and not jd and marked:
            clears.append((names, i))                # 判据与包名都空+标记=清除指令(界面删了判据与包)
        elif names:
            zipless.append((names, jd, marked))
        return
    if not names:
        raise SystemExit(f"对应表第 {i} 行填了 zip「{z}」但层级列全空——没有节点路径,对应不了")
    if not marked:
        unmarked_zip.append(names[-1])
        zipless.append((names, jd, marked))
        return
    out.append((names, z, i, jd))


def _map_nothing_to_do(out, clears, sols, jd_col, zipless, en_rows) -> bool:
    """表里没有任何要执行的对应关系:无迁移行、无清除行、无解决方案、无可同步的判据结构行、无英文内容。"""
    return not out and not clears and not sols and not (jd_col is not None and zipless) \
        and not any(e["name_en"] or e["condition_en"] or e["sol_en"] for e in en_rows.values())


def load_map_excel(path: Path):
    """同事的对应表(xlsx/csv):层级列 + zip 列 + 可选判据列(表头兼容见 _parse_map_header;
    **没有 node_id**)。一行一节点、上级各层名字逐行照抄(与调优表相同)。行语义(全量表模式):
    - zip 有值 → 迁移行(判据列有值时**判据以表为准**——界面是用户最后编辑的状态,
      与包内判断方法不一致的在退回清单点名);
    - zip 空 + 判据有值(或表根本没有判据列)→ 结构行:节点在、没配包——表有判据列时
      判据与树上不一致就**同步判据进树(绑定不动)**,没有判据列则零动作
      (全量对账用它区分"还在只是没包" vs "节点已从树里删除");
    - zip 空 + 判据空 + **表有判据列** → 清除判据的行:用户在界面上删了该节点的判据与包
      → 清该节点的判据+绑定(**节点本身保留**;删除整节点走 --map-mode all 的全量对账)。
    返回 ([(路径名字列表, zip名, 行号, 判据文本)], [(结构行路径, 判据文本, 标记)],
          [(清除判据行路径, 行号)], 层级列数, {路径元组: 解决方案文本},
          {路径元组: {"name_en", "condition_en", "sol_en", "marked"}})。
    英文列(表有才收):每行的英文名(本行最深层的英文格)、英文判据、英文解决方案原样收进末位字典,
    不看标记;判据列/解决方案列表里没有时对应值为 None,空格为 ""。
    **标记契约**(表有标记列时):内容动作只对标记行执行——未标记行的 zip 不迁(只当登记,
    提示一行)、判据与包名都空的行不清;结构增删不看标记(看行的出现/消失)。无标记列=全部行视同已标记。
    解决方案:只收**标记行的非空值**进末位字典(以表为准同步;空≠删除,不清)。"""
    header, rows = _read_table(path)
    lvls, zip_col, jd_col, mk_col, sol_col = _parse_map_header(header)
    if lvls is None:
        raise SystemExit(f"对应表格式不对:需要层级列(L1..Ln,裸名/带 (Chinese) 后缀/平台1 的"
                         f" L1_Name_CN 都认)+「zip」列(或平台1 的 Product_Name 列),"
                         f"且不该有 node_id 列;实际表头:{header}")
    en_lvls, jd_en_col, sol_en_col = _parse_map_header_en(header)
    has_en = bool(en_lvls or jd_en_col is not None or sol_en_col is not None)
    out, zipless, clears, sols, unmarked_zip, en_rows = [], [], [], {}, [], {}
    for i, r in enumerate(rows, start=2):
        z, jd, marked, filled = _map_row_cells(r, lvls, zip_col, jd_col, mk_col)
        names = [v for _l, v in filled]
        if names and has_en:
            en_rows[tuple(names)] = _map_en_cell(r, filled[-1][0], en_lvls, jd_en_col, sol_en_col, marked)
        if names and marked and sol_col is not None:
            sol = str(r.get(sol_col) or "").strip()
            if sol:
                sols[tuple(names)] = sol                 # 解决方案以表为准(空≠删除,不收)
        _map_sort_row(i, names, z, jd, marked, jd_col, out, zipless, clears, unmarked_zip)
    if unmarked_zip:
        print(f"  [标记] {len(unmarked_zip)} 行填了 zip 但未标记变更——按标记列的规则不迁移,只当结构参照:"
              + "、".join(unmarked_zip[:6]) + ("…" if len(unmarked_zip) > 6 else ""))
    if _map_nothing_to_do(out, clears, sols, jd_col, zipless, en_rows):
        raise SystemExit("对应表里没有任何一行填了 zip 列,也没有清除判据的行(判据与包名都空)或可同步的"
                         "判据/解决方案/英文行——没有要执行的对应关系")
    return out, zipless, clears, len(lvls), sols, en_rows


def find_map_table(pkg: Path):
    """交付包里自动找对应表(同事把表和 zip 打在一个包里给,不用单独 --map 指):
    扫 xlsx/xlsm/csv,表头像对应表(层级列+zip 列、无 node_id)的才算。
    恰一张 → 返回;多张 → 停止并报错(不猜哪张算数);没有 → None。"""
    hits = []
    for p in sorted(pkg.rglob("*")):
        if p.suffix.lower() not in (".xlsx", ".xlsm", ".csv") or p.name.startswith("~$"):
            continue
        try:
            header, _rows = _read_table(p)
        except Exception:
            continue                                     # 读不动的(交付包里的测试数据表等)不算
        if _parse_map_header(header)[0] is not None:
            hits.append(p)
    if len(hits) > 1:
        raise SystemExit(f"交付包里有 {len(hits)} 张都像对应表(层级列+zip 列):"
                         f"{[p.name for p in hits]}——留一张,或用 --map 明确指定哪张算数。")
    return hits[0] if hits else None


def _name_chain(nodes, nid):
    """根→nid 的名字链(路径消歧与增节点的祖先层照抄共用)。"""
    out, cur = [], nid
    while cur and cur in nodes:
        out.append(str(nodes[cur].get("name") or "").strip())
        cur = nodes[cur].get("parent")
    return list(reversed(out))


def resolve_node_by_path(nodes, path_names):
    """按「名字路径」定位节点:目标=路径最深一格;同名节点用**祖先链 ⊂ 路径(有序子序列)**消歧
    (平台母表的高层可能比我们子树多——如 L1 大类在我们的树外——所以不逐层硬对齐,只要求
    我们的根→节点名字链按顺序出现在表中路径里)。返回 (nid|None, 失败说明, 候选清单文本)。"""
    target = path_names[-1]
    cands = [nid for nid, nd in nodes.items()
             if str(nd.get("name") or "").strip() == target]
    chain = lambda nid: _name_chain(nodes, nid)  # noqa: E731

    def is_subseq(sub, full):
        it = iter(full)
        return all(any(s == f for f in it) for s in sub)

    if not cands:
        return None, f"树上没有名为「{target}」的节点", ""
    if len(cands) == 1:
        return cands[0], "", ""                          # 全树唯一同名:直接匹配(路径只是佐证,少填上级不卡)
    hits = [nid for nid in cands if is_subseq(chain(nid), path_names)]
    detail = "; ".join(f"{nid}: {'/'.join(chain(nid))}" for nid in (hits or cands)[:4])
    if len(hits) == 1:
        return hits[0], "", ""
    if not hits:
        return None, ("树上有多个同名节点,而表中路径与它们的祖先链都对不上"
                      "(把上级各层名字填全以消歧)"), detail
    return None, "路径仍不唯一(树里存在全路径重名,先清树)", detail


def build_zip_registry(pkg: Path, tmp: Path):
    """合集里可匹配的子包登记:①目录形态里躺着的 *.zip(逐个解到临时目录)②嵌套 zip 已被
    unpack 解出的子目录。键=zip 名(带/不带 .zip 都注册)与子目录名;值=子包根目录。"""
    reg = {}

    def add(key, root):
        k = str(key).strip()
        if k:
            reg.setdefault(k, root)

    for z in sorted(pkg.rglob("*.zip")):
        root = tmp / f"mapz_{z.stem}"
        _unzip(z, root)
        inner = [d for d in root.iterdir() if d.is_dir()]
        sub = inner[0] if len(inner) == 1 and not any(root.glob("*.md")) else root
        add(z.name, sub)
        add(z.stem, sub)
    try:
        for sub in find_subpackages(pkg):
            if sub != pkg:
                add(sub.name, sub)
                add(sub.name + ".zip", sub)
    except SystemExit:
        pass                                             # 奇形包(顶层+子目录都有 md):zip 登记照常,由行级失败点名
    return reg


def _pick_for_row(sub: Path, row_name: str, md_name: str):
    """对应表驱动的脚本匹配:多脚本合集里**先按表行的节点名**找同名脚本(表为准 + 交付
    命名规范 fault_check_<节点名>.py),找不到再按包内判断方法的名字——同一份合集被表挂到
    别的节点名下时(联调实测:平台把一个对象摊给多行),不会把合集里别人的脚本绑过来;
    单脚本包/同名复用(dscp/8021p 共用一个包)行为不变。"""
    row_name = (row_name or "").strip()
    if row_name and row_name != (md_name or "").strip():
        want = f"fault_check_{row_name}.py"
        if any(p.name == want for p in sub.rglob("fault_check_*.py")):
            return find_scripts(sub, None, node_name=row_name)
    return find_scripts(sub, None, node_name=md_name)


def _print_map_summary(n_rows, n_ok, n_add, n_refreshed, has_fails, counted) -> None:
    """对应表批量迁移的汇总行:生效行数 + 各类动作计数(为 0 的不显示)+ 工具链公共文件刷新;
    表里没有标记行、结构也没变时,说清楚这一单到底做了什么。"""
    head = f"落地 {n_ok + n_add}/{n_rows} 行" + (f"(其中新增节点 {n_add} 个)" if n_add else "")
    parts = [text.format(n=n) for n, text in counted if n]
    if n_refreshed:
        parts.append(f"工具链公共文件刷新 {n_refreshed} 处")
    print("\n对应表批量迁移汇总:" + ",".join([head] + parts))
    if not (n_rows or n_add or has_fails or any(n for n, _t in counted[1:7])):
        print("  表里没有标记行,结构与树一致——本次"
              + ("只刷新了工具链公共文件" if n_refreshed else "没有任何改动(工具链公共文件也已是最新)"))


# 两种批量模式(--auto 逐子包 / --map 对应表)共用的累计器与备注拼接
class _Acc:
    """批量模式逐子包的累计器(只是个属性袋):oks / fails / skips 清单,seen_nid / seen_fc 查重,gates 契约信息。"""

    def __init__(self):
        self.oks, self.fails, self.skips = [], [], []    # ok=(sub,method,fc,deps,entry,per_ne,kwargs,nid,note,fp)
        self.seen_nid, self.seen_fc, self.gates = {}, {}, {}


def _join_note(note: str, extra: str) -> str:
    """节点备注串接:已有内容用「;」分隔。"""
    return (note + ";" if note else "") + extra


def _gate_note(note: str, ginfo: dict) -> str:
    """契约检查信息并进节点备注:管道已修 / 契约WARN(适配函数可处理)。"""
    if ginfo["repairs"]:
        note = _join_note(note, f"管道已修 {','.join(ginfo['repairs'])}")
    if ginfo["warns"]:
        note = _join_note(note, f"契约WARN {','.join(ginfo['warns'])}(适配函数可处理,详见退回清单)")
    return note


class _MapCtx(_Acc):
    """对应表模式一轮运行的上下文+累计器(只是个属性袋,各阶段辅助函数读写):参数/产物/树/台账/子包登记,
    以及 adds(增节点行)、jd_edits(判据同步)、sadds(纯判据新增)、struct_miss、used_roots。"""

    def __init__(self, a, product: Path, nodes, ledger, reg, mode: str):
        super().__init__()
        self.a, self.product, self.nodes, self.ledger, self.reg, self.mode = a, product, nodes, ledger, reg, mode
        self.roots = sorted({str(v) for v in reg.values()})
        # 不同包名、内容逐字节相同 = 同一份包挂在多个节点名下,必有错位(联调实测:平台把
        # 5 个对象摊给 16 行)。同名包被多行复用是合法惯例,不在此列——按指纹分组只抓前者
        self.fp_groups: dict[str, list[str]] = {}
        for _r in self.roots:
            self.fp_groups.setdefault(_fingerprint(Path(_r)), []).append(_r)
        self.tangled = {f for f, rs in self.fp_groups.items() if len(rs) > 1}
        self.adds, self.jd_edits, self.sadds, self.struct_miss = [], [], [], []
        self.used_roots = set()


def _map_preimport(product: Path) -> None:
    """手工改过调优表(表比 tree_index 新)→ 开头就先导入,这一轮的匹配直接看新树。
    不能只靠 _surgery 里的 [0/3]:全跳过/全失败的轮次进不了写入阶段,导入永远轮不上。"""
    tis0 = sorted((product / "tree").glob("*.tree_index.json"))
    xls0 = sorted(p for p in (product / "tree").glob("*.tuning.xlsx")
                  if not p.name.startswith("~$"))
    if not REBUILD_TUNING and xls0 and tis0 and xls0[0].stat().st_mtime > tis0[0].stat().st_mtime:
        print("  [预导入] 调优表比 tree_index 新(可能有手工修改)→ 先导入,再按新树匹配…")
        try:
            run_step(product, "import_tree_excel.py", "预导入(收编现有调优表)")
        except SystemExit as e:
            raise SystemExit(str(e) + "\n提示:卡的是**存量调优表**(手工修改不合规?),按上面报错"
                             "修表重跑;确认表里没有要保的手工修改,可加 --rebuild-tuning 重建。")


def _map_locate(ctx: _MapCtx, names, zname: str, rowno: int):
    """表行定位 → (label, 子包根, nid, 增节点的父 nid|None):zip 找不到 / 路径定位失败 / 增节点条件不满足
    都进失败清单并返回 None。增节点分支:全树无同名 + 父路径能唯一定位(MULTI 单不动树结构)。"""
    label = f"「{names[-1]}」(表第{rowno}行, zip={zname})"
    sub = ctx.reg.get(zname)
    if sub is None:
        avail = sorted({Path(r).name for r in ctx.roots})
        ctx.fails.append((label, f"合集里找不到 zip「{zname}」;可匹配的包:{avail[:10]}"
                                 f"{'…' if len(avail) > 10 else ''}", None))
        return None
    nid, why, detail = resolve_node_by_path(ctx.nodes, names)
    add_parent = None
    if nid is None and why.startswith("树上没有名为") and len(names) >= 2:
        if ctx.mode == "multi":                          # MULTI 单(部分表):不动树结构
            ctx.fails.append((label, "树上没有该节点;MULTI 单(部分表)不动树结构,不新增"
                                     "——增删节点请走全量表(optimizeType=ALL)", None))
            return None
        pnid, pwhy, _pd = resolve_node_by_path(ctx.nodes, names[:-1])
        if pnid is not None:                             # 增节点分支:全树无同名 + 父路径能唯一定位
            add_parent = pnid                            # (加法可回滚、有台账 → 自动做;删除仍只点名)
        else:
            ctx.fails.append((label, f"新增节点失败:全树无同名,但父路径也定位不了({pwhy})——"
                                     "父节点若也是本次新增,先跑一遍落父、再跑一遍落子;否则核对路径", None))
            return None
    elif nid is None:
        ctx.fails.append((label, f"路径定位失败:{why}" + (f"\n       {detail}" if detail else ""),
                          f'--only "{names[-1]}" --node <节点号>(去掉 --map 手工补)'))
        return None
    return label, sub, nid, add_parent


def _map_fresh(ctx: _MapCtx, label: str, sub: Path, nid, jd: str):
    """内容指纹:与台账一致的包跳过(幂等;判据格变了只同步判据、绑定不动);内容与别的 zip 逐字节相同的包=
    错位,进失败清单。返回指纹;跳过/失败 → None。"""
    fp = _fingerprint(sub)
    if nid and not ctx.a.force and ctx.ledger.get(nid, {}).get("fingerprint") == fp:
        cur = str((ctx.nodes.get(nid) or {}).get("condition") or "")
        if jd and _norm_jd(jd) != _norm_jd(cur):
            ctx.jd_edits.append((nid, jd))               # 包没变但界面改了判据:只同步判据(绑定不动)
        ctx.skips.append((nid, label))                   # 全量交付里没变的包:跳过(此后的人工调优不被冲)
        ctx.seen_nid.setdefault(nid, label)
        return None
    if fp in ctx.tangled:
        peers = sorted(Path(r).name for r in ctx.fp_groups[fp] if r != str(sub))
        try:
            mdn = parse_method_md(sub)["name"]
        except SystemExit:
            mdn = "?"
        ctx.fails.append((label, f"包内容与 {peers} 逐字节相同(内含判断方法「{mdn}」)——"
                                 "同一份包挂在多个不同 zip 名下,必有错位(上传错了对象,"
                                 "或表的 zip 列错了行),不猜:核对上传与对应表后重跑", None))
        return None
    return fp


def _map_probe(ctx: _MapCtx, label: str, sub: Path, names, nid):
    """包解析 → 脚本挑选(表行名优先)→ 入口探测 → 补库 → 试跑仲裁。
    返回 (method, fc, deps, entry, per_ne, kwargs, note, supplied);任一步不过 → 进失败清单,返回 None。"""
    try:
        method = parse_method_md(sub)
        fc, deps = _pick_for_row(sub, names[-1], method["name"])
    except SystemExit as e:
        ctx.fails.append((label, str(e), None))
        return None
    eff_fc = _effective(ctx.product, fc)                 # 修过管道的副本生效(重跑续迁不被原件卡死)
    entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
    if entry is None:
        hint = _stage_no_entry(ctx.product, fc, deps)
        ctx.fails.append((label, f"探测不到可用入口(顶层函数:{all_defs[:8]}{';' + note if note else ''})——{hint}",
                          f'或人工指定:--only "{method["name"]}" --node {nid or "<节点号>"} --entry <函数名>(去掉 --map)'))
        return None
    supplied, unsupplied = _with_supplied(fc, deps)
    if unsupplied:
        ctx.fails.append((label, f"包缺共享模块 {unsupplied} 且引擎里也没有副本——让交付方补进包(规范附录8)", None))
        return None
    try:
        entry, per_ne, kwargs, sm = arbitrate_entry(
            eff_fc, [_effective(ctx.product, p) for p in deps] + [s for _n, s in supplied],
            entry, per_ne, kwargs, explicit=False)
    except SystemExit as e:
        ctx.fails.append((label, str(e), f'--only "{method["name"]}" --node {nid or "<节点号>"} --entry <函数名>(去掉 --map)'))
        return None
    if sm:
        note = _join_note(note, sm)
    return method, fc, deps, entry, per_ne, kwargs, note, supplied


def _map_admit(ctx: _MapCtx, label: str, sub: Path, names, key: str, probe, jd: str):
    """同节点两行/同名脚本内容不同查重 → 包内名≠表名备注 → 契约检查 → 判据以表为准。
    返回 (note, cond);拦下 → 进失败清单,返回 None。"""
    method, fc, deps, entry, per_ne, kwargs, note, supplied = probe
    if key in ctx.seen_nid:
        ctx.fails.append((label, f"与表中「{ctx.seen_nid[key]}」都对到 {key}——同一节点两行,核对对应表", None))
        return None
    prev = ctx.seen_fc.get(fc.name)
    if prev is not None and prev[1] != fc.read_bytes():
        ctx.fails.append((label, f"脚本文件名「{fc.name}」与「{prev[0]}」重名且内容不同——让交付方改名", None))
        return None
    ctx.seen_nid[key], ctx.seen_fc[fc.name] = label, (label, fc.read_bytes())
    if method["name"].strip() != names[-1]:
        own = fc.name == f"fault_check_{names[-1]}.py"
        note = _join_note(note, (
            f"包内判断方法是「{method['name']}」≠表中「{names[-1]}」,脚本已按表行同名命中(判据以表为准)"
            if own else f"包内节点名「{method['name']}」≠表中「{names[-1]}」,以对应表为准"))
    verdict, ginfo = _contract_gate(ctx.product, sub, fc, deps, supplied,
                                    entry=entry, per_ne=per_ne, kwargs=kwargs)
    if verdict == "fail":
        ctx.fails.append((label, ginfo, None))
        return None
    if ginfo:
        ctx.gates[key] = ginfo
        note = _gate_note(note, ginfo)
    cond = jd if jd else method["condition"]         # 判据列有值=界面最后编辑的状态,以表为准
    if jd and _norm_jd(jd) != _norm_jd(method["condition"]):
        note = _join_note(note, "判据以对应表为准(包内判断方法与表不一致,退回交付方对齐)")
    return note, cond


def _map_row(ctx: _MapCtx, names, zname: str, rowno: int, jd: str) -> None:
    """对应表一行(zip↔路径):定位 → 指纹/错位 → 探测 → 查重与契约检查 → 进 oks(既有节点)/ adds(增节点)。"""
    loc = _map_locate(ctx, names, zname, rowno)
    if loc is None:
        return
    label, sub, nid, add_parent = loc
    ctx.used_roots.add(str(sub))
    key = nid if nid else f"+{add_parent}/{names[-1]}"   # 新增行还没有 nid,用 父+名 当对账键
    fp = _map_fresh(ctx, label, sub, nid, jd)
    if fp is None:
        return
    probe = _map_probe(ctx, label, sub, names, nid)
    if probe is None:
        return
    admit = _map_admit(ctx, label, sub, names, key, probe, jd)
    if admit is None:
        return
    method, fc, deps, entry, per_ne, kwargs, _note, _supplied = probe
    note, cond = admit
    if add_parent is not None:
        ctx.adds.append((sub, method, fc, deps, entry, per_ne, kwargs, add_parent, names[-1], note, fp, cond))
    else:
        ctx.oks.append((sub, method, fc, deps, entry, per_ne, kwargs, nid, note, fp, cond))


def _map_sync_zipless(ctx: _MapCtx, zipless):
    """判据同步行:zip 空 + 判据有值——树上有该节点且判据与表不同 → 只同步判据(绑定不动);
    树上没有 + ALL 单 → 纯判据新增(结构节点,无绑定);MULTI/旧默认不动结构。
    返回 (本次同步判据的 nid 集合, 未标记行判据不同的 [(nid, 名)])。"""
    synced_nids = {e[0] for e in ctx.jd_edits}
    unmarked_diff = []
    for names, jd, mk in zipless:
        if not jd and ctx.mode != "all":
            continue                                     # 老表(无判据列):结构行零动作,照旧
        znid, _zw, _zd = resolve_node_by_path(ctx.nodes, names)
        if znid is not None:
            if not jd or znid in synced_nids or znid in ctx.seen_nid:
                continue
            cur = str((ctx.nodes.get(znid) or {}).get("condition") or "")
            if _norm_jd(jd) != _norm_jd(cur):
                if mk:
                    ctx.jd_edits.append((znid, jd))
                    synced_nids.add(znid)
                else:                                    # 标记契约:未标记不动,只提示
                    unmarked_diff.append((znid, names[-1]))
        elif ctx.mode == "all" and len(names) >= 2:
            # 结构以表为准,增节点不看标记:jd 空 = 平台的无判据结构层,建裸节点
            pnid, _pw, _pd = resolve_node_by_path(ctx.nodes, names[:-1])
            if pnid is not None:
                ctx.sadds.append((pnid, _name_chain(ctx.nodes, pnid), names[-1], jd, None))
            else:
                ctx.struct_miss.append((names, _pw))
        elif ctx.mode == "all":
            ctx.struct_miss.append((names, "路径只有一层且树上无同名,定位不了"))
    return synced_nids, unmarked_diff


def _map_sync_solutions(product: Path, nodes, sols):
    """解决方案同步(标记行、非空、与产物 solutions.json 的中文文案不同才动;空≠删除)。返回 [(nid, 文本)]。"""
    sol_edits = []
    if sols:
        sp = product / "tree" / "solutions.json"
        try:
            cur_sols = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        except Exception:
            cur_sols = {}
        for names_t, sol in sols.items():
            snid2, _sw, _sd = resolve_node_by_path(nodes, list(names_t))
            if snid2 is None:
                continue                                 # 本次新增节点的解决方案:下一单幂等补上
            cur = str((cur_sols.get(snid2) or {}).get("zh") or "")
            if _norm_jd(sol) != _norm_jd(cur):
                sol_edits.append((snid2, sol))
    return sol_edits


def _map_parse_clears(ctx: _MapCtx, clears):
    """清除判据的行先解析(执行仍在写入之后):ALL 单里,判据与包名都空的行定位不到=平台树有这层、
    本地树没有(平台的无判据结构节点导出就是两格都空)→ 补建空节点(无判据无绑定);
    它名下的新叶子行第二遍运行即可挂上(与增节点"先落父再落子"同一条规矩)。
    返回 (要清的 [(nid, 名)], 已是空跳过 [(nid, 名)], 定位失败 [(路径, 原因)], 清除过半的守护说明|None)。"""
    c_edits, clear_already, clear_miss = [], [], []
    clear_guard = None
    if clears and not ctx.a.no_clear:
        c_edits, clear_already, clear_miss = _clear_targets(ctx.nodes, clears)
        with_jd = sum(1 for nd in ctx.nodes.values() if str(nd.get("condition") or "").strip())
        if not ctx.a.force and c_edits and len(c_edits) > max(3, with_jd // 2):
            # 与删除过半的检查对称:清除过半更像「平台判据数据没录全」,不像专家批量删判据
            clear_guard = (f"清除判据的行要实际清掉 {len(c_edits)}/{with_jd} 个有判据的节点,超过一半"
                           "——更像平台判据数据没录全(空≠删除),而非专家批量删判据;"
                           "清除一律不执行。确认表里的空判据确为删除指令,加 --force 重跑")
            c_edits = []
        if ctx.mode == "all" and clear_miss:
            rest = []
            for cnames, cwhy in clear_miss:
                pnid = resolve_node_by_path(ctx.nodes, cnames[:-1])[0] if len(cnames) >= 2 else None
                if pnid is not None:
                    ctx.sadds.append((pnid, _name_chain(ctx.nodes, pnid), cnames[-1], "", None))
                else:
                    rest.append((cnames, cwhy))
            clear_miss = rest
    return c_edits, clear_already, clear_miss, clear_guard


def _map_delete_candidates(ctx: _MapCtx, entries, zipless, clears, n_lvls: int):
    """ALL 单全量对账的删除候选:整张表(迁移/结构/清除判据行)里踪影全无的树节点。
    返回 (dels [(nid, 名)], too_deep [(nid, 名)], 守护说明|None)。"""
    dels, too_deep, del_guard = [], [], None
    if ctx.mode == "all":
        all_paths = [names for names, _z, _r, _j in entries] \
            + [names for names, _jd, _mk in zipless] + [names for names, _r in clears]
        res = _delete_targets(ctx.nodes, all_paths, n_lvls, force=ctx.a.force)
        if res[0] is None:
            del_guard = res[1]
        else:
            dels, too_deep = res
    return dels, too_deep, del_guard


def _en_cond_changed(e: dict, cur: dict, enid: str, cn_changed, clear_ids) -> bool:
    """英文判据要不要按表更新:本次中文判据被改的节点严格以表为准(表里为空也算改动=清空);否则非空且与树不同才算;
    中文判据为空的节点、本次清除的节点、表里没有英文判据列的行一律不动。"""
    has_cn = bool(str(cur.get("condition") or "").strip()) and enid not in clear_ids
    if not (e["condition_en"] is not None and (has_cn or enid in cn_changed) and enid not in clear_ids):
        return False
    if enid in cn_changed:
        return (e["condition_en"] or "") != (cur.get("condition_en") or "")
    return bool(e["condition_en"] and e["condition_en"] != (cur.get("condition_en") or ""))


def _map_en_delta(e: dict, cur: dict, enid: str, cn_changed, clear_ids, cur_sols_en) -> dict:
    """一个节点的英文改动(与树/solutions.json 不同的格才收):英文名非空且不同才存;英文判据见 _en_cond_changed;
    英文解决方案非空且不同才存。"""
    d = {}
    if e["name_en"] and e["name_en"] != (cur.get("name_en") or "") and e["name_en"] != cur.get("name"):
        d["name_en"] = e["name_en"]
    if _en_cond_changed(e, cur, enid, cn_changed, clear_ids):
        d["condition_en"] = e["condition_en"]
    if e["sol_en"] and e["sol_en"] != str((cur_sols_en.get(enid) or {}).get("en") or ""):
        d["sol_en"] = e["sol_en"]
    return d


def _map_sync_english(ctx: _MapCtx, en_rows, synced_nids, dels, c_edits):
    """英文同步(表带英文列时):英文名/英文判据/英文解决方案以表为准——非空就存,不看标记(补英文
    不是结构或判断内容的改动);本次中文判据被改的节点,英文判据严格以表为准,表里为空就清空
    (旧英文对应的是旧中文,留着会误导);中文判据为空的节点不存英文判据;本次删除的节点不动。
    返回 (en_edits [(nid, {…})], en_adds {(父nid, 名): 英文dict} 本次新增节点的英文随新增行一起写)。"""
    en_edits, en_adds = [], {}
    if en_rows:
        sp = ctx.product / "tree" / "solutions.json"
        try:
            cur_sols_en = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        except Exception:
            cur_sols_en = {}
        cn_changed = set(synced_nids)
        for _sub, _m, _fc, _deps, _e, _p, _k, onid, _n, _f, ocond in ctx.oks:
            if _norm_jd(ocond) != _norm_jd(str((ctx.nodes.get(onid) or {}).get("condition") or "")):
                cn_changed.add(onid)
        del_ids = {dnid for dnid, _nm in dels}
        clear_ids = {cnid for cnid, _nm in c_edits}
        for names_t, e in en_rows.items():
            enid, _ew, _ed = resolve_node_by_path(ctx.nodes, list(names_t))
            if enid is None:                             # 本次新增的节点:英文随新增行一起写
                if len(names_t) >= 2:
                    pnid = resolve_node_by_path(ctx.nodes, list(names_t[:-1]))[0]
                    if pnid is not None:
                        en_adds[(pnid, names_t[-1])] = e
                continue
            if enid in del_ids:
                continue
            d = _map_en_delta(e, ctx.nodes.get(enid) or {}, enid, cn_changed, clear_ids, cur_sols_en)
            if d:
                en_edits.append((enid, d))
    return en_edits, en_adds


def _map_surgery_rows(ctx: _MapCtx, en_adds):
    """写入用的行:既有行改判据+S 绑定(oks);新增行 = 带脚本绑定的 adds + 纯判据/结构节点的 sadds(英文随行)。"""
    edits = [(nid, cond, _binding(fc.name, entry, per_ne, kwargs, m["name"]))
             for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, _fp, cond in ctx.oks]
    adds = [(pnid, _name_chain(ctx.nodes, pnid), name, cond,
             _binding(fc.name, entry, per_ne, kwargs, m["name"]), en_adds.get((pnid, name)))
            for _sub, m, fc, _deps, entry, per_ne, kwargs, pnid, name, _note, _fp, cond in ctx.adds] \
        + [(pnid, chain, name, scond, b, en_adds.get((pnid, name)))
           for pnid, chain, name, scond, b in ctx.sadds]
    return edits, adds


def _find_child(nodes, pnid: str, name: str):
    """按 (父 nid, 名字) 找节点号(新增行导入后认领用);找不到 → None。"""
    return next((k for k, nd in nodes.items()
                 if str(nd.get("name") or "").strip() == name
                 and nd.get("parent") == pnid), None)


def _map_claim_new(ctx: _MapCtx, led: list):
    """新行的 nN 由 import 刚分配:重读 index 认领进台账(led 就地追加),并生成新增汇报。
    返回 (added_report [(nid, 名, 父, 脚本, 入口, 备注)], sadd_report [(nid, 名, 父, 有无判据)])。"""
    new_nodes = load_index(ctx.product).get("nodes") or {}
    added_report, sadd_report = [], []
    for _sub, m, fc, _deps, entry, per_ne, kwargs, pnid, name, note, fp, _cond in ctx.adds:
        new_nid = _find_child(new_nodes, pnid, name)
        if new_nid:
            led.append((new_nid, m["name"], fc.name, entry, per_ne, kwargs, fp,
                        _gate_extra(ctx.gates.get(f"+{pnid}/{name}"))))
        added_report.append((new_nid, name, pnid, fc.name, entry, note))
    for pnid, _chain, name, scond, _b in ctx.sadds:
        new_nid = _find_child(new_nodes, pnid, name)
        sadd_report.append((new_nid, name, pnid, bool(str(scond or "").strip())))
    return added_report, sadd_report


def _ledger_stamp(product: Path, nids, field: str) -> None:
    """台账留痕:有记录的节点写 <field>=现在(删节点记 deleted_at;清判据+绑定记 cleared_at——
    评估反思的清绑定合规通道按此认,不追杀);一个都没命中就不写盘。"""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    mig = _ledger_read(product)
    hit = False
    for nid in nids:
        if nid in mig:
            mig[nid][field] = now
            hit = True
    if hit:
        (product / "tree" / "migrations.json").write_text(
            json.dumps(mig, ensure_ascii=False, indent=2), encoding="utf-8")


def _map_write(ctx: _MapCtx, sol_edits, dels, en_edits, en_adds):
    """写入阶段(有任何改动才进):脚本进场 → 一轮调优表修改(原子生效)→ 脚本框架升级 → 台账
    (新增行重读 index 认领;删除的节点记 deleted_at)。返回 (added_report, sadd_report)。"""
    added_report, sadd_report = [], []
    if not (ctx.oks or ctx.adds or ctx.sadds or ctx.jd_edits or sol_edits or dels or en_edits):
        return added_report, sadd_report
    product = ctx.product
    for _sub, _m, fc, deps, *_rest in ctx.oks + ctx.adds:
        _copy_scripts(product, fc, deps)
    edits, adds = _map_surgery_rows(ctx, en_adds)
    _surgery(product, edits, adds,
             cond_edits=ctx.jd_edits, dels=[dnid for dnid, _nm in dels], sol_edits=sol_edits,
             en_edits=en_edits)
    upgrade_cross_scaffold(product)
    led = [(nid, m["name"], fc.name, entry, per_ne, kwargs, fp, _gate_extra(ctx.gates.get(nid)))
           for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, fp, _cond in ctx.oks]
    if ctx.adds or ctx.sadds:                            # 新行的 nN 由 import 刚分配:重读 index 认领进台账
        added_report, sadd_report = _map_claim_new(ctx, led)
    _ledger(product, led)
    if dels:                                             # 台账留痕:节点连行一起删了,记 deleted_at
        _ledger_stamp(product, [dnid for dnid, _nm in dels], "deleted_at")
    return added_report, sadd_report


def _map_exec_clears(ctx: _MapCtx, clears, c_edits):
    """清除判据的行执行(解析在写入前已做):界面删了判据与包 → 清判据+绑定(节点保留;
    走调优表修改,与迁移同一合规通道);--no-clear 只点名不执行。返回实际清掉的 [(nid, 名)]。"""
    if clears and ctx.a.no_clear:
        print(f"\n  [info] --no-clear:{len(clears)} 个清除判据的行(判据与包名都空)只点名不执行")
        return []
    if clears and c_edits:
        print(f"\n  清除判据的行执行:清 {len(c_edits)} 个节点的判据+绑定(节点保留;"
              "删除整节点仍走调优表删行,人确认):")
        for cnid, cnm in c_edits:
            print(f"  - {cnid}「{cnm}」")
        _surgery(ctx.product, [(cnid, "", None) for cnid, _nm in c_edits],
                 note="清除判据的行:判据原文列清空+全部绑定列清空(节点保留)")
        _ledger_stamp(ctx.product, [cnid for cnid, _nm in c_edits], "cleared_at")   # 台账留痕:有记录+绑定空=合规清除
        return c_edits
    return []


def _map_print_actions(ctx: _MapCtx, added_report, sadd_report, sol_edits, en_edits, dels) -> None:
    """逐条打印本次动作:✓ 迁移 / + 新增 / ~ 判据同步 / ≈ 解决方案与英文同步 / ✂ 删除 / = 跳过。"""
    nodes = ctx.nodes
    for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, note, _fp, _cond in ctx.oks:
        print(f"  ✓ {nid}「{nodes[nid].get('name')}」 ← {fc.name}:{entry}"
              + ("/单网元" if per_ne else "") + (f"  ⚠ {note}" if note else ""))
    for new_nid, name, pnid, fc_name, entry, note in added_report:
        print(f"  + {new_nid or '?'}「{name}」 新增,挂在 {pnid}「{nodes[pnid].get('name')}」下 ← {fc_name}:{entry}"
              + (f"  ⚠ {note}" if note else ""))
    for new_nid, name, pnid, has_jd in sadd_report:
        print(f"  + {new_nid or '?'}「{name}」 新增({'仅判据,无绑定' if has_jd else '结构节点:平台树有、本地没有'}),"
              f"挂在 {pnid}「{nodes[pnid].get('name')}」下")
    for snid, _jd in ctx.jd_edits:
        print(f"  ~ {snid}「{nodes[snid].get('name')}」 判据已按对应表更新(绑定不动;"
              "脚本若落后于新判据,由交付方补包后走迁移行换血)")
    for snid, _s in sol_edits:
        print(f"  ≈ {snid}「{nodes[snid].get('name')}」 解决方案已按对应表更新")
    _en_label = {"name_en": "英文名", "condition_en": "英文判据", "sol_en": "英文解决方案"}
    for enid, d in en_edits:
        print(f"  ≈ {enid}「{nodes[enid].get('name')}」 英文已按对应表更新"
              f"({'、'.join(_en_label[k] + ('清空' if v == '' else '') for k, v in d.items())})")
    for dnid, dnm in dels:
        print(f"  ✂ {dnid}「{dnm}」 已删除(全量表里已无该节点;调优表已删行,台账留 deleted_at)")
    for nid, label in ctx.skips:
        print(f"  = {nid}「{nodes[nid].get('name')}」 未变化,跳过(此前的人工调优不受影响)")


def _map_print_notes(ctx: _MapCtx, del_guard, clear_guard, too_deep, unmarked_diff) -> None:
    """守护与提示:删除/清除过半的守护、深度超出表层级列数的节点、纯判据行定位失败、未标记行的判据差异。"""
    if del_guard:
        print(f"\n  ⚠ {del_guard}")
    if clear_guard:
        print(f"\n  ⚠ {clear_guard}")
    if too_deep:
        print(f"\n  [info] {len(too_deep)} 个节点深度超出表层级列数(表表达不了),不删:"
              + "、".join(f"{n}「{nm}」" for n, nm in too_deep[:6]))
    if ctx.struct_miss:
        print(f"\n  [info] {len(ctx.struct_miss)} 个纯判据行定位不了(父路径也对不上,不猜):")
        for snames, swhy in ctx.struct_miss[:6]:
            print(f"  - {'/'.join(snames)}:{swhy}")
    if unmarked_diff:
        print(f"\n  [info] {len(unmarked_diff)} 个未标记行的判据与树不同(按标记列的规则未动,"
              "确要改就在表里标记后重跑):"
              + "、".join(f"{n}「{nm}」" for n, nm in unmarked_diff[:6]))


def _map_print_fails(ctx: _MapCtx, clear_miss) -> None:
    """未落地行(对应不上绝不猜,附手工补的命令)、清除判据的行定位失败、合集里没被表引用的子包。"""
    if ctx.fails:
        print(f"\n  未落地 {len(ctx.fails)} 行(对应不上绝不猜):")
        for label, why, cmd in ctx.fails:
            print(f"  ✗ {label}:{why}")
            if cmd:
                print(f"     → 确认后:--package \"{ctx.a.package}\" --product \"{ctx.a.product}\" {cmd}")
    if clear_miss:
        print(f"\n  清除判据的行定位失败 {len(clear_miss)} 个(不猜、不清):")
        for cnames, cwhy in clear_miss:
            print(f"  ✗ {'/'.join(cnames)}:{cwhy}")
    unused = sorted({Path(r).name for r in ctx.roots} - {Path(r).name for r in ctx.used_roots})
    if unused:
        print(f"\n  [info] 合集里有 {len(unused)} 个子包没被对应表引用(未迁移):{unused[:8]}"
              f"{'…' if len(unused) > 8 else ''}")


def _map_reconcile(ctx: _MapCtx, zipless, cleared_report, clear_already, synced_nids, dels, sol_edits) -> None:
    """全量对账:台账里迁移过、这次对应表里却没有配 zip 的节点——上游删了节点?撤了优化包?
    只点名不动手(删节点是结构改动,必须人确认;评估反思/删除指令能力接上后由那边闭环)。"""
    zipless_nids = set()
    for names, _jd, _mk in zipless:
        znid, _w, _d = resolve_node_by_path(ctx.nodes, names)
        if znid:
            zipless_nids.add(znid)
    covered = set(ctx.seen_nid) \
        | {cnid for cnid, _nm in cleared_report} | {cnid for cnid, _nm in clear_already} \
        | synced_nids | {dnid for dnid, _nm in dels} | {snid for snid, _s in sol_edits}
    gone, retracted = [], []
    for nid in sorted(ctx.ledger, key=lambda x: int(x[1:]) if x[1:].isdigit() else 10 ** 9):
        if nid in covered or nid not in ctx.nodes:
            continue
        (retracted if nid in zipless_nids else gone).append(nid)
    if retracted:
        print(f"\n  ⚠ 台账里迁移过、表里节点还在但**没配 zip** 的 {len(retracted)} 个(优化包撤了?):"
              + "、".join(f"{n}「{ctx.nodes[n].get('name')}」" for n in retracted[:6])
              + " ——保留现状,向交付方确认")
    if gone and ctx.mode != "multi":                      # MULTI=部分表,不在表里是常态,不该吓人
        print(f"\n  ⚠ 台账里迁移过、**对应表里已无该节点** 的 {len(gone)} 个(上游可能删了节点):"
              + "、".join(f"{n}「{ctx.nodes[n].get('name')}」" for n in gone[:6])
              + " ——树里仍保留,不自动删;确认后走调优表删行(删行→import 即生效)")


def migrate_map(a, pkg: Path, product: Path, tmp: Path, map_path: Path) -> None:
    """对应表驱动的全自动批量:每行 zip↔路径,路径定位节点(同名靠全路径消歧),zip 匹配子包,
    写入逻辑与 --auto 批量同一套(一轮调优表修改,原子生效);任一行对应不上→进失败清单,退出码 3。
    **全量交付语义**:内容指纹与台账一致的包自动跳过(幂等;人工调优不被冲;--force 强制全重迁);
    台账里有、表里已消失的迁移节点点名待人工确认(不自动删树);
    **清除判据的行**(表有判据列时,判据格与 zip 格都空=界面删了该节点的判据与包)→ 清该节点判据+绑定
    (节点保留、幂等、台账 cleared_at 留痕;--no-clear 只点名不执行)。"""
    entries, zipless, clears, n_lvls, sols, en_rows = load_map_excel(map_path)
    mode = getattr(a, "map_mode", "auto") or "auto"
    _map_preimport(product)
    index = load_index(product)
    nodes = index.get("nodes") or {}
    ctx = _MapCtx(a, product, nodes, _ledger_read(product), build_zip_registry(pkg, tmp), mode)
    print(f"对应表:{len(entries)} 行有 zip 对应"
          + (f",清除判据的行(判据格与 zip 格都空){len(clears)} 个" if clears else "")
          + f";合集里可匹配的子包 {len(ctx.roots)} 个"
          + (f";模式 {mode}(multi=不动结构 / all=结构以表为准)" if mode != "auto" else ""))
    for names, zname, rowno, jd in entries:
        _map_row(ctx, names, zname, rowno, jd)

    synced_nids, unmarked_diff = _map_sync_zipless(ctx, zipless)
    sol_edits = _map_sync_solutions(product, nodes, sols)
    c_edits, clear_already, clear_miss, clear_guard = _map_parse_clears(ctx, clears)
    dels, too_deep, del_guard = _map_delete_candidates(ctx, entries, zipless, clears, n_lvls)
    en_edits, en_adds = _map_sync_english(ctx, en_rows, synced_nids, dels, c_edits)

    n_refreshed = _refresh_standard(product)             # 收表必刷:全 FALSE 的全量表也等于"刷新技能"
    added_report, sadd_report = _map_write(ctx, sol_edits, dels, en_edits, en_adds)
    cleared_report = _map_exec_clears(ctx, clears, c_edits)

    _print_map_summary(len(entries), len(ctx.oks), len(ctx.adds), n_refreshed, bool(ctx.fails), [
        (len(ctx.skips), "跳过 {n} 行(内容与上次迁移一致;--force 强制重迁)"),
        (len(ctx.jd_edits), "判据同步 {n} 个(绑定不动)"),
        (len(sol_edits), "解决方案同步 {n} 个"),
        (len(ctx.sadds), "纯判据新增 {n} 个"),
        (len(dels), "删除节点 {n} 个(全量表模式)"),
        (len(cleared_report), "清除 {n} 个节点(判据+绑定)"),
        (len(en_edits), "英文同步 {n} 个"),
        (len(clear_already), "清除判据的行已是空、跳过 {n} 个(幂等)"),
    ])
    _map_print_actions(ctx, added_report, sadd_report, sol_edits, en_edits, dels)
    _map_print_notes(ctx, del_guard, clear_guard, too_deep, unmarked_diff)
    _map_print_fails(ctx, clear_miss)
    _map_reconcile(ctx, zipless, cleared_report, clear_already, synced_nids, dels, sol_edits)
    _factory_report([(ctx.seen_nid.get(k, str(k)), v["warn_items"], v["repairs"])
                     for k, v in ctx.gates.items()])
    if ctx.oks:
        print("\n  迁移就地生效(产物=打包技能 assets/ 时无需重新打包);台账 → tree/migrations.json。")
    sys.exit(3 if ctx.fails or clear_miss else 0)


# ---------- 批量模式(多节点合集包 + --auto) ----------
def _batch_match_node(index, nodes, method, label, acc: _Acc):
    """精确同名唯一 → nid;同名多个用包内「节点路径」消歧;匹配不上列候选进失败清单,返回 None。"""
    exact = [cnid for cnid, nd in nodes.items()
             if str(nd.get("name") or "").strip() == method["name"].strip()]
    nid = exact[0] if len(exact) == 1 else _path_pick(nodes, method, exact)
    if nid is not None and len(exact) > 1:
        print(f"  [路径消歧] 「{method['name']}」同名 {len(exact)} 个,包内「节点路径」指向 {nid}")
    if nid is None:
        cand = "\n".join(f"         {s:5.3f}  {cnid:>6}  {nm}"
                         for s, cnid, nm in rank_nodes(index, method["name"], method["core"]))
        why = ((f"树上有 {len(exact)} 个同名节点({exact}),精确匹配不唯一"
                "(tester 包补「## 节点路径」可自动消歧)" if exact
                else "无精确同名节点") + ",候选:\n" + cand)
        acc.fails.append((label, why, f'--only "{label}" --node <节点号>'))
    return nid


def _batch_probe(a, product: Path, index, nodes, ledger, sub: Path, acc: _Acc):
    """子包解析 → 脚本挑选 → 入口探测 → 精确同名匹配 → 指纹跳过。
    返回 (label, method, fc, deps, eff_fc, entry, per_ne, kwargs, note, nid, fp);进失败/跳过清单时返回 None。"""
    label = sub.name
    try:
        method = parse_method_md(sub)
        label = method["name"]
        fc, deps = find_scripts(sub, None, node_name=method["name"])
    except SystemExit as e:
        acc.fails.append((label, str(e), None))
        return None
    eff_fc = _effective(product, fc)                     # 修过管道的副本生效(重跑续迁不被原件卡死)
    entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
    if entry is None:
        hint = _stage_no_entry(product, fc, deps)
        acc.fails.append((label, f"探测不到可用入口(顶层函数:{all_defs[:8]}{';' + note if note else ''})——{hint}",
                          f'或人工指定:--only "{label}" --node <节点号> --entry <吃目录参数的函数名>'))
        return None
    nid = _batch_match_node(index, nodes, method, label, acc)
    if nid is None:
        return None
    fp = _fingerprint(sub)
    if not a.force and ledger.get(nid, {}).get("fingerprint") == fp:
        acc.skips.append((nid, label))                   # 全量交付里没变的包:跳过(此后的人工调优不被冲)
        acc.seen_nid.setdefault(nid, label)
        return None
    return label, method, fc, deps, eff_fc, entry, per_ne, kwargs, note, nid, fp


def _batch_admit(product: Path, sub: Path, probe, acc: _Acc) -> None:
    """补库 → 试跑仲裁 → 同节点/同名脚本查重 → 契约检查;都过了才进 oks,否则进失败清单。"""
    label, method, fc, deps, eff_fc, entry, per_ne, kwargs, note, nid, fp = probe
    supplied, unsupplied = _with_supplied(fc, deps)
    if unsupplied:
        acc.fails.append((label, f"包缺共享模块 {unsupplied} 且引擎里也没有副本——让交付方补进包(规范附录8)", None))
        return
    try:
        entry, per_ne, kwargs, sm = arbitrate_entry(
            eff_fc, [_effective(product, p) for p in deps] + [s for _n, s in supplied],
            entry, per_ne, kwargs, explicit=False)
    except SystemExit as e:
        acc.fails.append((label, str(e), f'--only "{label}" --node {nid} --entry <函数名>'))
        return
    if sm:
        note = _join_note(note, sm)
    if nid in acc.seen_nid:
        acc.fails.append((label, f"与子包「{acc.seen_nid[nid]}」都对到 {nid}——同一节点两份交付包,人工定夺谁算数",
                          f'--only "{label}" --node {nid}'))
        return
    prev = acc.seen_fc.get(fc.name)
    if prev is not None and prev[1] != fc.read_bytes():
        acc.fails.append((label, f"脚本文件名「{fc.name}」与子包「{prev[0]}」重名且内容不同"
                                 "——让交付方改名后把该子包单独再迁", None))
        return
    acc.seen_nid[nid], acc.seen_fc[fc.name] = label, (label, fc.read_bytes())
    verdict, ginfo = _contract_gate(product, sub, fc, deps, supplied,
                                    entry=entry, per_ne=per_ne, kwargs=kwargs)
    if verdict == "fail":
        acc.fails.append((label, ginfo, None))
        return
    if ginfo:
        acc.gates[nid] = ginfo
        note = _gate_note(note, ginfo)
    acc.oks.append((sub, method, fc, deps, entry, per_ne, kwargs, nid, note, fp))


def _batch_write(product: Path, oks, gates) -> None:
    """写入阶段:脚本进场 → 一轮调优表修改(原子生效)→ 脚本框架升级 → 台账。"""
    for _sub, _m, fc, deps, *_rest in oks:
        _copy_scripts(product, fc, deps)
    _surgery(product, [(nid, m["condition"], _binding(fc.name, entry, per_ne, kwargs, m["name"]))
                       for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, _fp in oks])
    upgrade_cross_scaffold(product)
    _ledger(product, [(nid, m["name"], fc.name, entry, per_ne, kwargs, fp, _gate_extra(gates.get(nid)))
                      for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, fp in oks])


def _batch_report(a, nodes, subs, acc: _Acc) -> None:
    """批量迁移汇总:落地/跳过计数,逐条 ✓ / = 行,未落地清单(附补充匹配命令),退回清单,生效提示。"""
    print(f"\n批量迁移汇总:落地 {len(acc.oks)}/{len(subs)} 个"
          + (f",跳过 {len(acc.skips)} 个(内容与上次迁移一致;--force 强制重迁)" if acc.skips else ""))
    for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, note, _fp in acc.oks:
        print(f"  ✓ {nid}「{nodes[nid].get('name')}」 ← 「{m['name']}」({fc.name}:{entry}"
              + ("/单网元" if per_ne else "") + ")" + (f"  ⚠ {note}" if note else ""))
        if kwargs:
            print(f"      ⚠ 入口有额外必填参数 {sorted(kwargs)},已按空 dict 传入;"
                  f"不对就 --only \"{m['name']}\" --node {nid} --entry-kwargs 重迁指定")
    for nid, label in acc.skips:
        print(f"  = {nid}「{nodes[nid].get('name')}」 未变化,跳过(此前的人工调优不受影响)")
    if acc.fails:
        print(f"\n  未落地 {len(acc.fails)} 个(匹配不上绝不猜,逐个补充匹配):")
        for label, why, cmd in acc.fails:
            print(f"  ✗ 「{label}」:{why}")
            if cmd:
                print(f"     → 确认后重跑:--package \"{a.package}\" --product \"{a.product}\" {cmd}")
    _factory_report([(acc.seen_nid.get(k, str(k)), v["warn_items"], v["repairs"])
                     for k, v in acc.gates.items()])
    if acc.oks:
        print("\n  迁移就地生效(产物=打包技能 assets/ 时无需重新打包);台账 → tree/migrations.json;"
              "执行时 runner 加 --data raw=<原始目录> 最忠实。")


def migrate_batch(a, pkg: Path, product: Path) -> None:
    """逐子包全自动循环:精确同名唯一的直接迁入(一轮调优表修改,原子生效);匹配不上/探不到入口的
    **绝不猜**——收集起来最后统一列清单,每个附补充匹配的命令(退出码 3=还有没迁入的)。"""
    groups = find_subpackages(pkg)
    subs = sorted(groups, key=lambda p: p.name)
    index = load_index(product)
    nodes = index.get("nodes") or {}
    ledger = _ledger_read(product)
    print(f"合集包:{len(subs)} 个子包,逐个匹配(精确同名唯一才自动迁入,其余列候选)…")
    acc = _Acc()
    for sub in subs:
        probe = _batch_probe(a, product, index, nodes, ledger, sub, acc)
        if probe is not None:
            _batch_admit(product, sub, probe, acc)

    _refresh_standard(product)
    if acc.oks:
        _batch_write(product, acc.oks, acc.gates)
    _batch_report(a, nodes, subs, acc)
    sys.exit(3 if acc.fails else 0)


# ---------- 主流程 ----------
def _build_parser() -> argparse.ArgumentParser:
    """命令行参数定义(帮助文案原样)。"""
    ap = argparse.ArgumentParser(description="tester 交付包 → 场景产物迁移(判据替换+S 绑定换血)")
    ap.add_argument("--package", help="交付包目录或 zip(--refresh-only 时可省)")
    ap.add_argument("--refresh-only", action="store_true",
                    help="不迁移任何包,只对产物/打包技能做引擎检查+全量翻新(custom_lib/内核/导入导出/"
                         "runner/check_data/zip2csv/SKILL 正文,与本机 skills 目录下的源逐文件比对、不同即刷新)——"
                         "手头没有交付包、只想把旧技能刷到当前代时用")
    ap.add_argument("--product", required=True, help="场景产物目录(或打包技能的 assets/ 目录)")
    ap.add_argument("--node", help="目标节点号(如 n17);不给=只做匹配、列候选")
    ap.add_argument("--auto", action="store_true",
                    help="节点名与树上某节点**一字不差且唯一**时直接迁入(匹配第一级:代码精确匹配);"
                         "无精确命中则列候选后以退出码 3 结束(交模型/人对齐)。"
                         "多节点合集包 + --auto = 批量循环:能精确匹配的全部迁入,其余列候选")
    ap.add_argument("--only", help="多节点合集包里只处理指定「包节点名」的那个子包"
                                   "(批量没匹配上的,逐个用它配合 --node/--entry 补充匹配)")
    ap.add_argument("--map", help="对应表 xlsx/csv(层级列 L1..Ln + zip 列,无 node_id):"
                                  "zip 名↔树路径全自动批量匹配,同名节点靠全路径消歧")
    ap.add_argument("--map-mode", choices=["auto", "multi", "all"], default="auto",
                    help="对应表模式(对应平台 optimizeType):multi=部分表,不动树结构(迁移/清除/"
                         "判据同步照做,不新增节点);all=全量表,树结构以表为准(增节点含纯判据"
                         "结构节点;表里踪影全无的节点自动删——调优表 .bak 可回退、台账 deleted_at "
                         "留痕);auto=旧默认(能加不删,删除只点名)")
    ap.add_argument("--no-clear", action="store_true",
                    help="对应表模式:不执行清除判据的行(判据格与 zip 格都空=界面删了判据→清该节点判据+绑定),只点名")
    ap.add_argument("--force", action="store_true",
                    help="内容指纹与台账一致的包默认跳过(全量交付幂等,人工调优不被冲)——"
                         "加本参数强制全部重迁")
    ap.add_argument("--script", help="包里有多个 fault_check_*.py 时指定文件名")
    ap.add_argument("--rebuild-tuning", action="store_true",
                    help="存量调优表备份为 .bak 后从产物重建(预导入报「多个顶层节点」这类"
                         "旧版导出器结构病时用;有未导入的手工修改可从 .bak 找回)")
    ap.add_argument("--entry", help="入口函数名(默认自动探测)")
    ap.add_argument("--per-ne", action="store_true", help="入口吃单网元目录(自动探测不准时手工指定)")
    ap.add_argument("--entry-kwargs", help='入口额外关键字参数 JSON(如 {"judgment_conditions": {}})')
    return ap


def _resolve_product(a) -> Path:
    """--product 定位:给的是打包技能根目录就自动进 assets/;不像场景产物目录(缺 tree/ 或 scripts/)→ 停止并报错。"""
    product = Path(a.product).expanduser().resolve()
    if not (product / "tree").is_dir() or not (product / "scripts").is_dir():
        if (product / "assets" / "tree").is_dir() and (product / "assets" / "scripts").is_dir():
            product = product / "assets"                 # 给的是打包技能根目录:产物在 assets/,自动进
            print(f"[info] --product 是打包技能目录,已自动定位到产物:{product}")
        else:
            raise SystemExit(f"--product 不像场景产物目录(缺 tree/ 或 scripts/):{product}")
    return product


def _refresh_only(product: Path) -> None:
    """无包翻新:只走工具链公共文件同步 + cross 脚本框架升级,逐项汇报动了什么。"""
    touched = refresh_engine(product)
    upgrade_cross_scaffold(product)
    if touched:
        print(f"\n翻新完成。本次动了 {len(touched)} 处(旧文件均留 .bak,汇报时把下面清单原样列出):")
        for t in touched:
            print(f"  - {t}")
    else:
        print("\n翻新完成:所有工具链公共文件与本机 skills 目录下的源一致,没有文件需要刷新。")
    print("场景件(树/绑定/cross_analyzer/各指标脚本/节点脚本/knowledge)一概未动;技能名与描述保持原样。")


def _sub_names(groups) -> list:
    """合集包里各子包的节点名(按子目录名排序;判断方法解析失败的点名)。"""
    out = []
    for sub in sorted(groups, key=lambda p: p.name):
        try:
            out.append(parse_method_md(sub)["name"])
        except SystemExit:
            out.append(f"{sub.name}(判断方法解析失败)")
    return out


def _dispatch_map(a, pkg: Path, product: Path, td: str) -> bool:
    """对应表模式分流:--map 显式,或表就打在交付包里(自动发现,不用记参数)→ 进对应表批量并返回 True
    (migrate_map 自带退出码);与手工参数同用 → 停止并报错;没有表 → False。"""
    manual = a.node or a.only or a.script or a.entry or a.per_ne or a.entry_kwargs
    map_path = Path(a.map).expanduser().resolve() if a.map else None
    if map_path is None and not manual:
        map_path = find_map_table(pkg)               # 表和 zip 打在一个包里给:自动发现,不用记参数
        if map_path is not None:
            print(f"[info] 交付包里发现对应表「{map_path.name}」→ 按对应表批量匹配"
                  "(不想按表:把表移出包外,或用 --only 单挑)")
    if map_path is None:                             # 对应表模式:先于子包探测(目录形态的合集顶层没有 md)
        return False
    if manual:
        raise SystemExit("对应表是全自动批量对应:不能与 --node/--only/--script/--entry/"
                         "--per-ne/--entry-kwargs 同用;个别包要手工处理,去掉 --map 用 --only 单挑。")
    migrate_map(a, pkg, product, Path(td), map_path)
    return True


def _pick_subpackage(a, pkg: Path, product: Path, groups):
    """合集包分流:--only 选定子包后走单节点流程(全部参数可用);多节点合集包不带 --only 时,
    --auto 走批量(自带退出码,返回 None),否则列出两种走法以退出码 3 结束;单包原样返回。"""
    if a.only:                                       # 合集包单挑:选定子包后走单节点流程(全部参数可用)
        hit = next((sub for sub in groups
                    if _safe_name(sub) == a.only.strip()), None)
        if hit is None:
            raise SystemExit(f"--only「{a.only}」在包里找不到;包里的节点:{_sub_names(groups)}")
        return hit
    if len(groups) > 1:                              # 多节点合集包
        if a.node or a.script or a.entry or a.per_ne or a.entry_kwargs:
            raise SystemExit("这是多节点合集包:--node/--script/--entry/--per-ne/--entry-kwargs "
                             "不知道给哪个子包用——加 --only \"<包节点名>\" 选定子包再带这些参数。")
        if not a.auto:
            print(f"多节点合集包({len(groups)} 个子包),两种走法:")
            print("  批量全自动:原命令加 --auto(精确同名的全部迁入,其余列候选)")
            print("  单挑一个:加 --only \"<包节点名>\"(可配 --node/--entry 等)")
            print("包里的节点:")
            for nm in _sub_names(groups):
                print(f"  - {nm}")
            sys.exit(3)
        migrate_batch(a, pkg, product)
        return None                                  # migrate_batch 自带退出码,到不了这里
    return pkg


def _single_probe(a, pkg: Path, product: Path):
    """单包解析:判断方法 + 脚本 + 入口探测(--entry / --entry-kwargs 人工指定覆盖,0 参入口同样拦下)。
    返回 (method, fc, deps, eff_fc, entry, per_ne, kwargs, all_defs, note)。"""
    method = parse_method_md(pkg)
    fc, deps = find_scripts(pkg, a.script, node_name=method["name"])
    eff_fc = _effective(product, fc)                 # 修过管道的副本生效(重跑续迁不被原件卡死)
    entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
    if a.entry:
        entry, per_ne = a.entry, a.per_ne
        if argc.get(entry, 1) < 1:                   # 手工指定也过同一道检查:0 参入口运行期必出错
            raise SystemExit(f"--entry 指定的「{entry}」不接收数据目录参数(脚本从 sys.argv 自取路径)"
                             "——适配函数必传目录参数,选它运行期必报 TypeError。"
                             "让交付方按规范改成 diagnose(data_dir),或选一个吃目录参数的函数。")
    if a.entry_kwargs:
        kwargs = json.loads(a.entry_kwargs)
    return method, fc, deps, eff_fc, entry, per_ne, kwargs, all_defs, note


def _single_pick_node(a, index, nodes, method):
    """匹配三级:① 代码精确匹配(名字一字不差且唯一)→ --auto 直接迁入;
             ② 无精确命中 → 列候选,模型按语义对齐后带 --node 重跑;
             ③ 模型也拿不准 → 问人。②③发生在技能层,本脚本只负责①和出候选。
    返回 nid;只列候选时返回 None(--auto 下以退出码 3 结束);指定的节点不在树里 → 停止并报错。"""
    exact = [nid for nid, nd in nodes.items()
             if str(nd.get("name") or "").strip() == method["name"].strip()]
    nid = a.node.strip() if a.node else None
    if nid is None and a.auto and len(exact) == 1:
        nid = exact[0]
        print(f"精确命中:{nid}「{nodes[nid].get('name')}」(节点名与交付包一字不差)——按 --auto 直接迁入")
    elif nid is None and a.auto and len(exact) > 1:
        nid = _path_pick(nodes, method, exact)
        if nid is not None:
            print(f"[路径消歧] 同名 {len(exact)} 个,包内「节点路径」指向"
                  f" {nid}「{nodes[nid].get('name')}」——按 --auto 迁入")
    if nid is None:                                  # 匹配模式:列候选
        if len(exact) > 1:
            print(f"[warn] 树上有 {len(exact)} 个同名节点({exact})——精确匹配不唯一,"
                  "必须人工指定(tester 包补「## 节点路径」可自动消歧)")
        print("\n候选节点(相似度=名字 0.6 + 判据正文 0.4;=为一字不差):")
        for score, cnid, name in rank_nodes(index, method["name"], method["core"]):
            mark = "=" if cnid in exact else " "
            print(f"  {mark}{score:5.3f}  {cnid:>6}  {name}")
        print("\n确认后执行:加 --node <节点号> 重跑本命令执行迁移。")
        if a.auto:
            sys.exit(3)                              # 给上层一个"没能自动匹配"的机器可读信号
        return None
    if nid not in nodes:
        raise SystemExit(f"节点 {nid} 不在故障树里——先跑匹配模式看候选")
    return nid


def _single_arbitrate(a, product: Path, fc: Path, deps, eff_fc: Path, entry, per_ne, kwargs, all_defs, note):
    """执行前把关:入口须探到(否则停止并报错)→ 额外必填参数提示 → 补库 → 试跑仲裁。
    返回 (entry, per_ne, kwargs, supplied)。"""
    if entry is None:
        raise SystemExit(f"探测不到可用入口函数(脚本顶层函数:{all_defs[:8]}{';' + note if note else ''})"
                         "——入口须接收数据目录参数,如 diagnose(data_dir);让交付方按规范改脚本,"
                         "或用 --entry 指定一个吃目录参数的函数")
    if kwargs and not a.entry_kwargs:
        print(f"[warn] 入口 {entry} 有额外必填参数 {sorted(kwargs)},已按空 dict 传入;"
              "不对就用 --entry-kwargs 显式指定")
    supplied, unsupplied = _with_supplied(fc, deps)
    if unsupplied:
        raise SystemExit(f"包缺共享模块 {unsupplied} 且引擎里也没有副本——让交付方补进包(规范附录8)")
    entry, per_ne, kwargs, _sn = arbitrate_entry(
        eff_fc, [_effective(product, p) for p in deps] + [s for _n, s in supplied],
        entry, per_ne, kwargs, explicit=bool(a.entry))
    return entry, per_ne, kwargs, supplied


def _single_migrate(a, pkg: Path, product: Path, nodes, nid: str, method, fc: Path, deps,
                    entry, per_ne, kwargs, supplied) -> None:
    """执行模式:指纹跳过 → 契约检查 → 0.引擎检查+补全 → 1.脚本进场 → 2.调优表修改 → 3.脚本框架检查+台账 → 汇报。"""
    fp = _fingerprint(pkg)
    if not a.force and _ledger_read(product).get(nid, {}).get("fingerprint") == fp:
        print(f"\n跳过:{nid}「{nodes[nid].get('name')}」的交付包内容与上次迁移完全一致"
              "(全量交付幂等;此后的人工调优不受影响)。确要重迁加 --force。")
        return

    verdict, ginfo = _contract_gate(product, pkg, fc, deps, supplied,
                                    entry=entry, per_ne=per_ne, kwargs=kwargs,
                                    explicit=bool(a.entry))
    if verdict == "fail":
        raise SystemExit(ginfo)
    if ginfo and ginfo["repairs"]:
        print(f"  [管道已修] {','.join(ginfo['repairs'])}(修复副本生效,基准 .orig 保留)")
    if ginfo and ginfo["warns"]:
        print(f"  [格式WARN] {','.join(ginfo['warns'])}(适配函数可处理;详见结尾的退回清单)")

    # 0. 引擎检查+补全(旧产物的内核/函数库/导入校验不认 S 类,先刷新再改)
    refresh_engine(product)

    # 1. 脚本进场
    _copy_scripts(product, fc, deps)
    print(f"  已拷入 scripts/custom/:{fc.name}" + (f" + {len(deps)} 个共享模块" if deps else ""))

    # 2. 调优表修改(预导入→导出→改行→导入→回导,细节见 _surgery)
    _surgery(product, [(nid, method["condition"],
                        _binding(fc.name, entry, per_ne, kwargs, method["name"]))])

    # 3. 脚本框架检查 + 台账
    upgrade_cross_scaffold(product)
    _ledger(product, [(nid, method["name"], fc.name, entry, per_ne, kwargs, fp,
                       _gate_extra(ginfo))])

    print(f"\n迁移完成:{nid}「{nodes[nid].get('name')}」")
    print(f"  判据 ← 判断方法.md 全文(含故障描述/判断条件/数据来源表等);绑定 ← S(fault_check → custom/{fc.name}:{entry})")
    print(f"  台账 → tree/migrations.json;调优表已同步,后续人工调优照常。")
    print("  执行提示:runner 加 --data raw=<zip2csv 的 extracted 目录> 可让脚本直读原始 txt"
          "(不加也能跑,自动从三表重建)。")
    print("  打包说明:迁移是**就地生效**——产物就是打包技能的 assets/ 时,技能已更新完毕,"
          "**无需重新打包**;只有在独立产物目录上迁移、之后要发布成技能时才跑 package_scenario.py。")
    if ginfo:
        _factory_report([(method["name"], ginfo["warn_items"], ginfo["repairs"])])


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = _build_parser().parse_args()
    global REBUILD_TUNING
    REBUILD_TUNING = bool(getattr(a, "rebuild_tuning", False))
    product = _resolve_product(a)

    if a.refresh_only:                                   # 无包翻新:只走工具链公共文件同步 + cross 脚本框架升级
        _refresh_only(product)
        return

    if not a.package:
        raise SystemExit("--package 必填(交付包目录或 zip);只想翻新旧技能不迁包,用 --refresh-only。")

    with tempfile.TemporaryDirectory(prefix="migrate_") as td:
        pkg = unpack(Path(a.package).expanduser().resolve(), Path(td))
        # 处理方式按输入动态定:①有对应表(--map 显式,或表就打在交付包里→自动发现)→ 对应表批量;
        # ②无表+合集包+--auto → 逐子包精确匹配批量;③无表+单包 → 单节点流程。全在下面顺序分流。
        if _dispatch_map(a, pkg, product, td):
            return                                       # migrate_map 自带退出码,到不了这里
        groups = find_subpackages(pkg)
        if not groups:
            raise SystemExit(f"交付包里找不到 判断方法*.md(排除规范文件):{pkg}")
        pkg = _pick_subpackage(a, pkg, product, groups)
        if pkg is None:
            return                                       # migrate_batch 自带退出码,到不了这里

        method, fc, deps, eff_fc, entry, per_ne, kwargs, all_defs, note = _single_probe(a, pkg, product)
        index = load_index(product)
        print(f"交付包:节点名称「{method['name']}」 脚本 {fc.name} 入口 {entry or '?'}"
              f"{'(单网元模式)' if per_ne else ''} 共享模块 {[d.name for d in deps] or '无'}"
              + (f"\n  ⚠ {note}" if note else ""))
        nodes = index.get("nodes") or {}
        nid = _single_pick_node(a, index, nodes, method)
        if nid is None:
            return
        entry, per_ne, kwargs, supplied = _single_arbitrate(a, product, fc, deps, eff_fc,
                                                            entry, per_ne, kwargs, all_defs, note)
        _single_migrate(a, pkg, product, nodes, nid, method, fc, deps, entry, per_ne, kwargs, supplied)


if __name__ == "__main__":
    main()
