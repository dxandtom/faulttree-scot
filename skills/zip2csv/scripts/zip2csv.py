"""
ZIP 解压 + 自动转换流水线

功能：
1. 解压 zip 包到本地目录
2. 递归扫描 LOG/CONFIG/DIAG 目录（支持嵌套如 Switch01/DIAG/）
3. 遍历 LOG/ 目录下所有 .txt/.log 文件，调用 logbuffer2csv.convert_file 转换为 CSV
4. 遍历 CONFIG/ 目录下所有 .txt/.log 文件，调用 config2csv.parse_config_to_csv 转换为 CSV
5. 遍历 DIAG/ 目录下所有 .txt/.log 文件，调用 parse_diag_to_csv.parse_diag_to_csv 转换为 CSV
6. 基于 DIAG 输出的 CSV，在 CMD/ 目录下生成新的 CSV：
   - NEName  = 原 NEName 列值
   - CmdName = 原 CmdName 列值
   - OriginCmd = 原 CmdName 列值
   - CmdResult = 原 ObjContent 列值
7. 将所有 CSV 文件（保留 NE/LOG/CONFIG/DIAG/CMD 目录结构）打包为 ZIP

输出结构：
    <output_dir>/csv/<NEName>/LOG/*.csv
    <output_dir>/csv/<NEName>/CONFIG/*.csv
    <output_dir>/csv/<NEName>/DIAG/*.csv
    <output_dir>/csv/<NEName>/CMD/*.csv
    <output_dir>/<zip_basename>_csv.zip

进料三形态自动识别(数据解析全链路 1→2→3):
    ①原始散包(单个/一堆 log/txt、rar、混杂 zip,**没有** LOG/CONFIG/DIAG/CMD 分类目录)
      → 先调 vendor/ifilter.py 归类出 formated_result/<网元>/<CMD|CONFIG|LOG>/ 再走②;
    ②分类目录(网元/LOG|CONFIG|DIAG|CMD 下裸 txt)→ 本脚本常规转换;
    ③已是 csv → 不需要本脚本,直接喂绑定/runner。
ifilter 是同事维护的原样引入件(vendor,不改),依赖三方件(rarfile/chardet/olefile/
oletools/pandas)且首跑会自动 pip 安装——装不上时①走不了,②③不受影响。

用法：
    python zip2csv.py <zip包或原始数据目录> [output_dir]     # 目录直接给,无需先压成 zip
    python zip2csv.py <zip包或原始数据目录> -o <output_dir>
    python zip2csv.py <单个原始log/txt或rar>                 # 散文件也直接吃(自动走 ifilter 归类)
"""

import argparse
import shutil
import subprocess
import sys
import os
import zipfile
import csv
import glob
import datetime

# 将当前脚本所在目录加入 sys.path，确保能 import 同目录模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from logbuffer2csv import convert_file as logbuffer_convert
from config2csv import parse_config_to_csv as config_convert
from parse_diag_to_csv import parse_diag_to_csv as diag_convert


VENDOR_IFILTER = os.path.join(os.path.dirname(SCRIPT_DIR), 'vendor', 'ifilter.py')


def run_ifilter(raw_dir):
    """原始散包形态(没有分类目录)→ 调 vendor/ifilter.py 把散 log/txt/嵌套包归类成
    formated_result/<网元>/<CMD|CONFIG|LOG>/。成功返回 formated_result 路径,失败返回 None
    (响亮报原因,绝不静默转出 0 个文件)。ifilter 会清掉 raw_dir 下它自己的旧产物
    (formated_result*/_merged_temp_*),这是它的既有约定。"""
    if not os.path.isfile(VENDOR_IFILTER):
        print("  [err] 找不到 vendor/ifilter.py——原始散包解析不了。"
              "请提供分类目录形态(网元/CMD|CONFIG|LOG 下裸 txt)或已转好的 csv")
        return None
    print(f"[2b/7] 调用 ifilter 归类原始散包: {raw_dir}")
    print("  (ifilter 依赖三方件 rarfile/chardet/olefile/oletools/pandas,首跑会自动 pip 安装)")
    try:
        r = subprocess.run([sys.executable, VENDOR_IFILTER, raw_dir])
    except Exception as e:  # noqa: BLE001
        print(f"  [err] ifilter 启动失败: {e}")
        return None
    formated = os.path.join(raw_dir, 'formated_result')
    if r.returncode != 0 or not os.path.isdir(formated):
        print(f"  [err] ifilter 未产出归类结果(退出码 {r.returncode})。常见原因:三方件装不上"
              "(受控环境禁 pip)。可在能装依赖的机器上运行"
              " skills/zip2csv/vendor/ifilter.py <数据目录> 得到 formated_result/ 后再喂本脚本")
        return None
    return formated


