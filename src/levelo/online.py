"""Optional online checks (privacy-friendly: plain GET of public files, no data sent).

1. links.json in the GitHub repository: lets the maintainer enable/change the
   donation link for every installed copy without shipping a new version.
2. GitHub "latest release": tells the user when a new version is available.
"""

import json
import threading
import time
import urllib.request

from . import GITHUB_REPO, __version__

CHECK_EVERY_S = 12 * 3600
TIMEOUT_S = 10


def _get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Levelo/{__version__}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _version_tuple(v):
    out = []
    for part in str(v).lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(remote, local=__version__):
    return _version_tuple(remote) > _version_tuple(local)


def fetch_links():
    """{'donate_url': str, 'website': str} or None."""
    if not GITHUB_REPO:
        return None
    data = _get_json(f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/links.json")
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("donate_url", "website"):
        val = data.get(key, "")
        # only accept https links
        out[key] = val if isinstance(val, str) and val.startswith("https://") else ""
    return out


def fetch_latest_release():
    """(version, page_url) of the latest GitHub release, or None."""
    if not GITHUB_REPO:
        return None
    data = _get_json(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
    tag, url = data.get("tag_name"), data.get("html_url")
    if not tag or not isinstance(url, str) or not url.startswith("https://github.com/"):
        return None
    return tag.lstrip("vV"), url


class OnlineChecker(threading.Thread):
    """Background thread; calls on_result(links, release) after every check."""

    def __init__(self, on_result):
        super().__init__(daemon=True, name="OnlineChecker")
        self.on_result = on_result

    def run(self):
        time.sleep(20)   # do not slow down startup / login
        while True:
            links = release = None
            try:
                links = fetch_links()
            except Exception:
                pass
            try:
                release = fetch_latest_release()
            except Exception:
                pass
            try:
                self.on_result(links, release)
            except Exception:
                pass
            time.sleep(CHECK_EVERY_S)
