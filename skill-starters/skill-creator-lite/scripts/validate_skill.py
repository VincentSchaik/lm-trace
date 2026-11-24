"""Check a SKILL.md draft against the Agent Skills specification (the checks of skills-ref validate).

Input: the SKILL.md text on stdin (a ```markdown code block around it is removed).
Output: JSON on stdout: {"valid": bool, "name": str, "errors": [...], "hints": [...]}.
Errors break the specification. Hints are best practices from agentskills.io.
Standard library only, no input prompts.
"""
import json, re, sys, unicodedata

ALLOWED = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}


def unfence(text):
    """The draft without a code block around it (```markdown … ```, ````md title … ````, ~~~ … ~~~). As in
    CommonMark, the closing fence uses the same character and is at least as long as the opening fence."""
    lines = text.strip().splitlines()
    if len(lines) >= 2:
        o = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", lines[0])
        c = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", lines[-1])
        if o and c and c.group(1)[0] == o.group(1)[0] and len(c.group(1)) >= len(o.group(1)) \
                and not (o.group(1)[0] == "`" and "`" in o.group(2)):
            return "\n".join(lines[1:-1])
    return text


def scalar(v):
    """A YAML scalar. Quoted: the text between the quotes ("…" with \\" escapes, '…' with '' escapes); a comment after
    the closing quote is not part of the value. Without quotes: a comment (space, then #) is removed. A quote that is
    not closed stays as written."""
    v = v.strip()
    if v[:1] == '"':
        m = re.match(r'^"((?:[^"\\]|\\.)*)"(?:[ \t]+#.*)?[ \t]*$', v)
        return m.group(1).replace('\\"', '"') if m else v
    if v[:1] == "'":
        m = re.match(r"^'((?:[^']|'')*)'(?:[ \t]+#.*)?[ \t]*$", v)
        return m.group(1).replace("''", "'") if m else v
    return re.sub(r"\s+#.*$", "", v)


def parse(text):
    """(frontmatter, body, error): key: value, quoted values, folded lines, > and | blocks, one nested level."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", text, re.S)
    if not m:
        return {}, text, "SKILL.md must start with YAML frontmatter (---)" if not text.startswith("---") else "SKILL.md frontmatter not properly closed with ---"
    data, lines, i = {}, m.group(1).split("\n"), 0
    while i < len(lines):
        line = lines[i].rstrip("\r")
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not km:
            return data, m.group(2), f"Invalid YAML in frontmatter: line {i} is not 'key: value': {line.strip()[:60]}"
        key, val = km.group(1), km.group(2).strip()
        if key in data:
            return data, m.group(2), f"Invalid YAML in frontmatter: duplicate key '{key}'"
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                block.append(lines[i].strip())
                i += 1
            data[key] = (" " if val.startswith(">") else "\n").join(block).strip()
        elif val == "":
            sub = {}
            while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
                sm = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", lines[i])
                if sm:
                    sub[sm.group(1)] = scalar(sm.group(2))
                i += 1
            data[key] = sub
        else:
            while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
                val += " " + lines[i].strip()
                i += 1
            data[key] = scalar(val)
    return data, m.group(2), None


def name_errors(name):
    if not isinstance(name, str) or not name.strip():
        return ["Field 'name' must be a non-empty string"]
    name, errors = unicodedata.normalize("NFKC", name.strip()), []
    if len(name) > 64:
        errors.append(f"Skill name '{name}' exceeds 64 character limit ({len(name)} chars)")
    if name != name.lower():
        errors.append(f"Skill name '{name}' must be lowercase")
    if name.startswith("-") or name.endswith("-"):
        errors.append("Skill name cannot start or end with a hyphen")
    if "--" in name:
        errors.append("Skill name cannot contain consecutive hyphens")
    if not all(c.isalnum() or c == "-" for c in name):
        errors.append(f"Skill name '{name}' contains invalid characters. Only letters, digits, and hyphens are allowed.")
    return errors


def check(text):
    fm, body, err = parse(unfence(text.strip("﻿")))
    errors, hints = ([err] if err else []), []
    if not err:
        extra = set(fm) - ALLOWED
        if extra:
            errors.append(f"Unexpected fields in frontmatter: {', '.join(sorted(extra))}. Only {sorted(ALLOWED)} are allowed.")
        if "name" not in fm:
            errors.append("Missing required field in frontmatter: name")
        else:
            errors += name_errors(fm["name"])
        d = fm.get("description")
        if "description" not in fm:
            errors.append("Missing required field in frontmatter: description")
        elif not isinstance(d, str) or not d.strip():
            errors.append("Field 'description' must be a non-empty string")
        elif len(d) > 1024:
            errors.append(f"Description exceeds 1024 character limit ({len(d)} chars)")
        c = fm.get("compatibility")
        if "compatibility" in fm and not isinstance(c, str):
            errors.append("Field 'compatibility' must be a string")
        elif isinstance(c, str) and len(c) > 500:
            errors.append(f"Compatibility exceeds 500 character limit ({len(c)} chars)")
        if isinstance(d, str) and d.strip():
            if not re.search(r"\b(use|when)\b", d, re.I):
                hints.append('description: say when to use the skill ("Use this skill when...").')
            elif len(d) < 60:
                hints.append("description: name the tasks and words that should activate the skill.")
    lines = body.count("\n") + 1 if body.strip() else 0
    if not err and not body.strip():
        hints.append("The body is empty: write the steps the model must follow.")
    if lines > 500:
        hints.append(f"SKILL.md has {lines} lines: keep it under 500; move details to references/.")
    name = fm.get("name").strip() if isinstance(fm.get("name"), str) else ""
    return {"valid": not errors, "name": name, "errors": errors, "hints": hints}


if __name__ == "__main__":
    print(json.dumps(check(sys.stdin.read()), indent=2))
