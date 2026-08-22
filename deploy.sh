#!/usr/bin/env bash
set -euo pipefail

git add app/ docs/ static/ vault/ tests/ scripts/ .github/ \
        requirements.txt Dockerfile docker-compose.yml docker-compose.staging.yml \
        run_server.py check_rag.py reprocess_file.py ruff.toml pytest.ini \
        CLAUDE.md .env.example railway.toml

echo "Staged files:"
git status --short

read -rp "Commit message [deploy: sync local changes to Railway]: " MSG
MSG="${MSG:-deploy: sync local changes to Railway}"

git commit -m "$MSG" || echo "Nothing new to commit."

git push origin master
echo "Pushed to GitHub."

# The script that lives in scripts/, not at the repo root. Getting this path
# wrong meant `set -e` killed the script HERE — after the push, before the
# deploy — so master moved ahead while the running container never changed.
# Non-fatal on purpose now: syncing instructions is an extra, and must never
# again be the reason a deploy does not happen.
echo "Syncing learned instructions to Railway DB..."
python3 scripts/copy_instructions.py || echo "⚠️  Instruction sync failed — continuing to deploy anyway."

echo "Deploying to Railway..."
unset RAILWAY_TOKEN
railway up --detach

echo ""
echo "Monitor: https://railway.app/project/369d826d-2dd9-42ae-879e-69f3806db3ed"
echo "App URL: https://shan-ai.up.railway.app"
echo ""
echo "Verify the deploy actually landed (commit should match HEAD below):"
echo "  local HEAD: $(git rev-parse --short=12 HEAD)"
echo "  deployed:   curl -s https://shan-ai.up.railway.app/health | grep -o '\"commit\":\"[^\"]*\"'"
echo "Done."
