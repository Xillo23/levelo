"""Levelo - automatic volume leveling for Windows."""

__version__ = "0.10.1"
BETA = True   # shown as "beta" in the panel; set False for 1.0

APP_NAME = "Levelo"
APP_ID = "Levelo"

# GitHub repository "owner/name". Used to read links.json (donation link that can
# be enabled later for every installed copy) and to check for new releases.
# Empty = no online checks at all.
GITHUB_REPO = "Xillo23/levelo"

# Built-in fallbacks (links.json in the repository overrides them)
REPO_URL = f"https://github.com/{GITHUB_REPO}" if GITHUB_REPO else ""
DONATE_URL = "https://ko-fi.com/xillo23"
