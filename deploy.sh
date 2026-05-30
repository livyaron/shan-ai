#!/usr/bin/env bash
set -euo pipefail

git add app/ docs/ static/ vault/ \
        requirements.txt Dockerfile docker-compose.yml docker-compose.staging.yml \
        run_server.py check_rag.py reprocess_file.py ruff.toml CLAUDE.md

echo "Staged files:"
git status --short

read -rp "Commit message [deploy: sync local changes to Railway]: " MSG
MSG="${MSG:-deploy: sync local changes to Railway}"

git commit -m "$MSG" || echo "Nothing new to commit."

git push origin master
echo "Pushed to GitHub."

echo "Syncing learned instructions to Railway DB..."
python3 copy_instructions.py

echo "Deploying to Railway..."
unset RAILWAY_TOKEN
railway up --detach

echo ""
echo "Monitor: https://railway.app/project/369d826d-2dd9-42ae-879e-69f3806db3ed"
echo "App URL: https://easygoing-endurance-production-df54.up.railway.app"
echo "Done."
