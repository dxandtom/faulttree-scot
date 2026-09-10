"""
Parse Huawei switch configuration file into structured CSV.

Input:  txt file with # separated config sections
Output: CSV with columns NEName, CfgType, Module, level1~level5
"""

import csv
import re
import os


def parse_config_to_csv(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    # Extract NEName from <...> in header lines
    ne_name = ''
    for line in lines:
        match = re.search(r'<([^>]+)>', line)
        if match:
            ne_name = match.group(1)
            break

    cfg_type = 'RUNNING'

    # Split into sections by top-level '#' lines (no leading whitespace)
    sections = []
    current_section = []
    for line in lines:
        if re.match(r'^#\s*$', line):
            if current_section:
                sections.append(current_section)
                current_section = []
        else:
            current_section.append(line)
    if current_section:
        sections.append(current_section)

    headers = [
        'NEName', 'CfgType', 'Module', 'level1',
        'L2Module', 'level2', 'L3Module', 'level3',
        'L4Module', 'level4', 'L5Module', 'level5'
    ]
    rows = []

    # Skip first section (header with NEName), process config sections
    for section in sections[1:]:
        # Filter: skip empty lines, comment lines (!), and sub-section '#' separators
        filtered_lines = [
            l.rstrip() for l in section
            if l.strip() and not l.strip().startswith('!') and l.strip() != '#'
        ]
        if not filtered_lines:
            continue

        # Track current parent path at each indentation level
        # current_path[0] = level1 line, current_path[1] = level2 line, etc.
        current_path = []

        for line in filtered_lines:
            # Determine level by counting leading spaces
            stripped = line.lstrip(' ')
            leading_spaces = len(line) - len(stripped)
            level = leading_spaces + 1  # 0 spaces -> level1, 1 space -> level2, ...
            if level > 5:
                level = 5

            # Update path: keep ancestors up to level-1, then set current level
            current_path = current_path[:level - 1]
            current_path.append(stripped)

            # Build row from path
            row = [ne_name, cfg_type]
            for i in range(5):
                if i < len(current_path):
                    full_line = current_path[i]
                    first_word = full_line.split()[0] if full_line.split() else ''
                    row.append(first_word)   # Module or LxModule
                    row.append(full_line)     # level1~level5
                else:
                    row.append('')
                    row.append('')

            rows.append(row)

    # Write CSV (utf-8-sig for Excel compatibility)
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"CSV generated: {output_file}")
    print(f"Total rows: {len(rows)}")


if __name__ == '__main__':
    input_file = r'D:\需求相关\2026\PI4\数通skill调试\Switch02\CONFIG\Switch02_cfg.txt'
    output_file = r'D:\upload\upload\webot\Switch02_cfg.csv'

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    parse_config_to_csv(input_file, output_file)
