#!/usr/bin/env python3
"""数据入口检查:诊断前先看用户给的路径里到底是什么,结构对不对、该怎么喂。零三方依赖。

为什么要它(实测两起):①用户给了顶层目录,网元层其实在 <顶层>/formated_result 下——raw= 传错层,
S 脚本一个网元都找不到,全程"数据不足";②目录里躺着一张采集清单 xlsx(列是 neName/fileType/
relativePath),弱模型看见"neName"就当数据表喂,42 个判据集体报"找不到列"。这类错越早暴露越好:
诊断跑完才发现,浪费一整轮。

用法:python check_data.py <用户给的数据路径>
  退出码 0=认得出可用形态(报告里带**建议的传入方式**,照抄即可);2=结构不认识/只有清单——
  把报告原样给用户,让用户改目录或确认,**不要强行继续**。

识别的形态(可并存,逐个报):
  A. 原始采集目录:<网元层父目录>/<网元>/CONFIG|CMD|LOG|DIAG/*.txt(raw= 必须传网元层父目录);
     平铺单网元(路径下直接是 CONFIG/CMD/…)也认,网元名=目录名;
  B. 已解析表:csv/xlsx,表头带 CmdName/ObjContent(cmd)、Summary/Log(log)、level1/CfgType(config);
     表头对不上这三种的其它 csv/xlsx(无线的话统、CHR 这类宽表)认作**通用数据表**,按文件名对指标名
     (技能的 assets/plan.json 里有指标清单时,文件名对得上哪个就建议哪个);故障树/调优表 xlsx 不算数据;
  C. 采集清单/索引 xlsx(fileType/relativePath/zipFile 这类列)——**是目录不是数据**,点名跳过;
  D. zip 压缩包——用 <assets>/zip2csv/scripts/zip2csv.py 转三表(它既接受 zip 也接受目录)。
"""
from __future__ import annotations

import sys
from pathlib import Path

KINDS = ("CONFIG", "CMD", "LOG", "DIAG", "OPR")          # DIAG=CMD 的旧名,两名并存兼容
# 表头指纹:数据表按列认指标;清单/索引表按列拒收(neName 会骗过"看见 NEName 就是数据"的直觉)
_TABLE_SIGNS = {"cmd": {"CmdName", "ObjContent", "CmdResult"},
                "log": {"Summary", "Log", "Level", "Content"},
                "config": {"level1", "CfgType", "L2Module"}}
_MANIFEST_SIGNS = {"fileType", "relativePath", "zipFile"}
# 故障树母表/调优表的表头特征:它们是树,不是数据,混在数据目录里也不能当数据表传入
_TREE_SIGNS = ("L1(Chinese)", "L1_Name_CN", "Judgment Condition", "判据:原文", "node_id")
_DATA_EXTS = (".csv", ".xlsx", ".xlsm")


def _plan_indicators() -> list:
    """技能形态下 assets/plan.json 里的指标名(各 <指标>_analyzer 脚本步骤);找不到或读不了返回 []。"""
    import json
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "assets" / "plan.json", here.parent / "plan.json"):
        if cand.exists():
            try:
                plan = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
            names = [str(n.get("name") or "") for n in (plan.get("nodes") or []) if n.get("kind") == "script"]
            return [n[:-len("_analyzer")] for n in names if n.endswith("_analyzer") and n != "cross_analyzer"]
    return []


def _match_indicator(stem: str, indicators: list):
    """文件名(去扩展名)对指标名:相等优先,其次文件名含指标名(与 runner 的匹配规则一致);对不上返回 None。"""
    s = stem.lower()
    for ind in indicators:
        if s == ind.lower():
            return ind
    hits = [ind for ind in indicators if ind.lower() in s]
    return hits[0] if len(hits) == 1 else None


def _kind_dirs(d: Path):
    return [k for k in KINDS if (d / k).is_dir()]


def _txt_count(d: Path) -> int:
    return sum(1 for p in d.glob("*.txt")) + sum(1 for p in d.glob("*.log"))


def _find_ne_parents(root: Path, max_depth=4):
    """向下找"网元层父目录":其子目录里至少一个是网元目录(带 CONFIG/CMD/… 子目录)。"""
    out = []
    def walk(d: Path, depth: int):
        try:
            subs = [p for p in d.iterdir() if p.is_dir()]
        except OSError:
            return
        nes = [p for p in subs if _kind_dirs(p)]
        if nes:
            out.append((d, nes))
        if depth < max_depth:
            for p in subs:
                if not _kind_dirs(p):                    # 网元目录内部不用再钻
                    walk(p, depth + 1)
    walk(root, 0)
    return out


