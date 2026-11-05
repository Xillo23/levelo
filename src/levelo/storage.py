"""Settings and crash-recovery files in %APPDATA%\\Levelo."""

import json
import os

from . import APP_ID

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_ID)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATE_FILE = os.path.join(DATA_DIR, "restore.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out = json.loads(json.dumps(default))
            out.update(data)
            return out
    except Exception:
        pass
    return json.loads(json.dumps(default))


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
