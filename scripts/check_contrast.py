"""Check the colour tokens against WCAG 2.2 contrast minimums, both themes.

    python scripts/check_contrast.py

Body text needs 4.5:1; large text, icons and focus indicators need 3:1.
Exits non-zero if any pair fails.
"""

from __future__ import annotations

import os
import re
import sys

TOKENS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "static", "css", "tokens.css")

# (foreground, background, minimum ratio, what it's used for)
PAIRS = [
    ("text", "bg", 4.5, "body text"),
    ("text", "surface", 4.5, "text in panels"),
    ("text", "raised", 4.5, "hovered rows, inputs"),
    ("text-2", "bg", 4.5, "secondary text"),
    ("text-2", "surface", 4.5, "secondary text in panels"),
    ("text-2", "raised", 4.5, "region chips, hovered rows"),
    ("muted", "bg", 4.5, "captions, labels"),
    ("muted", "surface", 4.5, "captions in panels"),
    ("muted", "raised", 4.5, "captions on hovered rows"),
    ("accent-text", "bg", 4.5, "links"),
    ("accent-text", "surface", 4.5, "links in panels"),
    ("accent-ink", "accent", 4.5, "text on amber buttons and badges"),
    ("pos", "bg", 4.5, "positive numbers"),
    ("pos", "surface", 4.5, "positive numbers in panels"),
    ("neg", "bg", 4.5, "negative numbers"),
    ("neg", "surface", 4.5, "negative numbers in panels"),
    ("mixed", "surface", 4.5, "mixed reviews"),
    ("accent", "bg", 3.0, "focus ring, meter needle zone"),
    ("accent", "surface", 3.0, "accent marks in panels"),
]


def parse(css: str) -> dict[str, dict[str, str]]:
    def block(selector: str) -> dict[str, str]:
        m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", m.group(1))) if m else {}

    dark = block(":root")
    light = {**dark, **block(':root[data-theme="light"]')}
    return {"dark": dark, "light": light}


def luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main() -> int:
    with open(TOKENS, encoding="utf-8") as f:
        themes = parse(f.read())
    failures = 0
    for theme, tokens in themes.items():
        print(f"\n{theme} theme")
        for fg, bg, minimum, use in PAIRS:
            r = ratio(tokens[fg], tokens[bg])
            ok = r >= minimum
            failures += not ok
            print(f"  {'ok  ' if ok else 'FAIL'} {r:5.2f}:1 (needs {minimum}) {fg} on {bg} — {use}")
    print(f"\n{'All pairs pass.' if not failures else f'{failures} pair(s) fail.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
