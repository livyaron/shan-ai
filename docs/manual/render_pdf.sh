#!/usr/bin/env bash
# Render the user manual to PDF via headless Chromium (one-shot Docker image).
# Usage: ./docs/manual/render_pdf.sh
# Output: docs/manual/manual.pdf
#
# Why Chromium and not weasyprint: weasyprint requires GTK/Pango/Cairo system
# libs that the slim Python container doesn't ship. Chromium has them all and
# renders Hebrew RTL + inline SVG perfectly.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTML_HOST="$SCRIPT_DIR/index.html"
PDF_HOST="$SCRIPT_DIR/manual.pdf"

if [ ! -f "$HTML_HOST" ]; then
    echo "❌ HTML source missing: $HTML_HOST"
    exit 1
fi

echo "Rendering $HTML_HOST → $PDF_HOST ..."
# MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from translating /data paths
# to C:/Program Files/Git/data. On Linux/macOS this var is harmless.
MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$SCRIPT_DIR:/data" \
    zenika/alpine-chrome:with-puppeteer \
    chromium-browser --headless --disable-gpu --no-sandbox \
        --print-to-pdf=/data/manual.pdf --no-pdf-header-footer \
        file:///data/index.html

if [ -f "$PDF_HOST" ]; then
    SIZE=$(stat -c%s "$PDF_HOST" 2>/dev/null || stat -f%z "$PDF_HOST")
    echo "✅ Generated $PDF_HOST ($SIZE bytes)"
else
    echo "❌ Output PDF not found at $PDF_HOST"
    exit 1
fi
