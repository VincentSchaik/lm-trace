// A SKILL.md in an answer of the model (for example from skill-creator-lite): the Skills page shows a
// Create skill button under the answer. Also the example message of a skill (Try now). Pure functions, without the page, so node can test them: node --test tests/
(function (root) {
  // The name in the frontmatter, or "" when the text is not a SKILL.md (it must start with --- and have name:).
  function draftName(text) {
    const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(String(text ?? "").trimStart());
    if (!m) return "";
    const n = /^name:[ \t]*(.*)$/m.exec(m[1]);
    return n ? n[1].trim().replace(/^(["'])(.*)\1$/, "$2") : "";
  }

  // Each SKILL.md in a Markdown answer: [{ name, text }].
  // In a fenced code block (``` or ~~~, any language tag): the block closes with the same fence, at least as long
  // (CommonMark), so a SKILL.md with a code block inside needs a longer outer fence (````).
  // An answer that is a SKILL.md itself (it starts with ---) also counts.
  function findSkillDrafts(answer) {
    const text = String(answer ?? ""), out = [], lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const open = /^ {0,3}(`{3,}|~{3,})[ \t]*([\w.+-]*)[ \t]*$/.exec(lines[i]);
      if (!open) continue;
      const fence = open[1], body = [];
      let j = i + 1;
      for (; j < lines.length; j++) {
        const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(lines[j]);
        if (close && close[1][0] === fence[0] && close[1].length >= fence.length) break;
        body.push(lines[j]);
      }
      const block = body.join("\n").trim() + "\n", name = draftName(block);
      if (name) out.push({ name, text: block });
      i = j;  // past the closing fence (or the end: an open block runs to the end)
    }
    if (!out.length) {
      const name = draftName(text);
      if (name) out.push({ name, text: text.trim() + "\n" });
    }
    return out;
  }

  // An example message for a skill: the text without a /name at the start, a code block or quotes around it.
  // Like the built-in examples, it does not name the skill, so Try now also tests if the model picks the skill.
  function cleanExample(text, name) {
    let t = String(text ?? "").trim();
    const f = /^(`{3,}|~{3,})[\w.+-]*[ \t]*\r?\n([\s\S]*?)\r?\n\1\s*$/.exec(t);
    if (f) t = f[2].trim();
    t = t.replace(/^`([^`]+)`$/, "$1").replace(/^(["“])([\s\S]*)(["”])$/, "$2").trim();
    if (name) t = t.replace(new RegExp(`^/${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s|$)\\s*`), "");
    return t.trim();
  }

  // The test message that skill-creator-lite writes after the SKILL.md: a line with /<name> and an input.
  function draftTest(answer, name) {
    for (const line of String(answer ?? "").split(/\r?\n/)) {
      const t = line.trim().replace(/^[-*]\s+|^\d+\.\s+/, "").replace(/^`|`$/g, "");
      if (t.startsWith(`/${name} `)) return cleanExample(t, name);
    }
    return "";
  }

  // What the loaded model gets when you ask it to write an example message for a skill (the sparkle button).
  const examplePrompt = skillMd => [
    { role: "system", content: "You write test messages for Agent Skills." },
    { role: "user", content: `Here is a skill:\n\n${skillMd}\n\nWrite one realistic message that a user sends when they need this skill. Put the real input in it (for example the notes, the list or the text that the skill works on), with concrete details. Do not name the skill. Write only the message, in the words of the user: no quotes, no title, no explanation.` },
  ];

  const api = { draftName, findSkillDrafts, cleanExample, draftTest, examplePrompt };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkillDraft = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
