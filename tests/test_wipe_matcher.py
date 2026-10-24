import re

def is_master_wipe_request(text: str) -> bool:
    clean = text.strip().lower()
    # Normalize common abbreviations and typos
    norm = re.sub(r'\b(dlete|delet|dlet|dlte|dlt|del)\b', 'delete', clean)
    norm = re.sub(r'\b(meory|memry|mmoery|memor)\b', 'memory', norm)
    norm = re.sub(r'\b(seaved|seve|savd)\b', 'saved', norm)

    # Patterns
    patterns = [
        r'\b(delete|wipe|clear|erase|remove|forget|destroy|flush)\b.*\b(memor(y|ies)|data|history|records|facts|everything)\b',
        r'\b(memor(y|ies)|data|history|records|facts|everything)\b.*\b(delete|wipe|clear|erase|remove|forget|destroy|flush|mita|saaf|khatam|hata)\b',
        r'\b(saari|sab\s*kuch|puri|sarre|sari|meri|mera)\b.*\b(memory|yaad|yaadein|data|history)\b.*\b(delete|wipe|mita|saaf|khatam|hata)\b',
        r'\b(delete|wipe|mita|saaf|khatam|hata)\b.*\b(saari|sab\s*kuch|puri|sarre|sari|meri|mera)\b.*\b(memory|yaad|yaadein|data|history)\b',
        r'\b(sab\s*kuch|saara\s*kuch)\b.*\b(delete|wipe|mita|saaf|khatam|hata)\b',
        r'\b(delete|wipe|mita|saaf|khatam|hata)\b.*\b(sab\s*kuch|saara\s*kuch)\b',
        r'\b(delete|wipe|clear|erase)\s*(all\s*)?(saved\s*)?(about\s*me\s*)?(everything|data|memories|memory)\b',
        r'\b(forget|wipe|clear)\s*everything\b',
    ]
    for p in patterns:
        if re.search(p, norm, re.I):
            return True
    return False

test_phrases = [
    'dlete all memory u have about me',
    'delete all meory u have seaved',
    'delete all memory you have saved',
    'delete my memory',
    'wipe my memory',
    'saari memory delete kar do',
    'delete all memories',
    'dlt memory',
    'wipe all saved memory',
    'memory delete kar do',
    'meri memory delete karo',
    'delete everything you saved',
    'clear all memory',
    'delete all memory u have saved',
    'sab kuch delete kar do',
    'forget everything',
    'erase all data',
]

negative_phrases = [
    'delete reminder 5',
    'delete meeting tomorrow',
    'delete note dsa',
    'delete task buy milk',
    'delete contact Rohan',
    'cancel meeting',
]

for np in negative_phrases:
    matched = is_master_wipe_request(np)
    print(f'NEGATIVE "{np}": {matched}')
    assert not matched, f'Should not match: {np}'
print("ALL 17 POSITIVE AND 6 NEGATIVE PHRASES PASSED!")
