"""
Hardcoding Audit Script for Part B:
Scans git diff 4c042fb..HEAD across Python files to find:
1. Regex over user text (re.search, re.match, etc.)
2. Keyword / phrase lists and sets
3. Mapping dictionaries from words to meanings
4. Hardcoded names / cities / brands
5. User-visible string literals in tools/state/connectors
"""
import subprocess
import re

def run_diff_audit():
    cmd = ['git', 'diff', '4c042fb..HEAD', '--', '*.py', 'agent/*.py', 'state/*.py', 'connectors/*.py', 'core/*.py']
    out = subprocess.check_output(cmd, text=True, errors='ignore')

    added_lines = []
    curr_file = ''
    line_num = 0

    for line in out.split('\n'):
        if line.startswith('+++ b/'):
            curr_file = line[6:]
        elif line.startswith('@@ '):
            m = re.search(r'\+(\d+)', line)
            if m:
                line_num = int(m.group(1))
        elif line.startswith('+') and not line.startswith('+++'):
            added_lines.append((curr_file, line_num, line[1:]))
            line_num += 1
        elif not line.startswith('-'):
            line_num += 1

    print(f"Total added lines in diff: {len(added_lines)}")

    regex_findings = []
    set_findings = []
    dict_findings = []
    city_brand_findings = []
    user_strings = []

    words_of_interest = ['daily', 'english', 'hindi', 'cancel', 'confirm', 'haan', 'nahi', 'kal', 'aaj', 'roj', 'meeting', 'jogging']
    city_brands = ['jodhpur', 'mumbai', 'delhi', 'jaipur', 'zomato', 'swiggy', 'inox', 'miraj']

    for f, ln, code in added_lines:
        code_str = code.strip()
        # 1. Regex
        if re.search(r're\.(search|match|findall|sub)\(', code):
            # Exclude structural/allowed regex (UUID, token, email, ISO date)
            if not any(pat in code for pat in ['SECRET_PATTERNS', 'uuid', 'iso', 'token', 'ghp_', 'gsk_', 'AIza']):
                regex_findings.append((f, ln, code_str))

        # 2. Sets / Tuples / Lists of natural language words
        for w in words_of_interest:
            if f"'{w}'" in code_str or f'"{w}"' in code_str:
                if any(kw in code_str for kw in ['in (', 'in [', 'in {', '==', '!=', 'set(', '{']):
                    set_findings.append((f, ln, code_str))
                    break

        # 3. Dict mappings (e.g. 'en': 'english')
        if any(w in code_str for w in [": 'english'", ': "english"', ": 'hindi'", ': "hindi"']):
            dict_findings.append((f, ln, code_str))

        # 4. City / brand names
        for cb in city_brands:
            if re.search(rf'\b{cb}\b', code_str, re.IGNORECASE):
                city_brand_findings.append((f, ln, code_str))
                break

    print("\n--- 1. REGEX OVER USER TEXT ---")
    for item in regex_findings:
        print(f"{item[0]}:{item[1]} -> {item[2]}")

    print("\n--- 2. KEYWORD / PHRASE LISTS & SETS ---")
    for item in set_findings:
        print(f"{item[0]}:{item[1]} -> {item[2]}")

    print("\n--- 3. MAPPING DICTIONARIES ---")
    for item in dict_findings:
        print(f"{item[0]}:{item[1]} -> {item[2]}")

    print("\n--- 4. HARDCODED CITIES / BRANDS ---")
    for item in city_brand_findings:
        print(f"{item[0]}:{item[1]} -> {item[2]}")

if __name__ == '__main__':
    run_diff_audit()
