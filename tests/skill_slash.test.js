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

test("skillInHistory: from /name or from load_skill that worked", () => {
  const user = { role: "user", content: '<skill_content name="meeting-notes">\nX\n</skill_content>\n\nHi' };
  let n = 0;
  const call = (name, result) => {
    const id = String(++n);
    const msgs = [{ role: "assistant", content: "", tool_calls: [{ id, type: "function", function: { name: "load_skill", arguments: JSON.stringify({ name }) } }] }];
    if (result != null) msgs.push({ role: "tool", tool_call_id: id, content: result });
    return msgs;
  };
  assert.equal(skillInHistory([], "meeting-notes"), false);
  assert.equal(skillInHistory(undefined, "meeting-notes"), false);
  assert.equal(skillInHistory([user], "meeting-notes"), true);
  assert.equal(skillInHistory([user], "meeting"), false);  // not a prefix match
  assert.equal(skillInHistory(call("meeting-notes", "# Meeting notes\n1. …"), "meeting-notes"), true);
  assert.equal(skillInHistory(call("release-notes", "# Release notes"), "meeting-notes"), false);
  // The tag in an answer or a tool result is not an activation.
  assert.equal(skillInHistory([{ role: "assistant", content: user.content }, { role: "tool", content: user.content }], "meeting-notes"), false);
  // Bad JSON in the arguments: no crash.
  assert.equal(skillInHistory([{ role: "assistant", tool_calls: [{ id: "x", function: { name: "load_skill", arguments: "{bad" } }] }], "x"), false);
});

test("skillInHistory: a failed load_skill call is not an activation", () => {
  const call = (id, result) => [
    { role: "assistant", content: "", tool_calls: [{ id, type: "function", function: { name: "load_skill", arguments: '{"name":"meeting-notes"}' } }] },
    ...(result == null ? [] : [{ role: "tool", tool_call_id: id, content: result }]),
  ];
  // The server said no (a skill that is off, or a name that does not exist), or the harness skipped the call.
  assert.equal(skillInHistory(call("1", "Error: there is no skill named 'meeting-notes'. The skills are: none."), "meeting-notes"), false);
  assert.equal(skillInHistory(call("2", "Error: name: not one of the allowed values."), "meeting-notes"), false);
  // No result for the call (yet): not loaded.
  assert.equal(skillInHistory(call("3"), "meeting-notes"), false);
  // A result for another call does not count.
  assert.equal(skillInHistory([...call("4"), { role: "tool", tool_call_id: "5", content: "# Meeting notes" }], "meeting-notes"), false);
  // A failed call, then a call that worked: loaded.
  assert.equal(skillInHistory([...call("6", "Error: x"), ...call("7", "# Meeting notes")], "meeting-notes"), true);
  // The "already loaded" note of the harness is not an error: the skill is in the chat.
  assert.equal(skillInHistory(call("8", "The skill meeting-notes is already loaded: its instructions are earlier in this conversation."), "meeting-notes"), true);
});
