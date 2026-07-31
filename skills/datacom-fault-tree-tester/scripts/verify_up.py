import re

with open(r'D:\CS\Skill飞轮\数通\接口类问题\数据\01\formated_result\TEST\EHYI001-CO-01 - TEST\CMD\EHYI001-CO-01_13JAN.log', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

start_idx = content.find('display interface\n===============================================================================\n')
interface_section = content[start_idx:]

# 查找所有 UP 状态接口及其 rate
interface_pattern = re.compile(
    r'^(100GE\d+/\d+/\d+|GigabitEthernet\d+/\d+/\d+)\s+current state\s*:\s*UP\s*\(',
    re.MULTILINE
)

up_interfaces = []
for match in interface_pattern.finditer(interface_section):
    name = match.group(1)
    start = match.start()
    # 找到下一个接口或块结束
    next_match = interface_pattern.search(interface_section, match.end())
    end = next_match.start() if next_match else len(interface_section)
    block = interface_section[start:end]
    
    input_match = re.search(r'Last\s+\d+\s+seconds\s+input\s+rate\s*:\s*(\d+)\s*bits/sec', block, re.I)
    output_match = re.search(r'Last\s+\d+\s+seconds\s+output\s+rate\s*:\s*(\d+)\s*bits/sec', block, re.I)
    
    input_rate = int(input_match.group(1)) if input_match else 0
    output_rate = int(output_match.group(1)) if output_match else 0
    
    up_interfaces.append((name, input_rate, output_rate))

# 统计
zero_input = sum(1 for _, i, _ in up_interfaces if i == 0)
zero_output = sum(1 for _, _, o in up_interfaces if o == 0)
zero_both = sum(1 for _, i, o in up_interfaces if i == 0 and o == 0)
zero_any = sum(1 for _, i, o in up_interfaces if i == 0 or o == 0)

print(f'UP 状态接口总数: {len(up_interfaces)}')
print(f'  - input rate 为 0: {zero_input}')
print(f'  - output rate 为 0: {zero_output}')
print(f'  - 两者都为 0: {zero_both}')
print(f'  - 任一为 0: {zero_any}')

# 显示几个样例
print('\n=== 样例（显示前5个UP状态接口） ===')
for name, input_rate, output_rate in up_interfaces[:5]:
    print(f'{name}: input={input_rate}, output={output_rate}')
