"""Extract Python DAT text from TouchAPI.tox for auditing vs toe/src/*.py.

Usage: python scripts/extract_tox.py  (requires TouchDesigner installed)

The .tox format is a TD binary. TD ships a `toeexpand` tool that unpacks
tox/toe files into a readable folder of XML + Python. If that tool is on PATH,
this script invokes it. Otherwise it prints instructions for manual extraction.

Audited DATs:
  - `td_api_src` -> toe/src/td_api.py
  - `td_layout`  -> toe/src/td_layout.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOX = REPO / "toe" / "TouchAPI.tox"
SOURCES = {
    "td_api_src": REPO / "toe" / "src" / "td_api.py",
    "td_layout":  REPO / "toe" / "src" / "td_layout.py",
}


def main() -> int:
    if not TOX.exists():
        print(f"no tox at {TOX}; nothing to extract")
        return 0
    toeexpand = shutil.which("toeexpand")
    if toeexpand:
        subprocess.run([toeexpand, str(TOX)], check=True)
        print(f"Expanded {TOX.name}. Diff each DAT against its source:")
        for dat_name, src_path in SOURCES.items():
            print(f"  {dat_name} -> {src_path}")
        return 0
    print(
        "toeexpand not in PATH. To audit: open the .tox in TD, and for each of\n"
        "the DATs below copy its text and diff against git. Reject the PR if\n"
        "anything in the .tox differs from what's in source."
    )
    for dat_name, src_path in SOURCES.items():
        print(f"  {dat_name} -> {src_path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
