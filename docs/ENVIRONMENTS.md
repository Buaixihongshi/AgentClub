# Isolated environments and release gates

## Read-only environment status

From any checkout containing this change:

```sh
./scripts/env.sh status
./scripts/env.sh status --json
./scripts/env.sh status --checkout ../another-clone --ports 8300:5176
```

The command does not install dependencies, change branches, refresh remotes,
restart services, recover missions, or delete worktrees. Python 3 is required;
it uses this checkout's `.venv/bin/python` when present. `lsof` and `ps` add
listener PID, process start time and working-directory evidence on macOS/Linux.
If those tools are unavailable, health/identity inspection still works, but
listener details can be empty. Status is an inventory command, not a CI gate;
an absent optional environment does not make it exit nonzero.

It inspects loopback ports 8000/5173, 8100/5174 and 8200/5175, plus additional
`--ports backend:frontend` pairs. It inventories registered worktrees, existing
sibling `agentclub-environments/{development,dev,test,runtime}` clones, listener
working directories, and explicit `--checkout` paths. Independent clones in
other locations need `--checkout`; it does not scan the whole machine.

The local report includes absolute paths and must not be committed or attached
to public issues without review. It never reads conversation bodies, credentials
or provider configuration contents into the report, and never prints process
command arguments or remote URLs.

### Source identity

`GET /api/health` retains its existing fields and adds `runtime`:

- `started`: a frozen backend module-initialization snapshot: SHA, branch,
  dirty flag, source digest, checkout ID, environment, capture time, PID, Python,
  data/config/artifact/maintenance paths and the demo configuration flag.
- `current`: the current on-disk source snapshot, separate from `started`.
- `source_changed`: whether the SHA or source digest changed since initialization;
  `null` means there is insufficient Git/source evidence.

The frontend embeds its own snapshot at Vite startup or production build and
serves the same public metadata at `/__environment`. Production builds emit that
file alongside the bundle; Vite preview serves the built file rather than
replacing it with the checkout's latest identity. Custom static hosts must serve
this file as JSON rather than rewrite it to the app's HTML.

The page compares front/backend SHA, source digest, checkout ID and environment,
and shows on-disk drift. It refreshes backend health every 30 seconds while the
page is visible and on window focus. Frontend identity is a startup/build
snapshot, not a claim about every module after HMR. A backend reloader creates
a new application snapshot in its worker. A non-reloading backend retains its
old snapshot even if the branch or files change.

The digest covers first-party `backend/`, `frontend/`, `scripts/`, `config/` and
`requirements.txt`, including staged, unstaged, deleted and nonignored untracked
files. Ignored runtime state and docs-only edits do not alter that digest;
the separate dirty flag covers the entire checkout. This prevents different
uncommitted code at the same SHA from being labeled the same source. The
checkout ID is a hash of its canonical path, not a credential.

`YIREN_ENVIRONMENT` sets a label only; it does not isolate state or prove a
release. Existing isolated launchers set `development`/`test`; `dev-backend.sh`
defaults to `development`; other launches default to `local`. A deployment may
explicitly set `alpha`/`runtime`. The demo flag is configuration evidence, not
proof that a real Provider call succeeded; consult turn records for actual use.

Old services lacking identity remain **unknown**. A healthy response, process
start time, `checkout-sha` file or current Git HEAD cannot retroactively prove
the code already loaded in that process. Adopt identity on a later authorized
restart; do not restart a running service just to populate this report.

The initial inventory and non-destructive cleanup plan are recorded in
[the PR 1 inventory](plans/2026-10-22-environment-inventory.md).

## Existing isolated launchers

Use separate clones of the same remote for development and test. Never run these commands in the production checkout.

```sh
git clone https://github.com/Buaixihongshi/AgentClub.git agentclub-dev
git clone https://github.com/Buaixihongshi/AgentClub.git agentclub-test
# In the respective clone, after checking out the proposed PR head:
bash scripts/start-isolated.sh development
bash scripts/start-isolated.sh test
```

Development uses backend/frontend ports 8100/5174; test uses 8200/5175. Each has private config, SQLite, artifacts, maintenance paths and runtime logs under `.yiren/<environment>/`. Configuration is copied once; existing config is preserved. Both default to demo mode and disable maintenance execution. `checkout-sha` records the checkout; a test report must also record whether it is dirty. Run the full CI commands before requesting release. No script switches or restarts production.

## Owner release

Only Buaixihongshi may run the **Owner release** workflow. Enter the PR number and full reviewed head SHA. It writes the required `Owner approval` status only for an open PR against main at that SHA. New commits require a new approval. Backend and frontend CI must independently pass. Agents must never invoke this workflow or post approval statuses on the owner's behalf.

This workflow must first be merged into main to become dispatchable. Activate its required status rule only after bootstrap merge; otherwise bootstrap cannot pass. GitHub native reviews cannot approve one's own PR, so the explicit owner workflow also supports PRs opened by the owner's account. The shared account's credentials cannot technically distinguish a human from an agent: credential separation is needed for that security boundary.

Production remains at its current deployment. A future switch requires idle-service verification, an exact approved SHA, isolated production state, and a recorded previous SHA for rollback. No automatic deployment is configured by this change.
