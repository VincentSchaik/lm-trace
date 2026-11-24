"""Agent Skills for LM Trace: the Skills page.

A skill is a folder with a SKILL.md file (YAML frontmatter + Markdown), as in the Agent Skills standard
(agentskills.io/specification), and optional files: scripts/, references/, assets/, or any other folder.

The skills live in skills/ (one folder each, gitignored: they are the user's). The starter skills are in
skill-starters/ (in the repository); the server copies them to skills/ when skills/ does not exist yet, and again
on Reset. skills-meta.json keeps, for each skill, whether it is on and where it came from.

LM Trace is the harness: the page puts the name and description of each skill that is on into the system prompt
(layer 1), and gives the model three tools. This module runs them:
  load_skill(name)                  layer 2: the body of SKILL.md and the list of the other files of the skill
  read_skill_file(name, path)       layer 3: one file of the skill, as text
  run_skill_script(name, path, input)  layer 3: run one script of the skill (the page asks you first)

Import: a skill folder from GitHub (github.com/<owner>/<repo>/tree/<ref>/<path>), with the GitHub contents API.
Standard library only.
"""
import base64, json, mimetypes, os, re, shutil, subprocess, threading, time, unicodedata, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(ROOT, "skills")
STARTERS = os.path.join(ROOT, "skill-starters")
META = os.path.join(ROOT, "skills-meta.json")
MAX_FILE = 200_000      # the most characters that read_skill_file returns
MAX_OUT = 20_000        # the most characters of script output that go back to the model
MAX_IMPORT = 3_000_000  # the most bytes of one imported skill
lock = threading.Lock()


# ---------------------------------------------------------------- SKILL.md: frontmatter and body

def parse_skill_md(text):
    """Split SKILL.md into (frontmatter dict, body, error). A small YAML reader for the fields of the spec:
    key: value, quoted values, plain values that go on over indented lines, block values (> and |), comments (#),
    and one level of nested keys (metadata). A key twice is an error, as in strictyaml (used by skills-ref)."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", text, re.S)
    if not m:
        return {}, text, "SKILL.md must start with YAML frontmatter (---)" if not text.startswith("---") else "SKILL.md frontmatter not properly closed with ---"
    fm_text, body = m.group(1), m.group(2)
    data, lines, i = {}, fm_text.split("\n"), 0
    try:
        while i < len(lines):
            line = lines[i].rstrip("\r")
            i += 1
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
            if not km:
                return data, body, f"Invalid YAML in frontmatter: line {i} is not 'key: value': {line.strip()[:60]}"
            key, val = km.group(1), km.group(2).strip()
            if key in data:
                return data, body, f"Invalid YAML in frontmatter: duplicate key '{key}'"
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                block = []
                while i < len(lines) and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                    block.append(lines[i].strip())
                    i += 1
                data[key] = (" " if val.startswith(">") else "\n").join(b for b in block).strip()
            elif val == "":
                sub = {}
                while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
                    sm = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", lines[i])
                    if sm:
                        sub[sm.group(1)] = scalar(sm.group(2))
                    i += 1
                data[key] = sub
            else:
                # A plain or quoted value can go on over the next indented lines (folded into one line with spaces).
                while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip():
                    val += " " + lines[i].strip()
                    i += 1
                data[key] = scalar(val)
    except Exception as e:  # never fail the whole list for one bad file
        return data, body, f"Invalid YAML in frontmatter: {e}"
    return data, body, None


def scalar(v):
    """A YAML scalar: quotes removed; a comment (space, then #) removed from a value without quotes."""
    v = v.strip()
    if v[:1] in "\"'":
        return unquote(v)
    return re.sub(r"\s+#.*$", "", v)


def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
        return v.replace('\\"', '"') if v else v
    return v


ALLOWED_FIELDS = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}


def name_errors(name, folder=None):
    """The name rules of skills-ref: Unicode letters and digits, lowercase, single hyphens, at most 64 characters."""
    if not isinstance(name, str) or not name.strip():
        return ["Field 'name' must be a non-empty string"]
    errors = []
    name = unicodedata.normalize("NFKC", name.strip())
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
    if folder is not None and unicodedata.normalize("NFKC", folder) != name:
        errors.append(f"Directory name '{folder}' must match skill name '{name}'")
    return errors


