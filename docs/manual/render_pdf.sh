#!/usr/bin/env bash
# Render the user manual to PDF using weasyprint inside the shan-ai-api container.
# Usage: ./docs/manual/render_pdf.sh
# Output: docs/manual/manual.pdf
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTML_HOST="$SCRIPT_DIR/index.html"
PDF_HOST="$SCRIPT_DIR/manual.pdf"
HTML_CONTAINER="/app/docs/manual/index.html"
PDF_CONTAINER="/app/docs/manual/manual.pdf"

if ! docker ps --format '{{.Names}}' | grep -q '^shan-ai-api$'; then
    echo "shan-ai-api container is not running. Start it with:"
    echo "  docker-compose up -d"
    exit 1
fi

echo "Ensuring weasyprint is installed in the container..."
docker exec shan-ai-api pip install --quiet 'weasyprint>=60' 2>&1 | tail -3

echo "Rendering $HTML_CONTAINER → $PDF_CONTAINER ..."
docker exec shan-ai-api python -c "
from weasyprint import HTML
HTML('$HTML_CONTAINER').write_pdf('$PDF_CONTAINER')
print('OK')
"

if [ -f "$PDF_HOST" ]; then
    SIZE=$(stat -c%s "$PDF_HOST" 2>/dev/null || stat -f%z "$PDF_HOST")
    echo "✅ Generated $PDF_HOST ($SIZE bytes)"
else
    echo "❌ Output PDF not found at $PDF_HOST"
    exit 1
fi
