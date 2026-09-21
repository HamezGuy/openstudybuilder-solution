# Working rules for this repository

**Main only.** Owner directive, 2026-09-20, for every repository under C:\Projects. Work on
`main` in this checkout. Never create or switch to another branch, never create a git
worktree (no `git worktree add`, no worktree session), never work in `.claude\worktrees`.
Commit finished work to `main` and push `main` to origin before you stop; git's HTTPS
transport hangs on the credential manager here, so push with
`git -c http.extraheader="Authorization: Basic $(printf 'x-access-token:%s' "$(gh auth token)" | base64 -w0)" push origin main`.
The shared hooks in `C:\Projects\.githooks` refuse commits off `main` and pushes of any
other branch; do not bypass them. Do not re-implement what already exists: search first,
extend the existing module, and delete duplicates. The full rule is `C:\Projects\CLAUDE.md`.

Never merge or fast-forward a `codex/igs-production-*` snapshot branch: those are pruned
runtime snapshots that delete whole top-level trees. Their added files were landed on
`main` on 2026-09-20; the snapshot tips survive only as `archive/*` tags.
