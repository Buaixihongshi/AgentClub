const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validateApproval } = require('./owner-approval.cjs');
const sha = 'a'.repeat(40);
const input = { actor: 'owner', owner: 'owner', ref: 'refs/heads/main', sha,
  pr: { state: 'open', draft: false, merged: false, base: { ref: 'main' }, head: { sha } } };
test('approves only the exact ready head', () => {
  assert.equal(validateApproval(input).context, 'Owner approval');
  assert.equal(validateApproval(input).sha, sha);
});
test('rejects other actors, feature refs, stale SHAs, drafts and closed PRs', () => {
  for (const delta of [{ actor: 'agent' }, { ref: 'refs/heads/feature' }, { sha: 'b'.repeat(40) },
    { sha: 'short' }, { pr: { ...input.pr, draft: true } }, { pr: { ...input.pr, state: 'closed' } },
    { pr: { ...input.pr, base: { ref: 'feature' } } }]) {
    assert.throws(() => validateApproval({ ...input, ...delta }));
  }
});
