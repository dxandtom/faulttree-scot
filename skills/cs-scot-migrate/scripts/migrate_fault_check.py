#!/usr/bin/env python3
"""datacom-fault-tree-tester 交付包 → 场景产物:判据替换 + 绑定换血,一条龙机械迁移。

链路(用户拍板):a. 把交付包里人确认过的「判断方法」替换到故障树对应节点的判据上;
b. 把该节点旧绑定去掉,换成一条 S 绑定(func=fault_check 通用转接头),直接跑交付的脚本。

两种模式:
  匹配模式(不带 --node):解析交付包,和产物故障树逐节点算相似度,列出候选——
      python migrate_fault_check.py --package <交付包目录|zip> --product <场景产物目录>
  执行模式(带 --node):人确认节点号后落地迁移——
      python migrate_fault_check.py --package <…> --product <…> --node n17
      可选:--entry <入口函数名> --per-ne --entry-kwargs '<JSON>'(自动探测不准时手工指定)

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
def unpack(package: Path, tmp: Path) -> Path:
    """zip → 解压到临时目录;目录 → 原样返回。"""
    if package.is_dir():
        return package
    if package.suffix.lower() != ".zip":
        raise SystemExit(f"交付包应是目录或 .zip:{package}")
    dst = tmp / "package"
    try:
        zf = zipfile.ZipFile(package, metadata_encoding="gbk")   # Windows 打的包常见 GBK 文件名
    except TypeError:                                            # 老 Python 无该参数
        zf = zipfile.ZipFile(package)
    with zf:
        zf.extractall(dst)
    inner = [d for d in dst.iterdir() if d.is_dir()]
    return inner[0] if len(inner) == 1 and not any(dst.glob("*.md")) else dst


def parse_method_md(pkg: Path):
    """判断方法.md → {name, condition, full}。规范格式(## 分节)解析;解析不动就整文兜底。"""
    cands = [p for p in pkg.rglob("判断方法*.md") if "规范" not in p.name]
    if not cands:
        raise SystemExit(f"交付包里找不到 判断方法*.md(排除规范文件):{pkg}")
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
    condition = secs.get("判断条件") or ""
    if not condition:
        print("[warn] 判断方法.md 里没解析到「## 判断条件」——判据将用整份文档正文,建议核对格式")
        condition = text.strip()
    return {"name": name, "condition": condition.strip(), "file": cands[0]}


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


def detect_entry(script: Path):
    """静态探测入口函数:diagnose > main > check_ne > diagnose_ne > check。
    入口契约=至少接收 1 个位置参数(数据目录);0 参函数(从 sys.argv 自取路径的老脚本)一律跳过
    并记入 note——运行期转接头必传目录参数,选了 0 参入口就是必炸的 TypeError,在迁移时拦下。
    单网元入口(check_ne/diagnose_ne)→ per_ne;首参之外的无默认值参数 → entry_kwargs 需求。
    返回 (entry, per_ne, kwargs, 顶层函数清单, argc 表, note)。"""
    tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    argc = {n: len(f.args.args) for n, f in defs.items()}
    entry, skipped = None, []
    for c in ("diagnose", "main", "check_ne", "diagnose_ne", "check"):
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
    fn = defs[entry]
    pos = [a.arg for a in fn.args.args]
    required_extra = pos[1: len(pos) - len(fn.args.defaults)] if len(pos) > 1 else []
    kwargs = {p: {} for p in required_extra}             # 常见如 judgment_conditions={};其余待人核
    return entry, entry in ("check_ne", "diagnose_ne"), kwargs, sorted(defs), argc, note


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
def edit_tuning_excel(product: Path, nid: str, condition: str, binding: dict):
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
    id_col = header.index("node_id") + 1
    raw_col = (header.index(RAW_TAG) + 1) if RAW_TAG in header else None
    if raw_col is None:                                  # 没有原文列就在最右补一列(导入按表头名识别,位置无关)
        raw_col = len(header) + 1
        ws.cell(row=1, column=raw_col, value=RAW_TAG)
    tag_cols = [i + 1 for i, h in enumerate(header) if h.startswith("判据:") and h != RAW_TAG]
    bind_cols = {h[len("绑定:"):]: i + 1 for i, h in enumerate(header) if h.startswith("绑定:")}
    if "cross" not in bind_cols:                         # 老表没有 cross 列:同样在最右补(导入放行)
        bind_cols["cross"] = raw_col + 1 if raw_col > len(header) else len(header) + 1
        ws.cell(row=1, column=bind_cols["cross"], value="绑定:cross")
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
    wb.save(xls[0])
    return xls[0]


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
    "condition_kernels.py": ("def k_script", "def alias_sources"),
    "custom_lib.py": ('@register("fault_check")',),
    "import_tree_excel.py": ('{"P", "S"}',),             # S/P 放行 + cross 列支持同代
    "export_tree_excel.py": ('!= "cross"',),             # cross 列进表 + 相对深度同代
}
RUNNER_CAP = "源名兼容"                                  # raw 通道 / diag↔cmd 互认同代


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


