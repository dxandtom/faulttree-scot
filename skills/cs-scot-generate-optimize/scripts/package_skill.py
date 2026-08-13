#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发行打包:把生成/优化前门 + 两条线(generate/migrate)+ 打包素材(execute/rca/zip2csv,
内嵌 generate/ 下)合成一个自包含包。

用法(在仓库任意位置):
    python skills/cs-scot-generate-optimize/scripts/package_skill.py
    # 可选:--out <输出根目录>(默认 <仓库>/dist)  --no-zip(只出目录不打zip)

产出:
    dist/cs-scot-generate-optimize/          自包含技能目录(交付平台就是它)
    dist/cs-scot-generate-optimize.zip       同内容 zip(平台认 zip 就给这个;同一提交可复现)
    包内 MANIFEST.txt                        全部文件清单 + 来源提交(带 +dirty 标记)

原则:纯复制,不改写任何源文件;输出目录与任何源目录重叠即拒绝执行;
关键文件逐一核验,缺一响亮失败并清掉残局(不留半个包)。
打包素材(cs-scot-execute / fault-tree-rca / zip2csv)内嵌在 generate/ 下——
装了这个包,生成线直出的场景技能就是完整可执行成品,不再有"止于产物形态"的边界。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PKG_NAME = "cs-scot-generate-optimize"

# 收进包的源目录 → 包内位置。打包素材(execute/rca/zip2csv)统一内嵌进 generate/ 下
# (对齐 generate 的素材查找次序①"技能内嵌";migrate 的 _mat 查找也带 generate/ 后备,
#  不再需要包根重复放一份)——装了这个包,生成线直出的场景技能就是完整可执行成品。
LAYOUT = {
    "cs-scot-generate-optimize": ["."],
    "cs-scot-generate": ["generate"],
    "cs-scot-migrate": ["migrate"],
    "zip2csv": ["generate/zip2csv"],
    "cs-scot-execute": ["generate/cs-scot-execute"],
    "fault-tree-rca": ["generate/fault-tree-rca"],
}

# 打包后必须存在的关键文件(相对包根)——各线手册点名必用件全收,防"半个 checkout"打出静默残包
_GEN_SCRIPTS = [
    "analyzer.py", "assemble_scenario.py", "check_tree_coverage.py", "condition_kernels.py",
    "custom_lib.py", "excel_to_tree.py", "excel_to_tree_v2.py", "export_tree_excel.py",
    "import_tree_excel.py", "kernel_selftest.py", "make_dispatch.py", "merge_conditions.py",
    "mo_config.py", "package_scenario.py", "validate_conditions.py", "xlsx_reader.py",
]
_GEN_REFS = ["condition_binding_schema.md", "datacom_log_adapter.md", "plan_schema.md"]
_MIG_ENGINE = [
    "assemble_scenario.py", "check_contract.py", "check_data.py", "condition_kernels.py",
    "custom_lib.py", "export_tree_excel.py", "import_tree_excel.py", "runner.py",
    "search_cmd.py", "search_config.py", "search_diag_txt.py", "search_log.py", "xlsx_reader.py",
]
_Z2C = ["zip2csv.py", "config2csv.py", "logbuffer2csv.py", "parse_diag_to_csv.py"]

REQUIRED = (
    [
        "SKILL.md",
        "generate/SKILL.md", "generate/knowledge.md",
        "migrate/SKILL.md", "migrate/scripts/migrate_fault_check.py", "migrate/scripts/sync_engine.py",
    ]
    + [f"generate/scripts/{n}" for n in _GEN_SCRIPTS]
    + [f"generate/references/{n}" for n in _GEN_REFS]
    + [f"migrate/engine/{n}" for n in _MIG_ENGINE]
    + [f"generate/zip2csv/{s}" for s in ["SKILL.md"] + [f"scripts/{n}" for n in _Z2C]]
    + [f"generate/cs-scot-execute/{s}" for s in
       ("SKILL.md", "scripts/runner.py", "scripts/check_data.py")]
    + [f"generate/fault-tree-rca/{s}" for s in
       ("SKILL.md", "references/rca_rules.md",
        "scripts/draft_rca.py", "scripts/finalize_rca.py", "scripts/render_report.py",
        "scripts/validate_rca.py", "scripts/rca_query.py")]
)

# 复制时排除(缓存/系统噪音/打包器自身产物)
EXCLUDE_DIRS = {"__pycache__", ".git", "dist"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}
# 前门自己的打包脚本不进发行包(平台侧用不上,也别让包里再套打包器)
EXCLUDE_REL = {"scripts/package_skill.py"}


