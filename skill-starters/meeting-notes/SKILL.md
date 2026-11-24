---
name: meeting-notes
description: Turn a meeting transcript or rough notes into structured meeting notes with decisions and action items. Use this skill when the user pastes a transcript, a chat log or notes from a meeting, a call or a 1:1, even if they only say "clean this up" or "what did we agree?".
metadata:
  author: lm-trace
  version: "1.0"
---

# Meeting notes

1. Find the meeting type: team meeting, 1:1, or external call. Ask only if you cannot tell.
2. Read the format file of that type before you write: `references/team-meeting.md`, `references/one-on-one.md` or `references/external-call.md`. The format file is the source of truth for the headings.
3. Run `scripts/extract_action_items.py` with the transcript as input. It gives a first list of action items as JSON. Check each one against the transcript: it is a start, not the final list.
4. Write the notes in the format. Use the names from the transcript.

## Gotchas

- An action item needs an owner. If the transcript gives none, write "Owner: not set".
- "We should…" is not a decision. A decision says what changes: "We move the release to December 3."
- Dates: keep the words of the transcript ("Friday"); do not guess a date.
