---
name: skill-creator-lite
description: Create a new Agent Skill (a SKILL.md file) with the user, one step at a time - ask, write, check, improve. Use this skill when the user wants to make, write or improve a skill, or to turn a task they explain into a skill.
metadata:
  author: lm-trace
  version: "1.0"
  inspired-by: anthropics/skills skill-creator
---

# Skill creator (light)

Help the user make one skill. Do the steps in this order. Do one step per reply when you need an answer from the user.

## 1. Ask

If the message does not say it already, ask these three questions in one short message, then stop:

- What task should the skill do? Give one real example: the input and the output you want.
- When should it activate? Which requests or words?
- What goes wrong without the skill?

If the message already answers them, go to step 2.

## 2. Write

Read `references/best-practices.md`. Then write the full SKILL.md:

- `name`: lowercase letters, digits and single hyphens, at most 64 characters.
- `description`: what the skill does, then "Use this skill when …" with the words a user says. At most 1,024 characters.
- Body: numbered steps, the output format, then `## Gotchas`. Only what a model cannot know. Keep it under 100 lines.

It must start with the frontmatter, exactly like this:

```markdown
---
name: daily-standup
description: Write … Use this skill when the user asks for …
---

# Daily standup

1. …

## Gotchas

- …
```

## 3. Check

Always run `scripts/validate_skill.py` with the full SKILL.md as input, before step 4, even when the draft looks right. Fix each error and run it again. Hints are advice.

## 4. Give it to the user

Reply with only these three parts:

1. The SKILL.md in one markdown code block.
2. The line: Click **Create skill** under this answer to save it.
3. One test message: a slash, the skill name, and a real input.

## 5. Improve

When the user says what went wrong, change only that, and give the full SKILL.md again as in step 4.

## Gotchas

- A SKILL.md with a code block inside: use four backticks for the outer code block.
- One skill does one task. If the user asks for more, suggest one skill for each.
- Do not explain what a model already knows. Write the steps, the format and the checks.
- Never invent the user's format: use their example, or ask for one.
- A skill can name scripts and reference files, but you cannot create them. Write the SKILL.md only, and tell the user which other files to add, if any.