def validate(folder, fm, err):
    """skills-ref validate: the same checks and messages as the reference library of the Agent Skills specification
    (github.com/agentskills/agentskills, skills-ref). It checks the frontmatter only."""
    if err:
        return [err]
    errors = []
    extra = set(fm) - ALLOWED_FIELDS
    if extra:
        errors.append(f"Unexpected fields in frontmatter: {', '.join(sorted(extra))}. Only {sorted(ALLOWED_FIELDS)} are allowed.")
    if "name" not in fm:
        errors.append("Missing required field in frontmatter: name")
    else:
        errors += name_errors(fm["name"], folder)
    if "description" not in fm:
        errors.append("Missing required field in frontmatter: description")
    elif not isinstance(fm["description"], str) or not fm["description"].strip():
        errors.append("Field 'description' must be a non-empty string")
    elif len(fm["description"]) > 1024:
        errors.append(f"Description exceeds 1024 character limit ({len(fm['description'])} chars)")
    if "compatibility" in fm:
        c = fm["compatibility"]
        if not isinstance(c, str):
            errors.append("Field 'compatibility' must be a string")
        elif len(c) > 500:
            errors.append(f"Compatibility exceeds 500 character limit ({len(c)} chars)")
    return errors


REF_RE = re.compile(r"(?<![\w/.:-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6})(?![\w/])")


def lint(folder, fm, body, err, files=None):
    """Errors: skills-ref validate. Hints: best practices of agentskills.io (not part of the validation)."""
    errors, hints = validate(folder, fm, err), []
    if files is not None:  # File references: relative paths from the skill root, and say when to load each file
        named = {m for m in REF_RE.findall(body) if not m.startswith(("http", "www.", "skills/"))}
        missing = sorted(x for x in named if x not in files)
        if missing:
            hints.append("SKILL.md names files that the skill does not have: " + ", ".join(missing) + ".")
        text = body + " " + json.dumps(fm)  # a file can be named in the frontmatter too (license: … LICENSE.txt)
        unnamed = [x for x in files if x not in named and os.path.basename(x) not in text]
        if unnamed:
            hints.append("SKILL.md does not name " + ", ".join(unnamed) + ": say when to use " + ("it" if len(unnamed) == 1 else "each one") + ".")
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        if not re.search(r"\b(use|when)\b", desc, re.I):
            hints.append("description: say when to use the skill (\"Use this skill when…\").")
        elif len(desc) < 60:
            hints.append("description: name the tasks and words that should activate the skill.")
    lines = body.count("\n") + 1 if body.strip() else 0
    if lines > 500:
        hints.append(f"SKILL.md has {lines} lines: keep it under 500; move details to other files.")
    if re.search(r"\bsee (the )?(references?|docs?)/? for (more )?details\b", body, re.I):
        hints.append("Say when to read each reference file, not only \"see references/\".")
    return errors, hints


def validate_text(text):
    """Validate SKILL.md text before it is saved (the Validate button). Save names the folder after the name in the
    frontmatter, so the folder check uses that name."""
    if not isinstance(text, str):
        return 400, {"error": {"message": "expected {\"skillMd\": str}"}}
    fm, body, err = parse_skill_md(text)
    folder = fm.get("name").strip() if isinstance(fm.get("name"), str) else ""
    errors, hints = lint(unicodedata.normalize("NFKC", folder), fm, body, err)
    return 200, {"name": folder, "errors": errors, "hints": hints}


# ---------------------------------------------------------------- the skills folder

SEEDED_BEFORE = {"meeting-notes", "release-notes", "release-notes-verbose"}  # the starters before "_starters" was kept


