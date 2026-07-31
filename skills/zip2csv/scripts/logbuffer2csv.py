"""
Switch02 logbuffer.txt -> CSV 转换脚本 v2
修复内容（相比 v1）：
1. skip_lines 默认值改为 5，避免多跳过有效数据行
2. LINE_PATTERN 支持 _active: / _clear: 格式（无 (l)/(s)/(t) 标记的日志）
3. PARAM_PATTERN 支持 Content 以 ').'' 结尾的情况
4. MAX_PARA 从 20 提升到 30，避免参数被截断
5. 支持多行日志条目：续行合并到上一条记录
"""

import re
import csv
from datetime import datetime

MAX_PARA = 30

# ============================================================
# 核心解析逻辑
# ============================================================

# 行格式：Jul 20 2020 19:12:19-05:00 Switch02 %%01NTP/2/NTP_TRAP_SYNCHRONIZE_STATUS(l):CID=0x803603fe;...
# 同时支持 _active: / _clear: 格式（无括号标记）
LINE_PATTERN = re.compile(
    r'^(\w{3}\s+\d{1,2}\s+\d{4})\s+'   # 1: 日期 (Jul 20 2020)
    r'(\d{2}:\d{2}:\d{2})'              # 2: 时间 (19:12:19)
    r'([+-]\d{2}:\d{2})\s+'            # 3: 时区 (-05:00)
    r'(\S+)\s+'                          # 4: NEName (Switch02)
    r'%%\d+'                             # %%01 前缀
    r'(\w+)/'                            # 5: Module (NTP)
    r'(\d+)/'                            # 6: Level (2)
    r'(\w+)'                             # 7: Summary (NTP_TRAP_SYNCHRONIZE_STATUS)
    r'(?:\([a-z]*\))?:'                  # (l): / (t): / (): / 直接冒号（_active/_clear格式）
    r'(.*)'                              # 8: Content (剩余全部)
)

# 用于判断一行是否为新日志条目的起始行
NEW_ENTRY_PATTERN = re.compile(
    r'^\w{3}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\s+\S+\s+%%\d+'
)

# 从 Content 末尾提取包含 key=value 的括号组
# 修复：支持以 ). 结尾（句号在括号外）
PARAM_PATTERN = re.compile(r'\(([^()]*=[^()]*)\)\.?\s*$')

# 单个 key=value 对：key 支持字母/数字/连字符/空格，value 支持任意字符（含空格、连字符）
# 用前瞻匹配到下一个 key 或结尾，避免多词 value 被截断
KV_PATTERN = re.compile(r'([A-Za-z][\w\s-]*?)\s*=\s*(.*?)(?=,\s*[A-Za-z][\w\s-]*?\s*=|$)')


def parse_time(date_str: str, time_str: str) -> str:
    """将 'Jul 20 2020' + '19:12:19' 转为 '2020/7/20 19:12:19'"""
    dt = datetime.strptime(f"{date_str} {time_str}", "%b %d %Y %H:%M:%S")
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def extract_params(content: str) -> dict:
    """
    从 Content 中提取末尾括号内的 key=value 参数对。
    key 支持含连字符和空格（如 M-LAG ID），value 支持多词。
    返回 {'Para1Name': x, 'Para1': y, ...}，最多 MAX_PARA 组。
    """
    result = {}
    m = PARAM_PATTERN.search(content)
    if not m:
        return result

    kv_str = m.group(1)
    pairs = KV_PATTERN.findall(kv_str)

    for i, (name, value) in enumerate(pairs[:MAX_PARA], start=1):
        result[f'Para{i}Name'] = name.strip()
        result[f'Para{i}'] = value.strip()

    return result


def parse_line(line: str) -> dict | None:
    """解析单行日志，返回各字段字典；解析失败返回 None"""
    m = LINE_PATTERN.match(line.strip())
    if not m:
        return None

    date_str, time_str, tz, nename, module, level, summary, content = m.groups()

    # 完整原始日志
    log = line.strip()

    # 时间格式转换
    time_formatted = parse_time(date_str, time_str)

    # 提取参数
    params = extract_params(content)

    row = {
        'NEName': nename,
        'Time': time_formatted,
        'Module': module,
        'Level': level,
        'Summary': summary,
        'Content': content,
        'Log': log,
    }
    # 填充 Para1~ParaN 及对应 Name，缺失则留空
    for i in range(1, MAX_PARA + 1):
        row[f'Para{i}Name'] = params.get(f'Para{i}Name', '')
        row[f'Para{i}'] = params.get(f'Para{i}', '')

    return row


