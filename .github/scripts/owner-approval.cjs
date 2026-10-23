// Pure policy, shared by the manual workflow and offline tests.
function validateApproval({ actor, ref, owner, pr, sha }) {
  if (actor !== owner || ref !== 'refs/heads/main') throw Error('Only the owner may approve from main');
  if (!/^[0-9a-f]{40}$/.test(sha)) throw Error('A full lowercase commit SHA is required');
  if (pr.state !== 'open' || pr.draft || pr.merged || pr.base.ref !== 'main' || pr.head.sha !== sha) {
    throw Error('PR must be open, ready and based on main at the exact reviewed SHA');
  }
  return { state: 'success', context: 'Owner approval', sha,
    description: `${owner} approved this exact head` };
}
module.exports = { validateApproval };
