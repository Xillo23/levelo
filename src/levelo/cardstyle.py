"""ttk styles for controls that sit on a card.

The Sun Valley theme draws every control with images whose corners are already
painted with the window background colour (not transparent). On a card, which
is lighter (dark theme) or whiter (light theme) than the window, a switch or a
button then shows a dark/grey square around it.

This module makes copies of those images with the outside repainted in the card
colour, creates "LvCard.*" elements from them and the styles
    Card.Switch.TCheckbutton  Card.TRadiobutton  Card.TButton
    Card.Accent.TButton       Card.Toggle.TButton  Card.TCombobox
which look like the normal ones, but without the square (and without the focus
ring that stayed around a switch or a button after clicking it).
With prefix="Plain" and the window colour, the same styles without the focus
ring for the controls of the panel.
"""

import base64
import io
import os
import re

import numpy as np
from PIL import Image

# the styles copied: "<prefix>.<style>" (e.g. Card.TButton) for each of these
BASES = ("Switch.TCheckbutton", "TCheckbutton", "TRadiobutton", "TButton",
         "Accent.TButton", "Toggle.TButton", "TCombobox")
STYLES = tuple(f"Card.{b}" for b in BASES)
ELEMENTS = ("Switch.indicator", "Checkbutton.indicator", "Radiobutton.indicator", "Button.button",
            "AccentButton.button", "ToggleButton.button", "Combobox.field")

_images = []      # keep the PhotoImages alive


def _theme_dir():
    import sv_ttk
    return os.path.join(os.path.dirname(sv_ttk.__file__), "theme")


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def recolor(img, bg, card):
    """Move a sprite painted on the theme background onto the card colour.

    Each pixel is read as a translucent white (lighter) or black (darker) layer
    over the background, as in Windows 11: its opacity a is estimated from how far
    it is from the background, and the background part (1 - a) is swapped for the
    card colour. Corners (a = 0) become the card; solid colours (a = 1) stay."""
    arr = np.asarray(img.convert("RGBA"), dtype=np.float32)
    rgb = arr[..., :3]
    up = (rgb - bg) / np.maximum(255.0 - bg, 1.0)
    down = (bg - rgb) / np.maximum(bg, 1.0)
    a = np.clip(np.maximum(up, down).max(axis=2), 0.0, 1.0)
    rgb += (card - bg) * (1.0 - a)[..., None]
    arr[..., :3] = np.clip(rgb, 0, 255)
    return Image.fromarray(arr.round().astype(np.uint8), "RGBA")


def _element_specs(tcl_source):
    """{element: (image list text, options)} for the elements we copy."""
    specs = {}
    pat = re.compile(r"ttk::style element create (\S+) image \\\s*\[list (.*?)\]\s*([^\n]*)",
                     re.S)
    for name, lst, opts in pat.findall(tcl_source):
        if name in ELEMENTS:
            lst = " ".join(lst.replace("\\\n", " ").split())
            # no focus ring: after a mouse click it stayed around the control like
            # a frame (Windows shows it only when the keyboard is used)
            lst = re.sub(r"(\{[^}]*\bfocus\b[^}]*\}|\bfocus)\s+\$I\([a-z0-9-]+\)\s*", "", lst)
            specs[name] = (lst.strip(), opts.strip())
    return specs


def install(root, card_hex, prefix="Card"):
    """Create the <prefix>.* styles for the current theme (once per theme)."""
    tk = root.tk
    theme = tk.call("ttk::style", "theme", "use")
    tag = re.sub(r"\W", "_", f"{theme}_{prefix}_{card_hex}")
    if tk.eval(f"info exists ::lvcard_done({tag})") == "1":
        return
    kind = "dark" if "dark" in str(theme) else "light"
    folder = _theme_dir()
    sheet = Image.open(os.path.join(folder, f"spritesheet_{kind}.png")).convert("RGBA")
    with open(os.path.join(folder, f"sprites_{kind}.tcl"), encoding="utf-8") as f:
        info = {n: tuple(map(int, v)) for n, *v in
                re.findall(r"^\s*([a-z0-9-]+) (\d+) (\d+) (\d+) (\d+)", f.read(), re.M)}
    with open(os.path.join(folder, f"{kind}.tcl"), encoding="utf-8") as f:
        specs = _element_specs(f.read())
    bg = np.array(sheet.getpixel((info["button-rest"][0], info["button-rest"][1]))[:3],
                  dtype=np.float32)
    card = _hex(card_hex)

    from tkinter import PhotoImage
    made = {}

    def image(name):
        if name not in made:
            x, y, w, h = info[name]
            buf = io.BytesIO()
            recolor(sheet.crop((x, y, x + w, y + h)), bg, card).save(buf, "PNG")
            ph = PhotoImage(master=root, name=f"lvcard_{tag}_{name}",
                            data=base64.b64encode(buf.getvalue()).decode("ascii"))
            _images.append(ph)
            made[name] = str(ph)
        return made[name]

    script = []
    for elem, (lst, opts) in specs.items():
        lst = re.sub(r"\$I\(([a-z0-9-]+)\)", lambda m: image(m.group(1)), lst)
        opts = re.sub(r"\$I\(([a-z0-9-]+)\)", lambda m: image(m.group(1)), opts)
        script.append(f"ttk::style element create Lv{prefix}.{elem} image [list {lst}] {opts}")
    tk.eval(f"ttk::style theme settings {theme} {{\n" + "\n".join(script) + "\n}")

    names = "|".join(re.escape(e) for e in specs)
    for base in BASES:
        style = f"{prefix}.{base}"
        layout = tk.eval(f"ttk::style layout {base}")
        layout = re.sub(rf"(?<![\w.])({names})(?![\w.])", rf"Lv{prefix}.\1", layout)
        tk.eval(f"ttk::style layout {style} {{{layout}}}")
        tk.call("ttk::style", "configure", style, "-background", card_hex)
    tk.eval(f"set ::lvcard_done({tag}) 1")


def fallback(root, prefix):
    """If install() failed: the <prefix>.* styles as plain copies, so they exist."""
    for base in BASES:
        try:
            layout = root.tk.eval(f"ttk::style layout {base}")
            root.tk.eval(f"ttk::style layout {prefix}.{base} {{{layout}}}")
        except Exception:
            pass