def _xlsx_header(p: Path):
    """xlsx 表头:优先同技能 assets/scripts 的零依赖 xlsx_reader;没有再试 openpyxl;都没有返回 None。"""
    for cand in (Path(__file__).resolve().parent.parent / "assets" / "scripts",
                 Path(__file__).resolve().parent):
        if (cand / "xlsx_reader.py").exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    try:
        import xlsx_reader
        briefs = xlsx_reader.sheet_briefs(p)
        return briefs[0]["columns"] if briefs else []
    except Exception:
        pass
    try:
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        row = next(wb.active.iter_rows(values_only=True), [])
        wb.close()
        return [("" if c is None else str(c)).strip() for c in row]
    except Exception:
        return None


def _csv_header(p: Path):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
        try:
            line = p.read_text(encoding=enc).splitlines()[0] if p.stat().st_size else ""
            return [c.strip().strip('"') for c in line.split(",")]
        except (UnicodeDecodeError, IndexError):
            continue
    return None


def _dir_of_input_file(root: Path):
    """输入是单个文件:zip 直接指路给 zip2csv(返回 None 表示已处理完),其它文件按它所在目录检查。"""
    if root.suffix.lower() == ".zip":
        print("• 输入是 zip 压缩包 → 用 <assets>/zip2csv/scripts/zip2csv.py 转三表(它既接受 zip 也接受目录),转完把")
        print("  extracted/ 当原始目录再跑一次本检查拿 raw= 的正确层。")
        return None
    root_dir = root.parent
    print(f"• 输入是单个文件,按它所在目录检查:{root_dir}\n")
    return root_dir


def _report_flat_ne(root: Path, flat: list, usable: list, problems: list, suggest: list) -> None:
    """A. 平铺单网元结构(路径下直接是 CONFIG/CMD/…):报文件数、给 raw= 的传入方式、空目录记为问题。"""
    counts = "、".join(f"{k}/ {_txt_count(root / k)} 个文件" for k in flat)
    print(f"✓ 平铺单网元结构:{root.name} 下直接有 {counts}(网元名=目录名)")
    usable.append("raw")
    suggest.append(f'runner 的原始目录参数:--data "raw={root}"(平铺单网元,自动整形)')
    empty = [k for k in flat if _txt_count(root / k) == 0]
    if empty:
        problems.append(f"目录 {', '.join(empty)} 里没有任何 .txt/.log 文件——空目录判不出东西,确认采集是否齐")


def _report_ne_parents(root: Path, usable: list, problems: list, suggest: list) -> None:
    """A. 向下找网元层父目录并逐个报告(最多 3 个父目录、各列 8 个网元),提醒 raw= 要传的层级。"""
    parents = _find_ne_parents(root)
    for parent, nes in parents[:3]:
        print(f"✓ 原始采集结构:网元层父目录 = {parent}")
        for ne in nes[:8]:
            kd = _kind_dirs(ne)
            counts = "、".join(f"{k}/{_txt_count(ne / k)}文件" for k in kd)
            print(f"    网元「{ne.name}」:{counts}")
            if all(_txt_count(ne / k) == 0 for k in kd):
                problems.append(f"网元「{ne.name}」的数据目录全是空的——确认采集是否齐")
        if len(nes) > 8:
            print(f"    …(共 {len(nes)} 个网元)")
        usable.append("raw")
        suggest.append(f'runner 的原始目录参数:--data "raw={parent}"')
        if parent != root:
            print(f"    ⚠ 注意:raw= 要传**这一层**({parent.name}),不是你给的顶层——传错层脚本找不到网元")
    # 没有原始结构,往下看表


def _scan_tables(root: Path) -> tuple:
    """B/C. 扫描根目录下的 csv/xlsx/xlsm,按表头分成 数据表 [(文件名, 命中的指标)]、清单表、读不了表头的表。"""
    tables, manifests, unknown_x, generic = [], [], [], []
    for p in sorted(q for q in root.iterdir() if q.is_file() and q.suffix.lower() in _DATA_EXTS):
        if p.name.startswith("~$"):
            continue
        header = _csv_header(p) if p.suffix.lower() == ".csv" else _xlsx_header(p)
        if header is None:
            unknown_x.append(p.name)
            continue
        hs = set(header)
        if len(hs & _MANIFEST_SIGNS) >= 2:
            manifests.append(p.name)
            continue
        hit = [ind for ind, signs in _TABLE_SIGNS.items() if hs & signs]
        if hit:
            tables.append((p.name, hit))
            continue
        if any(sign in h for h in header for sign in _TREE_SIGNS):   # 故障树母表/调优表:不是数据
            continue
        cols = [h for h in header if h]
        if len(cols) >= 2 and not p.name.lower().endswith(".tuning.xlsx"):
            generic.append((p.name, cols))
    return tables, manifests, unknown_x, generic


