"""Settings and crash-recovery files in %APPDATA%\\Levelo."""

import json
import os

from . import APP_ID

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_ID)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATE_FILE = os.path.join(DATA_DIR, "restore.json")
LOG_FILE = os.path.join(DATA_DIR, "levelo.log")


def load_json(path, default):
    """Read a settings file, falling back to its backup copy if it is damaged."""
    for candidate in (path, path + ".bak"):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out = json.loads(json.dumps(default))
                out.update(data)
                return out
        except Exception:
            continue
    return json.loads(json.dumps(default))


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            # keep the previous good copy: used if the file is ever damaged
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        pass


def log(msg):
    """Append a line to levelo.log (kept small): helps with bug reports."""
    try:
        import time
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass
