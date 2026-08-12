#!/usr/bin/env python3
"""datacom-fault-tree-tester 交付包 → 场景产物:判据替换 + 绑定换血,一条龙机械迁移。

链路(用户拍板):a. 把交付包里人确认过的「判断方法」替换到故障树对应节点的判据上;
b. 把该节点旧绑定去掉,换成一条 S 绑定(func=fault_check 通用转接头),直接跑交付的脚本。

三种模式:
  匹配模式(不带 --node):解析交付包,和产物故障树逐节点算相似度,列出候选——
      python migrate_fault_check.py --package <交付包目录|zip> --product <场景产物目录>
  执行模式(带 --node):人确认节点号后落地迁移——
      python migrate_fault_check.py --package <…> --product <…> --node n17
      可选:--entry <入口函数名> --per-ne --entry-kwargs '<JSON>'(自动探测不准时手工指定)
  批量模式(多节点合集包 + --auto):逐子包循环,精确同名唯一的全部自动落地(一轮手术原子落地),
      对不上号的列候选决不赌;之后逐个 --only "<包节点名>" --node nX 补对号——
      python migrate_fault_check.py --package <合集包.zip> --product <…> --auto
  对应表模式(--map <excel>):同事提供的对应表(调优表同款层级列 L1..Ln,**无 node_id**,
      新增 zip 列=交付 zip 名)驱动全自动批量——按「名字路径」定位节点(同名节点靠全路径消歧,
      不再要人指定),按 zip 列把合集里的包对号入座——
      python migrate_fault_check.py --package <合集目录|zip> --product <…> --map <对应表.xlsx>

落地动作(全走产物自带的调优表往返,复用其全部校验):
  1. 交付包脚本(fault_check_*.py + search_*.py 共享模块)拷进 产物/scripts/custom/;
  2. export_tree_excel → 改该节点一行:判据落「判据:原文」列(标签列清空)、单源绑定列清空、
     「绑定:cross」列写 S 绑定 → import_tree_excel 机械编译回产物 → 再 export 让调优表同步;
  3. 产物 cross_analyzer.py 骨架过旧(不支持 S 全量喂源/raw 通道)时,借 cs-scot-generate 的
     骨架手术就地升级(绑定原样保留);借不到就响亮提醒;
  4. 迁移台账记进 产物/tree/migrations.json(哪个节点、来自哪个包、入口是什么)。

设计红线:判断逻辑照单全收(对错归交付脚本,不改写、不"顺手优化");调优表已有**未导入**的
人工修改时硬停(绝不覆盖别人手里的活)。
"""
from __future__ import annotations

import argparse
import ast
import datetime
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAW_TAG = "判据:原文"


# ---------- 交付包解析 ----------
def _unzip(zp: Path, dst: Path) -> None:
    try:
        zf = zipfile.ZipFile(zp, metadata_encoding="gbk")        # Windows 打的包常见 GBK 文件名
    except TypeError:                                            # 老 Python 无该参数
        zf = zipfile.ZipFile(zp)
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
    返回 {子包根: [md 路径…]};单节点包 → {pkg: [md]}。顶层与子目录同时有 md 的奇形包 → 硬停
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


def parse_method_md(pkg: Path):
    """判断方法.md → {name, condition(=全文逐字), core(=判断条件段,只用于相似度匹配), file}。
    判据替换用**全文**:故障描述/判断条件/命中条件/输出/数据来源表/注意事项一个不丢——
    树上的判据从此就是整份规范文档(数据来源表是核对采集覆盖的依据,绝不能丢);
    对号匹配仍用「判断条件」段,免得模板固定段落稀释相似度。"""
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
    text = cands[0].read_text(encoding="utf-8", errors="replace")
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
    name = (secs.get("节点名称") or "").splitlines()[0].strip() if secs.get("节点名称") else ""
    if not name:
        m = re.search(r"^#\s*判断方法[::]\s*(\S.*?)\s*$", text, re.M)
        name = m.group(1) if m else cands[0].stem.replace("判断方法_", "")
    core = secs.get("判断条件") or ""
    if not core:
        print("[warn] 判断方法.md 里没解析到「## 判断条件」——格式不合规范,对号匹配退化为全文比对,建议核对")
    return {"name": name, "condition": text.strip(), "core": (core or text).strip(), "file": cands[0]}


def find_scripts(pkg: Path, script_arg: str | None):
    """交付包里的 fault_check 脚本 + 共享模块(search_*.py)。verify_*.py 调试遗留不收。"""
    fcs = sorted(set(pkg.rglob("fault_check_*.py")))
    if script_arg:
        hit = [p for p in fcs if p.name == script_arg] or [pkg / script_arg]
        if not hit[0].exists():
            raise SystemExit(f"--script 指定的脚本在包里找不到:{script_arg}")
        fc = hit[0]
    elif len(fcs) == 1:
        fc = fcs[0]
    elif not fcs:
        raise SystemExit("交付包里没有 fault_check_*.py(无脚本的包只有判断方法文档,走人工改调优表即可)")
    else:
        raise SystemExit(f"交付包里有多个脚本,用 --script 指定一个:{[p.name for p in fcs]}")
    deps = sorted({p for p in pkg.rglob("search_*.py")}, key=lambda p: p.name)
    return fc, deps


# 入口候选(优先级序)。fault_check 是 tester 规范的约定入口名(与文件名前缀同名),排最前——
# 实测漏了它:脚本入口叫 fault_check 时名单撞上内部帮手 check_ne(吃 Path、返回 tuple),运行期必炸。
_ENTRY_CANDIDATES = ("fault_check", "diagnose", "main", "check_ne", "diagnose_ne", "check")


def _required_kwargs(fn) -> dict:
    """首参之外的无默认值参数 → entry_kwargs 需求(常见如 judgment_conditions={};其余待人核)。"""
    pos = [a.arg for a in fn.args.args]
    required_extra = pos[1: len(pos) - len(fn.args.defaults)] if len(pos) > 1 else []
    return {p: {} for p in required_extra}


