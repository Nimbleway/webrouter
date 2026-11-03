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
- [ ] README support table and docs updated if provider capabilities or the public API changed
- [ ] Opened as a draft, marked ready only after Qodo (the AI review bot on this repo)
      posted its review for the latest commit, and every finding has a reply starting with `@qodo`:
      `Fixed in <sha>: <what>` or `Not fixing: <reason>` (on a draft, a `/agentic_review` comment asks Qodo to review)
