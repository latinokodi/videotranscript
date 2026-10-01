#!/usr/bin/env python3
"""
gui.py - desktop app for turning a video's audio into a transcript with
accurate timestamps. 100% local, GPU-accelerated.

    uv run python gui.py

Design notes
------------
* All colour, spacing, radius and type values live in one token table
  (`DARK` / `LIGHT` in `vtgui/theme.py`), and the stylesheet is *generated* from
  those tokens, so the two themes can never drift apart.
* Palette: near-black neutrals (never pure #000), one desaturated emerald
  accent, pale tints for status. Body text is >= 4.5:1 on its surface.
* Fonts: Segoe UI Variable Display (system-native) + Cascadia Mono for numbers.
* Icons are Qt's own standard pixmaps - one consistent family, no emoji.

The implementation lives in the `vtgui` package; this module is the stable
facade that the launcher, the screenshot tool and the tests import.
"""

from __future__ import annotations

# Re-exported on purpose: tests walk a window's actions with `gui.QAction`.
from PySide6.QtGui import QAction  # noqa: F401

from vtgui import *  # noqa: F403 - re-export the documented GUI surface
from vtgui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
