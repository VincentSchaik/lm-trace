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
import json, os, re, shutil, subprocess, threading, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(ROOT, "skills")
STARTERS = os.path.join(ROOT, "skill-starters")
META = os.path.join(ROOT, "skills-meta.json")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # the spec: lowercase letters, digits and single hyphens
MAX_FILE = 200_000      # the most characters that read_skill_file returns
MAX_OUT = 20_000        # the most characters of script output that go back to the model
MAX_IMPORT = 3_000_000  # the most bytes of one imported skill
lock = threading.Lock()


# ---------------------------------------------------------------- SKILL.md: frontmatter and body

def parse_skill_md(text):
    """Split SKILL.md into (frontmatter dict, body, error). A small YAML reader for the fields of the spec:
    key: value, quoted values, block values (> and |), and one level of nested keys (metadata)."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", text, re.S)
    if not m:
        return {}, text, "SKILL.md must start with YAML frontmatter between two --- lines."
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
                return data, body, f"Frontmatter line {i} is not 'key: value': {line.strip()[:60]}"
            key, val = km.group(1), km.group(2).strip()
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
                        sub[sm.group(1)] = unquote(sm.group(2).strip())
                    i += 1
                data[key] = sub
            else:
                data[key] = unquote(val)
    except Exception as e:  # never fail the whole list for one bad file
        return data, body, f"Frontmatter: {e}"
    return data, body, None


def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
        return v.replace('\\"', '"') if v else v
    return v


def lint(folder, fm, body, err):
    """Problems against the Agent Skills specification (errors) and its best practices (hints)."""
    errors, hints = [], []
    if err:
        errors.append(err)
    name, desc = fm.get("name"), fm.get("description")
    if not isinstance(name, str) or not name:
        errors.append("name is required.")
    else:
        if len(name) > 64:
            errors.append(f"name has {len(name)} characters: at most 64.")
        if not NAME_RE.match(name):
            errors.append("name: only lowercase letters, digits and single hyphens; no hyphen at the start or end.")
        if name != folder:
            errors.append(f"name must match the folder name ({folder}).")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("description is required.")
    else:
        if len(desc) > 1024:
            errors.append(f"description has {len(desc)} characters: at most 1,024.")
        if not re.search(r"\b(use|when)\b", desc, re.I):
            hints.append("description: say when to use the skill (\"Use this skill when…\"). It is the only text the model sees before it activates the skill.")
        elif len(desc) < 60:
            hints.append("description is very short: list the tasks and the words that should activate the skill.")
    comp = fm.get("compatibility")
    if isinstance(comp, str) and len(comp) > 500:
        errors.append(f"compatibility has {len(comp)} characters: at most 500.")
    lines = body.count("\n") + 1 if body.strip() else 0
    if lines > 500:
        hints.append(f"SKILL.md body has {lines} lines: keep it under 500. Move details to reference files.")
    if re.search(r"\bsee (the )?(references?|docs?)/? for (more )?details\b", body, re.I):
        hints.append("Say when to read each reference file, not only \"see references/ for details\".")
    return errors, hints


# ---------------------------------------------------------------- the skills folder

def seed():
    """Copy the starter skills to skills/ the first time."""
    with lock:
        if not os.path.isdir(SKILLS) and os.path.isdir(STARTERS):
            shutil.copytree(STARTERS, SKILLS)
            meta = {n: {"on": starter_on(n), "source": "Starter"} for n in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, n))}
            write_meta(meta)


def starter_on(name):
    """A starter skill is on, unless its SKILL.md says lm-trace-on: "false" (the bad examples start off)."""
    try:
        fm, _, _ = parse_skill_md(open(os.path.join(STARTERS, name, "SKILL.md"), encoding="utf-8").read())
        return (fm.get("metadata") or {}).get("lm-trace-on", "true") != "false"
    except OSError:
        return True


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


def skill_dir(name):
    """The folder of a skill, or None: the name must be a plain folder name in skills/."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name):
        return None
    d = os.path.join(SKILLS, name)
    return d if os.path.isfile(os.path.join(d, "SKILL.md")) else None


def safe_path(d, rel):
    """A file in the skill folder d, or None. No absolute paths, no .., no links out of the folder."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        return None
    p = os.path.realpath(os.path.join(d, rel))
    root = os.path.realpath(d)
    return p if p.startswith(root + os.sep) and os.path.isfile(p) else None


def files_of(d):
    out = []
    for base, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "__pycache__")
        for n in sorted(names):
            if n.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(base, n), d).replace(os.sep, "/")
            if rel != "SKILL.md":
                out.append({"path": rel, "size": os.path.getsize(os.path.join(base, n))})
    return out


def list_skills():
    seed()
    meta = read_meta()
    out = []
    if not os.path.isdir(SKILLS):
        return out
    for folder in sorted(os.listdir(SKILLS)):
        d = os.path.join(SKILLS, folder)
        f = os.path.join(d, "SKILL.md")
        if not os.path.isfile(f):
            continue
        text = open(f, encoding="utf-8", errors="replace").read()
        fm, body, err = parse_skill_md(text)
        errors, hints = lint(folder, fm, body, err)
        m = meta.get(folder, {})
        out.append({"name": folder, "description": fm.get("description") if isinstance(fm.get("description"), str) else "",
                    "frontmatter": fm, "body": body, "skillMd": text, "files": files_of(d),
                    "on": m.get("on", True), "source": m.get("source", "Created"), "url": m.get("url"),
                    "errors": errors, "hints": hints})
    return out


def set_on(name, on):
    if not skill_dir(name):
        return 404, {"error": {"message": f"No skill named {name}."}}
    with lock:
        meta = read_meta()
        meta.setdefault(name, {"source": "Created"})["on"] = bool(on)
        write_meta(meta)
    return 200, {"ok": True}


def save_skill(name, skill_md, old=None):
    """Create or replace SKILL.md. The folder takes the name of the frontmatter, as the spec asks."""
    if not isinstance(skill_md, str) or len(skill_md) > 200_000:
        return 400, {"error": {"message": "SKILL.md must be text, at most 200,000 characters."}}
    fm, body, err = parse_skill_md(skill_md)
    new = fm.get("name")
    if err or not isinstance(new, str) or not NAME_RE.match(new) or len(new) > 64:
        return 400, {"error": {"message": err or "name: 1–64 lowercase letters, digits and single hyphens."}}
    with lock:
        meta = read_meta()
        d = os.path.join(SKILLS, new)
        if old and old != new:
            if not skill_dir(old):
                return 404, {"error": {"message": f"No skill named {old}."}}
            if os.path.exists(d):
                return 409, {"error": {"message": f"A skill named {new} already exists."}}
            os.rename(os.path.join(SKILLS, old), d)
            meta[new] = meta.pop(old, {"source": "Created", "on": True})
        elif not old and os.path.exists(d):
            return 409, {"error": {"message": f"A skill named {new} already exists."}}
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_md)
        meta.setdefault(new, {"source": "Created", "on": True})
        write_meta(meta)
    return 200, {"name": new}


def delete_skill(name):
    d = skill_dir(name)
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
        write_meta(meta)
    return 200, {"ok": True}


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
        text = open(os.path.join(d, "SKILL.md"), encoding="utf-8", errors="replace").read()
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
    if not NAME_RE.match(folder) or len(folder) > 64:
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
    if "SKILL.md" not in files:
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
