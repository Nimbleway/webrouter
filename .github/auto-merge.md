# Review completion and automatic merging

Two workflows answer different questions:

| Gate | Meaning | Required for |
| --- | --- | --- |
| `qodo-reviewed` | Qodo finished reviewing this PR's current commit and its findings are available | Every merge, manual or automatic |
| `Qodo approved` label, applied by Qodo | Qodo's approval conditions are satisfied | Enabling automatic merging into `main` only |

An open finding does not fail `qodo-reviewed`. A code owner assesses it before approving, including
documenting a deliberate decision not to fix it. Without the approval label, maintainers can merge
manually once CI, review completion, code-owner approval and conversation requirements pass.
The label never overrides any GitHub protection.

## Review completion

`qodo-reviewed.yml` uses the official MCP SDK, pinned in the `review-gate` dependency group and
`uv.lock`, to read Qodo's stored `get-pr-review-findings` results. It calls no review-generation
or finding-resolution tools. Qodo documents MCP as its integration for
[remote workflows](https://docs.qodo.ai/agentic-toolbox/mcp).

Success requires the expected PR URL, a completed session with a valid run ID and UTC start time,
`review_complete: true`,
a matching current head SHA, and available, nontruncated findings. Finding severity, count, attribution
status and the approval label do not affect completion. The adapter re-reads the current head before
publishing success and writes the status only on the reviewed SHA.

PR opening, new commits, reopening, retargeting and becoming ready trigger reconciliation. Authenticated
Qodo issue-comment events wake the same reader when review results arrive. Comment text
is never parsed as evidence. The reader makes twelve attempts with ten-second pauses and a
three-minute deadline for stored results to catch up.
Missing, running, incomplete and stale reviews leave the status pending; API or authentication failures
leave it in error. A missing status also blocks merging once GitHub requires it.

There is no documented vendor completion-webhook guarantee. If no notification arrives, or propagation
outlasts the bounded wait, use **Actions → Qodo review completion → Run workflow**, selecting `main`
and entering the PR number. This re-reads stored results; it does not spend tokens on another review.
If Qodo has not reviewed the latest commit, request a final `/agentic_review` and reconcile after it
finishes. Disabling per-push reviews does not make an older review valid for a newer commit.

Completion reconciliation handles every target branch. GitHub enforcement must be configured on every
target branch covered by the all-merges policy, including integration branches; requiring the status
only on `main` does not enforce it elsewhere. GitHub commit statuses are shared by PRs at the same
SHA: the published evidence links the originating PR, but an identical SHA can reuse its status.
Code-owner approval and conversation rules remain PR-specific. Do not treat this as independent
verification of each PR's base branch or diff.

## Auto-merge

`auto-merge.yml` manages only native auto-merge requests. For a non-draft PR into `main`, it checks
that the approval label is currently present and that GitHub's latest event for that label records
Qodo's bot account (ID `151058649`) as the actor. It re-reads live state before arming squash
auto-merge with `--match-head-commit`. It also requires the latest App-produced `qodo-reviewed`
status to be successful, with the label applied no earlier than that review's UTC start time.
Completion-status events wake the controller too, so label and completion notifications can arrive
in either order. Each wake re-reads the status; webhook data alone never proves completion.
GitHub then waits for all required checks and reviews.

A new commit, draft conversion, removed or untrusted label, or retarget away from `main` disables
existing auto-merge. Every target branch is reconciled, so a queued retarget cleanup can be replaced
without losing cleanup on the next event. A delayed label-removal event cannot undo a newer authentic
label. Integration branches are never armed automatically; any existing auto-merge is disabled there.
Stack parents can
retain merge commits, with squash for the final PR into `main`.

Label presence authorizes arming; label removal revokes it asynchronously. An already eligible merge
can race the removal webhook before the workflow disables auto-merge. The label is deliberately not
a required GitHub check because that would also prevent the approved manual-merge path.
Comparing the label time to the review start rejects an old retained label, but does not establish a
vendor-guaranteed commit binding against delayed vendor writes. A later review of the same commit
may require Qodo to refresh the label before automation re-arms; manual merging remains available.
The controller cannot prevent someone with write access from manually enabling native auto-merge;
maintainers should follow the label policy when using automation.

Both workflows check out trusted default-branch code only, never PR code, and do not persist Git
credentials. The completion token has no merge permission, and the merge token cannot produce review evidence.
Do not add a credentialed `pull_request_review` trigger: that event uses the PR's workflow definition.

## Configuration and rollout

Install a repository-scoped GitHub App with Contents write, Pull requests write and Commit statuses
write permissions. The merge workflow requests only Contents/Pull requests write; the completion
workflow requests only Contents/Pull requests read and Commit statuses write.

Set `WEBROUTER_AUTOMERGE_APP_ID` and `WEBROUTER_AUTOMERGE_APP_PRIVATE_KEY`. Provision
`QODO_REVIEW_READ_API_KEY` separately through GitHub secrets, using a CI-specific Qodo principal with
access to this repository and the read tool. Do not copy a developer's keychain credential.
The adapter uses the documented SaaS endpoint; a different deployment needs its own reviewed
endpoint configuration. Never log the key or raw vendor responses.

Roll out with automatic merging disabled until the required gate is verified:

1. Verify the effective GitHub Actions policy permits these trusted `pull_request_target` workflows.
2. Merge the workflows, then disable **Auto-merge controller** before configuring its App credentials.
3. Configure the App and Qodo read key. Run completion on a draft PR and verify that the App publishes
   `qodo-reviewed` on the PR head SHA, not just a successful job on the base branch.
4. Exercise a completed review with open findings and no approval label, a new push after approval,
   a stale/running review, and missing or invalid Qodo credentials. Verify the completion notification
   path on a fork PR and manual dispatch recovery. Qodo issue-comment wakeups run on the default
   branch. Recovery dispatch must select the trusted default branch too.
5. Require `qodo-reviewed` from the controller App on all target branches covered by the policy,
   alongside existing CI, code-owner and conversation requirements. Remove `qodo-ready` if it was
   previously configured. Preserve stale-review dismissal and administrator enforcement.
6. Enable **Auto-merge controller**. Verify authenticated-label arming and label-removal cleanup.
   Test manual merging without the label after a completed review and current code-owner approval.

A draft PR or local test cannot validate live credentials, GitHub event policy or branch protection.
Until these rollout steps are complete, the new gate is proposed, not enforced.

## Recovery and rollback

Re-run the completion workflow after restoring its credential or connectivity. Re-run a failed merge
controller only after checking the current label and head. Never manufacture a green completion
status or remove required checks just to merge a PR with missing evidence.

To roll back automation alone, disable **Auto-merge controller**; review completion continues to gate
manual merges. To roll back the completion integration, coordinate removal of `qodo-reviewed` from
every protected target branch before disabling its producer. Other CI and human-review protections
remain in effect.