def _ignore(src_root: Path, pkg_rel_root: str):
    def ignore(dirpath, names):
        skip = set()
        for n in names:
            p = Path(dirpath) / n
            rel_in_src = p.relative_to(src_root).as_posix()
            if n in EXCLUDE_DIRS and p.is_dir():
                skip.add(n)
            elif n in EXCLUDE_NAMES or p.suffix in EXCLUDE_SUFFIX:
                skip.add(n)
            elif pkg_rel_root == "." and rel_in_src in EXCLUDE_REL:
                skip.add(n)
        return skip
    return ignore


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _git_rev(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return "(非 git 环境)"
        rev = out.stdout.strip()
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if st.returncode == 0 and st.stdout.strip():
            rev += "+dirty"                              # 包内容与铭牌提交不完全对应,明说
        return rev
    except Exception:
        return "(非 git 环境)"


def _zip_datetime(repo: Path):
    """zip 条目统一用来源提交时间(同一提交两次打包 → 字节级一致的 zip)。"""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y,%m,%d,%H,%M,%S"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            parts = tuple(int(x) for x in out.stdout.strip().split(","))
            if len(parts) == 6 and parts[0] >= 1980:
                return parts
    except Exception:
        pass
    return (2020, 1, 1, 0, 0, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/优化前门发行打包")
    ap.add_argument("--out", default=None, help="输出根目录(默认 <仓库>/dist)")
    ap.add_argument("--no-zip", action="store_true", help="只出目录,不打 zip")
    args = ap.parse_args()

    here = Path(__file__).resolve()
    skills_dir = here.parent.parent.parent          # .../skills
    repo = skills_dir.parent
    out_root = Path(args.out).resolve() if args.out else repo / "dist"
    pkg_dir = out_root / PKG_NAME

    # 0a. 源目录齐备检查
    missing_src = [name for name in LAYOUT if not (skills_dir / name).is_dir()]
    if missing_src:
        print(f"✗ 缺源技能目录:{', '.join(missing_src)}(应在 {skills_dir} 下)")
        return 1

    # 0b. 重叠防护:输出目录与任何源目录相同/互为父子 → 拒绝(rmtree 会毁源文件)
    for name in LAYOUT:
        src = (skills_dir / name).resolve()
        if _overlaps(pkg_dir.resolve(), src):
            print(f"✗ 输出目录与源目录重叠:{pkg_dir} ↔ {src}——拒绝执行(会毁源文件),换个 --out")
            return 2

    # 1. 清掉旧包重建——只删自己上次打的包(有 MANIFEST.txt 才认),别的目录一概不动
    if pkg_dir.exists():
        if not (pkg_dir / "MANIFEST.txt").is_file():
            print(f"✗ 目标已存在且不是本工具打的包(无 MANIFEST.txt):{pkg_dir}——拒删,挪走或换 --out")
            return 2
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    # 2. 纯复制
    for src_name, dests in LAYOUT.items():
        src = skills_dir / src_name
        for rel in dests:
            dst = pkg_dir if rel == "." else pkg_dir / rel
            if rel == ".":
                # 前门内容平铺到包根(目标已存在,不能整目录 copytree)
                for item in sorted(src.iterdir()):
                    if item.name in EXCLUDE_DIRS or item.name in EXCLUDE_NAMES:
                        continue
                    if item.is_dir():
                        shutil.copytree(item, dst / item.name, ignore=_ignore(src, rel))
                    elif item.suffix not in EXCLUDE_SUFFIX and \
                            item.relative_to(src).as_posix() not in EXCLUDE_REL:
                        shutil.copy2(item, dst / item.name)
                # 平铺后清一层:scripts/ 里只有打包器时会剩空目录,删掉
                sdir = dst / "scripts"
                if sdir.is_dir() and not any(sdir.iterdir()):
                    sdir.rmdir()
            else:
                shutil.copytree(src, dst, ignore=_ignore(src, rel))

    # 3. 关键文件核验——缺一即失败,并清掉残局(不完整目录 + 旧 zip),不留半个包
    missing = [r for r in REQUIRED if not (pkg_dir / r).is_file()]
    if missing:
        print("✗ 打包不完整,缺关键文件:")
        for m in missing:
            print(f"    {m}")
        shutil.rmtree(pkg_dir, ignore_errors=True)
        stale_zip = out_root / f"{PKG_NAME}.zip"
        if stale_zip.exists():
            stale_zip.unlink()
        print("  已清理不完整包目录与旧 zip(避免残局被误当成品)。")
        return 1

    # 4. MANIFEST(全部文件 + 尺寸 + 来源提交)
    files = sorted(p for p in pkg_dir.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    lines = [
        f"# {PKG_NAME} 发行包清单",
        f"# 来源提交: {_git_rev(repo)}",
        f"# 文件数: {len(files)+1}  总大小: {total} 字节(不含本清单)",
        "",
    ]
    lines += [f"{p.stat().st_size:>10}  {p.relative_to(pkg_dir).as_posix()}" for p in files]
    (pkg_dir / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 5. zip:条目按路径排序、时间戳统一用来源提交时间 → 同一提交两次打包字节一致
    zip_path = digest = None
    if not args.no_zip:
        zip_path = out_root / f"{PKG_NAME}.zip"
        if zip_path.exists():
            zip_path.unlink()
        dt = _zip_datetime(repo)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(pkg_dir.rglob("*")):
                if p.is_file():
                    zi = zipfile.ZipInfo(
                        f"{PKG_NAME}/{p.relative_to(pkg_dir).as_posix()}", date_time=dt)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = 0o644 << 16
                    zf.writestr(zi, p.read_bytes())
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]

    # 6. 汇报
    print(f"✓ 包目录: {pkg_dir}")
    print(f"  文件 {len(files)+1} 个,{total/1024:.0f} KB;关键文件 {len(REQUIRED)}/{len(REQUIRED)} 核验通过")
    if zip_path:
        print(f"✓ zip: {zip_path}  (sha256 前16位 {digest};同一提交重打可复现)")
    print("  交付平台:给整个包目录,或给 zip(平台认哪种给哪种)。")
    print("  素材(execute/fault-tree-rca/zip2csv)已内嵌 generate/——生成线直出的技能即完整可执行成品。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
