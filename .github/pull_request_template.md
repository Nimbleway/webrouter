## What

<!-- What this changes and why, in a few sentences a reviewer can follow without the diff.
     You answer for every claim, whoever drafted it. For a large PR, suggest a reading order. -->

## Verification

<!-- CI already runs lint, types and unit tests: don't paste those. Show what CI does not run:
     live calls (required when a provider mapping changes), integration or browser tests, a manual check.
     Command plus the tail of its output. "Nothing beyond CI" is a fine answer. -->

## Breaking changes

<!-- What existing callers must change. "None" is fine. -->

## Checklist

- [ ] Tests added or updated
- [ ] A changelog fragment in `changelog.d/` if users would notice the change
      (format in `changelog.d/README.md`); otherwise the `skip-changelog` label,
      which a maintainer adds on a fork PR
- [ ] Provider tables in `docs/providers.mdx` and docs updated if provider capabilities or the public API changed
- [ ] Opened ready for review
- [ ] Before merging, Qodo reviewed the latest commit, and every finding has a reply starting with
      `@qodo`: `Fixed in <sha>: <what>` or `Not fixing: <reason>`