# ---------- 主流程 ----------
def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="tester 交付包 → 场景产物迁移(判据替换+S 绑定换血)")
    ap.add_argument("--package", required=True, help="交付包目录或 zip")
    ap.add_argument("--product", required=True, help="场景产物目录(或打包技能的 assets/ 目录)")
    ap.add_argument("--node", help="目标节点号(如 n17);不给=只做匹配、列候选")
    ap.add_argument("--auto", action="store_true",
                    help="节点名与树上某节点**一字不差且唯一**时直接落地(对号第一级:代码精确匹配);"
                         "无精确命中则列候选后以退出码 3 结束(交模型/人对齐)")
    ap.add_argument("--script", help="包里有多个 fault_check_*.py 时指定文件名")
    ap.add_argument("--entry", help="入口函数名(默认自动探测)")
    ap.add_argument("--per-ne", action="store_true", help="入口吃单网元目录(自动探测不准时手工指定)")
    ap.add_argument("--entry-kwargs", help='入口额外关键字参数 JSON(如 {"judgment_conditions": {}})')
    a = ap.parse_args()
    product = Path(a.product).expanduser().resolve()
    if not (product / "tree").is_dir() or not (product / "scripts").is_dir():
        raise SystemExit(f"--product 不像场景产物目录(缺 tree/ 或 scripts/):{product}")

    with tempfile.TemporaryDirectory(prefix="migrate_") as td:
        pkg = unpack(Path(a.package).expanduser().resolve(), Path(td))
        method = parse_method_md(pkg)
        fc, deps = find_scripts(pkg, a.script)
        entry, per_ne, kwargs, all_defs, argc, note = detect_entry(fc)
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
            for score, cnid, name in rank_nodes(index, method["name"], method["condition"]):
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

        # 0. 引擎体检+补全(旧产物的内核/函数库/导入校验不认 S 类,先刷新再动手术)
        refresh_engine(product)

        # 1. 脚本进场
        custom = product / "scripts" / "custom"
        custom.mkdir(parents=True, exist_ok=True)
        for p in [fc] + list(deps):
            shutil.copy2(p, custom / p.name)
        print(f"  已拷入 scripts/custom/:{fc.name}" + (f" + {len(deps)} 个共享模块" if deps else ""))

        # 2. 调优表手术。表比 tree_index 新(可能有未导入的人工修改,也可能只是上次迁移的回导)
        # → 先导入收编,绝不覆盖:合法修改被收进产物;表填坏了 import 会响亮失败、迁移就此打住。
        tis = sorted((product / "tree").glob("*.tree_index.json"))
        xls = sorted(p for p in (product / "tree").glob("*.tuning.xlsx")
                     if not p.name.startswith("~$"))
        if xls and tis and xls[0].stat().st_mtime > tis[0].stat().st_mtime:
            print("  [0/3] 调优表比 tree_index 新 → 先导入收编现有内容(有未导入的人工修改也不会丢)…")
            run_step(product, "import_tree_excel.py", "预导入(收编现有调优表)")
        print("  [1/3] 导出调优表…")
        run_step(product, "export_tree_excel.py", "导出调优表")
        binding = {"class": "S", "func": "fault_check",
                   "args": {"script": f"custom/{fc.name}", "entry": entry,
                            **({"per_ne": True} if per_ne else {}),
                            **({"entry_kwargs": kwargs} if kwargs else {})},
                   "mapping_reason": f"迁移自 tester 交付包「{method['name']}」:判断逻辑=脚本原样"
                                     "(经业务确认+样本数据验证);数据优先 --data raw= 原始目录直读,否则三表兜底重建"}
        edit_tuning_excel(product, nid, method["condition"], binding)
        print(f"  [2/3] 已改调优表:{nid} 判据→原文列,旧绑定清空,绑定:cross→S(fault_check)")
        print("  [3/3] 编译回产物…")
        run_step(product, "import_tree_excel.py", "导入编译")
        run_step(product, "export_tree_excel.py", "回导调优表(同步)")

        # 3. 骨架体检 + 台账
        upgrade_cross_scaffold(product)
        mig_p = product / "tree" / "migrations.json"
        mig = json.loads(mig_p.read_text(encoding="utf-8")) if mig_p.exists() else {}
        mig[nid] = {"package_node": method["name"], "script": f"custom/{fc.name}", "entry": entry,
                    "per_ne": per_ne, "entry_kwargs": kwargs or {},
                    "migrated_at": datetime.datetime.now().isoformat(timespec="seconds")}
        mig_p.write_text(json.dumps(mig, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n迁移完成:{nid}「{nodes[nid].get('name')}」")
        print(f"  判据 ← 判断方法.md「判断条件」;绑定 ← S(fault_check → custom/{fc.name}:{entry})")
        print(f"  台账 → tree/migrations.json;调优表已同步,后续人工调优照常。")
        print("  执行提示:runner 加 --data raw=<zip2csv 的 extracted 目录> 可让脚本直读原始 txt"
              "(不加也能跑,自动从三表重建);产物在打包技能里的话记得重新 package。")


if __name__ == "__main__":
    main()
