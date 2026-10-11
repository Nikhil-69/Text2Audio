#!/bin/bash
# One-click macOS setup: Python env, dependencies, ffmpeg/espeak-ng, and a Text2Audio.app
# in /Applications. Safe to re-run. Keep this folder where it is (the app launches from it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
say() { printf '\n\033[36m>>> %s\033[0m\n' "$*"; }
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

say "Text2Audio setup starting"

# 1) Homebrew (provides Python, ffmpeg and espeak-ng)
if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew (it may ask for your password)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

# 2) System tools + Python
for pkg in python@3.11 ffmpeg espeak-ng; do
  brew list "$pkg" >/dev/null 2>&1 || { say "Installing $pkg"; brew install "$pkg"; }
done
PYBIN="$(brew --prefix python@3.11)/bin/python3.11"

# 3) Virtual environment + dependencies (PyTorch's default macOS build uses Apple GPU/CPU)
[ -x .venv/bin/python ] || { say "Creating virtual environment"; "$PYBIN" -m venv .venv; }
say "Installing Text2Audio dependencies (first time takes several minutes)"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch
.venv/bin/python -m pip install -r requirements.txt pywebview

# 4) The app
say "Building Text2Audio.app"
./scripts/make_mac_app.sh
rm -rf /Applications/Text2Audio.app 2>/dev/null || true
if cp -R dist/Text2Audio.app /Applications/ 2>/dev/null; then
  say "Done! Open Text2Audio from your Applications folder (or Launchpad)."
else
  say "Done! Open dist/Text2Audio.app (copy it to Applications if you like)."
fi