def seed():
    """Copy the starter skills to skills/ the first time. Later, add each new starter once: meta["_starters"] lists
    the starters already given (a name with "_" cannot be a skill). A starter you uninstall does not come back;
    Reset brings all of them back. A skill of yours with the name of a new starter stays as it is."""
    with lock:
        if not os.path.isdir(STARTERS):
            return
        names = sorted(n for n in os.listdir(STARTERS) if os.path.isdir(os.path.join(STARTERS, n)))
        if not os.path.isdir(SKILLS):
            shutil.copytree(STARTERS, SKILLS)
            meta = {n: {"on": starter_on(n), "source": "Starter"} for n in names}
            meta["_starters"] = names
            write_meta(meta)
            return
        meta = read_meta()
        given = meta.get("_starters")
        if isinstance(given, list) and set(names) <= set(given):
            return
        given = given if isinstance(given, list) else sorted(SEEDED_BEFORE)
        for n in names:
            dst = os.path.join(SKILLS, n)
            if n not in given and not os.path.exists(dst):
                shutil.copytree(os.path.join(STARTERS, n), dst)
                meta[n] = {"on": starter_on(n), "source": "Starter"}
        meta["_starters"] = sorted(set(given) | set(names))
        write_meta(meta)


STARTERS_OFF = {"release-notes-verbose"}  # the bad example starts off: turn it on to compare


def starter_on(name):
    return name not in STARTERS_OFF


def read_meta():
    try:
        with open(META) as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(meta):
    tmp = META + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, META)


def find_skill_md(d):
    """The SKILL.md file of a folder: SKILL.md first, else skill.md (as skills-ref accepts it), else None."""
    for n in ("SKILL.md", "skill.md"):
        if os.path.isfile(os.path.join(d, n)) and n in os.listdir(d):  # listdir: the exact case, also on macOS
            return os.path.join(d, n)
    return None


def folder_of(name):
    """A folder in skills/ by name (with or without SKILL.md), or None. No paths, no hidden names."""
    if not isinstance(name, str) or not name or len(name) > 100 or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
        return None
    d = os.path.join(SKILLS, name)
    return d if os.path.isdir(d) else None


def skill_dir(name):
    """The folder of a skill, or None: the name must be a plain folder name in skills/."""
    if not isinstance(name, str) or not name or len(name) > 100 or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
        return None
    d = folder_of(name)
    return d if d and find_skill_md(d) else None