def detect_entry(script: Path):
    """静态探测入口函数(候选见 _ENTRY_CANDIDATES;选中后还要过 arbitrate_entry 的空目录烟测)。
    入口契约=至少接收 1 个位置参数(数据目录);0 参函数(从 sys.argv 自取路径的老脚本)一律跳过
    并记入 note——运行期转接头必传目录参数,选了 0 参入口就是必炸的 TypeError,在迁移时拦下。
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
    note = (f"({'/'.join(skipped)}() 不接收数据目录参数——脚本从 sys.argv 自取路径,不符合入口契约,已跳过)"
            if skipped else "")
    if entry is None:
        return None, False, {}, sorted(defs), argc, note
    return entry, entry in ("check_ne", "diagnose_ne"), _required_kwargs(defs[entry]), sorted(defs), argc, note


class _StrPathLite(str):
    """烟测入参:与引擎 custom_lib._StrPath 同款的 str/Path 双兼容路径(str 子类,支持 / 拼接与
    Path 方法)——烟测传参必须和运行期转接头同型,否则烟测放行的脚本运行期照炸(或反之)。"""
    def __truediv__(self, other):
        import os as _os
        return _StrPathLite(_os.path.join(str(self), str(other)))

    def __getattr__(self, name):
        return getattr(Path(str(self)), name)


def _shape_ok(r):
    """返回值是否是转接头认识的形状(与 custom_lib._fc_normalize 同口径,只验形不验语义)。"""
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
    """空目录烟测:以库方式(argv 隔离/stdout 收走/异常拦截,与运行期转接头同款护现场)调一次
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
    """入口烟测仲裁:选中的入口先真调一次;不合契约时——auto 模式按候选序换下一个(谁返回契约
    形状谁当入口,机械裁决不猜名字),全不行硬停并逐个报原因;人工 --entry 指定的只警告不换(人>名单)。
    返回 (entry, per_ne, kwargs, 仲裁说明)。"""
    with tempfile.TemporaryDirectory(prefix="stage_") as sd:
        stage = Path(sd)
        for p in [fc] + list(deps):
            shutil.copy2(p, stage / p.name)              # 依赖凑齐再测,和落地后 custom/ 同布局
        ok, why = _smoke_call(stage, fc.name, entry, kwargs)
        if ok:
            return entry, per_ne, kwargs, ""
        if explicit:
            print(f"  ⚠ 烟测:--entry 指定的「{entry}」空目录调用不合契约({why})——按人工指定继续;"
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
                note = f"烟测仲裁:「{entry}」不合契约({why}),换用「{c}」(空目录调用返回契约形状)"
                print(f"  {note}")
                return c, c in ("check_ne", "diagnose_ne"), k2, note
            tried.append((c, why2))
        raise SystemExit("烟测:所有候选入口空目录调用都不合契约:\n  "
                         + "\n  ".join(f"{n}(): {w}" for n, w in tried)
                         + "\n——契约要求:入口吃数据根目录,返回 {matched,evidence,details}"
                           "(空数据=undetermined)。确认真入口后用 --entry 指定强推(只警告不拦),"
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


# ---------- 调优表手术 ----------
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
    ws.cell(row=row, column=raw_col).value = condition   # 判据整段进原文列(逐字保真)
    for c in tag_cols:
        ws.cell(row=row, column=c).value = None          # 标签列清空(原文/标签二选一)
    for src, c in bind_cols.items():
        ws.cell(row=row, column=c).value = (json.dumps(binding, ensure_ascii=False)
                                            if src == "cross" else None)
    wb.save(path)
    return path


def edit_tuning_add(product: Path, parent_nid: str, parent_chain, name: str,
                    condition: str, binding: dict):
    """调优表**新增行**(增节点):node_id 留空(import 自动分配 nN,历史 id 不复用),
    祖先各层名字照抄、自己的名字落在父层+1 的层级列,判据进原文列、绑定只写 cross(S)。
    插在父节点子树的最后一行之后——保持 DFS 连续,import 按"上级和上文对得上"重建父链才稳。"""
    path, wb, ws, header, id_col = _open_tuning(product)
    lvl_re = re.compile(r"^L(\d+)(?:\(Chinese\))?$")
    lvl_cols = {}
    for i, h in enumerate(header):
        m = lvl_re.match(h.replace("：", ":").strip())
        if m:
            lvl_cols[int(m.group(1))] = i + 1
    child_depth = len(parent_chain) + 1                  # 相对深度:根=L1(与 export 同口径)
    for d in range(1, child_depth + 1):
        if d not in lvl_cols:                            # 树长深了一层:补层级列(导入按表头识别)
            lvl_cols[d] = _ensure_col(ws, header, f"L{d}(Chinese)")
    raw_col = _ensure_col(ws, header, RAW_TAG)
    cross_col = _ensure_col(ws, header, "绑定:cross")
    prow = next((r for r in range(2, ws.max_row + 1)
                 if str(ws.cell(row=r, column=id_col).value or "").strip() == parent_nid), None)
    if prow is None:
        raise SystemExit(f"调优表里找不到父节点 {parent_nid} 的行——核对产物")
    end = prow
    for r in range(prow + 1, ws.max_row + 1):            # 父节点子树最后一行(比父深的连续行)
        depth_r = max((l for l, c in lvl_cols.items()
                       if str(ws.cell(row=r, column=c).value or "").strip()), default=0)
        if depth_r <= len(parent_chain):
            break
        end = r
    ws.insert_rows(end + 1)
    new = end + 1
    for d, nm in enumerate(parent_chain, start=1):
        ws.cell(row=new, column=lvl_cols[d]).value = nm  # 祖先各层逐格照抄(导入按它挂父链)
    ws.cell(row=new, column=lvl_cols[child_depth]).value = name
    ws.cell(row=new, column=raw_col).value = condition
    ws.cell(row=new, column=cross_col).value = json.dumps(binding, ensure_ascii=False)
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
        raise SystemExit(f"{desc} 失败(exit {r.returncode})——上面是它的报错,产物未落地,修正后重跑迁移")
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]
    for l in tail:
        print(f"    {l}")


# 产物里属于"引擎"的自包含副本(assemble 拷进去的,工具链所有,人不该手改)。
# 补全按**能力标记**判:目标文件里这些标记都在 → 一律不碰(它可能比本技能还新,
# 字节比对会把"人家更新"误当"过时"而降级);缺任一 → 用引擎源补。
# 引擎源两级:本机同门 cs-scot-generate / cs-scot-execute(更新鲜)→ 本技能内置 engine/
# (自包含兜底;维护:引擎侧改动后跑 scripts/sync_engine.py 对齐)。
ENGINE_CAPS = {
    "condition_kernels.py": ("def k_script", "def alias_sources", "【位置 ",
                             "except SystemExit",
                             "xlsx_reader"),            # 末项=读 xlsx 零依赖(走同目录 xlsx_reader)
    "xlsx_reader.py": ("def read_rows",),                # 零依赖 xlsx 读取器(旧产物没有此文件→直接补)
    # 注意:引擎文件行为语义有变时要**升级这里的标记**(加一个只在新版存在的串),
    # 否则旧产物副本因"标记齐全"永远不被刷新——results 信封识别就是靠新增标记下发的。
    "custom_lib.py": ('@register("fault_check")', 'result.get("results")', "argv_bak",
                      "_fc_tristate", "matched_items", "_StrPath",
                      "raw_note", "_fc_no_judgment"),   # 末两项=raw 下落点破 + 单网元风格脚本逐网元重调
    "import_tree_excel.py": ('{"P", "S"}', "xlsx_reader"),   # S/P 放行 + cross 列 + 读表零依赖同代
    "export_tree_excel.py": ('!= "cross"', "os.utime"),  # cross 列/相对深度 + 导出后表 mtime 对齐 index(消假警报)
}
RUNNER_CAP = "execution_status"                          # 逐节点执行状态落盘(评估反思归因信号);
                                                         # 兼含前代:raw-only 放行 / diag↔cmd 互认


def _engine_src(name: str, caps, sibling: Path):
    """引擎文件取源:同门技能优先,内置 engine/ 兜底;两处都必须带齐能力标记才可信。"""
    for d in (sibling, Path(__file__).resolve().parents[1] / "engine"):
        p = d / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            if all(c in text for c in caps):
                return p
    return None


def refresh_engine(product: Path) -> None:
    """产物引擎体检+补全(只补缺,不追平):逐文件查能力标记,齐的不碰,缺的从引擎源刷新
    (.bak 备份;custom_lib 里手工注册的函数被顶掉会点名提醒从 .bak 拿回)。
    产物在打包技能里(assets/ 形态)时,顺带体检技能根下的 runner.py。
    某文件缺能力又找不到可信引擎源 → 硬停(迁过去也是全线'未知类别')。"""
    sdir = product / "scripts"
    gen = Path(__file__).resolve().parents[2] / "cs-scot-generate" / "scripts"
    changed, extra_regs = [], set()
    for name, caps in ENGINE_CAPS.items():
        dst = sdir / name
        old_text = dst.read_text(encoding="utf-8", errors="replace") if dst.exists() else ""
        if old_text and all(c in old_text for c in caps):
            continue                                     # 能力齐全:不碰(可能比本技能还新)
        src = _engine_src(name, caps, gen)
        if src is None:
            raise SystemExit(f"产物 scripts/{name} 缺 S 迁移所需能力,且本机同门技能与内置 engine/ "
                             "都拿不出可信版本——迁移技能安装不完整(engine/ 目录丢了?),重装后再迁。")
        if name == "custom_lib.py" and old_text:         # 手工 @register 的函数会被顶掉:点名,别静默丢
            extra_regs = set(re.findall(r'@register\("([^"]+)"\)', old_text)) \
                - set(re.findall(r'@register\("([^"]+)"\)', src.read_text(encoding="utf-8")))
        if dst.exists():
            shutil.copy2(dst, dst.with_suffix(".py.bak"))
        shutil.copy2(src, dst)
        changed.append(name)
    if changed:
        print(f"  产物引擎补全:{', '.join(changed)} 缺 S 能力已刷新(旧副本各备份 .py.bak)"
              "——旧版不认识 S 类/fault_check,不刷新迁移无效")
    if extra_regs:
        print(f"  ⚠ 旧 custom_lib.py 里有手工注册的函数 {sorted(extra_regs)} 被引擎版顶掉——"
              "从 custom_lib.py.bak 把它们拷回文件末尾,否则对应 S 绑定会报「未注册」")
    # 打包技能形态:assets/ 的上一级是技能根(有 SKILL.md + scripts/runner.py)→ runner 一并体检
    skill_root = product.parent
    run_dst = skill_root / "scripts" / "runner.py"
    if run_dst.exists() and (skill_root / "SKILL.md").exists():
        if RUNNER_CAP not in run_dst.read_text(encoding="utf-8", errors="replace"):
            run_src = _engine_src("runner.py", (RUNNER_CAP,),
                                  Path(__file__).resolve().parents[2] / "cs-scot-execute" / "scripts")
            if run_src is None:
                print("  [warn] 打包技能的 runner.py 是旧版且无处取新版——不影响 S 判定,"
                      "但 --data 里 diag/cmd 两代源名不互认,喂数据时按 plan 要的名字写")
            else:
                shutil.copy2(run_dst, run_dst.with_suffix(".py.bak"))
                shutil.copy2(run_src, run_dst)
                print("  打包技能的 runner.py 已刷新(旧版不认 raw 通道/双代源名,备份 .py.bak)")
        cd_dst = skill_root / "scripts" / "check_data.py"
        if not cd_dst.exists():                          # 旧打包技能没有数据体检:一并补挂
            cd_src = _engine_src("check_data.py", ("def main",),
                                 Path(__file__).resolve().parents[2] / "cs-scot-execute" / "scripts")
            if cd_src is not None:
                shutil.copy2(cd_src, cd_dst)
                print("  打包技能补挂 scripts/check_data.py(数据入口体检:诊断前先验目录结构/点名清单表)")
        z2c_dst = product / "zip2csv"                    # 旧打包技能 assets/ 里没有/是旧版 zip2csv:补挂/刷新
        z2c_src = Path(__file__).resolve().parents[2] / "zip2csv"
        z2c_ok = (z2c_src / "scripts" / "zip2csv.py").exists()
        if not z2c_dst.is_dir():                         # (裸 txt 数据入口指引按它指路,没有它模型只能干瞪眼)
            if z2c_ok:
                shutil.copytree(z2c_src, z2c_dst,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                print("  打包技能补挂 assets/zip2csv(裸 txt→三表转换器)")
            else:
                print("  [warn] 打包技能缺 assets/zip2csv,且本机没有同门 zip2csv 技能可拷——"
                      "裸 txt 数据现场转不了三表,原有单源绑定会整体数据不足")
        else:
            main_py = z2c_dst / "scripts" / "zip2csv.py"
            cur = main_py.read_text(encoding="utf-8", errors="replace") if main_py.exists() else ""
            if "list_data_files" not in cur:             # 能力标记:旧版只认 .txt(log.log 整目录跳过)、
                if z2c_ok:                               # 只吃 zip、GBK 乱码——按代刷新脚本,SKILL.md 一并带
                    (z2c_dst / "scripts").mkdir(parents=True, exist_ok=True)
                    if main_py.exists():
                        shutil.copy2(main_py, main_py.with_suffix(".py.bak"))
                    for p in sorted((z2c_src / "scripts").glob("*.py")):
                        shutil.copy2(p, z2c_dst / "scripts" / p.name)
                    if (z2c_src / "SKILL.md").exists():
                        shutil.copy2(z2c_src / "SKILL.md", z2c_dst / "SKILL.md")
                    print("  打包技能 assets/zip2csv 已刷新(旧版只认 .txt/只吃 zip;主脚本备份 .py.bak)")
                else:
                    print("  [warn] 打包技能的 assets/zip2csv 是旧版(只认 .txt/只吃 zip),"
                          "且本机没有同门 zip2csv 技能可刷新——log.log 类数据会整目录跳过")
        # 根因规则副本(package_scenario 放在 assets/fault-tree-rca):缺→补挂;旧→整目录刷新
        rca_dst = product / "fault-tree-rca"
        rca_src = Path(__file__).resolve().parents[2] / "fault-tree-rca"
        rca_src_draft = rca_src / "scripts" / "draft_rca.py"
        rca_ok = rca_src_draft.exists() and \
            "auto_verdict" in rca_src_draft.read_text(encoding="utf-8", errors="replace")
        _rca_ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.py.bak", "runs", "result*")
        if not rca_dst.is_dir():
            if rca_ok:
                shutil.copytree(rca_src, rca_dst, ignore=_rca_ignore)
                print("  打包技能补挂 assets/fault-tree-rca(根因规则副本:自动定档+机械报告这一代)")
            else:
                print("  [warn] 打包技能缺 assets/fault-tree-rca,且本机没有同门 fault-tree-rca 可拷——"
                      "RCA 步骤没有规则手册与骨架/合成脚本,诊断走不到根因")
        else:
            cur_draft = rca_dst / "scripts" / "draft_rca.py"
            cur = cur_draft.read_text(encoding="utf-8", errors="replace") if cur_draft.exists() else ""
            if "auto_verdict" not in cur \
                    or not (rca_dst / "scripts" / "render_report.py").exists():   # 能力标记:自动定档+机械报告
                if rca_ok:
                    bak = rca_dst.with_name("fault-tree-rca.bak")
                    if bak.exists():
                        shutil.rmtree(bak)
                    rca_dst.rename(bak)
                    shutil.copytree(rca_src, rca_dst, ignore=_rca_ignore)
                    print("  打包技能 assets/fault-tree-rca 已刷新(旧版无自动定档/机械报告;"
                          "旧副本整目录备份 fault-tree-rca.bak)")
                else:
                    print("  [warn] 打包技能的 assets/fault-tree-rca 是旧版(无自动定档/机械报告),"
                          "且本机没有同门 fault-tree-rca 技能可刷新——RCA 仍走旧口径手工裁决")
        sk = skill_root / "SKILL.md"
        sk_text = sk.read_text(encoding="utf-8", errors="replace")
        if "render_report" not in sk_text:               # SKILL.md 正文过旧(没有机械渲染报告这一代;
            exe_sk = Path(__file__).resolve().parents[2] / "cs-scot-execute" / "SKILL.md"   # 换正文保身份
            m_old = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", sk_text, re.S)
            new_txt = exe_sk.read_text(encoding="utf-8", errors="replace") if exe_sk.exists() else ""
            m_new = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", new_txt, re.S)
            if m_old and m_new and "render_report" in new_txt:
                shutil.copy2(sk, sk.with_suffix(".md.bak"))
                sk.write_text(f"---\n{m_old.group(1)}\n---\n" + new_txt[m_new.end():], encoding="utf-8")
                print("  打包技能 SKILL.md 正文已刷新到当前执行指引(数据入口/体检/机械渲染报告等;"
                      "技能名与描述保留原样,旧文备份 SKILL.md.bak)")
            else:
                print("  [warn] 打包技能 SKILL.md 正文是旧版(缺机械渲染报告指引),但无法自动刷新"
                      "(frontmatter 异常或本机同门 cs-scot-execute 版本不够新)——建议重新 package")


def upgrade_cross_scaffold(product: Path) -> None:
    """产物 cross_analyzer.py 骨架过旧(无 S 全量喂源/raw 通道)→ 借 generate 的手术就地升级。"""
    ca = product / "scripts" / "cross_analyzer.py"
    if not ca.exists():
        return
    text = ca.read_text(encoding="utf-8", errors="replace")
    if "raw_dir" in text and "load_all" in text:
        return                                           # 已是新骨架
    asm_p = _engine_src("assemble_scenario.py", ("raw_dir", "_upgrade_cross_scaffold"),
                        Path(__file__).resolve().parents[2] / "cs-scot-generate" / "scripts")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("asm_for_migrate", asm_p)
        asm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(asm)
        up, why = asm._upgrade_cross_scaffold(text, asm.CROSS_SCAFFOLD)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] cross_analyzer.py 骨架是旧版(S 类会拿不到数据),且自动升级不可用({e})——"
              "用最新 cs-scot-generate 重跑 assemble_scenario 升级骨架后再执行诊断")
        return
    if up is None:
        print(f"[warn] cross_analyzer.py 骨架无法自动升级:{why}——需人工处理后 S 类才能拿到数据")
    elif up != text:
        shutil.copy2(ca, ca.with_suffix(".py.bak"))
        ca.write_text(up, encoding="utf-8")
        print("  cross_analyzer.py 骨架已升级(绑定原样保留,旧文件备份 .py.bak)")


