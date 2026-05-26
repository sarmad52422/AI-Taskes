import json
from pathlib import Path

DATA_PATH = Path('/app/payout_locks.json')


def load_document(path=DATA_PATH):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def save_document(document, path=DATA_PATH):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write('\n')
    return document