def _report_tables(tables: list, manifests: list, unknown_x: list, usable: list, suggest: list,
                   generic: list = ()) -> None:
    """B/C. 打印表与清单的识别结果:数据表给传入方式,通用表按文件名对指标,清单点名不许传入,读不了的先跳过。"""
    if tables:
        print("✓ 已解析数据表:")
        for name, hit in tables:
            print(f"    {name} → 像 {'/'.join(hit)} 表(按表头判)")
        usable.append("tables")
        suggest.append("已解析表直接按指标名喂:--data \"<指标>=<表路径>\"(cmd/diag 两代名 runner 互认)")
    if generic:
        _report_generic(list(generic), usable, suggest)
    if manifests:
        print(f"⚠ 采集清单/索引表(不是数据,**禁止**当数据表喂):{', '.join(manifests)}")
        print("    (列是 fileType/relativePath/zipFile 这类——它只登记'数据都有哪些文件',真数据在它指的目录里)")
    if unknown_x:
        print(f"• 读不了表头的表文件(缺读取器,先跳过):{', '.join(unknown_x)}")


def _report_generic(generic: list, usable: list, suggest: list) -> None:
    """B 的补充:表头不是数通三表的 csv/xlsx(无线话统、CHR 等宽表)——按文件名对指标名给传入方式。"""
    inds = _plan_indicators()
    print("✓ 通用数据表(表头不是 cmd/log/config 三表,按文件名对指标名):")
    matched = []
    for name, cols in generic:
        stem = Path(name).stem
        ind = _match_indicator(stem, inds) if inds else None
        head = "、".join(cols[:6]) + ("…" if len(cols) > 6 else "")
        tail = f" → 对上指标「{ind}」" if ind else ("(文件名对不上本技能的任何指标:" + "/".join(inds) + ")" if inds else "")
        print(f"    {name}:列 {head}{tail}")
        if ind:
            matched.append((ind, name))
    usable.append("tables")
    if matched:
        suggest.append("通用表按对上的指标名传入:" + " ".join(f'--data "{i}=<目录>/{n}"' for i, n in matched))
    else:
        suggest.append("通用表按指标名传入:--data \"<指标>=<表路径>\"(指标名=assets/scripts/<指标>_analyzer.py 的前缀,文件名要对得上)")


def _conclude(root: Path, usable: list, problems: list, suggest: list) -> int:
    """收尾:列问题、给建议的传入方式(退出码 0);认不出形态时指路散文件补救或让用户确认(退出码 2)。"""
    print()
    if problems:
        print("发现问题:")
        for pr in problems:
            print(f"  ✗ {pr}")
    if usable:
        print("建议的传入方式(结构没问题,照抄):")
        for s in suggest:
            print(f"  → {s}")
        if "raw" in usable:
            print("  → 原始日志文件还要转成三张表供原有绑定使用:python <assets>/zip2csv/scripts/zip2csv.py <上面 raw= 的目录> -o <输出>")
        return 0
    print("✗ 认不出可用的数据形态:既没有 <网元>/CONFIG|CMD|LOG 结构,也没有任何可用的 csv/xlsx 数据表。")
    # 原始散文件(零散的 log/txt/嵌套 zip,连分类目录都没有)是可以自动补救的:zip2csv 会调内置
    # ifilter 归类后再转——这种情况指路,别一律停手问用户(rar 它不再解,用户要先手工解压)
    raw_loose = next((p for p in root.rglob("*") if p.is_file()
                      and p.suffix.lower() in (".txt", ".log", ".zip", ".rar")), None)
    if raw_loose:
        print("  目录里有散落的 log/txt/zip/rar → 疑似**原始散文件**:先跑"
              " python <assets>/zip2csv/scripts/zip2csv.py <本目录> -o <输出>(它自动调 ifilter 归类),")
        print("  再对它打印的 formated_result/ 路径重跑本检查;zip2csv 若报 ifilter 依赖装不上,把报错原样给用户。")
        return 2
    print("  把本报告原样给用户:请确认数据目录给对了没(常见错:给了外层目录/给了采集清单)。**不要强行继续**。")
    return 2


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("用法: python check_data.py <用户给的数据路径>")
        return 2
    root = Path(sys.argv[1]).expanduser()
    if not root.exists():
        print(f"✗ 路径不存在:{root}")
        return 2
    print(f"数据入口检查:{root}\n")
    usable, problems, suggest = [], [], []

    if root.is_file():
        root = _dir_of_input_file(root)
        if root is None:
            return 0

    # A. 原始采集结构
    flat = _kind_dirs(root)
    if flat:
        _report_flat_ne(root, flat, usable, problems, suggest)
    else:
        _report_ne_parents(root, usable, problems, suggest)

    # B/C. 表与清单
    tables, manifests, unknown_x, generic = _scan_tables(root)
    _report_tables(tables, manifests, unknown_x, usable, suggest, generic)

    return _conclude(root, usable, problems, suggest)


if __name__ == "__main__":
    sys.exit(main())
