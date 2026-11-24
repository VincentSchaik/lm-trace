// A SKILL.md in an answer of the model (for example from skill-creator-lite): the Skills page shows a
// Create skill button under the answer. Pure functions, without the page, so node can test them: node --test tests/
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

  const api = { draftName, findSkillDrafts };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkillDraft = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
