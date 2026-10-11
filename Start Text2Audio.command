#!/bin/bash
# Double-click to open Text2Audio Studio in its own window (no browser).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
[ -x .venv/bin/python ] || { echo "Missing .venv - see INSTALL.md"; read -n1 -p "Press any key"; exit 1; }
.venv/bin/python desktop.py
