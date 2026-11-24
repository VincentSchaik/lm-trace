// The / command of the Skills page: /skill-name at the start of the message activates that skill.
// The user, not the model, chooses the skill (agentskills.io/integrate-skills, "User-explicit activation").
// Pure functions, without the page, so node can test them: node --test tests/
(function (root) {
  // "/meeting-notes Summarize this" -> { name: "meeting-notes", rest: "Summarize this" }.
  // Only at the start of the message, and only a name without "/", so "/usr/bin is…" is a normal message.
  function parseSlash(text) {
    const m = /^\/([^\s/]+)(?:\s+([\s\S]*))?$/.exec(String(text ?? "").trim());
    return m ? { name: m[1], rest: (m[2] || "").trim() } : null;
  }

  // The menu shows while you type the name: "/" or "/mee", with the caret in it, and no space yet.
  // Returns what you typed after "/" (maybe ""), or null when there is no menu.
  function slashQuery(text, caret) {
    const before = String(text ?? "").slice(0, caret ?? String(text ?? "").length);
    const m = /^\/([^\s/]*)$/.exec(before);
    return m ? m[1] : null;
  }

  // The skills for the menu: names that start with the query first, then names that contain it. Case does not matter.
  function slashMatches(query, skills) {
    const q = String(query ?? "").toLowerCase();
    const start = [], inside = [];
    for (const s of skills || []) {
      const n = s.name.toLowerCase();
      if (n.startsWith(q)) start.push(s); else if (n.includes(q)) inside.push(s);
    }
    return [...start, ...inside];
  }

  // The text after you pick a skill: "/name " plus what you typed after the name before (if any).
  function pickSkill(text, name) {
    const m = /^\/[^\s/]*\s*([\s\S]*)$/.exec(String(text ?? ""));
    return `/${name} ${m ? m[1] : ""}`;
  }

  // What the model gets: the skill block, then your message.
  const withSkill = (content, rest) => rest ? `${content}\n\n${rest}` : content;

  // The skills that are in the conversation: activated by /name, or loaded by a load_skill call that worked. The
  // harness keeps this list itself, apart from the messages, so it works with both tool notes (role "tool" results,
  // or OBSERVATION user messages) and a failed call never counts. A stopped turn goes back to the list before it.
  // Then the harness does not add a skill twice (agentskills.io: "Deduplicate activations").
  function loadedSkills() {
    let set = new Set();
    return {
      has: name => set.has(name),
      add: name => { set.add(name); },
      snapshot: () => new Set(set),
      restore: saved => { set = new Set(saved); },
      clear: () => { set = new Set(); },
      list: () => [...set],
    };
  }

  const api = { parseSlash, slashQuery, slashMatches, pickSkill, withSkill, loadedSkills };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SkillSlash = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
