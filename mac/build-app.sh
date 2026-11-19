#!/usr/bin/env bash
# Build Hangar.app (Hangar for Mac's menu bar app) into mac/build/.
# It runs Hangar from this checkout (mac/bin/hangar, agents on Apple's container),
# so keep the checkout where it is, or rebuild after moving it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
app="$here/build/Hangar.app"
version="0.1.0"

echo "Building…"
swift build -c release --package-path "$here/HangarMac" >/dev/null
bin="$(swift build -c release --package-path "$here/HangarMac" --show-bin-path)/HangarMac"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$bin" "$app/Contents/MacOS/Hangar"

# Icon: ✦ on Hangar's night blue.
tmp="$(mktemp -d)"
uv run --project "$repo" --with pillow python "$here/tools/make_icon.py" "$tmp/icon.png"
iconset="$tmp/AppIcon.iconset"; mkdir -p "$iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$tmp/icon.png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$tmp/icon.png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
rm -rf "$tmp"

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hangar</string>
  <key>CFBundleDisplayName</key><string>Hangar</string>
  <key>CFBundleIdentifier</key><string>dev.hangar.mac</string>
  <key>CFBundleExecutable</key><string>Hangar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>HangarRepo</key><string>$repo</string>
  <key>CFBundleURLTypes</key><array><dict>
    <key>CFBundleURLName</key><string>dev.hangar.mac</string>
    <key>CFBundleURLSchemes</key><array><string>hangar-mac</string></array>
  </dict></array>
</dict></plist>
PLIST

# Hangar's windows (the Qt app, as on Hangar OS) run from a helper app inside this
# one, so the Dock shows "Hangar" with its icon, not "python3.x": a copy of the
# project's Python interpreter, pointed at the project's environment.
uv sync --project "$repo" --extra apps >/dev/null 2>&1 || true
venv="$repo/.venv"
home="$(sed -n 's/^home = //p' "$venv/pyvenv.cfg")"
py="$(cd "$venv/bin" && readlink -f python)"
ui="$app/Contents/Helpers/Hangar.app/Contents"
mkdir -p "$ui/MacOS" "$ui/Resources"
cp "$py" "$ui/MacOS/Hangar"
cp "$venv/pyvenv.cfg" "$ui/pyvenv.cfg"          # Contents/ is this interpreter's environment...
ln -s "$venv/lib" "$ui/lib"                      # ...with the project's packages
cp "$app/Contents/Resources/AppIcon.icns" "$ui/Resources/"
cat > "$ui/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hangar</string>
  <key>CFBundleDisplayName</key><string>Hangar</string>
  <key>CFBundleIdentifier</key><string>dev.hangar.mac.windows</string>
  <key>CFBundleExecutable</key><string>Hangar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$ui/MacOS/Hangar" >/dev/null 2>&1 || true   # its environment files aren't bundle code
codesign --force --sign - "$app" >/dev/null 2>&1   # ad-hoc: runs on this Mac
echo "Built $app"