def safe_path(d, rel):
    """A file in the skill folder d, or None. No absolute paths, no .., no links out of the folder."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        return None
    p = os.path.realpath(os.path.join(d, rel))
    root = os.path.realpath(d)
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None


def is_binary(p):
    """A file that is not text for the model: a PDF or an image (not SVG) by its type, else a null byte or bytes
    that do not decode as UTF-8 (archives, data)."""
    t = mimetypes.guess_type(p)[0] or ""
    if t == "application/pdf" or (t.startswith("image/") and t != "image/svg+xml"):
        return True
    with open(p, "rb") as f:
        head = f.read(8192)
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start < len(head) - 4  # a character cut at the end of the 8 KB is still text
    return False


def mime_of(p):
    return mimetypes.guess_type(p)[0] or ("application/octet-stream" if is_binary(p) else "text/plain")


def files_of(d):
    main = os.path.basename(find_skill_md(d) or "SKILL.md")
    out = []
    for base, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "__pycache__")
        for n in sorted(names):
            if n.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(base, n), d).replace(os.sep, "/")
            if rel != main:
                full = os.path.join(base, n)
                out.append({"path": rel, "size": os.path.getsize(full), "binary": is_binary(full), "type": mime_of(full)})
    return out


def list_skills():
    seed()
    meta = read_meta()
    out = []
    if not os.path.isdir(SKILLS):
        return out
    for folder in sorted(os.listdir(SKILLS)):
        d = os.path.join(SKILLS, folder)
        if folder.startswith(".") or not os.path.isdir(d):
            continue
        f = find_skill_md(d)
        if not f:  # as skills-ref validate reports it; the folder can be uninstalled
            out.append({"name": folder, "description": "", "frontmatter": {}, "body": "", "skillMd": "", "files": files_of(d),
                        "on": False, "source": meta.get(folder, {}).get("source", "Created"), "url": None,
                        "errors": ["Missing required file: SKILL.md"], "hints": []})
            continue
        text = open(f, encoding="utf-8", errors="replace").read()
        fm, body, err = parse_skill_md(text)
        errors, hints = lint(folder, fm, body, err, [x["path"] for x in files_of(d)])
        m = meta.get(folder, {})
        out.append({"name": folder, "file": os.path.basename(f), "description": fm.get("description") if isinstance(fm.get("description"), str) else "",
                    "frontmatter": fm, "body": body, "skillMd": text, "files": files_of(d),
                    "on": m.get("on", True), "source": m.get("source", "Created"), "url": m.get("url"),
                    "example": m.get("example"), "errors": errors, "hints": hints})
    return out


def set_on(name, on):
    if not skill_dir(name):
        return 404, {"error": {"message": f"No skill named {name}."}}
    with lock:
        meta = read_meta()
        meta.setdefault(name, {"source": "Created"})["on"] = bool(on)
        write_meta(meta)
    return 200, {"ok": True}


def set_example(name, example):
    """The message that Try now puts in the message box. None removes it (back to the built-in example, if any)."""
    if not skill_dir(name):
        return 404, {"error": {"message": f"No skill named {name}."}}
    if example is not None and (not isinstance(example, str) or len(example) > 20_000):
        return 400, {"error": {"message": "The example must be text, at most 20,000 characters."}}
    with lock:
        meta = read_meta()
        m = meta.setdefault(name, {"source": "Created"})
        if example is None:
            m.pop("example", None)
        else:
            m["example"] = example
        write_meta(meta)
    return 200, {"ok": True}


def save_skill(name, text, old=None):
    """Create or replace SKILL.md. The folder takes the name of the frontmatter, as the spec asks.
    All or nothing: on a failure the folder, SKILL.md and skills-meta.json stay as they were."""
    if not isinstance(text, str) or len(text) > 200_000:
        return 400, {"error": {"message": "SKILL.md must be text, at most 200,000 characters."}}
    fm, body, err = parse_skill_md(text)
    new = fm.get("name")
    bad = [err] if err else name_errors(new)
    if bad or "/" in new or new in (".", ".."):
        return 400, {"error": {"message": " ".join(bad) or "Skill name: not a folder name."}}
    new = unicodedata.normalize("NFKC", new.strip())
    with lock:
        meta = read_meta()
        d = os.path.join(SKILLS, new)
        rename = bool(old) and old != new
        if old:
            src = skill_dir(old)
            if not src:
                return 404, {"error": {"message": f"No skill named {old}."}}
            if rename and os.path.exists(d):
                return 409, {"error": {"message": f"A skill named {new} already exists."}}
        elif os.path.exists(d):
            return 409, {"error": {"message": f"A skill named {new} already exists."}}
        else:
            src = None
        # 1. Write the new text next to the old file (a temp file), so a failed write changes nothing.
        work = src or d
        made = not src
        main = os.path.basename(find_skill_md(work) or "SKILL.md") if src else "SKILL.md"  # keep skill.md in lowercase
        tmp = os.path.join(work, ".SKILL.md.tmp")
        prev, moved, replaced = None, False, False
        try:
            if made:
                os.makedirs(d)
            else:
                with open(os.path.join(work, main), "rb") as f:  # the exact bytes, to restore them on a failure
                    prev = f.read()
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            # 2. Move the folder, then put the new file in place, then the metadata.
            if rename:
                os.rename(src, d)
                moved = True
                tmp = os.path.join(d, ".SKILL.md.tmp")
            os.replace(tmp, os.path.join(d, main))
            replaced = True
            m = meta.pop(old, None) if rename else meta.get(new)
            meta[new] = m or {"source": "Created", "on": True}
            write_meta(meta)
        except OSError as e:
            # Undo only the steps that happened, in the reverse order. Each undo step is atomic, and a failed undo
            # step is reported, not hidden.
            here = d if moved else work
            undo_errors = []
            def undo(what, fn):
                try:
                    fn()
                except OSError as x:
                    undo_errors.append(f"{what} ({x.strerror or x})")
            def restore():  # the old SKILL.md back: a temp file, then os.replace, so the file is never half written
                u = os.path.join(here, ".SKILL.md.undo")
                with open(u, "wb") as f:
                    f.write(prev)
                os.replace(u, os.path.join(here, main))
            if replaced and prev is not None:
                undo("restore SKILL.md", restore)
            if moved:
                undo(f"rename the folder back to {old}", lambda: os.rename(d, src))
            if made and os.path.isdir(d):
                undo("remove the new folder", lambda: shutil.rmtree(d))
            for t in {os.path.join(work, ".SKILL.md.tmp"), os.path.join(d, ".SKILL.md.tmp"),
                      os.path.join(here, ".SKILL.md.undo"), META + ".tmp"}:
                if os.path.exists(t):
                    undo(f"remove {os.path.basename(t)}", lambda t=t: os.remove(t))
            msg = f"Could not save the skill: {e.strerror or e}."
            if undo_errors:
                msg += " The undo also failed, check the folder by hand: " + "; ".join(undo_errors) + "."
            return 500, {"error": {"message": msg}}
    return 200, {"name": new}


FILE_RE = re.compile(r"^(?!.*\.\.)[\w.-]+(/[\w.-]+){0,3}$")


def save_file(name, rel, text=None, b64=None):
    """Add or change one file of a skill (not SKILL.md: Edit changes it). Text, or bytes as base64 (an upload)."""
    d = skill_dir(name)
    if not d:
        return 404, {"error": {"message": f"No skill named {name}."}}
    if not isinstance(rel, str) or not FILE_RE.match(rel) or rel.split("/")[-1].startswith("."):
        return 400, {"error": {"message": "Path: a relative path like references/guide.md, scripts/run.py or assets/template.txt (letters, digits, . _ -; at most 4 levels)."}}
    if rel.lower() == "skill.md":
        return 400, {"error": {"message": "Change SKILL.md with Edit."}}
    if b64 is not None:
        try:
            data = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return 400, {"error": {"message": "The upload is not valid base64."}}
    elif isinstance(text, str):
        data = text.encode("utf-8")
    else:
        return 400, {"error": {"message": "expected text or b64"}}
    if len(data) > 1_000_000:
        return 400, {"error": {"message": "A file can have at most 1 MB."}}
    p = os.path.realpath(os.path.join(d, rel))
    if not p.startswith(os.path.realpath(d) + os.sep):
        return 400, {"error": {"message": "The path must stay in the skill folder."}}
    with lock:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return 200, {"path": rel, "size": len(data)}


def delete_file(name, rel):
    d = skill_dir(name)
    p = d and safe_path(d, rel)
    if not p or os.path.dirname(p) == os.path.realpath(d) and os.path.basename(p).lower() == "skill.md":
        return 404, {"error": {"message": "No such file (SKILL.md cannot be deleted)."}}
    with lock:
        os.remove(p)
        parent, root = os.path.dirname(p), os.path.realpath(d)
        while parent != root and not os.listdir(parent):  # remove folders that are now empty
            os.rmdir(parent)
            parent = os.path.dirname(parent)
    return 200, {"ok": True}


def zip_skill(name):
    """The skill folder as a .zip, with the folder at the top (meeting-notes/SKILL.md…), as skills are shared."""
    import io, zipfile
    d = skill_dir(name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(find_skill_md(d), f"{name}/{os.path.basename(find_skill_md(d))}")
        for f in files_of(d):
            z.write(os.path.join(d, f["path"]), f"{name}/{f['path']}")
    return buf.getvalue()


def delete_skill(name):
    d = folder_of(name)  # also a folder without SKILL.md (not valid)
    if not d:
        return 404, {"error": {"message": f"No skill named {name}."}}
    with lock:
        shutil.rmtree(d)
        meta = read_meta()
        meta.pop(name, None)
        write_meta(meta)
    return 200, {"ok": True}


def reset():
    """Put the starter skills back. Your own and imported skills stay; a starter you changed is replaced."""
    if not os.path.isdir(STARTERS):
        return 404, {"error": {"message": "No starter skills."}}
    with lock:
        os.makedirs(SKILLS, exist_ok=True)
        meta = read_meta()
        for n in os.listdir(STARTERS):
            src = os.path.join(STARTERS, n)
            if os.path.isdir(src):
                dst = os.path.join(SKILLS, n)
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                meta[n] = {"on": starter_on(n), "source": "Starter"}
                meta["_starters"] = sorted(set(meta.get("_starters") or []) | {n})
        write_meta(meta)
    return 200, {"ok": True}


# ---------------------------------------------------------------- user-explicit activation

def activate(name):
    """/name in the message box: the user, not the model, activates the skill (agentskills.io/integrate-skills,
    "User-explicit activation"). The harness adds the instructions itself, so no tool call is needed. Returns
    (status, {name, result, files}): result is the <skill_content> block that goes before the user's message."""
    sk = next((x for x in list_skills() if x["name"] == name), None) if isinstance(name, str) else None
    if not sk:
        names = [x["name"] for x in list_skills() if x["on"] and not x["errors"]]
        return 404, {"error": {"message": f"There is no skill named /{name}. The skills are: {', '.join('/' + n for n in names) or 'none'}."}}
    if sk["errors"]:
        return 409, {"error": {"message": f"The skill {name} is not valid: {sk['errors'][0]} Fix it in Skills first."}}
    if not sk["on"]:
        return 409, {"error": {"message": f"The skill {name} is off. Turn it on in Skills first."}}
    files = [f["path"] for f in sk["files"]]
    result = (f'<skill_content name="{name}">\n{sk["body"].strip()}\n\nSkill folder: skills/{name}\n'
              f'Files: {", ".join(files) if files else "none"}\n</skill_content>')
    return 200, {"name": name, "result": result, "files": files}


