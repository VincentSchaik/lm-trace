# Best practices for a SKILL.md

From the Agent Skills specification (agentskills.io).

## The description decides if the skill is used

The model sees only the name and the description until it activates the skill.

- Say what the skill does, then when to use it: "Use this skill when the user …".
- Name the words a user says: "standup", "daily update", "what did I do yesterday".
- Bad: `description: Helps with notes.` Good: `description: Write a daily standup update (Yesterday, Today, Blockers) from rough notes. Use this skill when the user asks for a standup, a daily update or a status for the team.`

## The body: only what the model cannot know

- Numbered steps, in the order the model does them.
- The exact output format, as a short template.
- `## Gotchas`: the mistakes a model makes without the skill. These lines are worth the most.
- Leave out general knowledge ("A standup is a short meeting…"). It costs tokens and teaches nothing.
- One default, not a menu: "Use this format", not "Choose a format you like".

## Keep it small

- Under 500 lines; most good skills are under 100.
- Move long details to `references/<file>.md`, and say when to read each file: "Read `references/format.md` before you write."
- Code that must run the same way each time goes in `scripts/`. Say what the script takes and what it returns.

## Check before you save

- `name`: lowercase, digits, single hyphens, 64 characters at most. The folder has the same name.
- `description`: not empty, 1,024 characters at most.
- Allowed fields: name, description, license, compatibility, metadata, allowed-tools.
