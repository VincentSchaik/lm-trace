// Tests for skill-draft.js: find a SKILL.md in an answer (the Create skill button). Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { draftName, findSkillDrafts } = require("../skill-draft.js");

const MD = (name, body = "# Steps\n\n1. Do it.") => `---\nname: ${name}\ndescription: Does a thing. Use this skill when testing.\n---\n\n${body}`;

test("draftName: the name in the frontmatter", () => {
  assert.equal(draftName(MD("daily-standup")), "daily-standup");
  assert.equal(draftName(`---\nname: "quoted-name"\ndescription: x\n---\nbody`), "quoted-name");
  assert.equal(draftName(`  \n${MD("lead-space")}`), "lead-space");
  assert.equal(draftName("# Not a skill"), "");
  assert.equal(draftName("---\ndescription: no name\n---\nbody"), "");
  assert.equal(draftName("---\nname: open frontmatter"), "");  // not closed
  assert.equal(draftName(null), "");
});

test("findSkillDrafts: one block with a language tag", () => {
  const a = `Here is your skill:\n\n\`\`\`markdown\n${MD("daily-standup")}\n\`\`\`\n\nClick Create skill.`;
  assert.deepEqual(findSkillDrafts(a), [{ name: "daily-standup", text: MD("daily-standup") + "\n" }]);
});

test("findSkillDrafts: no tag, ~~~ fences, yaml tag", () => {
  for (const [o, c] of [["```", "```"], ["~~~", "~~~"], ["```yaml", "```"], ["```md", "```"]])
    assert.equal(findSkillDrafts(`${o}\n${MD("a-b")}\n${c}`)[0]?.name, "a-b", o);
});

test("findSkillDrafts: a code block inside, with a longer outer fence", () => {
  const inner = MD("release", "Template:\n\n```\n## v1.0\n```\n\nDone.");
  const r = findSkillDrafts(`\`\`\`\`markdown\n${inner}\n\`\`\`\``);
  assert.equal(r.length, 1);
  assert.equal(r[0].text, inner + "\n");  // the inner ``` does not close the outer ````
});

test("findSkillDrafts: other code blocks are not drafts", () => {
  assert.deepEqual(findSkillDrafts("```python\nprint(1)\n```\n\n```\n---\ntitle: x\n---\n```"), []);
  assert.deepEqual(findSkillDrafts("Just text."), []);
  assert.deepEqual(findSkillDrafts(""), []);
});

test("findSkillDrafts: two drafts, in order", () => {
  const a = `\`\`\`markdown\n${MD("one")}\n\`\`\`\ntext\n\`\`\`markdown\n${MD("two")}\n\`\`\``;
  assert.deepEqual(findSkillDrafts(a).map(d => d.name), ["one", "two"]);
});

test("findSkillDrafts: the answer is a SKILL.md, no code block", () => {
  assert.deepEqual(findSkillDrafts(MD("plain")), [{ name: "plain", text: MD("plain") + "\n" }]);
});

test("findSkillDrafts: an open block runs to the end", () => {
  assert.equal(findSkillDrafts(`\`\`\`markdown\n${MD("cut")}`)[0]?.name, "cut");
});
