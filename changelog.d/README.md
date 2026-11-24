# Changelog fragments

A pull request with a change users would notice adds one file here instead of
editing `CHANGELOG.md`:

```
changelog.d/<name>.<type>.md
```

- `<name>`: any short lowercase slug, such as your branch name (`a-z`, `0-9`, `-`, `_`).
- `<type>`: `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`.
- The file holds one entry: the text of a single bullet, without the leading `- `,
  at most 400 characters. Say what changed for users, not how it was built.

Two changes, two files. A pull request with nothing users would notice (CI, docs,
tests, refactors) takes the `skip-changelog` label instead.

Every file here is checked by `tests/test_changelog_fragments.py`, and the
Changelog check fails a pull request that adds none. At release, `make changelog`
moves every fragment under `## [Unreleased]` in `CHANGELOG.md` and deletes the files.
