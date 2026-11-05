"""Entry point.

    Levelo.exe                 start (or show the running instance)
    Levelo.exe --background    start hidden in the notification area
    Levelo.exe --quit          close the running instance cleanly
"""

from . import _bootstrap  # noqa: F401  (must come first)

import sys

from .winsys import SingleInstance


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    instance = SingleInstance()
    if "--quit" in argv:
        if instance.already:
            instance.signal_quit()
        return 0
    if instance.already:
        instance.signal_show()
        return 0
    from .ui import App
    App(background="--background" in argv, instance=instance).run()
    return 0
