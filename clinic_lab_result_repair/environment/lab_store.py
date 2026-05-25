import json
from pathlib import Path

DATA_PATH = Path('/app/lab_data.json')

def load_document():
    with DATA_PATH.open() as f:
        return json.load(f)

def save_document(document):
    with DATA_PATH.open('w') as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write('\n')
    return document
