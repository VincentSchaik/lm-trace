// Tests for skill-slash.js: the / command of the Skills page. Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseSlash, slashQuery, slashMatches, pickSkill, withSkill, loadedSkills } = require("../skill-slash.js");

test("parseSlash: name and the rest of the message", () => {
  assert.deepEqual(parseSlash("/meeting-notes"), { name: "meeting-notes", rest: "" });
  assert.deepEqual(parseSlash("/meeting-notes Summarize this"), { name: "meeting-notes", rest: "Summarize this" });
  assert.deepEqual(parseSlash("  /meeting-notes   Summarize  "), { name: "meeting-notes", rest: "Summarize" });
  assert.deepEqual(parseSlash("/meeting-notes\nLine 1\nLine 2"), { name: "meeting-notes", rest: "Line 1\nLine 2" });
  assert.deepEqual(parseSlash("/nope hi"), { name: "nope", rest: "hi" });  // an unknown name: the server says so
});

test("parseSlash: not a command", () => {
  for (const t of ["", "   ", "Hello", "Use /meeting-notes", "/", "/ meeting-notes", "/usr/bin is a folder", "//x", null, undefined])
    assert.equal(parseSlash(t), null, String(t));
});

test("slashQuery: the menu shows only while you type the name", () => {
  assert.equal(slashQuery("/", 1), "");
  assert.equal(slashQuery("/mee", 4), "mee");
  assert.equal(slashQuery("/mee"), "mee");          // no caret: the end
  assert.equal(slashQuery("/meeting-notes x", 3), "me");  // caret in the name
  assert.equal(slashQuery("/meeting-notes x", 16), null); // after the space: you type the task
  assert.equal(slashQuery("/meeting-notes ", 15), null);
  assert.equal(slashQuery("hi /mee", 7), null);
  assert.equal(slashQuery("/usr/", 5), null);
  assert.equal(slashQuery("", 0), null);
});

test("slashMatches: start of the name first, then inside, case does not matter", () => {
  const sk = ["release-notes", "meeting-notes", "notes-helper", "pdf"].map(name => ({ name }));
  assert.deepEqual(slashMatches("", sk).map(s => s.name), ["release-notes", "meeting-notes", "notes-helper", "pdf"]);
  assert.deepEqual(slashMatches("notes", sk).map(s => s.name), ["notes-helper", "release-notes", "meeting-notes"]);
  assert.deepEqual(slashMatches("MEE", sk).map(s => s.name), ["meeting-notes"]);
  assert.deepEqual(slashMatches("zzz", sk), []);
  assert.deepEqual(slashMatches("a", []), []);
});

test("pickSkill: puts the full name, keeps the task", () => {
  assert.equal(pickSkill("/", "meeting-notes"), "/meeting-notes ");
  assert.equal(pickSkill("/mee", "meeting-notes"), "/meeting-notes ");
  assert.equal(pickSkill("/mee Summarize this", "meeting-notes"), "/meeting-notes Summarize this");
  assert.equal(pickSkill("/mee\nline 2", "meeting-notes"), "/meeting-notes line 2");
});

test("withSkill: the skill block, then your message", () => {
  assert.equal(withSkill("<skill_content name=\"a\">\nX\n</skill_content>", "Do it"), "<skill_content name=\"a\">\nX\n</skill_content>\n\nDo it");
  assert.equal(withSkill("BLOCK", ""), "BLOCK");
});

test("loadedSkills: the skills in the chat, apart from the messages", () => {
  const t = loadedSkills();
  assert.equal(t.has("meeting-notes"), false);
  t.add("meeting-notes");
  assert.equal(t.has("meeting-notes"), true);
  assert.equal(t.has("meeting"), false);  // not a prefix match
  t.add("meeting-notes");
  assert.deepEqual(t.list(), ["meeting-notes"]);
  t.clear();
  assert.deepEqual(t.list(), []);
});

test("loadedSkills: a stopped turn goes back to the list before it", () => {
  const t = loadedSkills();
  t.add("release-notes");
  const before = t.snapshot();
  t.add("meeting-notes");  // loaded in a turn that is then stopped
  t.restore(before);
  assert.deepEqual(t.list(), ["release-notes"]);
  before.add("x");  // the snapshot is a copy: changing it later changes nothing
  assert.equal(t.has("x"), false);
});
