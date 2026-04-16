"""Extract Python DAT text from TouchAPI.tox for auditing vs toe/src/td_api.py.

Usage: python scripts/extract_tox.py  (requires TouchDesigner installed)

The .tox format is a TD binary. TD ships a `toeexpand` tool that unpacks
tox/toe files into a readable folder of XML + Python. If that tool is on PATH,
this script invokes it. Otherwise it prints instructions for manual extraction.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOX = REPO / "toe" / "TouchAPI.tox"
OUT = REPO / "toe" / "src" / "td_api.py"


def main() -> int:
    if not TOX.exists():
        print(f"no tox at {TOX}; nothing to extract")
        return 0
    toeexpand = shutil.which("toeexpand")
    if toeexpand:
        subprocess.run([toeexpand, str(TOX)], check=True)
        print(f"Expanded {TOX.name}. Copy the td_api DAT text into {OUT} and diff.")
        return 0
    print(
        "toeexpand not in PATH. To audit: open the .tox in TD, find the td_api\n"
        "Text DAT inside the component, copy its contents into toe/src/td_api.py,\n"
        "and diff against git. Reject the PR if anything in the .tox differs from\n"
        "what's in source."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