# ============================================================
# 表头
# ============================================================

HEADERS = ['NEName', 'Time', 'Module', 'Level', 'Summary', 'Content', 'Log']
for i in range(1, MAX_PARA + 1):
    HEADERS.append(f'Para{i}')
    HEADERS.append(f'Para{i}Name')


# ============================================================
# 单行测试
# ============================================================

SAMPLE_LINE_1 = (
    "Jul 20 2020 19:12:19-05:00 Switch02 %%01NTP/2/NTP_TRAP_SYNCHRONIZE_STATUS(l):"
    "CID=0x802603fe;NTP synchronization state changed. "
    "(hwNtpState=unsynchronized, hwNtpSource=10.116.29.130, hwNtpSourceVpnName=VRF_OM)"
)

SAMPLE_LINE_2 = (
    "Jul 20 2020 19:12:19-05:00 Switch02 %%01NTP/2/NTP_SYNCHRONIZE_STATUS(l):"
    "CID=0x802603fe;NTP state changed from synchronized to unsynchronized. "
    "(SynchronizationSourceAddress = 10.116.29.130 , VpnInstance = VRF_OM, "
    "Reason = Synchronization distance greater than distance threshold)"
)

SAMPLE_LINE_3 = (
    "Jul 20 2020 19:03:07-05:00 Switch02 %%01ETRUNK/4/hwMlagPortDown_clear(l):"
    "CID=0x81de271e-alarmID=0x0ae52007-clearType=service_resume;"
    "The status of M-LAG member interfaces changes to Up, or the M-LAG member interfaces "
    "have non-dual-homing configurations. "
    "(M-LAG ID=3, LocalIfname=Eth-Trunk3, LocalSystemMAC=8ce5-ef19-54c1, RemoteSystemMAC=8ce5-ef19-5601)"
)

# v2 新增测试：_active 格式（无括号标记）
SAMPLE_LINE_4 = (
    "Jul  5 2026 02:36:25+03:00 DMM-ASG-02 %%01FEI/2/hwLinkHeartbeatChangeAlarm_active:"
    "The link-heartbeat function detected that the packet modification rate reached or exceeded the threshold. "
    "(InterfaceIfIndex=1, InterfaceName=400GE1/1/0, IfIndex=1, IfName=400GE1/1/0, ExtIfIndex=1)"
)

# v2 新增测试：Content 以 ). 结尾
SAMPLE_LINE_5 = (
    "Jul  5 2026 02:21:09+03:00 DMM-ASG-02 %%01ISIS/4/ISIS_LDP_SYNC_EVENT(l):"
    "CID=0x80860586;Interface Eth-Trunk100.21 received LDP event NbrDown, "
    "LDP synchronization state Init change to Init (IsisSysInstance=11, SuppressCount=0)."
)


def _run_test(label, line, expected):
    """通用单行测试"""
    print("=" * 70)
    print(f"测试: {label}")
    print("=" * 70)
    print(f"输入: {line}\n")

    result = parse_line(line)
    if result is None:
        print("ERROR: 解析失败！\n")
        return False

    all_pass = True
    for key in expected:
        actual = result.get(key, '<MISSING>')
        exp = expected[key]
        status = "PASS" if actual == exp else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {key:20s}: {actual:50s} | expected: {exp:50s} [{status}]")

    print(f"\n结果: {'ALL PASSED' if all_pass else 'HAS FAILURES'}\n")
    return all_pass