def find_data_dirs(extract_dir):
    """
    在解压目录中递归查找所有包含 LOG/CONFIG/DIAG 子目录的路径。
    支持 ZIP 内有多层嵌套，如 Switch01/DIAG/ 或直接 DIAG/。
    返回 dict: {'LOG': [path1, ...], 'CONFIG': [path1, ...], 'DIAG': [path1, ...]}
    """
    result = {'LOG': [], 'CONFIG': [], 'DIAG': [], 'CMD': []}   # 命名迁移:DIAG 更名 CMD(两名并存兼容:新包 CMD、老包 DIAG 都认,同款回显解析)
    for dirpath, dirnames, filenames in os.walk(extract_dir):
        dirname = os.path.basename(dirpath).upper()
        if dirname in result:
            result[dirname].append(dirpath)
    return result


DEFAULT_NE = 'NE'   # 平铺形态(数据类型目录直接在根下,没有网元层)的兜底网元名;目录输入时用目录名


_CATEGORY_NAMES = {'LOG', 'CONFIG', 'DIAG', 'CMD'}


def collect_data_files(dir_path):
    """数据目录里的原始文本文件:**递归收**(现场常按日期子目录存放,只扫一层会整批静默漏掉,
    实测事故),.txt/.log 都认、大小写不敏(Linux 的 glob 区分大小写,.TXT 会漏)。
    返回 [(绝对路径, 输出基名)]:子目录文件的基名带子路径前缀(2026-08-11/x.log → 2026-08-11_x),
    防不同子目录同名文件互相覆盖;不下钻同名类别子目录(LOG 里再套 CMD 属另一类别,
    由 find_data_dirs 单独发现,这里收了会双算)。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(dir_path):
        dirnames[:] = sorted(d for d in dirnames if d.upper() not in _CATEGORY_NAMES)
        for f in filenames:
            if os.path.splitext(f)[1].lower() not in ('.txt', '.log'):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), dir_path)
            parts = rel.replace('\\', '/').split('/')
            base = '_'.join(parts[:-1] + [os.path.splitext(parts[-1])[0]])
            out.append((os.path.join(dirpath, f), base))
    return sorted(out)


def uniq_csv(ne_output_dir, base, used, label):
    """输出 csv 落点:同名已占则 _2/_3 顺延并响亮播报(多外层同名网元时,静默覆盖=丢整台设备
    的数据,实测事故)。used 为本次运行内已占路径集合(跨源目录共享);重跑覆盖旧产物照旧。"""
    csv_file = os.path.join(ne_output_dir, base + '.csv')
    n = 2
    while csv_file in used:
        csv_file = os.path.join(ne_output_dir, f"{base}_{n}.csv")
        n += 1
    if n > 2:
        print(f"  ⚠ 同名防覆盖: {label}/{base}.csv 已被占用(多外层同名网元?)"
              f" -> 改存 {os.path.basename(csv_file)}")
    used.add(csv_file)
    return csv_file


def get_ne_name(data_dir, extract_dir):
    """
    从数据目录路径中提取 NE 名称。
    例如 extract_dir/extracted/Switch01/DIAG -> Switch01
    如果没有中间层，返回 DEFAULT_NE。
    """
    rel = os.path.relpath(data_dir, extract_dir)
    parts = rel.replace('\\', '/').split('/')
    if len(parts) >= 2:
        return parts[-2]
    return DEFAULT_NE


def unzip_file(zip_path, extract_dir):
    """解压 zip 文件到指定目录"""
    print(f"[1/7] 解压 {zip_path} -> {extract_dir}")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_dir)
    print(f"  解压完成，共 {len(zf.namelist())} 个文件")
    return extract_dir


def process_log_dirs(log_dirs, csv_dir, extract_dir):
    """遍历所有 LOG/ 目录下 .txt 文件，调用 logbuffer2csv 转换，保留 NE 目录结构"""
    if not log_dirs:
        print("[3/7] LOG/ 目录不存在，跳过")
        return

    print(f"[3/7] 处理 LOG/ 目录 (共 {len(log_dirs)} 个)")
    total = 0
    used = set()

    for log_dir in log_dirs:
        ne_name = get_ne_name(log_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'LOG')
        os.makedirs(ne_output_dir, exist_ok=True)

        for txt_file, base in collect_data_files(log_dir):
            csv_file = uniq_csv(ne_output_dir, base, used, f"{ne_name}/LOG")
            print(f"  转换: {ne_name}/LOG/{os.path.basename(txt_file)} -> {os.path.basename(csv_file)}")
            logbuffer_convert(txt_file, csv_file)
            total += 1

    print(f"  共转换 {total} 个 LOG 文件")


def process_config_dirs(config_dirs, csv_dir, extract_dir):
    """遍历所有 CONFIG/ 目录下 .txt 文件，调用 config2csv 转换，保留 NE 目录结构"""
    if not config_dirs:
        print("[4/7] CONFIG/ 目录不存在，跳过")
        return

    print(f"[4/7] 处理 CONFIG/ 目录 (共 {len(config_dirs)} 个)")
    total = 0
    used = set()

    for config_dir in config_dirs:
        ne_name = get_ne_name(config_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'CONFIG')
        os.makedirs(ne_output_dir, exist_ok=True)

        for txt_file, base in collect_data_files(config_dir):
            csv_file = uniq_csv(ne_output_dir, base, used, f"{ne_name}/CONFIG")
            print(f"  转换: {ne_name}/CONFIG/{os.path.basename(txt_file)} -> {os.path.basename(csv_file)}")
            config_convert(txt_file, csv_file)
            total += 1

    print(f"  共转换 {total} 个 CONFIG 文件")


def process_diag_dirs(diag_dirs, csv_dir, extract_dir):
    """遍历所有 DIAG/ 目录下 .txt 文件，调用 parse_diag_to_csv 转换，保留 NE 目录结构"""
    if not diag_dirs:
        print("[5/7] DIAG/ 目录不存在，跳过")
        return []

    print(f"[5/7] 处理 DIAG/ 目录 (共 {len(diag_dirs)} 个)")
    output_csvs = []
    used = set()

    for diag_dir in diag_dirs:
        ne_name = get_ne_name(diag_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'DIAG')
        os.makedirs(ne_output_dir, exist_ok=True)

        for txt_file, base in collect_data_files(diag_dir):
            csv_file = uniq_csv(ne_output_dir, base, used, f"{ne_name}/DIAG")
            print(f"  转换: {ne_name}/DIAG/{os.path.basename(txt_file)} -> {os.path.basename(csv_file)}")
            diag_convert(txt_file, csv_file)
            output_csvs.append(csv_file)

    if not output_csvs:
        print("  未找到 .txt 文件，跳过")
    else:
        print(f"  共转换 {len(output_csvs)} 个 DIAG 文件")

    return output_csvs


def process_cmd_dirs(cmd_dirs, csv_dir, extract_dir):
    """遍历所有 CMD/ 目录下 .txt 文件(DIAG 更名 CMD 后的命令回显,解析与 DIAG 同款),
    调用 parse_diag_to_csv 转换,输出到 csv/<NE>/CMD/(列 NEName/CmdName/ObjContent;
    下游内核认 ObjContent↔CmdResult 同义,直接当 cmd 表喂)。"""
    if not cmd_dirs:
        print("[5b/7] CMD/ 目录不存在,跳过")
        return []

    print(f"[5b/7] 处理 CMD/ 目录 (共 {len(cmd_dirs)} 个)")
    output_csvs = []
    used = set()

    for cmd_dir in cmd_dirs:
        ne_name = get_ne_name(cmd_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'CMD')
        os.makedirs(ne_output_dir, exist_ok=True)

        for txt_file, base in collect_data_files(cmd_dir):
            csv_file = uniq_csv(ne_output_dir, base, used, f"{ne_name}/CMD")
            print(f"  转换: {ne_name}/CMD/{os.path.basename(txt_file)} -> {os.path.basename(csv_file)}")
            diag_convert(txt_file, csv_file)
            output_csvs.append(csv_file)

    print(f"  共转换 {len(output_csvs)} 个 CMD 文件")
    return output_csvs


def generate_cmd_csv(diag_csvs, csv_dir):
    """
    基于 DIAG 输出的 CSV，在 CMD/ 目录下生成新 CSV（保留 NE 目录结构）。
    新 CSV 列：NEName, CmdName, OriginCmd, CmdResult
    """
    if not diag_csvs:
        print("[6/7] 无 DIAG CSV 输入，跳过 CMD 生成")
        return

    print(f"[6/7] 生成 CMD/ CSV (共 {len(diag_csvs)} 个 DIAG CSV)")
    csv.field_size_limit(10 * 1024 * 1024)
    cmd_headers = ['NEName', 'CmdName', 'OriginCmd', 'CmdResult']

    for diag_csv_path in diag_csvs:
        # 从路径提取 NE 名称: csv_dir/<NEName>/DIAG/xxx.csv
        rel = os.path.relpath(diag_csv_path, csv_dir)
        parts = rel.replace('\\', '/').split('/')
        ne_name = parts[0] if len(parts) >= 2 else 'NE'

        cmd_output_dir = os.path.join(csv_dir, ne_name, 'CMD')
        os.makedirs(cmd_output_dir, exist_ok=True)

        basename = os.path.splitext(os.path.basename(diag_csv_path))[0]
        cmd_csv_path = os.path.join(cmd_output_dir, basename + '.csv')
        if os.path.exists(cmd_csv_path):                 # 混代输入(DIAG+CMD 并存):真 CMD 转换优先,
            print(f"  跳过: {ne_name}/CMD/{basename}.csv 已有真实 CMD 转换,"
                  f"不用 DIAG 派生视图覆盖")             # 派生视图让位(覆盖会连列形状一起换掉)
            continue

        rows_written = 0
        with open(diag_csv_path, 'r', encoding='utf-8-sig', newline='') as fin, \
             open(cmd_csv_path, 'w', encoding='utf-8-sig', newline='') as fout:
            reader = csv.DictReader(fin)
            writer = csv.DictWriter(fout, fieldnames=cmd_headers, quoting=csv.QUOTE_ALL)
            writer.writeheader()

            for row in reader:
                writer.writerow({
                    'NEName': row.get('NEName', ''),
                    'CmdName': row.get('CmdName', ''),
                    'OriginCmd': row.get('CmdName', ''),
                    'CmdResult': row.get('ObjContent', ''),
                })
                rows_written += 1

        print(f"  生成: {ne_name}/CMD/{basename}.csv ({rows_written} 条记录)")


def pack_csv_to_zip(csv_dir, output_dir, zip_basename):
    """将 csv_dir 下的所有 CSV 文件打包为 ZIP，保留目录结构"""
    print(f"[7/7] 打包 CSV -> ZIP")

    zip_filename = f"{zip_basename}_csv.zip"
    zip_path = os.path.join(output_dir, zip_filename)

    file_count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(csv_dir):
            for filename in filenames:
                if filename.endswith('.csv'):
                    full_path = os.path.join(dirpath, filename)
                    arcname = os.path.relpath(full_path, csv_dir)
                    zf.write(full_path, arcname)
                    file_count += 1
                    print(f"  添加: {arcname}")

    zip_size = os.path.getsize(zip_path)
    print(f"  打包完成: {zip_path} ({file_count} 个文件, {zip_size/1024:.1f} KB)")


def main():
    global DEFAULT_NE
    try:                                                  # Windows GBK 控制台下中文输出乱码 → 统一 utf-8
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # argparse 而非裸 sys.argv:实测 `-o <目录>` 被裸解析当成第二个位置参数,输出静默落进
    # 字面叫 "-o" 的目录——写错要响亮报错,不能静默吞参
    ap = argparse.ArgumentParser(
        description="原始采集包 → config/log/cmd 三表 CSV(zip 或目录都行,目录直接给、无需先压 zip)")
    ap.add_argument("src", help="原始数据 zip 包,或原始数据目录(内含 LOG/CONFIG/DIAG/CMD 子目录,可带网元层)")
    ap.add_argument("out_pos", nargs="?", metavar="output_dir",
                    help="输出目录(可选;zip 默认=zip 同名目录,目录默认=<目录>_csv)")
    ap.add_argument("-o", "--out", dest="out_opt", metavar="output_dir",
                    help="输出目录(与位置参数二选一,别两个都给)")
    args = ap.parse_args()
    if args.out_pos and args.out_opt:
        print(f"错误: 输出目录给了两次(位置参数「{args.out_pos}」和 -o「{args.out_opt}」)——留一个")
        sys.exit(2)
    out_arg = args.out_pos or args.out_opt

    src = os.path.abspath(args.src)
    if os.path.isdir(src):
        # 目录输入:免解压直接扫描(此前只吃 zip,逼着现场先 make_archive 再转,纯绕路)
        extract_dir = src
        zip_basename = os.path.basename(src.rstrip('/\\')) or 'data'
        DEFAULT_NE = zip_basename                         # 平铺单网元目录:网元名=目录名,别叫 'NE'
        output_dir = os.path.abspath(out_arg) if out_arg else src.rstrip('/\\') + '_csv'
        csv_dir = os.path.join(output_dir, 'csv')
        print(f"[1/7] 目录输入,跳过解压,直接扫描: {src}")
        print(f"  输出目录: {output_dir}")
    elif os.path.isfile(src) and not src.lower().endswith('.zip'):
        # 单个原始散文件(.log/.txt/.rar 等):拷进输出目录 raw/ 暂存区统一识别——不直接把
        # 文件所在目录交给 ifilter(它会把整个父目录连带无关文件全部卷进来)
        output_dir = os.path.abspath(out_arg) if out_arg else src + '_csv'
        extract_dir = os.path.join(output_dir, 'raw')
        os.makedirs(extract_dir, exist_ok=True)
        shutil.copy2(src, extract_dir)
        zip_basename = os.path.splitext(os.path.basename(src))[0]
        DEFAULT_NE = zip_basename
        csv_dir = os.path.join(output_dir, 'csv')
        print(f"[1/7] 单文件输入,暂存到 {extract_dir} 后识别形态")
    elif os.path.isfile(src):
        zip_path = src
        # 输出目录：默认为 zip 文件同名目录（去掉 .zip 后缀）
        output_dir = os.path.abspath(out_arg) if out_arg else os.path.splitext(zip_path)[0]

        # ZIP 文件基本名（用于命名输出 ZIP）
        zip_basename = os.path.basename(zip_path)
        if zip_basename.lower().endswith('.zip'):
            zip_basename = zip_basename[:-4]

        # 解压目录和 CSV 输出目录
        extract_dir = os.path.join(output_dir, 'extracted')
        csv_dir = os.path.join(output_dir, 'csv')

        # Step 1: 解压
        unzip_file(zip_path, extract_dir)
    else:
        print(f"错误: 文件/目录不存在 - {src}")
        sys.exit(1)

    # Step 2: 自动扫描 LOG/CONFIG/DIAG 目录
    data_dirs = find_data_dirs(extract_dir)
    print(f"[2/7] 扫描结果: LOG={len(data_dirs['LOG'])}, CONFIG={len(data_dirs['CONFIG'])}, "
          f"DIAG={len(data_dirs['DIAG'])}, CMD={len(data_dirs['CMD'])}")

    # Step 2b: 一个分类目录都没有 = 原始散包形态 → 先 ifilter 归类再转
    # (有分类目录就按分类走,散落的杂文件不再另行归类——混装时以分类目录为准)
    if not any(data_dirs.values()):
        formated = run_ifilter(extract_dir)
        if formated is None:
            sys.exit(3)
        extract_dir = formated
        data_dirs = find_data_dirs(extract_dir)
        print(f"[2c/7] ifilter 归类完成,重扫: LOG={len(data_dirs['LOG'])}, "
              f"CONFIG={len(data_dirs['CONFIG'])}, DIAG={len(data_dirs['DIAG'])}, "
              f"CMD={len(data_dirs['CMD'])}")
        if not any(data_dirs.values()):
            print("  [err] ifilter 跑完仍无分类目录(数据内容识别不出)——检查数据是否为设备采集回显/日志")
            sys.exit(3)

    # Step 3: 处理 LOG
    process_log_dirs(data_dirs['LOG'], csv_dir, extract_dir)

    # Step 4: 处理 CONFIG
    process_config_dirs(data_dirs['CONFIG'], csv_dir, extract_dir)

    # Step 5: 处理 DIAG，收集输出 CSV 路径
    diag_csvs = process_diag_dirs(data_dirs['DIAG'], csv_dir, extract_dir)

    # Step 5b: 处理 CMD(DIAG 更名 CMD;老包的 DIAG 目录上面照旧处理,两名并存兼容)
    process_cmd_dirs(data_dirs['CMD'], csv_dir, extract_dir)

    # Step 6: 生成 CMD CSV
    generate_cmd_csv(diag_csvs, csv_dir)

    # Step 7: 打包为 ZIP
    pack_csv_to_zip(csv_dir, output_dir, zip_basename)

    # S 类脚本绑定直读裸 txt 的 raw 通道该指哪:走过 ifilter 就指 formated_result,否则原样
    print(f"\nraw 通道建议(runner 的 --data raw=): {extract_dir}")
    print("\n全部完成！")


if __name__ == '__main__':
    main()