# ---------------------------------------------------------------- the three skill tools

def call(tool, args):
    """Run one skill tool. Returns {ok, result, ...}: result is the text that goes back to the model."""
    name = args.get("name")
    d = skill_dir(name)
    on = read_meta().get(name, {}).get("on", True) if d else False
    if not d or not on:  # a skill that is off is not in the skill list, so the model cannot use it
        names = [s["name"] for s in list_skills() if s["on"]]
        return {"ok": False, "result": f"Error: there is no skill named {name!r}. The skills are: {', '.join(names) or 'none'}."}
    if tool == "load_skill":
        text = open(find_skill_md(d), encoding="utf-8", errors="replace").read()
        _, body, _ = parse_skill_md(text)
        files = [f["path"] for f in files_of(d)]
        result = body.strip() + f"\n\nSkill folder: skills/{name}\nFiles: " + (", ".join(files) if files else "none")
        return {"ok": True, "result": result, "layer": 2, "files": files}
    if tool == "read_skill_file":
        rel = args.get("path")
        p = safe_path(d, rel)
        if not p:
            files = [f["path"] for f in files_of(d)]
            return {"ok": False, "result": f"Error: {name} has no file {rel!r}. The files are: {', '.join(files) or 'none'}."}
        if is_binary(p):  # an image, a PDF…: no text for the model, only what it is
            return {"ok": True, "result": f"{rel} is a binary file ({mime_of(p)}, {os.path.getsize(p):,} bytes). It has no text to read. Use it by its path, for example in a script or as an attachment.", "layer": 3}
        text = open(p, encoding="utf-8", errors="replace").read()
        cut = len(text) > MAX_FILE
        return {"ok": True, "result": text[:MAX_FILE] + ("\n[cut: the file is longer]" if cut else ""), "layer": 3}
    if tool == "run_skill_script":
        rel = args.get("path")
        p = safe_path(d, rel)
        if not p:
            return {"ok": False, "result": f"Error: {name} has no script {rel!r}."}
        ext = os.path.splitext(p)[1].lower()
        if ext == ".py":
            uv = shutil.which("uv")
            cmd = [uv, "run", "--no-project", "--quiet", p] if uv else [shutil.which("python3") or "python3", p]
        elif ext == ".sh":
            cmd = ["/bin/sh", p]
        elif ext in (".js", ".mjs") and shutil.which("node"):
            cmd = [shutil.which("node"), p]
        else:
            return {"ok": False, "result": f"Error: LM Trace runs .py, .sh and .js scripts, not {ext or 'this file'}."}
        t0 = time.time()
        try:
            r = subprocess.run(cmd, input=str(args.get("input") or ""), capture_output=True, text=True, timeout=60, cwd=d)
        except subprocess.TimeoutExpired:
            return {"ok": False, "result": "Error: the script ran for 60 s and was stopped.", "layer": 3, "ms": 60000}
        out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr.strip() else "")
        out = out.strip() or "(no output)"
        cut = len(out) > MAX_OUT
        ok = r.returncode == 0
        return {"ok": ok, "result": (out[:MAX_OUT] + ("\n[cut: the output is longer]" if cut else "")) if ok else f"Error (exit code {r.returncode}): {out[:MAX_OUT]}",
                "layer": 3, "ms": round((time.time() - t0) * 1000), "exit": r.returncode, "cmd": " ".join(os.path.basename(c) if i == 0 else c.replace(ROOT + os.sep, "") for i, c in enumerate(cmd))}
    return {"ok": False, "result": f"Error: unknown skill tool {tool!r}."}


