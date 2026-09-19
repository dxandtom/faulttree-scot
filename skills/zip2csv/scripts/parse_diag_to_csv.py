import csv
import re
import os
import sys

# ============================================================
# 核心解析逻辑（参考 parse_umc_data.py 中 parse_cmd_segment 的4种格式）
#
# 格式1：上下分隔线格式
#   ============...============
#            display xxx
#   ============...============
#   <命令输出内容>
#
# 格式2：内嵌命令格式
#   ============display xxx============
#   <命令输出内容>
#
# 格式3：命令提示符格式
#   <SwitchName>display xxx
#   <命令输出内容>
#
# 格式4：HUAWEI分隔线格式
#   ============HUAWEI============
#   display xxx
#   <命令输出内容>
#
# 过滤规则：
#   display current-configuration、display logbuffer、display trapbuffer、
#   display logfile xx、more xx 不输出
#
# 拆行规则：
#   ObjContent 超过 FIELD_LIMIT 字节时，按行拆分为多行记录输出，
#   每行记录的 NEName、CmdName 相同，避免 csv.field_size_limit 报错
# ============================================================

FIELD_LIMIT = 100000  # ObjContent 单字段最大字节数，略小于 csv 默认 131072

# 公共正则
sep_pattern = re.compile(r'^=+$')
# 格式1/4：独立命令行（不含=号），支持前导空格
cmd_standalone_pattern = re.compile(r'^\s*(display\s+[^=]+?)\s*$', re.I)
# 格式2：内嵌命令行 ===display xxx===，用 [^=] 避免误吞尾部等号
inline_cmd_pattern = re.compile(r'^=+\s*(display\s+[^=]+?)\s*=+$', re.I)
# 格式3：命令提示符 <SwitchName>display xxx
prompt_pattern = re.compile(r'^<(\S+)>\s*(dis\w*(?:\s+\S+)*)', re.I)
# 格式4：HUAWEI分隔线
huawei_pattern = re.compile(r'^=+.*HUAWEI.*=+$', re.I)

# 需要跳过的命令模式
skip_patterns = [
    re.compile(r'^display\s+current-configuration$', re.I),
    re.compile(r'^display\s+logbuffer\b', re.I),
    re.compile(r'^display\s+trapbuffer\b', re.I),
    re.compile(r'^display\s+logfile\b', re.I),
    re.compile(r'^more\s+', re.I),
]

def should_skip(cmd_name):
    """判断该命令是否需要跳过（不输出）"""
    for pat in skip_patterns:
        if pat.match(cmd_name):
            return True
    return False

# 判断某行是否是任意格式的起始行（用于各方法的输出收集终止条件）
def is_section_start(line_stripped):
    if sep_pattern.match(line_stripped):
        return True
    if inline_cmd_pattern.match(line_stripped):
        return True
    if prompt_pattern.match(line_stripped):
        return True
    if huawei_pattern.match(line_stripped):
        return True
    if line_stripped.lower().startswith('display '):
        return True
    return False


def _add_result(results, seen, ne_name, cmd_name, obj_content):
    """把一条 (命令, 回显) 记进 results:跳过过滤命令,多告警回显按分隔符拆成多条,按 (CmdName, 前60字符) 去重。"""
    if should_skip(cmd_name):
        return
    # 如果 obj_content 包含多个 Sequence:（多告警），按分隔符分割成多条记录
    seq_count = obj_content.count('Sequence    :')
    if seq_count > 1:
        # 按 \n\n 或 ======== 分隔符分割成多条告警
        # 分割后可能每个 block 开头有换行符，需要去掉
        alarm_blocks = [b.strip() for b in re.split(r'\n\n|={3,}', obj_content) if b.strip()]
        for block in alarm_blocks:
            # 再次去掉可能残留的换行符
            block = block.strip()
            if block:
                key = (cmd_name, block[:60])
                if key not in seen:
                    seen.add(key)
                    results.append({"NEName": ne_name, "CmdName": cmd_name, "ObjContent": block})
    else:
        key = (cmd_name, obj_content[:60])
        if key not in seen:
            seen.add(key)
            results.append({"NEName": ne_name, "CmdName": cmd_name, "ObjContent": obj_content})


def _collect_output(lines, start, n):
    """从 start 行起收集回显行,直到下一个段落起始行为止。返回 (回显行列表, 停止位置)。"""
    output_lines = []
    j = start
    while j < n:
        if is_section_start(lines[j].strip()):
            break
        output_lines.append(lines[j])
        j += 1
    return output_lines, j


def _parse_method1(lines, n):
    """方法1:上下分隔线格式 =========display xxx========。返回 [(命令, 回显)]。"""
    found = []
    i = 0
    while i < n - 2:
        line1 = lines[i].strip()
        line2 = lines[i + 1].strip()
        line3 = lines[i + 2].strip()

        if sep_pattern.match(line1) and sep_pattern.match(line3):
            cmd_match = cmd_standalone_pattern.match(line2)
            if cmd_match:
                cmd_name = cmd_match.group(1).strip()

                output_lines, j = _collect_output(lines, i + 3, n)

                obj_content = "\n".join(output_lines).strip()
                found.append((cmd_name, obj_content))
                i = j
                continue
        i += 1
    return found


def _parse_method2(lines, n):
    """方法2:内嵌命令格式 =============display xxx===============。返回 [(命令, 回显)]。"""
    found = []
    for i, line in enumerate(lines):
        match = inline_cmd_pattern.match(line.strip())
        if match:
            cmd_name = match.group(1).strip()

            output_lines, _j = _collect_output(lines, i + 1, n)
            obj_content = "\n".join(output_lines).strip()
            found.append((cmd_name, obj_content))
    return found


