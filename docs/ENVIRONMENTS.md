# Isolated environments and release gates

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
