import re

# 读取文件
with open(r'D:\CS\Skill飞轮\数通\接口类问题\数据\01\formated_result\TEST\EHYI001-CO-01 - TEST\CMD\EHYI001-CO-01_13JAN.log', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# 找 display interface 部分的起始位置
start_idx = content.find('display interface\n===============================================================================\n')
if start_idx >= 0:
    interface_section = content[start_idx:]
    
    # 统计各类状态
    up_count = len(re.findall(r'current state\s*:\s*UP\s*\(', interface_section, re.MULTILINE))
    admin_down = len(re.findall(r'current state\s*:\s*Administratively DOWN', interface_section, re.MULTILINE))
    error_down = len(re.findall(r'current state\s*:\s*ERROR DOWN', interface_section, re.MULTILINE))
    
    print(f'UP 状态接口: {up_count}')
    print(f'Administratively DOWN: {admin_down}')
    print(f'ERROR DOWN: {error_down}')
    
    # 解析所有接口块
    interface_pattern = re.compile(
        r'^(\S+)\s+current state\s*:\s*(.+?)(?=\n\S+\s+current state\s*:|$)',
        re.MULTILINE
    )
    
    matches = interface_pattern.findall(interface_section)
    print(f'\n共解析到 {len(matches)} 个接口')
    
    # 查找 UP 且 rate 为 0 的接口
    print('\n=== UP 状态且 input/output rate 为 0 的接口 ===')
    
    for name, state in matches:
        state = state.strip()
        if state == 'UP':
            # 查找该接口的 rate
            # 简化：查找接口名到下一个接口之间的内容
            pattern = re.compile(re.escape(name) + r'\s+current state.*?(?=\n\S+\s+current state|$)', re.DOTALL)
            block_match = pattern.search(interface_section)
            if block_match:
                block = block_match.group()
                input_match = re.search(r'Last\s+\d+\s+seconds\s+input\s+rate\s*:\s*(\d+)\s*bits/sec', block, re.I)
                output_match = re.search(r'Last\s+\d+\s+seconds\s+output\s+rate\s*:\s*(\d+)\s*bits/sec', block, re.I)
                input_rate = int(input_match.group(1)) if input_match else None
                output_rate = int(output_match.group(1)) if output_match else None
                
                if input_rate == 0 or output_rate == 0:
                    print(f'{name}: input={input_rate}, output={output_rate}')
else:
    print('未找到 display interface 输出')
