// Tests for skill-slash.js: the / command of the Skills page. Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseSlash, slashQuery, slashMatches, pickSkill, withSkill, skillInHistory } = require("../skill-slash.js");

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

test("skillInHistory: from /name or from load_skill", () => {
  const user = { role: "user", content: '<skill_content name="meeting-notes">\nX\n</skill_content>\n\nHi' };
  const call = name => ({ role: "assistant", content: "", tool_calls: [{ id: "1", type: "function", function: { name: "load_skill", arguments: JSON.stringify({ name }) } }] });
  assert.equal(skillInHistory([], "meeting-notes"), false);
  assert.equal(skillInHistory(undefined, "meeting-notes"), false);
  assert.equal(skillInHistory([user], "meeting-notes"), true);
  assert.equal(skillInHistory([user], "meeting"), false);  // not a prefix match
  assert.equal(skillInHistory([call("meeting-notes")], "meeting-notes"), true);
  assert.equal(skillInHistory([call("release-notes")], "meeting-notes"), false);
  // The tag in an answer or a tool result is not an activation.
  assert.equal(skillInHistory([{ role: "assistant", content: user.content }, { role: "tool", content: user.content }], "meeting-notes"), false);
  // Bad JSON in the arguments: no crash.
  assert.equal(skillInHistory([{ role: "assistant", tool_calls: [{ function: { name: "load_skill", arguments: "{bad" } }] }], "x"), false);
});
