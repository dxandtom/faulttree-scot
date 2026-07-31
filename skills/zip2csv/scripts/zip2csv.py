"""
ZIP 解压 + 自动转换流水线

功能：
1. 解压 zip 包到本地目录
2. 递归扫描 LOG/CONFIG/DIAG 目录（支持嵌套如 Switch01/DIAG/）
3. 遍历 LOG/ 目录下所有 .txt 文件，调用 logbuffer2csv.convert_file 转换为 CSV
4. 遍历 CONFIG/ 目录下所有 .txt 文件，调用 config2csv.parse_config_to_csv 转换为 CSV
5. 遍历 DIAG/ 目录下所有 .txt 文件，调用 parse_diag_to_csv.parse_diag_to_csv 转换为 CSV
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

用法：
    python zip2csv.py <zip_file_path> [output_dir]
"""

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


def find_data_dirs(extract_dir):
    """
    在解压目录中递归查找所有包含 LOG/CONFIG/DIAG 子目录的路径。
    支持 ZIP 内有多层嵌套，如 Switch01/DIAG/ 或直接 DIAG/。
    返回 dict: {'LOG': [path1, ...], 'CONFIG': [path1, ...], 'DIAG': [path1, ...]}
    """
    result = {'LOG': [], 'CONFIG': [], 'DIAG': []}
    for dirpath, dirnames, filenames in os.walk(extract_dir):
        dirname = os.path.basename(dirpath).upper()
        if dirname in result:
            result[dirname].append(dirpath)
    return result


def get_ne_name(data_dir, extract_dir):
    """
    从数据目录路径中提取 NE 名称。
    例如 extract_dir/extracted/Switch01/DIAG -> Switch01
    如果没有中间层，返回 'NE'。
    """
    rel = os.path.relpath(data_dir, extract_dir)
    parts = rel.replace('\\', '/').split('/')
    if len(parts) >= 2:
        return parts[-2]
    return 'NE'


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

    for log_dir in log_dirs:
        ne_name = get_ne_name(log_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'LOG')
        os.makedirs(ne_output_dir, exist_ok=True)

        txt_files = sorted(glob.glob(os.path.join(log_dir, '*.txt')))
        for txt_file in txt_files:
            basename = os.path.splitext(os.path.basename(txt_file))[0]
            csv_file = os.path.join(ne_output_dir, basename + '.csv')
            print(f"  转换: {ne_name}/LOG/{os.path.basename(txt_file)} -> {basename}.csv")
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

    for config_dir in config_dirs:
        ne_name = get_ne_name(config_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'CONFIG')
        os.makedirs(ne_output_dir, exist_ok=True)

        txt_files = sorted(glob.glob(os.path.join(config_dir, '*.txt')))
        for txt_file in txt_files:
            basename = os.path.splitext(os.path.basename(txt_file))[0]
            csv_file = os.path.join(ne_output_dir, basename + '.csv')
            print(f"  转换: {ne_name}/CONFIG/{os.path.basename(txt_file)} -> {basename}.csv")
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

    for diag_dir in diag_dirs:
        ne_name = get_ne_name(diag_dir, extract_dir)
        ne_output_dir = os.path.join(csv_dir, ne_name, 'DIAG')
        os.makedirs(ne_output_dir, exist_ok=True)

        txt_files = sorted(glob.glob(os.path.join(diag_dir, '*.txt')))
        for txt_file in txt_files:
            basename = os.path.splitext(os.path.basename(txt_file))[0]
            csv_file = os.path.join(ne_output_dir, basename + '.csv')
            print(f"  转换: {ne_name}/DIAG/{os.path.basename(txt_file)} -> {basename}.csv")
            diag_convert(txt_file, csv_file)
            output_csvs.append(csv_file)

    if not output_csvs:
        print("  未找到 .txt 文件，跳过")
    else:
        print(f"  共转换 {len(output_csvs)} 个 DIAG 文件")

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
    if len(sys.argv) < 2:
        print("用法: python zip2csv.py <zip_file_path> [output_dir]")
        sys.exit(1)

    zip_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(zip_path):
        print(f"错误: 文件不存在 - {zip_path}")
        sys.exit(1)

    # 输出目录：默认为 zip 文件同名目录（去掉 .zip 后缀）
    if len(sys.argv) >= 3:
        output_dir = os.path.abspath(sys.argv[2])
    else:
        output_dir = os.path.splitext(zip_path)[0]

    # ZIP 文件基本名（用于命名输出 ZIP）
    zip_basename = os.path.basename(zip_path)
    if zip_basename.lower().endswith('.zip'):
        zip_basename = zip_basename[:-4]

    # 解压目录和 CSV 输出目录
    extract_dir = os.path.join(output_dir, 'extracted')
    csv_dir = os.path.join(output_dir, 'csv')

    # Step 1: 解压
    unzip_file(zip_path, extract_dir)

    # Step 2: 自动扫描 LOG/CONFIG/DIAG 目录
    data_dirs = find_data_dirs(extract_dir)
    print(f"[2/7] 扫描结果: LOG={len(data_dirs['LOG'])}, CONFIG={len(data_dirs['CONFIG'])}, DIAG={len(data_dirs['DIAG'])}")

    # Step 3: 处理 LOG
    process_log_dirs(data_dirs['LOG'], csv_dir, extract_dir)

    # Step 4: 处理 CONFIG
    process_config_dirs(data_dirs['CONFIG'], csv_dir, extract_dir)

    # Step 5: 处理 DIAG，收集输出 CSV 路径
    diag_csvs = process_diag_dirs(data_dirs['DIAG'], csv_dir, extract_dir)

    # Step 6: 生成 CMD CSV
    generate_cmd_csv(diag_csvs, csv_dir)

    # Step 7: 打包为 ZIP
    pack_csv_to_zip(csv_dir, output_dir, zip_basename)

    print("\n全部完成！")


if __name__ == '__main__':
    main()
