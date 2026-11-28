# Maintainer guide for merge automation

See [CONTRIBUTING.md](../CONTRIBUTING.md#merging) for the merge policy.

## Rollout

1. Verify that the repository's GitHub Actions policy permits the workflows'
   `pull_request_target` events. Keep **Auto-merge controller** disabled during setup.
2. Install a repository-scoped GitHub App with write access to Contents, Pull requests,
   and Commit statuses. Set `WEBROUTER_AUTOMERGE_APP_ID` as a variable and
   `WEBROUTER_AUTOMERGE_APP_PRIVATE_KEY` as a secret. Set `QODO_REVIEW_READ_API_KEY`
   to a dedicated Qodo credential with access to read this repository's reviews.
3. On a fork PR, verify that Qodo review completion publishes `qodo-reviewed` on the PR
   head after Qodo finishes. Test a review with open findings and no approval label, a fresh
   push, and manual recovery. The workflows run trusted default-branch code.
4. Require `qodo-reviewed` from the GitHub App on every protected target covered by the
   policy, including integration branches. Preserve existing CI and review protections.
5. Enable **Auto-merge controller**. Verify that a fresh Qodo label arms squash auto-merge
   into `main` and that removing it disables the request. Verify the manual merge path
   without the label.

## Recovery and rollback

If the current commit still needs a review, request `/agentic_review`. If Qodo finished
and the status is still pending, open **Actions → Qodo review completion → Run workflow**,
select `main`, and enter the PR number. This reads stored results. Qodo's comment
notifications can be missed, so verify this recovery path before enforcing the check.

To pause automation, disable **Auto-merge controller** and clear existing auto-merge
requests as needed. To roll back the completion gate, remove its required status from
the affected branches before disabling **Qodo review completion**.

## Limitations

GitHub shares a commit status across PRs with the same head SHA. The status confirms review
of that commit. Review of each target branch's diff requires separate evidence.

The controller compares the current label timestamp with the review start to reject old
labels. Qodo's label carries no commit SHA, and removal is processed asynchronously.
GitHub can finish an eligible merge before the controller handles removal.

The completion reader uses Qodo's [versioned managed-tools API](https://www.npmjs.com/package/@qodo/sdk).
Its [client code](../scripts/qodo_api.py) handles result retrieval and polling.