def _parse_method3(lines, n):
    """方法3:命令提示符格式 <Switch>display xxx。返回 [(命令, 回显)]。"""
    found = []
    for i, line in enumerate(lines):
        match = prompt_pattern.match(line.strip())
        if match:
            cmd_name = match.group(2).strip()

            output_lines, _j = _collect_output(lines, i + 1, n)
            obj_content = "\n".join(output_lines).strip()
            found.append((cmd_name, obj_content))
    return found


def _huawei_output(lines, output_start, n):
    """方法4 的回显收集:到下一个段落起始行为止,丢掉其中的空行 / HUAWEI 分隔线 / 等号分隔线。"""
    output_end = n
    for k in range(output_start, n):
        if is_section_start(lines[k].strip()):
            output_end = k
            break

    output_lines = []
    for m in range(output_start, output_end):
        lm = lines[m].strip()
        if lm and not huawei_pattern.match(lm) and not sep_pattern.match(lm):
            output_lines.append(lines[m])
    return output_lines


def _parse_method4(lines, n):
    """方法4:HUAWEI分隔线格式 =========HUAWEI======== + 命令 + 输出。返回 [(命令, 回显)]。"""
    found = []
    for i, line in enumerate(lines):
        if huawei_pattern.match(line.strip()):
            # 在HUAWEI分隔线后最多5行内找命令
            for j in range(i + 1, min(i + 5, n)):
                l = lines[j].strip()
                if l and not huawei_pattern.match(l) and not sep_pattern.match(l):
                    cmd_match = cmd_standalone_pattern.match(l)
                    if cmd_match:
                        cmd_name = cmd_match.group(1).strip()
                    else:
                        cmd_name = l

                    output_lines = _huawei_output(lines, j + 1, n)
                    obj_content = "\n".join(output_lines).strip()
                    found.append((cmd_name, obj_content))
                    break
    return found


def _split_oversized(row):
    """ObjContent 超过 FIELD_LIMIT 的记录按段落分隔符拆成多条,仍超限的段落再按换行拆;不超限原样返回 [row]。"""
    oc = row["ObjContent"]
    if len(oc.encode("utf-8")) <= FIELD_LIMIT:
        return [row]
    out = []
    # 按多种空行模式拆分：\n\n、\r\r、-（3个以上）、=（3个以上）、+（3个以上）、#（3个以上）都算逻辑段落分隔
    # 过滤空字符串块，每个非空 block 直接输出一条记录
    blocks = [b for b in re.split(r'\n\n|\r\r|-{3,}|={3,}|\+{3,}|#{3,}', oc) if b.strip()]
    for block in blocks:
        block_bytes = len(block.encode("utf-8"))
        # 如果单个 block 超过 FIELD_LIMIT，按换行符进一步拆分
        if block_bytes > FIELD_LIMIT:
            sub_blocks = block.split("\n")
            for sub_block in sub_blocks:
                if sub_block.strip():
                    out.append({
                        "NEName": row["NEName"],
                        "CmdName": row["CmdName"],
                        "ObjContent": sub_block
                    })
        else:
            out.append({
                "NEName": row["NEName"],
                "CmdName": row["CmdName"],
                "ObjContent": block
            })
    return out


def _write_csv(output_file, final_results):
    """把记录写成 utf-8-sig 全引号 CSV(NEName, CmdName, ObjContent),输出目录不存在则建。"""
    csv.field_size_limit(10 * 1024 * 1024)
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["NEName", "CmdName", "ObjContent"], quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(final_results)


def parse_diag_to_csv(input_file, output_file, ne_name=None):
    """
    解析 DIAG 诊断文件，输出 CSV（NEName, CmdName, ObjContent）。

    Args:
        input_file: 输入 txt 文件路径
        output_file: 输出 csv 文件路径
        ne_name: NE 名称，默认取文件名（去扩展名）
    """
    if ne_name is None:
        ne_name = os.path.splitext(os.path.basename(input_file))[0]

    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    n = len(lines)

    results = []
    seen = set()  # 去重：(CmdName, ObjContent前60字符)

    # ---- 方法1：上下分隔线格式 =========display xxx======== ----
    for cmd_name, obj_content in _parse_method1(lines, n):
        _add_result(results, seen, ne_name, cmd_name, obj_content)

    # ---- 方法2：内嵌命令格式 =============display xxx=============== ----
    for cmd_name, obj_content in _parse_method2(lines, n):
        _add_result(results, seen, ne_name, cmd_name, obj_content)

    # ---- 方法3：命令提示符格式 <Switch>display xxx ----
    for cmd_name, obj_content in _parse_method3(lines, n):
        _add_result(results, seen, ne_name, cmd_name, obj_content)

    # ---- 方法4：HUAWEI分隔线格式 =========HUAWEI======== + 命令 + 输出 ----
    for cmd_name, obj_content in _parse_method4(lines, n):
        _add_result(results, seen, ne_name, cmd_name, obj_content)

    # ---- 拆分超大 ObjContent，按多种空行分隔符切分，防止 csv.field_size_limit 报错 ----
    # 分隔符包括：空行(\n\n)、换行符(\n)、回车符(\r)
    final_results = []
    for row in results:
        final_results.extend(_split_oversized(row))

    # 写入 CSV
    _write_csv(output_file, final_results)

    print(f"Done. Extracted {len(final_results)} records to {output_file}")
    return output_file


if __name__ == '__main__':
    input_file = r"D:\需求相关\2026\PI4\数通skill调试\Switch02\DIAG\Switch 2.txt"
    output_file = r"D:\需求相关\2026\PI4\数通skill调试\Switch02\DIAG\Switch 2.csv"
    parse_diag_to_csv(input_file, output_file)
