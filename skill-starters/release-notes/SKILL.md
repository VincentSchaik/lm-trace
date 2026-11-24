---
name: release-notes
description: Write release notes for a new version from a list of changes, commits or pull request titles. Use this skill when the user asks for release notes, a changelog entry or "what's new", even if they only paste a list of changes.
metadata:
  author: lm-trace
  version: "1.0"
---

# Release notes

Use this template. Leave out a section with no items.

```
## v<version> · <Month D, YYYY>

### Added
- <verb in past tense> <what>, 12 words at most

### Changed
- …

### Fixed
- …
```

- One line per change. Start with a past-tense verb: "Added", "Fixed", "Moved".
- Write for users: what they can do now, not how the code changed.
- Canadian spelling: colour, favourite, cancelled.

## Gotchas

- Changes marked "internal", "refactor", "chore" or "ci" never go in the notes.
- Never write ticket numbers (ABC-123) or names of people.
- No version given? Ask for it. Do not invent one.
