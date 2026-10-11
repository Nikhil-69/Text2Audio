#!/bin/bash
# Build a double-clickable Text2Audio.app (macOS) that opens the Studio in its own
# native window - no browser. The app is a thin launcher: it runs desktop.py with
# this repo's virtualenv, so keep the repo and .venv where they are.
#
#   ./scripts/make_mac_app.sh            -> dist/Text2Audio.app
#   cp -R dist/Text2Audio.app /Applications/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Missing $PY - create the virtualenv first (see INSTALL.md)"; exit 1; }
"$PY" -c "import webview" 2>/dev/null || "$PY" -m pip install -q pywebview

APP="$ROOT/dist/Text2Audio.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Text2Audio</string>
  <key>CFBundleDisplayName</key><string>Text2Audio</string>
  <key>CFBundleIdentifier</key><string>local.text2audio.studio</string>
  <key>CFBundleExecutable</key><string>Text2Audio</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
PLIST

# Finder starts apps with a minimal PATH; ffmpeg/espeak-ng live in Homebrew.
# macOS blocks apps from reading protected folders (Downloads, Desktop, Documents)
# unless they have Full Disk Access. If the project can't be read, fall back to
# Terminal (which already has access) so the app still opens.
cat > "$APP/Contents/MacOS/Text2Audio" <<LAUNCH
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
if ! cat "$ROOT/.venv/pyvenv.cfg" >/dev/null 2>&1; then
  open -a Terminal "$ROOT/Start Text2Audio.command"
  exit 0
fi
cd "$ROOT"
exec "$PY" desktop.py
LAUNCH
chmod +x "$APP/Contents/MacOS/Text2Audio"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
echo "Built $APP"
