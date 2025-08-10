import re

def extract_domain_suffix_rules(filepath):
    """从文件中提取所有 DOMAIN-SUFFIX 规则，返回去重后的集合"""
    domain_rules = set()
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 过滤空行、注释
            if not line or line.startswith('#'):
                continue
            # 只提取 DOMAIN-SUFFIX 规则
            if line.startswith('DOMAIN-SUFFIX'):
                domain_rules.add(line)
    return domain_rules

def parse_conf_sections(lines):
    """把配置按段落拆分，返回字典：{section_name: [lines]}"""
    sections = {}
    current_section = None
    for line in lines:
        line_strip = line.strip()
        if line_strip.startswith('[') and line_strip.endswith(']'):
            current_section = line_strip
            if current_section not in sections:
                sections[current_section] = []
        else:
            if current_section is None:
                # 文件开头无section的情况
                current_section = ''
                if current_section not in sections:
                    sections[current_section] = []
            sections[current_section].append(line)
    return sections

def merge_rules_to_anomad(sr_file, anomad_file, output_file):
    # 1. 提取 sr_cnip_ad.conf 的 DOMAIN-SUFFIX 规则
    new_rules = extract_domain_suffix_rules(sr_file)

    # 2. 读取 a-nomad.conf 整体内容
    with open(anomad_file, encoding='utf-8') as f:
        anomad_lines = f.readlines()

    # 3. 按段落拆分 a-nomad.conf
    sections = parse_conf_sections(anomad_lines)

    # 4. 获取 [Rule] 段已有规则，过滤出 DOMAIN-SUFFIX 规则集合
    existing_rules = set()
    rule_section = '[Rule]'
    if rule_section in sections:
        for line in sections[rule_section]:
            line_strip = line.strip()
            if line_strip.startswith('DOMAIN-SUFFIX'):
                existing_rules.add(line_strip)

    # 5. 计算要新增的规则（排除已有）
    to_add = new_rules - existing_rules

    # 6. 在 [Rule] 段末尾添加这些规则
    if rule_section not in sections:
        # 没有[Rule]段，添加一个
        sections[rule_section] = []
    if to_add:
        # 确保前面有空行分隔
        if sections[rule_section] and sections[rule_section][-1].strip() != '':
            sections[rule_section].append('\n')
        for rule in sorted(to_add):
            sections[rule_section].append(rule + '\n')

    # 7. 重新组合所有段落，保持顺序
    output_lines = []
    order = []
    current_section = None
    for line in anomad_lines:
        line_strip = line.strip()
        if line_strip.startswith('[') and line_strip.endswith(']'):
            current_section = line_strip
            if current_section not in order:
                order.append(current_section)
    # 可能有新增的[Rule]段没出现在文件里，则加到最后
    if rule_section not in order:
        order.append(rule_section)
    # 输出每个段落
    for sec in order:
        output_lines.append(sec + '\n')
        output_lines.extend(sections.get(sec, []))

    # 8. 写回到 output_file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)

    print(f"合并完成，写入文件：{output_file}")
    print(f"新增规则数量：{len(to_add)}")

if __name__ == "__main__":
    sr_cnip_ad_path = "sr_cnip_ad.conf"
    a_nomad_path = "a-nomad.conf"
    output_path = "a-nomad.conf"
    merge_rules_to_anomad(sr_cnip_ad_path, a_nomad_path, output_path)

