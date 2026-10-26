---
paths:
  - ".github/workflows/**"
  - "deploy.sh"
  - "Dockerfile"
  - "railway.*"
  - "tests/test_health_provenance.py"
---
# Deploy pipeline: full detail

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

- **Deployment is Railway-ONLY.** The local Docker stack is retired. All DB commands run against the Railway DB: `psql "$RAILWAY_DATABASE_URL" -c "..."` (URL in local `.env`, never commit it — repo is public). Never start a local Docker instance while Railway is live — it steals Telegram polling AND double-sends the 07:00 missions digest and overdue alerts.
- **Public URL:** `https://shan-ai.up.railway.app`. Never hardcode it in `app/` — use `settings.public_base_url`, which prefers Railway's auto-injected `RAILWAY_PUBLIC_DOMAIN` so a domain change self-heals. A test enforces this.
- **Build Cycle:** Pushing to `master` deploys itself. `.github/workflows/docker-build.yml` builds the image and runs the no-DB tests; a green run on `master` fires `.github/workflows/deploy.yml`, which does `railway up --detach --service easygoing-endurance` and then polls `/health` until it reports the new commit — so a **green deploy job is proof the commit is serving**, not merely uploaded. `./deploy.sh` from a machine with the Railway CLI is the manual fallback, not the primary path. **Verify every deploy landed**: `curl -s https://shan-ai.up.railway.app/health` reports the deployed `commit`; compare it against `git rev-parse --short=12 HEAD`. A 404 on a route you just added means the build is behind, not that the route is missing.
- **Always deploy after building (standing instruction, 2026-08-23):** don't stop at merging to `master` — verify the deploy landed, every time, without asking.
- **Remote sessions CAN deploy (corrected 2026-09-04).** The old note here said they could not, and it was wrong — it cost a "deploy still owed" report on a deploy that had already fired and failed unnoticed. What is true: the sandbox itself still cannot reach Railway (`shan-ai.up.railway.app`, `railway.app`, `backboard.railway.*` are all denied at the egress proxy) and holds no Railway CLI or `RAILWAY_TOKEN` — so **never `curl /health` from the sandbox to check a deploy; it will always look down.** But the deploy runs on a GitHub Actions runner, which is not in the sandbox, and the GitHub MCP tools drive it: `actions_list` (`list_workflow_runs`, branch `master`) to find the run, `get_job_logs` with `failed_only` to read a failure, `actions_run_trigger` (`rerun_failed_jobs`, or `run_workflow` on `deploy.yml`) to retry. Read the deploy job's own `/health` polling log for the verification, since the sandbox cannot do it.
- **A `railway up` timeout is not a code failure.** `error sending request ... backboard.railway.com ... Operation timed out (os error 110)` during Uploading is Railway's API being unreachable from the runner. Re-run the failed job; it has succeeded on the retry. Do not "fix" anything in response to it.
- **Check the deploy after every push to `master`.** A failed deploy is silent: `master` moves ahead and production keeps serving the old container. On 2026-09-04 production was found running a commit from 2026-08-23 for exactly this reason.
- **Always merge to master after building:** once a change builds clean on its feature branch (compiles / tests pass), merge it to `master` and push so Railway deploys. Standing authorization — no need to ask each time.
