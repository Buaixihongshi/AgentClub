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
- Merge only after the required CI checks pass and applicable reviews and
  review conversations are resolved. The owner decides whether to merge;
  agents must not merge or enable auto-merge on their own. No additional
  Owner release workflow or Owner approval status is required.
- After an additional commit, rerun checks for the new head and revisit any
  reviews affected by the change.
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

## Collaborator attribution

Every pull request must identify its actual collaborators and their contributions
in a **Collaborators / 协作者** section. Include human contributors and any AI
assistant that materially helped with implementation, testing, review, or docs.
Record only work that actually occurred; do not invent reviewers or approvals.

Keep the human Git author identity. For commits materially co-authored with
Codex, leave a blank line after the message and add this trailer:

```text
Co-authored-by: Codex <noreply@openai.com>
```

For other collaborators, use their confirmed name and preferred commit email;
do not guess account addresses. A PR description is useful disclosure, but it
does not replace commit attribution. When squash merging, preserve the relevant
`Co-authored-by` trailers in the final commit message on `main`.

GitHub's Contributors graph is computed from eligible commits on the default
branch and account-linked email addresses. An AI attribution trailer does not
guarantee a separate graph entry. Do not change the human author or create empty
commits merely to influence the graph.

References: [GitHub co-author trailers](https://docs.github.com/en/pull-requests/how-tos/commit-changes/creating-a-commit-with-multiple-authors),
[Contributors graph rules](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors).

## Local verification

Use the canonical check groups shared with CI:

```bash
./scripts/check.sh all
```

During development, choose the affected groups (backend, frontend, smoke, eval,
privacy, hygiene, audit). Full delivery evidence records the actual SHA and source
digest; a source change during validation invalidates the receipt. Real-model
checks are separately opt-in. See [SOP](docs/SOP.md) and [environments](docs/ENVIRONMENTS.md).

Read the exact current PR head, CI and review readiness with
`./scripts/release.sh prepare --pr <number>`. This read-only report does not
merge a PR or replace applicable review. For stacked PRs, merge the dependency
first, retarget the next PR to main, reconcile squash history and rerun checks
and relevant review before merging it.

## Development, testing, and production

Use one shared remote, `https://github.com/Buaixihongshi/AgentClub`, with
separate task worktrees, a clean main-only alpha checkout, and managed release
checkouts. Use `scripts/env.sh dev <task>` for task branches; never edit the
running daily-service checkout. Existing legacy test clones are retained until
a separate verified migration. Keep test
and production databases, configuration, ports, and artifact directories separate.

Validate the proposed head commit in the test environment and record its SHA
and results in the PR. `main` contains reviewed, releasable code; the production
deployment must record the exact deployed SHA. A merge does not authorize an
immediate restart: switch deployments only after active missions finish and
the service is idle, retaining the previous version for rollback.

The owner controls PR release. CI success is a prerequisite, not approval.
Repository administrators should enforce PR review, stale approval dismissal,
required CI checks, and no direct or force pushes to `main` in GitHub settings.
Use `scripts/release.sh gate status` to inspect actual protection. The retired
owner check can be removed with `scripts/release.sh gate remove-owner` when
authorized by the owner; the command preserves CI, PR rules and other settings
and verifies the readback. There is no command to reinstall it. Deployment and state
recovery are separate explicit owner actions; see [release operations](docs/RELEASING.md).
