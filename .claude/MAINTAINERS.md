# Maintaining Claude's instructions (for humans — not loaded by Claude)

- `CLAUDE.md` is the always-loaded root. Keep it under ~80 lines; anything domain-specific goes in `.claude/rules/*.md` with `paths:` frontmatter so it loads only when Claude touches a matching file.
- Watch rule-file weight: a file that matches several rules loads all of them (e.g. `app/main.py` → database + llm-providers + telegram; `app/routers/projects.py` → patterns-engine + projects-master-file ≈ 20 KB after the 2026-11-16 condense). Split or trim a rule when it grows past ~10 KB.
- Model strategy: Sonnet for routine coding; Opus for schema redesigns and multi-file refactors of the decision flow. Before an Opus pass: search for existing utilities, draft PLAN.md, then ask "Review @PLAN.md for logical fallacies and edge cases."
- Run `/clear` between unrelated tasks.
- After editing an instruction file, check by hand that every path, module and command it names still exists (there is no built-in audit command).