def test_single_line():
    """用示例数据验证解析正确性"""
    ok1 = _run_test(
        "1. key=value 无空格",
        SAMPLE_LINE_1,
        {
            'NEName': 'Switch02',
            'Time': '2020/07/20 19:12:19',
            'Module': 'NTP',
            'Level': '2',
            'Summary': 'NTP_TRAP_SYNCHRONIZE_STATUS',
            'Content': 'CID=0x802603fe;NTP synchronization state changed. (hwNtpState=unsynchronized, hwNtpSource=10.116.29.130, hwNtpSourceVpnName=VRF_OM)',
            'Log': SAMPLE_LINE_1,
            'Para1': 'unsynchronized',
            'Para1Name': 'hwNtpState',
            'Para2': '10.116.29.130',
            'Para2Name': 'hwNtpSource',
            'Para3': 'VRF_OM',
            'Para3Name': 'hwNtpSourceVpnName',
        },
    )

    ok2 = _run_test(
        "2. key = value 带空格 + 多词值",
        SAMPLE_LINE_2,
        {
            'NEName': 'Switch02',
            'Time': '2020/07/20 19:12:19',
            'Module': 'NTP',
            'Level': '2',
            'Summary': 'NTP_SYNCHRONIZE_STATUS',
            'Para1': '10.116.29.130',
            'Para1Name': 'SynchronizationSourceAddress',
            'Para2': 'VRF_OM',
            'Para2Name': 'VpnInstance',
            'Para3': 'Synchronization distance greater than distance threshold',
            'Para3Name': 'Reason',
        },
    )

    ok3 = _run_test(
        "3. key 含连字符+空格 (M-LAG ID), 4组Para",
        SAMPLE_LINE_3,
        {
            'NEName': 'Switch02',
            'Time': '2020/07/20 19:03:07',
            'Module': 'ETRUNK',
            'Level': '4',
            'Summary': 'hwMlagPortDown_clear',
            'Para1': '3',
            'Para1Name': 'M-LAG ID',
            'Para2': 'Eth-Trunk3',
            'Para2Name': 'LocalIfname',
            'Para3': '8ce5-ef19-54c1',
            'Para3Name': 'LocalSystemMAC',
            'Para4': '8ce5-ef19-5601',
            'Para4Name': 'RemoteSystemMAC',
        },
    )

    ok4 = _run_test(
        "4. _active 格式（无括号标记）",
        SAMPLE_LINE_4,
        {
            'NEName': 'DMM-ASG-02',
            'Time': '2026/07/05 02:36:25',
            'Module': 'FEI',
            'Level': '2',
            'Summary': 'hwLinkHeartbeatChangeAlarm_active',
            'Para1': '1',
            'Para1Name': 'InterfaceIfIndex',
            'Para2': '400GE1/1/0',
            'Para2Name': 'InterfaceName',
            'Para3': '1',
            'Para3Name': 'IfIndex',
            'Para4': '400GE1/1/0',
            'Para4Name': 'IfName',
        },
    )

    ok5 = _run_test(
        "5. Content 以 ). 结尾",
        SAMPLE_LINE_5,
        {
            'NEName': 'DMM-ASG-02',
            'Time': '2026/07/05 02:21:09',
            'Module': 'ISIS',
            'Level': '4',
            'Summary': 'ISIS_LDP_SYNC_EVENT',
            'Para1': '11',
            'Para1Name': 'IsisSysInstance',
            'Para2': '0',
            'Para2Name': 'SuppressCount',
        },
    )

    print("=" * 70)
    all_ok = ok1 and ok2 and ok3 and ok4 and ok5
    print(f"全部测试: {'ALL PASSED' if all_ok else 'HAS FAILURES'}")


# ============================================================
# 文件转换
# ============================================================

def merge_multiline(lines: list[str]) -> list[str]:
    """
    将多行日志合并为单条：如果一行不是新日志条目的起始行，
    则将其追加到前一条有效记录的内容中。
    """
    merged = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if NEW_ENTRY_PATTERN.match(stripped):
            merged.append(stripped)
        elif merged:
            # 续行：追加到上一行，用空格分隔
            merged[-1] += ' ' + stripped
        # else: 首部之前的非空行，丢弃
    return merged


def convert_file(input_path: str, output_path: str, skip_lines: int = 5):
    """读取 txt 文件并转换为 CSV"""
    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # 先跳过头部行
    data_lines = lines[skip_lines:]

    # 合并多行日志条目
    merged_lines = merge_multiline(data_lines)

    rows = []
    for line in merged_lines:
        row = parse_line(line)
        if row:
            rows.append(row)

    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in rows:
            # 换行符替换为字面量 \n，保证 Excel 兼容
            for k in row:
                if isinstance(row[k], str):
                    row[k] = row[k].replace('\n', '\\n').replace('\r', '')
            writer.writerow(row)

    print(f"转换完成: {len(rows)} 条记录 -> {output_path}")


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    # 先执行单行测试
    # test_single_line()

    # ---- 取消下方注释即可执行完整文件转换 ----
    input_file = r"D:\需求相关\2026\PI4\数通skill调试\Switch02\LOG\Switch02_logbuffer.txt"
    output_file = r"D:\upload\upload\webot\Switch02_logbuffer.csv"
    convert_file(input_file, output_file)
