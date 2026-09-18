#!/usr/bin/env python3
"""数据入口检查:诊断前先看用户给的路径里到底是什么,结构对不对、该怎么喂。零三方依赖。

为什么要它(实测两起):①用户给了顶层目录,网元层其实在 <顶层>/formated_result 下——raw= 传错层,
S 脚本一个网元都找不到,全程"数据不足";②目录里躺着一张采集清单 xlsx(列是 neName/fileType/
relativePath),弱模型看见"neName"就当数据表喂,42 个判据集体报"找不到列"。这类错越早暴露越好:
诊断跑完才发现,浪费一整轮。

用法:python check_data.py <用户给的数据路径>
  退出码 0=认得出可用形态(报告里带**建议喂法**,照抄即可);2=结构不认识/只有清单——
  把报告原样给用户,让用户改目录或确认,**不要强行继续**。

识别的形态(可并存,逐个报):
  A. 原始采集目录:<网元层父目录>/<网元>/CONFIG|CMD|LOG|DIAG/*.txt(raw= 必须传网元层父目录);
     平铺单网元(路径下直接是 CONFIG/CMD/…)也认,网元名=目录名;
  B. 已解析表:csv/xlsx,表头带 CmdName/ObjContent(cmd)、Summary/Log(log)、level1/CfgType(config);
  C. 采集清单/索引 xlsx(fileType/relativePath/zipFile 这类列)——**是目录不是数据**,点名跳过;
  D. zip 压缩包——直接交给 zip2csv(它既接受 zip 也接受目录)。
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
    print(f"数据入口体检:{root}\n")
    usable, problems, suggest = [], [], []

    if root.is_file():
        if root.suffix.lower() == ".zip":
            print("• 输入是 zip 压缩包 → 直接交给 zip2csv 转三表(它吃 zip 也吃目录),转完把")
            print("  extracted/ 当原始目录再跑一次本体检拿 raw= 的正确层。")
            return 0
        root_dir = root.parent
        print(f"• 输入是单个文件,按它所在目录体检:{root_dir}\n")
        root = root_dir

    # A. 原始采集结构
    flat = _kind_dirs(root)
    if flat:
        counts = "、".join(f"{k}/ {_txt_count(root / k)} 个文件" for k in flat)
        print(f"✓ 平铺单网元结构:{root.name} 下直接有 {counts}(网元名=目录名)")
        usable.append("raw")
        suggest.append(f'runner 的原始目录参数:--data "raw={root}"(平铺单网元,自动整形)')
        empty = [k for k in flat if _txt_count(root / k) == 0]
        if empty:
            problems.append(f"目录 {', '.join(empty)} 里没有任何 .txt/.log 文件——空目录判不出东西,确认采集是否齐")
    else:
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
        if not parents and not flat:
            pass                                          # 没有原始结构,往下看表

    # B/C. 表与清单
    tables, manifests, unknown_x = [], [], []
    for p in sorted(list(root.glob("*.csv")) + list(root.glob("*.xlsx")) + list(root.glob("*.xlsm"))):
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
    if tables:
        print("✓ 已解析数据表:")
        for name, hit in tables:
            print(f"    {name} → 像 {'/'.join(hit)} 表(按表头判)")
        usable.append("tables")
        suggest.append("已解析表直接按指标名喂:--data \"<指标>=<表路径>\"(cmd/diag 两代名 runner 互认)")
    if manifests:
        print(f"⚠ 采集清单/索引表(不是数据,**禁止**当数据表喂):{', '.join(manifests)}")
        print("    (列是 fileType/relativePath/zipFile 这类——它只登记'数据都有哪些文件',真数据在它指的目录里)")
    if unknown_x:
        print(f"• 读不了表头的表文件(缺读取器,先跳过):{', '.join(unknown_x)}")

    print()
    if problems:
        print("发现问题:")
        for pr in problems:
            print(f"  ✗ {pr}")
    if usable:
        print("建议喂法(结构没问题,照抄):")
        for s in suggest:
            print(f"  → {s}")
        if "raw" in usable:
            print("  → 裸 txt 还要转三表喂原有绑定:python <assets>/zip2csv/scripts/zip2csv.py <上面 raw= 的目录> -o <输出>")
        return 0
    print("✗ 认不出可用的数据形态:既没有 <网元>/CONFIG|CMD|LOG 结构,也没有表头能对上号的数据表。")
    # 原始散文件(零散的 log/txt/rar/嵌套 zip,连分类目录都没有)是可以自动补救的:zip2csv 会调内置
    # ifilter 归类后再转——这种情况指路,别一律停手问用户
    raw_loose = next((p for p in root.rglob("*") if p.is_file()
                      and p.suffix.lower() in (".txt", ".log", ".zip", ".rar")), None)
    if raw_loose:
        print("  目录里有散落的 log/txt/zip/rar → 疑似**原始散包**:先跑"
              " python <assets>/zip2csv/scripts/zip2csv.py <本目录> -o <输出>(它自动调 ifilter 归类),")
        print("  再对它打印的 formated_result/ 路径重跑本体检;zip2csv 若报 ifilter 依赖装不上,把报错原样给用户。")
        return 2
    print("  把本报告原样给用户:请确认数据目录给对了没(常见错:给了外层目录/给了采集清单)。**不许硬闯**。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
