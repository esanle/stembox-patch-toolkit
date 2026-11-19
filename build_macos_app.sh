#!/bin/bash
# Build macOS application for open-stembox

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

echo "📦 Building macOS application for open-stembox"
echo "   Project: $PROJECT_DIR"

# Step 1: Install Python dependencies
echo ""
echo "📋 Installing dependencies..."
pip3 install --break-system-packages --quiet PySide6-Essentials PySide6-Addons numpy onnxruntime 2>&1 | tail -2

# Step 2: Build Python package
echo ""
echo "📦 Building Python package..."
cd "$PROJECT_DIR/source"
pip3 install --break-system-packages --quiet . 2>&1 | tail -2

# Step 3: Create application bundle
echo ""
echo "📦 Creating macOS app bundle..."
mkdir -p "$PROJECT_DIR/dist"

# Create app.py script that can be run directly
cat > "$PROJECT_DIR/dist/app.py" << 'PYAPP'
#!/usr/bin/env python3
"""Open Stembox - Clean-room DJ application (macOS)"""

import sys
import os

# Add source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'source', 'src'))

from ostem.ui.mainwindow import MainWindow, build_window
from PySide6.QtWidgets import QApplication

def main():
    # Check for command line arguments (e.g., number of decks)
    decks = 2
    if len(sys.argv) > 1:
        try:
            decks = int(sys.argv[1])
        except ValueError:
            pass
    
    print(f"🎛️  Open Stembox v0.1.0")
    print(f"   Decks: {decks}  |  Platform: macOS")
    print(f"   Clean-room implementation | 1:1 feature parity with Stembox 2.0.0")
    print()
    
    # Create and show main window
    app = QApplication(sys.argv)
    app.setApplicationName("Open Stembox")
    app.setApplicationVersion("0.1.0")
    
    # Create themed window
    window = build_window(decks=decks, themed=True)
    window.setWindowTitle(f"Open Stembox v0.1.0 ({decks} decks)")
    window.resize(1400, 800)
    window.show()
    
    # Run event loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
PYAPP

chmod +x "$PROJECT_DIR/dist/app.py"

# Step 4: Create app launcher (for macOS .app)
echo ""
echo "📦 Creating .app launcher..."
mkdir -p "$PROJECT_DIR/dist/Open Stembox.app/Contents/MacOS"
cat > "$PROJECT_DIR/dist/Open Stembox.app/Contents/MacOS/app" << 'PYAPP'
#!/bin/bash
# Open Stembox launcher
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
export PYTHONPATH="$REPO_ROOT/source/src:$PYTHONPATH"
exec python3 "$SCRIPT_DIR/app.py" "$@"
PYAPP
chmod +x "$PROJECT_DIR/dist/Open Stembox.app/Contents/MacOS/app"

cat > "$PROJECT_DIR/dist/Open Stembox.app/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>app</string>
    <key>CFBundleIconFile</key>
    <string></string>
    <key>CFBundleIdentifier</key>
    <string>com.openstembox.app</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Open Stembox</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHumanReadableCopyright</key>
    <string>MIT License</string>
    <key>NSMainNibFile</key>
    <string></string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST

echo ""
echo "✅ Build complete!"
echo "   Run: $PROJECT_DIR/dist/app.py"
echo "   Or:  open \"$PROJECT_DIR/dist/Open Stembox.app\""
