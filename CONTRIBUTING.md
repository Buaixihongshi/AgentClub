# Contributing to AgentClub

AgentClub uses a lightweight trunk-based workflow. `main` is the only
long-lived branch and should remain releasable.

## Branches

Create one short-lived branch per independent change from the latest `main`:

```bash
git switch main
git pull --ff-only
git switch -c codex/<type>-<short-description>
```

Use one of these branch types when possible:

- `feat` for a new capability
- `fix` for a bug fix
- `refactor` for behavior-preserving restructuring
- `docs` for documentation-only changes
- `chore` for tooling, CI, or maintenance

Do not reuse a branch whose pull request has already been merged.

## Pull requests

- Keep one task in one branch and one pull request.
- Target `main` unless the pull request is intentionally stacked.
- Add tests for behavior changes and keep the pull request focused.
- Push review fixes to the same branch; the existing pull request updates
  automatically.
- Merge only after the required CI checks pass and all review conversations
  are resolved.
- Prefer squash merging so each pull request becomes one coherent commit on
  `main`.
- Delete the topic branch after merge.

If another pull request is merged first, update your branch before merging:

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease
```

Never force-push `main`.

## Local verification

Run the same core checks as CI before requesting review:

```bash
./.venv/bin/python -m pytest backend/tests -q
cd frontend
npm test
npm run build
```

CI also runs the offline smoke suite, dependency consistency check, and
privacy check.