# ---------------------------------------------------------------- import from GitHub

GH_TREE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/tree/([^/]+)/(.+?)/?$")


def gh_get(url, binary=False):
    host = urllib.parse.urlparse(url).hostname
    if host not in ("api.github.com", "raw.githubusercontent.com"):
        raise ValueError(f"not a GitHub address: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": "lm-trace", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(MAX_IMPORT + 1)
    return data if binary else json.loads(data)


def import_github(url):
    """Download one skill folder from GitHub into skills/. Refused if a skill with that name exists."""
    m = GH_TREE.match(url.strip() if isinstance(url, str) else "")
    if not m:
        return 400, {"error": {"message": "Paste the address of a skill folder on GitHub: https://github.com/<owner>/<repo>/tree/<branch>/<folder>"}}
    owner, repo, ref, path = m.groups()
    folder = path.rstrip("/").split("/")[-1]
    if name_errors(folder):
        return 400, {"error": {"message": f"The folder name {folder!r} is not a valid skill name."}}
    if os.path.exists(os.path.join(SKILLS, folder)):
        return 409, {"error": {"message": f"A skill named {folder} already exists. Delete it first."}}
    files, total = {}, 0

    def walk(p):
        nonlocal total
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{urllib.parse.quote(p)}?ref={urllib.parse.quote(ref)}"
        items = gh_get(api)
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if it.get("type") == "dir":
                walk(it["path"])
            elif it.get("type") == "file":
                if len(files) >= 200:
                    raise ValueError("the skill has more than 200 files")
                total += it.get("size", 0)
                if total > MAX_IMPORT:
                    raise ValueError("the skill is larger than 3 MB")
                rel = it["path"][len(path.rstrip("/")) + 1:]
                files[rel] = gh_get(it["download_url"], binary=True)
    try:
        walk(path.rstrip("/"))
    except urllib.error.HTTPError as e:
        return 502, {"error": {"message": f"GitHub: {e.code} {e.reason}" + (" (the address may be wrong)" if e.code == 404 else "")}}
    except (urllib.error.URLError, ValueError, OSError) as e:
        return 502, {"error": {"message": f"Import: {e}"}}
    if "SKILL.md" not in files and "skill.md" not in files:
        return 400, {"error": {"message": "That folder has no SKILL.md, so it is not a skill."}}
    with lock:
        d = os.path.join(SKILLS, folder)
        for rel, data in files.items():
            p = os.path.realpath(os.path.join(d, rel))
            if not p.startswith(os.path.realpath(d) + os.sep):
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        meta = read_meta()
        meta[folder] = {"on": False, "source": "GitHub", "url": url.strip()}  # off: read it first, then turn it on
        write_meta(meta)
    return 200, {"name": folder, "files": len(files)}
