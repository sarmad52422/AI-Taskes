import json
from pathlib import Path

STATE_PATH = Path('/app/route_state.json')

def load_state(path=STATE_PATH):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)

def save_state(document, path=STATE_PATH):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write('\n')
    return document