# ---------- 落地动作(单节点与批量共用) ----------
def _binding(fc_name: str, entry: str, per_ne: bool, kwargs: dict, pkg_node: str) -> dict:
    return {"class": "S", "func": "fault_check",
            "args": {"script": f"custom/{fc_name}", "entry": entry,
                     **({"per_ne": True} if per_ne else {}),
                     **({"entry_kwargs": kwargs} if kwargs else {})},
            "mapping_reason": f"迁移自 tester 交付包「{pkg_node}」:判断逻辑=脚本原样"
                              "(经业务确认+样本数据验证);数据优先 --data raw= 原始目录直读,否则三表兜底重建"}


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
    """共享模块兜底取源:同门 tester 优先,内置 engine/ 兜底。"""
    for d in (Path(__file__).resolve().parents[2] / "datacom-fault-tree-tester" / "scripts",
              Path(__file__).resolve().parents[1] / "engine"):
        if (d / fname).exists():
            return d / fname
    return None


def _with_supplied(fc: Path, deps):
    """包缺共享模块 → 兜底补齐名单:(supplied[(模块名,源路径)…], unsupplied[模块名…])。"""
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
    ①保修复:包没变而 custom/ 副本修过管道(.orig 基准==包里原件)→ 保留修复副本不覆盖;
      包真变了 → 正常覆盖并清掉过期 .orig(新包重新过体检);
    ②补库:脚本 import 的 search_*.py 包里没带 → 从同门 tester/engine 兜底拷入(不覆盖已有)。"""
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
        print(f"  [补库] {', '.join(supplied)}:包里没带的共享模块已从引擎兜底拷入"
              "(正本仍须自包含,反馈交付方)")


# ---------- 收编体检(契约闸)+ 管道修复循环 ----------
_CC_CACHE = []
# 体检 FAIL 里转接头运行时兜得住的项(能走到这一步=烟测仲裁已确认驱动得动):
#   S1 入口名/签名(仲裁选定入口+entry_kwargs 驱动)、D2x/D3x 调用崩(_StrPath/护现场/逐网元重调)
# ——降为 WARN 放行记台账;其余 FAIL(如 S3 只认 .txt、D2 缺数据冒充结论)执行必受影响,拦。
_ABSORBED = {"S1", "D2x", "D3x"}


def _contract_mod():
    """加载契约体检模块(同门 tester 优先,engine/ 兜底);都没有 → None(老环境,跳过体检)。"""
    if not _CC_CACHE:
        import importlib.util
        p = _engine_src("check_contract.py", ("def check_script",),
                        Path(__file__).resolve().parents[2] / "datacom-fault-tree-tester" / "scripts")
        mod = None
        if p is not None:
            try:
                spec = importlib.util.spec_from_file_location("cc_gate", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:                            # noqa: BLE001 体检组件坏了不拦迁移,响亮说
                print(f"  [warn] 契约体检组件加载失败({p})——本次跳过体检")
                mod = None
        _CC_CACHE.append(mod)
    return _CC_CACHE[0]


def _contract_gate(product: Path, sub: Path, fc: Path, deps, supplied,
                   entry=None, per_ne=False, kwargs=None, explicit=False):
    """收编契约闸(两档处置)。返回 ("skip"|"ok"|"fail", info|修法文本):
    - FAIL(兜不住)→ 不落绑定:把脚本连同 .orig 修复基准落进 scripts/custom/,返回修法清单
      ——模型照单只修管道(副本),重跑同命令续迁(保修复规则保证副本不被包里原件冲掉);
    - WARN(转接头兜得住/静态无法确证)→ 放行,记台账+返厂清单;
    - 回归保险:包里带 测试数据/ 时体检自动记基线;修复后基线 matched 与修前不一致 → 拦
      (管道修复不许改变判断结论)。"""
    cc = _contract_mod()
    if cc is None:
        return "skip", None
    custom = product / "scripts" / "custom"
    data = next((p for p in sorted(sub.rglob("测试数据")) if p.is_dir()), None) if sub else None
    mods_bak, path_bak = set(sys.modules), list(sys.path)
    try:
        with tempfile.TemporaryDirectory(prefix="gate_") as sd:
            stage = Path(sd)
            for p in [fc] + list(deps):
                shutil.copy2(_effective(product, p), stage / p.name)
            for name, src in supplied:
                shutil.copy2(src, stage / (name + ".py"))
            try:                                         # 体检按烟测仲裁的裁决口径跑(入口/单网元/kwargs)
                rep = cc.check_script(stage / fc.name, data_dir=data, entry=entry,
                                      per_ne=per_ne, entry_kwargs=kwargs)
            except TypeError:                            # 旧版体检组件不认裁决参数:退回老签名
                rep = cc.check_script(stage / fc.name, data_dir=data)
    finally:                                             # 体检导入的脚本/共享库不许留缓存串包
        for m in set(sys.modules) - mods_bak:
            if m.startswith(("search_", "cc_")):
                sys.modules.pop(m, None)
        sys.path[:] = [p for p in sys.path if p in path_bak]
    items = [i for s in rep["scripts"] for i in s["items"]]
    fails, warns = [], []
    for i in items:
        if i["tier"] == "FAIL" and i["id"] in _ABSORBED:
            warns.append(dict(i, tier="WARN",
                              msg=i["msg"] + "(烟测仲裁已确认转接头驱动得动——兜得住,正本仍须按契约改)"))
        elif i["tier"] == "FAIL":
            fails.append(i)
        elif i["tier"] == "WARN":
            warns.append(i)
    if supplied:
        warns.append({"id": "S6+", "tier": "WARN", "fix": "把脚本 import 的每个 search_*.py 打进交付包",
                      "msg": f"包缺共享模块 {[n for n, _s in supplied]}(收编已补库;正本必须自包含,规范附录8)"})
    baseline = rep.get("baseline")
    side = custom / f"{fc.stem}.contract.json"
    prev = {}
    if side.exists():
        try:
            prev = json.loads(side.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            prev = {}
    dst, orig = custom / fc.name, custom / (fc.name + ".orig")
    repaired = (dst.exists() and orig.exists() and orig.read_bytes() == fc.read_bytes()
                and dst.read_bytes() != fc.read_bytes())
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if fails and explicit:                               # 人工 --entry 强推:人>名单,只警告不拦(与仲裁同款)
        print(f"  ⚠ 契约体检 {len(fails)} 项 FAIL({','.join(i['id'] for i in fails)})"
              "——按人工 --entry 指定放行;运行期该节点判不了先查这些项")
        warns = warns + [dict(i, tier="WARN") for i in fails]
        fails = []
    if fails:
        _copy_scripts(product, fc, deps)                 # 脚本先进场(绑定不落),给修复留工作面
        for p in [fc] + list(deps):
            ob = custom / (p.name + ".orig")
            if not ob.exists():
                shutil.copy2(p, ob)                      # 修复基准=包里原件(回归/保修复都对着它)
        side.write_text(json.dumps(
            {"status": "pending_repair", "fails": fails, "warns": warns,
             "baseline": baseline, "at": now}, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [f"契约体检不过({len(fails)} 项转接头兜不住,绑定不落地):"]
        for i in fails:
            lines.append(f"    [FAIL] {i['id']} {i['msg']}")
            if i.get("fix"):
                lines.append(f"           修法:{i['fix']}")
        lines.append(f"  处置:脚本副本+修复基准已落 scripts/custom/({fc.name} 与 *.orig),"
                     "照修法**只修管道**(判断逻辑一行不动,拿不准算不算逻辑就停下问人),"
                     "改完重跑同一条迁移命令——包没变时修复副本不会被覆盖,体检绿了绑定才落地;"
                     "正本问题按结尾返厂清单反馈交付方。")
        return "fail", "\n".join(lines)
    if repaired and prev.get("status") == "pending_repair" \
            and (prev.get("baseline") or {}).get("matched") and baseline \
            and prev["baseline"]["matched"] != baseline.get("matched"):
        return "fail", (f"  回归不一致:修复前测试数据 matched={prev['baseline']['matched']},"
                        f"修复后={baseline.get('matched')}——管道修复改变了判断结论,这就动到逻辑了。"
                        f"对照 scripts/custom/{fc.name}.orig 回滚多余改动,或把差异交人定夺。")
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
    """契约闸信息 → 台账附加字段(只落非空项)。"""
    if not info:
        return {}
    out = {}
    if info.get("warns"):
        out["contract_warns"] = info["warns"]
    if info.get("repairs"):
        out["repairs"] = info["repairs"]
    return out


def _factory_report(rows):
    """返厂清单:rows=[(标签, warn_items, repairs)…] 有料才打印。副本已兜/已修不影响本次执行,
    但正本的窟窿必须回到交付方手里,否则每次全量交付都复活一遍。"""
    rows = [(lb, wi, rp) for lb, wi, rp in rows if wi or rp]
    if not rows:
        return
    print("\n  返厂清单(反馈交付方按规范改正本;副本已兜/已修,不影响本次执行):")
    for label, warn_items, repairs in rows:
        parts = []
        if repairs:
            parts.append(f"管道已修 {','.join(repairs)}(正本照 scripts/custom/ 副本翻新)")
        parts += [f"{i['id']} {i['msg']}" for i in warn_items]
        print(f"    「{label}」:")
        for s in parts:
            print(f"      - {s}")


def _surgery(product: Path, edits, adds=()) -> None:
    """edits=[(nid, condition, binding)…] 改既有行;adds=[(父nid, 父链名字, 节点名, condition, binding)…]
    新增行(增节点,node_id 留空由 import 分配)。预导入收编(如需)→ 一次导出 → 改/增 N 行 →
    一次导入 → 回导。批量也只走一轮 export/import:落地是**原子**的——任一行编译不过,整批不生效。
    表比 tree_index 新(可能有未导入的人工修改,也可能只是上次迁移的回导)→ 先导入收编,绝不覆盖。"""
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    xls = sorted(p for p in (product / "tree").glob("*.tuning.xlsx")
                 if not p.name.startswith("~$"))
    if xls and tis and xls[0].stat().st_mtime > tis[0].stat().st_mtime:
        print("  [0/3] 调优表比 tree_index 新 → 先导入收编现有内容(有未导入的人工修改也不会丢)…")
        try:
            run_step(product, "import_tree_excel.py", "预导入(收编现有调优表)")
        except SystemExit as e:
            raise SystemExit(str(e) + "\n提示:预导入失败卡的是**存量调优表**,不是这次的交付包。"
                             "若这份调优表里没有【尚未导入】的手工修改,把 tree/ 下的 *.tuning.xlsx 删掉重跑迁移即可"
                             "(会用当前版导出器从产物重建;「多个顶层节点/层级列错位」这类结构病多为旧版导出器所产)。")
    print("  [1/3] 导出调优表…")
    run_step(product, "export_tree_excel.py", "导出调优表")
    for nid, cond, b in edits:
        edit_tuning_excel(product, nid, cond, b)
    for pnid, pchain, name, cond, b in adds:
        edit_tuning_add(product, pnid, pchain, name, cond, b)
    print(f"  [2/3] 已改调优表 {len(edits)} 行"
          + (f" + 新增 {len(adds)} 行(node_id 由导入自动分配)" if adds else "")
          + ":判据→原文列,旧绑定清空,绑定:cross→S(fault_check)")
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
    """entries 每条 7 元组;可选第 8 元 dict(契约闸信息:contract_warns/repairs)直接并进记录。"""
    mig_p = product / "tree" / "migrations.json"
    mig = _ledger_read(product)
    for e in entries:
        nid, pkg_node, fc_name, entry, per_ne, kwargs, fp = e[:7]
        extra = e[7] if len(e) > 7 and e[7] else {}
        mig[nid] = {"package_node": pkg_node, "script": f"custom/{fc_name}", "entry": entry,
                    "per_ne": per_ne, "entry_kwargs": kwargs or {}, "fingerprint": fp,
                    "migrated_at": datetime.datetime.now().isoformat(timespec="seconds"), **extra}
    mig_p.write_text(json.dumps(mig, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 对应表模式(--map:zip 名 ↔ 树路径) ----------
# 层级列表头:格式没跟交付方钉死,能认的都认——裸 L1 / L1(Chinese) / L1(中文) / 全角括号/
# 大小写/内部空格;唯独「L3 Code」这类编码列(无括号带后缀)不吃。
_MAP_LVL_RE = re.compile(r"^L(\d+)(?:\([^)]*\))?$", re.IGNORECASE)


def _parse_map_header(header):
    """表头 → (层级列升序 [(层,原列名)…], zip 列名);不像对应表(缺层级列/缺 zip 列/带 node_id
    ——那是我们的调优表)→ (None, None)。zip 列:先认整格就叫 zip 的,没有再认唯一含 zip 的列名。"""
    lvls, zip_col, zipish = [], None, []
    for h in header:
        hn = str(h).strip().replace("（", "(").replace("）", ")").replace(" ", "")
        if hn.lower() == "node_id":
            return None, None                            # 有 node_id 的是调优表,不是对应表
        m = _MAP_LVL_RE.match(hn)
        if m:
            lvls.append((int(m.group(1)), h))
        elif hn.lower() == "zip":
            zip_col = h
        elif "zip" in hn.lower():
            zipish.append(h)
    if zip_col is None and len(zipish) == 1:
        zip_col = zipish[0]
    if not lvls or zip_col is None:
        return None, None
    return sorted(lvls), zip_col


def _read_table(path: Path):
    """读对应表 → (header, rows[dict])。xlsx 走零依赖 xlsx_reader(同门/engine 两级取径);
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
    for cand in (Path(__file__).resolve().parents[2] / "cs-scot-generate" / "scripts",
                 Path(__file__).resolve().parents[1] / "engine"):
        if (cand / "xlsx_reader.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    import xlsx_reader
    return xlsx_reader.read_xlsx(path)


def load_map_excel(path: Path):
    """同事的对应表(xlsx/csv):层级列 + zip 列(表头兼容见 _MAP_LVL_RE;**没有 node_id**)。
    一行一节点、上级各层名字逐行照抄(调优表口径);zip 列空=纯结构行,跳过。
    返回 [(路径名字列表, zip名, 行号)]。"""
    header, rows = _read_table(path)
    lvls, zip_col = _parse_map_header(header)
    if lvls is None:
        raise SystemExit(f"对应表格式不对:需要层级列(L1..Ln,裸名/带 (Chinese) 等后缀都认)"
                         f"+「zip」列,且不该有 node_id 列;实际表头:{header}")
    out, zipless = [], []
    for i, r in enumerate(rows, start=2):
        z = str(r.get(zip_col) or "").strip()
        names = [v for v in (str(r.get(c) or "").strip() for _l, c in lvls) if v]
        if not z:
            if names:
                zipless.append(names)                    # 结构行(节点在、没配包):全量对账时用来区分
            continue                                     # "节点还在只是没包" vs "节点已从树里删除"
        if not names:
            raise SystemExit(f"对应表第 {i} 行填了 zip「{z}」但层级列全空——没有节点路径,对应不了")
        out.append((names, z, i))
    if not out:
        raise SystemExit("对应表里没有任何一行填了 zip 列——没有要迁移的对应关系")
    return out, zipless


def find_map_table(pkg: Path):
    """交付包里自动找对应表(同事把表和 zip 打在一个包里给,不用单独 --map 指):
    扫 xlsx/xlsm/csv,表头像对应表(层级列+zip 列、无 node_id)的才算。
    恰一张 → 返回;多张 → 硬停(不猜哪张算数);没有 → None。"""
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
        return cands[0], "", ""                          # 全树唯一同名:直接对号(路径只是佐证,少填上级不卡)
    hits = [nid for nid in cands if is_subseq(chain(nid), path_names)]
    detail = "; ".join(f"{nid}: {'/'.join(chain(nid))}" for nid in (hits or cands)[:4])
    if len(hits) == 1:
        return hits[0], "", ""
    if not hits:
        return None, ("树上有多个同名节点,而表中路径与它们的祖先链都对不上"
                      "(把上级各层名字填全以消歧)"), detail
    return None, "路径仍不唯一(树里存在全路径重名,先清树)", detail


def build_zip_registry(pkg: Path, tmp: Path):
    """合集里可对号的子包登记:①目录形态里躺着的 *.zip(逐个解到临时目录)②嵌套 zip 已被
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


def migrate_map(a, pkg: Path, product: Path, tmp: Path, map_path: Path) -> None:
    """对应表驱动的全自动批量:每行 zip↔路径,路径定位节点(同名靠全路径消歧),zip 对号子包,
    落地机器与 --auto 批量同一套(一轮手术原子落地);任一行对应不上→进失败清单,退出码 3。
    **全量交付语义**:内容指纹与台账一致的包自动跳过(幂等;人工调优不被冲;--force 强制全重迁);
    台账里有、表里已消失的迁移节点点名待人工确认(不自动删树)。"""
    entries, zipless = load_map_excel(map_path)
    index = load_index(product)
    nodes = index.get("nodes") or {}
    ledger = _ledger_read(product)
    reg = build_zip_registry(pkg, tmp)
    roots = sorted({str(v) for v in reg.values()})
    print(f"对应表:{len(entries)} 行有 zip 对应;合集里可对号的子包 {len(roots)} 个")
    oks, adds, fails, skips = [], [], [], []
    seen_nid, seen_fc, gates = {}, {}, {}
    used_roots = set()
    for names, zname, rowno in entries:
        label = f"「{names[-1]}」(表第{rowno}行, zip={zname})"
        sub = reg.get(zname)
        if sub is None:
            avail = sorted({Path(r).name for r in roots})
            fails.append((label, f"合集里找不到 zip「{zname}」;可对号的包:{avail[:10]}"
                                 f"{'…' if len(avail) > 10 else ''}", None))
            continue
        nid, why, detail = resolve_node_by_path(nodes, names)
        add_parent = None
        if nid is None and why.startswith("树上没有名为") and len(names) >= 2:
            pnid, pwhy, _pd = resolve_node_by_path(nodes, names[:-1])
            if pnid is not None:                         # 增节点分支:全树无同名 + 父路径能唯一定位
                add_parent = pnid                        # (加法可回滚、有台账 → 自动做;删除仍只点名)
            else:
                fails.append((label, f"新增节点失败:全树无同名,但父路径也定位不了({pwhy})——"
                                     "父节点若也是本次新增,先跑一遍落父、再跑一遍落子;否则核对路径", None))
                continue
        elif nid is None:
            fails.append((label, f"路径定位失败:{why}" + (f"\n       {detail}" if detail else ""),
                          f'--only "{names[-1]}" --node <节点号>(去掉 --map 手工补)'))
            continue
        used_roots.add(str(sub))
        key = nid if nid else f"+{add_parent}/{names[-1]}"   # 新增行还没有 nid,用 父+名 当对账键
        fp = _fingerprint(sub)
        if nid and not a.force and ledger.get(nid, {}).get("fingerprint") == fp:
            skips.append((nid, label))                   # 全量交付里没变的包:跳过(此后的人工调优不被冲)
            seen_nid.setdefault(nid, label)
            continue
        try:
            method = parse_method_md(sub)
            fc, deps = find_scripts(sub, None)
        except SystemExit as e:
            fails.append((label, str(e), None))
            continue
        eff_fc = _effective(product, fc)                 # 修过管道的副本生效(重跑续迁不被原件卡死)
        entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
        if entry is None:
            fails.append((label, f"探测不到可用入口(顶层函数:{all_defs[:8]}{';' + note if note else ''})",
                          f'--only "{method["name"]}" --node {nid or "<节点号>"} --entry <函数名>(去掉 --map)'))
            continue
        supplied, unsupplied = _with_supplied(fc, deps)
        if unsupplied:
            fails.append((label, f"包缺共享模块 {unsupplied} 且引擎无兜底——让交付方补进包(规范附录8)", None))
            continue
        try:
            entry, per_ne, kwargs, sm = arbitrate_entry(
                eff_fc, [_effective(product, p) for p in deps] + [s for _n, s in supplied],
                entry, per_ne, kwargs, explicit=False)
        except SystemExit as e:
            fails.append((label, str(e), f'--only "{method["name"]}" --node {nid or "<节点号>"} --entry <函数名>(去掉 --map)'))
            continue
        if sm:
            note = (note + ";" if note else "") + sm
        if key in seen_nid:
            fails.append((label, f"与表中「{seen_nid[key]}」都对到 {key}——同一节点两行,核对对应表", None))
            continue
        prev = seen_fc.get(fc.name)
        if prev is not None and prev[1] != fc.read_bytes():
            fails.append((label, f"脚本文件名「{fc.name}」与「{prev[0]}」撞车且内容不同——让交付方改名", None))
            continue
        seen_nid[key], seen_fc[fc.name] = label, (label, fc.read_bytes())
        if method["name"].strip() != names[-1]:
            note = (note + ";" if note else "") + f"包内节点名「{method['name']}」≠表中「{names[-1]}」,以对应表为准"
        verdict, ginfo = _contract_gate(product, sub, fc, deps, supplied,
                                        entry=entry, per_ne=per_ne, kwargs=kwargs)
        if verdict == "fail":
            fails.append((label, ginfo, None))
            continue
        if ginfo:
            gates[key] = ginfo
            if ginfo["repairs"]:
                note = (note + ";" if note else "") + f"管道已修 {','.join(ginfo['repairs'])}"
            if ginfo["warns"]:
                note = (note + ";" if note else "") + f"契约WARN {','.join(ginfo['warns'])}(转接头兜,详见返厂清单)"
        if add_parent is not None:
            adds.append((sub, method, fc, deps, entry, per_ne, kwargs, add_parent, names[-1], note, fp))
        else:
            oks.append((sub, method, fc, deps, entry, per_ne, kwargs, nid, note, fp))

    added_report = []
    if oks or adds:
        refresh_engine(product)
        for _sub, _m, fc, deps, *_rest in oks + adds:
            _copy_scripts(product, fc, deps)
        _surgery(product,
                 [(nid, m["condition"], _binding(fc.name, entry, per_ne, kwargs, m["name"]))
                  for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, _fp in oks],
                 [(pnid, _name_chain(nodes, pnid), name, m["condition"],
                   _binding(fc.name, entry, per_ne, kwargs, m["name"]))
                  for _sub, m, fc, _deps, entry, per_ne, kwargs, pnid, name, _note, _fp in adds])
        upgrade_cross_scaffold(product)
        led = [(nid, m["name"], fc.name, entry, per_ne, kwargs, fp, _gate_extra(gates.get(nid)))
               for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, fp in oks]
        if adds:                                         # 新行的 nN 由 import 刚分配:重读 index 认领进台账
            new_nodes = load_index(product).get("nodes") or {}
            for _sub, m, fc, _deps, entry, per_ne, kwargs, pnid, name, note, fp in adds:
                new_nid = next((k for k, nd in new_nodes.items()
                                if str(nd.get("name") or "").strip() == name
                                and nd.get("parent") == pnid), None)
                if new_nid:
                    led.append((new_nid, m["name"], fc.name, entry, per_ne, kwargs, fp,
                                _gate_extra(gates.get(f"+{pnid}/{name}"))))
                added_report.append((new_nid, name, pnid, fc.name, entry, note))
        _ledger(product, led)

    print(f"\n对应表批量迁移汇总:落地 {len(oks) + len(adds)}/{len(entries)} 行"
          + (f"(其中新增节点 {len(adds)} 个)" if adds else "")
          + (f",跳过 {len(skips)} 行(内容与上次迁移一致;--force 强制重迁)" if skips else ""))
    for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, note, _fp in oks:
        print(f"  ✓ {nid}「{nodes[nid].get('name')}」 ← {fc.name}:{entry}"
              + ("/单网元" if per_ne else "") + (f"  ⚠ {note}" if note else ""))
    for new_nid, name, pnid, fc_name, entry, note in added_report:
        print(f"  + {new_nid or '?'}「{name}」 新增,挂在 {pnid}「{nodes[pnid].get('name')}」下 ← {fc_name}:{entry}"
              + (f"  ⚠ {note}" if note else ""))
    for nid, label in skips:
        print(f"  = {nid}「{nodes[nid].get('name')}」 未变化,跳过(此前的人工调优不受影响)")
    if fails:
        print(f"\n  未落地 {len(fails)} 行(对应不上决不赌):")
        for label, why, cmd in fails:
            print(f"  ✗ {label}:{why}")
            if cmd:
                print(f"     → 确认后:--package \"{a.package}\" --product \"{a.product}\" {cmd}")
    unused = sorted({Path(r).name for r in roots} - {Path(r).name for r in used_roots})
    if unused:
        print(f"\n  [info] 合集里有 {len(unused)} 个子包没被对应表引用(未迁移):{unused[:8]}"
              f"{'…' if len(unused) > 8 else ''}")

    # 全量对账:台账里迁移过、这次对应表里却没有配 zip 的节点——上游删了节点?撤了优化包?
    # 只点名不动手(删节点是结构手术,必须人确认;评估反思/删除指令能力接上后由那边闭环)。
    zipless_nids = set()
    for names in zipless:
        znid, _w, _d = resolve_node_by_path(nodes, names)
        if znid:
            zipless_nids.add(znid)
    covered = set(seen_nid)
    gone, retracted = [], []
    for nid in sorted(ledger, key=lambda x: int(x[1:]) if x[1:].isdigit() else 10 ** 9):
        if nid in covered or nid not in nodes:
            continue
        (retracted if nid in zipless_nids else gone).append(nid)
    if retracted:
        print(f"\n  ⚠ 台账里迁移过、表里节点还在但**没配 zip** 的 {len(retracted)} 个(优化包撤了?):"
              + "、".join(f"{n}「{nodes[n].get('name')}」" for n in retracted[:6])
              + " ——保留现状,向交付方确认")
    if gone:
        print(f"\n  ⚠ 台账里迁移过、**对应表里已无该节点** 的 {len(gone)} 个(上游可能删了节点):"
              + "、".join(f"{n}「{nodes[n].get('name')}」" for n in gone[:6])
              + " ——树里仍保留,不自动删;确认后走调优表删行(删行→import 即生效)")
    _factory_report([(seen_nid.get(k, str(k)), v["warn_items"], v["repairs"])
                     for k, v in gates.items()])
    if oks:
        print("\n  迁移就地生效(产物=打包技能 assets/ 时无需重新打包);台账 → tree/migrations.json。")
    sys.exit(3 if fails else 0)


# ---------- 批量模式(多节点合集包 + --auto) ----------
def migrate_batch(a, pkg: Path, product: Path) -> None:
    """逐子包全自动循环:精确同名唯一的直接落地(一轮手术原子落地);对不上号/探不到入口的
    **决不赌**——收集起来最后统一列清单,每个附补对号命令(退出码 3=还有没落地的)。"""
    groups = find_subpackages(pkg)
    subs = sorted(groups, key=lambda p: p.name)
    index = load_index(product)
    nodes = index.get("nodes") or {}
    ledger = _ledger_read(product)
    print(f"合集包:{len(subs)} 个子包,逐个对号(精确同名唯一才自动落地,其余列候选)…")
    oks, fails, skips = [], [], []                       # ok=(sub,method,fc,deps,entry,per_ne,kwargs,nid,note,fp)
    seen_nid, seen_fc, gates = {}, {}, {}
    for sub in subs:
        label = sub.name
        try:
            method = parse_method_md(sub)
            label = method["name"]
            fc, deps = find_scripts(sub, None)
        except SystemExit as e:
            fails.append((label, str(e), None))
            continue
        eff_fc = _effective(product, fc)                 # 修过管道的副本生效(重跑续迁不被原件卡死)
        entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
        if entry is None:
            fails.append((label, f"探测不到可用入口(顶层函数:{all_defs[:8]}{';' + note if note else ''})",
                          f'--only "{label}" --node <节点号> --entry <吃目录参数的函数名>'))
            continue
        exact = [cnid for cnid, nd in nodes.items()
                 if str(nd.get("name") or "").strip() == method["name"].strip()]
        if len(exact) != 1:
            cand = "\n".join(f"         {s:5.3f}  {cnid:>6}  {nm}"
                             for s, cnid, nm in rank_nodes(index, method["name"], method["core"]))
            why = ((f"树上有 {len(exact)} 个同名节点({exact}),精确匹配不唯一" if exact
                    else "无精确同名节点") + ",候选:\n" + cand)
            fails.append((label, why, f'--only "{label}" --node <节点号>'))
            continue
        nid = exact[0]
        fp = _fingerprint(sub)
        if not a.force and ledger.get(nid, {}).get("fingerprint") == fp:
            skips.append((nid, label))                   # 全量交付里没变的包:跳过(此后的人工调优不被冲)
            seen_nid.setdefault(nid, label)
            continue
        supplied, unsupplied = _with_supplied(fc, deps)
        if unsupplied:
            fails.append((label, f"包缺共享模块 {unsupplied} 且引擎无兜底——让交付方补进包(规范附录8)", None))
            continue
        try:
            entry, per_ne, kwargs, sm = arbitrate_entry(
                eff_fc, [_effective(product, p) for p in deps] + [s for _n, s in supplied],
                entry, per_ne, kwargs, explicit=False)
        except SystemExit as e:
            fails.append((label, str(e), f'--only "{label}" --node {nid} --entry <函数名>'))
            continue
        if sm:
            note = (note + ";" if note else "") + sm
        if nid in seen_nid:
            fails.append((label, f"与子包「{seen_nid[nid]}」都对到 {nid}——同一节点两份交付包,人工定夺谁算数",
                          f'--only "{label}" --node {nid}'))
            continue
        prev = seen_fc.get(fc.name)
        if prev is not None and prev[1] != fc.read_bytes():
            fails.append((label, f"脚本文件名「{fc.name}」与子包「{prev[0]}」撞车且内容不同"
                                 "——让交付方改名后把该子包单独再迁", None))
            continue
        seen_nid[nid], seen_fc[fc.name] = label, (label, fc.read_bytes())
        verdict, ginfo = _contract_gate(product, sub, fc, deps, supplied,
                                        entry=entry, per_ne=per_ne, kwargs=kwargs)
        if verdict == "fail":
            fails.append((label, ginfo, None))
            continue
        if ginfo:
            gates[nid] = ginfo
            if ginfo["repairs"]:
                note = (note + ";" if note else "") + f"管道已修 {','.join(ginfo['repairs'])}"
            if ginfo["warns"]:
                note = (note + ";" if note else "") + f"契约WARN {','.join(ginfo['warns'])}(转接头兜,详见返厂清单)"
        oks.append((sub, method, fc, deps, entry, per_ne, kwargs, nid, note, fp))

    if oks:
        refresh_engine(product)
        for _sub, _m, fc, deps, *_rest in oks:
            _copy_scripts(product, fc, deps)
        _surgery(product, [(nid, m["condition"], _binding(fc.name, entry, per_ne, kwargs, m["name"]))
                           for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, _fp in oks])
        upgrade_cross_scaffold(product)
        _ledger(product, [(nid, m["name"], fc.name, entry, per_ne, kwargs, fp, _gate_extra(gates.get(nid)))
                          for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, _note, fp in oks])

    print(f"\n批量迁移汇总:落地 {len(oks)}/{len(subs)} 个"
          + (f",跳过 {len(skips)} 个(内容与上次迁移一致;--force 强制重迁)" if skips else ""))
    for _sub, m, fc, _deps, entry, per_ne, kwargs, nid, note, _fp in oks:
        print(f"  ✓ {nid}「{nodes[nid].get('name')}」 ← 「{m['name']}」({fc.name}:{entry}"
              + ("/单网元" if per_ne else "") + ")" + (f"  ⚠ {note}" if note else ""))
        if kwargs:
            print(f"      ⚠ 入口有额外必填参数 {sorted(kwargs)},已按空 dict 传入;"
                  f"不对就 --only \"{m['name']}\" --node {nid} --entry-kwargs 重迁指定")
    for nid, label in skips:
        print(f"  = {nid}「{nodes[nid].get('name')}」 未变化,跳过(此前的人工调优不受影响)")
    if fails:
        print(f"\n  未落地 {len(fails)} 个(对不上号决不赌,逐个补对号):")
        for label, why, cmd in fails:
            print(f"  ✗ 「{label}」:{why}")
            if cmd:
                print(f"     → 确认后重跑:--package \"{a.package}\" --product \"{a.product}\" {cmd}")
    _factory_report([(seen_nid.get(k, str(k)), v["warn_items"], v["repairs"])
                     for k, v in gates.items()])
    if oks:
        print("\n  迁移就地生效(产物=打包技能 assets/ 时无需重新打包);台账 → tree/migrations.json;"
              "执行时 runner 加 --data raw=<原始目录> 最忠实。")
    sys.exit(3 if fails else 0)


# ---------- 主流程 ----------
def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="tester 交付包 → 场景产物迁移(判据替换+S 绑定换血)")
    ap.add_argument("--package", help="交付包目录或 zip(--refresh-only 时可省)")
    ap.add_argument("--refresh-only", action="store_true",
                    help="不迁移任何包,只对产物/打包技能做引擎体检+全量翻新(custom_lib/内核/导入导出/"
                         "runner/check_data/zip2csv/SKILL 正文,全部按能力标记只补缺不降级)——"
                         "手头没有交付包、只想把旧技能刷到当前代时用")
    ap.add_argument("--product", required=True, help="场景产物目录(或打包技能的 assets/ 目录)")
    ap.add_argument("--node", help="目标节点号(如 n17);不给=只做匹配、列候选")
    ap.add_argument("--auto", action="store_true",
                    help="节点名与树上某节点**一字不差且唯一**时直接落地(对号第一级:代码精确匹配);"
                         "无精确命中则列候选后以退出码 3 结束(交模型/人对齐)。"
                         "多节点合集包 + --auto = 批量循环:能精确对号的全部落地,其余列候选")
    ap.add_argument("--only", help="多节点合集包里只处理指定「包节点名」的那个子包"
                                   "(批量没对上号的,逐个用它配合 --node/--entry 补对号)")
    ap.add_argument("--map", help="对应表 xlsx/csv(层级列 L1..Ln + zip 列,无 node_id):"
                                  "zip 名↔树路径全自动批量对号,同名节点靠全路径消歧")
    ap.add_argument("--force", action="store_true",
                    help="内容指纹与台账一致的包默认跳过(全量交付幂等,人工调优不被冲)——"
                         "加本参数强制全部重迁")
    ap.add_argument("--script", help="包里有多个 fault_check_*.py 时指定文件名")
    ap.add_argument("--entry", help="入口函数名(默认自动探测)")
    ap.add_argument("--per-ne", action="store_true", help="入口吃单网元目录(自动探测不准时手工指定)")
    ap.add_argument("--entry-kwargs", help='入口额外关键字参数 JSON(如 {"judgment_conditions": {}})')
    a = ap.parse_args()
    product = Path(a.product).expanduser().resolve()
    if not (product / "tree").is_dir() or not (product / "scripts").is_dir():
        if (product / "assets" / "tree").is_dir() and (product / "assets" / "scripts").is_dir():
            product = product / "assets"                 # 给的是打包技能根目录:产物在 assets/,自动进
            print(f"[info] --product 是打包技能目录,已自动定位到产物:{product}")
        else:
            raise SystemExit(f"--product 不像场景产物目录(缺 tree/ 或 scripts/):{product}")

    if a.refresh_only:                                   # 无包翻新:只走引擎体检+补全(含打包技能的
        refresh_engine(product)                          # runner/check_data/zip2csv/SKILL 正文按代刷新)
        upgrade_cross_scaffold(product)
        print("\n翻新完成:引擎/内核/函数库(及打包技能的 runner/check_data/zip2csv/SKILL 正文)"
              "已按能力标记补齐到当前代——齐全的文件一律没动(不降级),动过的都有 .bak/.md.bak 备份。")
        return

    if not a.package:
        raise SystemExit("--package 必填(交付包目录或 zip);只想翻新旧技能不迁包,用 --refresh-only。")

    with tempfile.TemporaryDirectory(prefix="migrate_") as td:
        pkg = unpack(Path(a.package).expanduser().resolve(), Path(td))
        # 处理方式按输入动态定:①有对应表(--map 显式,或表就打在交付包里→自动发现)→ 对应表批量;
        # ②无表+合集包+--auto → 逐子包精确对号批量;③无表+单包 → 单节点流程。全在下面顺序分流。
        manual = a.node or a.only or a.script or a.entry or a.per_ne or a.entry_kwargs
        map_path = Path(a.map).expanduser().resolve() if a.map else None
        if map_path is None and not manual:
            map_path = find_map_table(pkg)               # 表和 zip 打在一个包里给:自动发现,不用记参数
            if map_path is not None:
                print(f"[info] 交付包里发现对应表「{map_path.name}」→ 按对应表批量对号"
                      "(不想按表:把表移出包外,或用 --only 单挑)")
        if map_path is not None:                         # 对应表模式:先于子包探测(目录形态的合集顶层没有 md)
            if manual:
                raise SystemExit("对应表是全自动批量对应:不能与 --node/--only/--script/--entry/"
                                 "--per-ne/--entry-kwargs 同用;个别包要手工处理,去掉 --map 用 --only 单挑。")
            migrate_map(a, pkg, product, Path(td), map_path)
            return                                       # migrate_map 自带退出码,到不了这里
        groups = find_subpackages(pkg)
        if not groups:
            raise SystemExit(f"交付包里找不到 判断方法*.md(排除规范文件):{pkg}")

        def _sub_names():
            out = []
            for sub in sorted(groups, key=lambda p: p.name):
                try:
                    out.append(parse_method_md(sub)["name"])
                except SystemExit:
                    out.append(f"{sub.name}(判断方法解析失败)")
            return out

        if a.only:                                       # 合集包单挑:选定子包后走单节点流程(全部参数可用)
            hit = next((sub for sub in groups
                        if _safe_name(sub) == a.only.strip()), None)
            if hit is None:
                raise SystemExit(f"--only「{a.only}」在包里找不到;包里的节点:{_sub_names()}")
            pkg = hit
        elif len(groups) > 1:                            # 多节点合集包
            if a.node or a.script or a.entry or a.per_ne or a.entry_kwargs:
                raise SystemExit("这是多节点合集包:--node/--script/--entry/--per-ne/--entry-kwargs "
                                 "不知道给哪个子包用——加 --only \"<包节点名>\" 选定子包再带这些参数。")
            if not a.auto:
                print(f"多节点合集包({len(groups)} 个子包),两种走法:")
                print("  批量全自动:原命令加 --auto(精确同名的全部落地,其余列候选)")
                print("  单挑一个:加 --only \"<包节点名>\"(可配 --node/--entry 等)")
                print("包里的节点:")
                for nm in _sub_names():
                    print(f"  - {nm}")
                sys.exit(3)
            migrate_batch(a, pkg, product)
            return                                       # migrate_batch 自带退出码,到不了这里

        method = parse_method_md(pkg)
        fc, deps = find_scripts(pkg, a.script)
        eff_fc = _effective(product, fc)                 # 修过管道的副本生效(重跑续迁不被原件卡死)
        entry, per_ne, kwargs, all_defs, argc, note = detect_entry(eff_fc)
        if a.entry:
            entry, per_ne = a.entry, a.per_ne
            if argc.get(entry, 1) < 1:                   # 手工指定也过同一道闸:0 参入口运行期必炸
                raise SystemExit(f"--entry 指定的「{entry}」不接收数据目录参数(脚本从 sys.argv 自取路径)"
                                 "——转接头必传目录参数,选它运行期必报 TypeError。"
                                 "让交付方按规范改成 diagnose(data_dir),或选一个吃目录参数的函数。")
        if a.entry_kwargs:
            kwargs = json.loads(a.entry_kwargs)
        index = load_index(product)
        print(f"交付包:节点名称「{method['name']}」 脚本 {fc.name} 入口 {entry or '?'}"
              f"{'(单网元模式)' if per_ne else ''} 共享模块 {[d.name for d in deps] or '无'}"
              + (f"\n  ⚠ {note}" if note else ""))

        # 对号三级:① 代码精确匹配(名字一字不差且唯一)→ --auto 直接落地;
        #          ② 无精确命中 → 列候选,模型按语义对齐后带 --node 重跑;
        #          ③ 模型也拿不准 → 问人。②③发生在技能层,本脚本只负责①和出候选。
        nodes = index.get("nodes") or {}
        exact = [nid for nid, nd in nodes.items()
                 if str(nd.get("name") or "").strip() == method["name"].strip()]
        nid = a.node.strip() if a.node else None
        if nid is None and a.auto and len(exact) == 1:
            nid = exact[0]
            print(f"精确命中:{nid}「{nodes[nid].get('name')}」(节点名与交付包一字不差)——按 --auto 直接落地")
        if nid is None:                                  # 匹配模式:列候选
            if len(exact) > 1:
                print(f"[warn] 树上有 {len(exact)} 个同名节点({exact})——精确匹配不唯一,必须人工指定")
            print("\n候选节点(相似度=名字 0.6 + 判据正文 0.4;=为一字不差):")
            for score, cnid, name in rank_nodes(index, method["name"], method["core"]):
                mark = "=" if cnid in exact else " "
                print(f"  {mark}{score:5.3f}  {cnid:>6}  {name}")
            print("\n确认后执行:加 --node <节点号> 重跑本命令落地迁移。")
            if a.auto:
                sys.exit(3)                              # 给上层一个"没能自动对号"的机器可读信号
            return
        if nid not in nodes:
            raise SystemExit(f"节点 {nid} 不在故障树里——先跑匹配模式看候选")
        if entry is None:
            raise SystemExit(f"探测不到可用入口函数(脚本顶层函数:{all_defs[:8]}{';' + note if note else ''})"
                             "——入口须接收数据目录参数,如 diagnose(data_dir);让交付方按规范改脚本,"
                             "或用 --entry 指定一个吃目录参数的函数")
        if kwargs and not a.entry_kwargs:
            print(f"[warn] 入口 {entry} 有额外必填参数 {sorted(kwargs)},已按空 dict 传入;"
                  "不对就用 --entry-kwargs 显式指定")
        supplied, unsupplied = _with_supplied(fc, deps)
        if unsupplied:
            raise SystemExit(f"包缺共享模块 {unsupplied} 且引擎无兜底——让交付方补进包(规范附录8)")
        entry, per_ne, kwargs, _sn = arbitrate_entry(
            eff_fc, [_effective(product, p) for p in deps] + [s for _n, s in supplied],
            entry, per_ne, kwargs, explicit=bool(a.entry))

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
            print(f"  [契约WARN] {','.join(ginfo['warns'])}(转接头兜着跑;详见结尾返厂清单)")

        # 0. 引擎体检+补全(旧产物的内核/函数库/导入校验不认 S 类,先刷新再动手术)
        refresh_engine(product)

        # 1. 脚本进场
        _copy_scripts(product, fc, deps)
        print(f"  已拷入 scripts/custom/:{fc.name}" + (f" + {len(deps)} 个共享模块" if deps else ""))

        # 2. 调优表手术(预导入收编→导出→改行→导入→回导,细节见 _surgery)
        _surgery(product, [(nid, method["condition"],
                            _binding(fc.name, entry, per_ne, kwargs, method["name"]))])

        # 3. 骨架体检 + 台账
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


if __name__ == "__main__":
    main()
