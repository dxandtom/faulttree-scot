#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发行打包:把生成/优化前门 + 三条线(generate/migrate/adopt)+ zip2csv 合成一个自包含包。

用法(在仓库任意位置):
    python skills/cs-scot-generate-optimize/scripts/package_skill.py
    # 可选:--out <输出根目录>(默认 <仓库>/dist)  --no-zip(只出目录不打zip)

产出:
    dist/cs-scot-generate-optimize/          自包含技能目录(交付平台就是它)
    dist/cs-scot-generate-optimize.zip       同内容 zip(平台认 zip 就给这个)
    包内 MANIFEST.txt                        全部文件清单 + 来源提交

原则:纯复制,不改写任何源文件;关键文件逐一核验,缺一即响亮失败(exit 1)。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PKG_NAME = "cs-scot-generate-optimize"

# 收进包的源目录 → 包内位置(zip2csv 内嵌进 generate/,对齐生成线的查找次序①)
LAYOUT = {
    "cs-scot-generate-optimize": ".",
    "cs-scot-generate": "generate",
    "cs-scot-migrate": "migrate",
    "cs-scot-adopt": "adopt",
    "zip2csv": "generate/zip2csv",
}

# 打包后必须存在的关键文件(相对包根)——缺任何一个说明源目录不完整,响亮失败
REQUIRED = [
    "SKILL.md",
    "generate/SKILL.md",
    "generate/scripts/excel_to_tree_v2.py",
    "generate/scripts/assemble_scenario.py",
    "generate/scripts/condition_kernels.py",
    "generate/scripts/make_dispatch.py",
    "generate/scripts/export_tree_excel.py",
    "generate/scripts/import_tree_excel.py",
    "generate/zip2csv/scripts/zip2csv.py",
    "migrate/SKILL.md",
    "migrate/scripts/migrate_fault_check.py",
    "migrate/engine/import_tree_excel.py",
    "migrate/engine/check_contract.py",
    "migrate/engine/runner.py",
    "adopt/SKILL.md",
    "adopt/scripts/align_scripts.py",
    "adopt/scripts/merge_digests.py",
]

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


def _git_rev(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "(非 git 环境)"


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

    # 0. 源目录齐备检查
    missing_src = [name for name in LAYOUT if not (skills_dir / name).is_dir()]
    if missing_src:
        print(f"✗ 缺源技能目录:{', '.join(missing_src)}(应在 {skills_dir} 下)")
        return 1

    # 1. 清掉旧包重建(只动 dist 下自己的目录,不碰别的)
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    # 2. 纯复制
    for src_name, rel in LAYOUT.items():
        src = skills_dir / src_name
        dst = pkg_dir if rel == "." else pkg_dir / rel
        if rel == ".":
            # 前门内容平铺到包根(不能整目录 copytree,目标已存在)
            for item in sorted(src.iterdir()):
                if item.name in EXCLUDE_DIRS or item.name in EXCLUDE_NAMES:
                    continue
                if item.is_dir():
                    shutil.copytree(item, dst / item.name, ignore=_ignore(src, rel))
                elif item.suffix not in EXCLUDE_SUFFIX and \
                        item.relative_to(src).as_posix() not in EXCLUDE_REL:
                    shutil.copy2(item, dst / item.name)
            # 平铺后单独清一层:scripts/ 里只有打包器时会剩个空目录,删掉
            sdir = dst / "scripts"
            if sdir.is_dir() and not any(sdir.iterdir()):
                sdir.rmdir()
        else:
            shutil.copytree(src, dst, ignore=_ignore(src, rel))

    # 3. 关键文件核验(缺一即失败)
    missing = [r for r in REQUIRED if not (pkg_dir / r).is_file()]
    if missing:
        print("✗ 打包不完整,缺关键文件:")
        for m in missing:
            print(f"    {m}")
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

    # 5. zip(按路径排序写入,内容稳定)
    zip_path = None
    if not args.no_zip:
        zip_path = out_root / f"{PKG_NAME}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(pkg_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, f"{PKG_NAME}/{p.relative_to(pkg_dir).as_posix()}")
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]

    # 6. 汇报
    print(f"✓ 包目录: {pkg_dir}")
    print(f"  文件 {len(files)+1} 个,{total/1024:.0f} KB;关键文件 {len(REQUIRED)}/{len(REQUIRED)} 核验通过")
    if zip_path:
        print(f"✓ zip: {zip_path}  (sha256 前16位 {digest})")
    print("  交付平台:给整个包目录,或给 zip(平台认哪种给哪种)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
