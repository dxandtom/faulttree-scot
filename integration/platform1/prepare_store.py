#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把批量下载的产物整理成 fake 货架:合集 zip 拆开取节点 zip,平铺到货架目录;对表核缺件。

背景:平台下载服务多条一批时返回一个合集 zip(里面是各节点 zip),单条返回文件本身;
分批下载后目录里是若干 part 合集包 + 可能的散节点 zip。本工具把它们统一平铺:
  - 合集 zip(内部含 *.zip)→ 抽出里面的每个 zip,按文件名落到货架;
  - 散的节点 zip(内部无 *.zip)→ 原样拷入货架。
给了 --excel 就按表对账:每个 Product_Name 在货架里有没有,缺的逐个点名。

用法:
  python integration/platform1/prepare_store.py --downloads <下载目录> --store <货架目录> \
      [--excel <平台导出表>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _looks_like_node_pkg(d: Path) -> bool:
    """tester 交付包被解压软件解开后的形态:顶层有 判断方法*.md / fault_check*.py /
    测试数据目录 之一(skill 产物目录顶层没有这些,不会误伤)。"""
    return (any(d.glob("判断方法*.md")) or any(d.glob("fault_check*.py"))
            or (d / "测试数据").is_dir())


def spread(downloads: Path, store: Path) -> int:
    store.mkdir(parents=True, exist_ok=True)
    n = 0
    # 1) 递归收所有 zip(嵌套多深都认):合集拆平铺,散节点包原样拷入
    for zp in sorted(list(downloads.rglob("*.zip"))):
        if zp.resolve() == (store / zp.name).resolve():
            continue                                     # 货架自身当输入目录时跳过已就位的文件
        with zipfile.ZipFile(zp) as zf:
            inner = [i for i in zf.namelist()
                     if i.lower().endswith(".zip") and not i.endswith("/")]
            if inner:
                print(f"[合集] {zp.name}:{len(inner)} 个节点包")
                for item in inner:
                    fname = Path(item).name
                    dst = store / fname
                    if dst.exists():
                        print(f"  [覆盖] {fname}(货架里已有同名,以后解的为准)")
                    with zf.open(item) as src, dst.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    n += 1
            else:
                print(f"[节点包] {zp.name}:原样入货架")
                shutil.copy2(zp, store / zp.name)
                n += 1
    # 2) 节点包被解压软件解成文件夹的:重新压成 zip 入货架(包内套一层同名目录,交付包口径)
    for d in sorted(list(p for p in downloads.rglob("*") if p.is_dir())):
        if d.resolve() == store.resolve() or not _looks_like_node_pkg(d):
            continue
        out = store / (d.name + ".zip")
        if out.exists():
            print(f"[跳过] {d.name}(货架里已有 {out.name})")
            continue
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    zf.write(p, f"{d.name}/{p.relative_to(d).as_posix()}")
        print(f"[压包] 文件夹 {d.name} → {out.name}(节点包被解开过,重新打包)")
        n += 1
    return n


def audit(excel: Path, store: Path) -> int:
    from driver import parse_platform_excel
    rows = parse_platform_excel(excel)
    missing = []
    for names, name, _obs in rows:
        fname = name if name.lower().endswith(".zip") else name + ".zip"
        if not (store / fname).exists():
            missing.append(("/".join(names), fname))
    print(f"\n[对账] 表里 {len(rows)} 个带包节点,货架缺 {len(missing)} 个:")
    for path, fname in missing:
        print(f"  缺 {fname}  ← {path}")
    if not missing:
        print("  一个不缺,货架齐了")
    return len(missing)


def main():
    ap = argparse.ArgumentParser(description="下载产物 → fake 货架(拆合集平铺 + 对表核缺件)")
    ap.add_argument("--downloads", required=True, help="批量下载脚本的输出目录")
    ap.add_argument("--store", required=True, help="货架目录(driver --fake-store 用)")
    ap.add_argument("--excel", help="平台导出表:给了就对账点名缺件")
    a = ap.parse_args()
    n = spread(Path(a.downloads), Path(a.store))
    print(f"\n[货架] 共落 {n} 个文件 → {a.store}")
    if a.excel:
        miss = audit(Path(a.excel), Path(a.store))
        sys.exit(2 if miss else 0)


if __name__ == "__main__":
    main()
