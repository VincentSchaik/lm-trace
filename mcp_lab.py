"""MCP for LM Trace: the MCP page.

Three parts, all with the standard library:

1. mcp.json: the MCP configuration of LM Studio (~/.lmstudio/mcp.json). LM Studio is the MCP host: it starts each
   server in the file and gives its tools to the model. LM Trace reads and writes the file for the page, but a
   secret (an API key in a URL, a header, an environment variable) never leaves this module: the page gets •••.
   When the page sends ••• back, the old value stays. A copy of the file is kept before each write.

2. A small MCP client (Inspect): it connects to one server of mcp.json, as a host does, and records every
   JSON-RPC message, so the page can show the real conversation: the handshake, then the tools, resources and prompts.

3. An MCP server (Streamable HTTP, at http://localhost:8765/mcp): the tools of the Tools page, served to LM Studio.
   LM Trace logs each message that it gets and sends, so the page can show what the host asked the server.
"""
import copy, json, os, queue, re, subprocess, threading, time, urllib.error, urllib.parse, urllib.request

MCP_JSON = os.path.expanduser("~/.lmstudio/mcp.json")
BACKUP = MCP_JSON + ".lm-trace.bak"  # not mcp.json.bak: that name may be the user's own copy
SECRET = "•••"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")  # the plugin id in LM Studio is mcp/<name>
CLIENT_INFO = {"name": "lm-trace", "title": "LM Trace", "version": "1.0"}
MODERN = "2026-07-28"  # no handshake: the client asks server/discover, and each request carries the version
LEGACY = "2025-11-25"  # the initialize handshake, for servers that do not know server/discover
lock = threading.Lock()


# ---------------------------------------------------------------- 1. mcp.json, with the secrets masked

def token_like(s):
    """A key or a token: a long run of letters and digits, without / or spaces (a path or a package name has them)."""
    return isinstance(s, str) and len(s) >= 16 and re.fullmatch(r"[A-Za-z0-9_\-.=+]+", s) is not None \
        and re.search(r"\d", s) is not None and re.search(r"[A-Za-z]", s) is not None


SECRET_KEY = re.compile(r"key|token|secret|auth|pass|sig|session|cred", re.I)


def mask_url(url):
    if not isinstance(url, str):
        return url
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return SECRET
    netloc = u.netloc
    if "@" in netloc:
        netloc = SECRET + "@" + netloc.rsplit("@", 1)[1]
    path = "/".join(SECRET if token_like(p) else p for p in u.path.split("/"))
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    query = "&".join(f"{k}={SECRET if SECRET_KEY.search(k) or token_like(v) else v}" for k, v in q)
    return urllib.parse.urlunsplit((u.scheme, netloc, path, query, u.fragment))


def mask_server(d):
    """One server of mcp.json, as the page may see it."""
    out = {}
    for k, v in d.items():
        if k in ("env", "headers") and isinstance(v, dict):  # a value is secret when its name or its form says so
            out[k] = {kk: SECRET if SECRET_KEY.search(kk) or token_like(vv) or not isinstance(vv, str) else vv for kk, vv in v.items()}
        elif k == "url":
            out[k] = mask_url(v)
        elif k == "args" and isinstance(v, list):
            out[k] = [SECRET if token_like(a) or (isinstance(a, str) and re.match(r"--?[\w-]*(key|token|secret)[\w-]*=", a, re.I)) else a for a in v]
        elif isinstance(v, (str, int, float, bool, type(None))) or k in ("command", "cwd"):
            out[k] = v if not (isinstance(v, str) and token_like(v) and k not in ("command", "cwd")) else SECRET
        else:
            out[k] = SECRET  # an unknown structure: not shown
    return out


def read_config():
    """(the parsed file, error text). A missing file is an empty configuration."""
    try:
        with open(MCP_JSON) as f:
            text = f.read()
    except FileNotFoundError:
        return {"mcpServers": {}}, None
    except OSError as e:
        return None, f"Cannot read mcp.json: {e}"
    try:
        cfg = json.loads(text) if text.strip() else {}
    except ValueError as e:
        return None, f"mcp.json is not valid JSON: {e}"
    if not isinstance(cfg, dict):
        return None, "mcp.json is not a JSON object"
    if not isinstance(cfg.get("mcpServers"), dict):
        cfg["mcpServers"] = {}
    return cfg, None


def kind_of(d):
    return "http" if isinstance(d.get("url"), str) else "stdio"


def config_view():
    """The page's view of mcp.json: the servers (masked) and the whole file as masked text."""
    cfg, err = read_config()
    if err:
        return {"path": MCP_JSON, "error": err, "servers": [], "text": ""}
    masked = {**{k: v for k, v in cfg.items() if k != "mcpServers"},
              "mcpServers": {n: mask_server(d) if isinstance(d, dict) else SECRET for n, d in cfg["mcpServers"].items()}}
    servers = [{"name": n, "type": kind_of(d), "def": masked["mcpServers"][n], "pluginId": f"mcp/{n}",
                "validName": bool(NAME_RE.match(n))} for n, d in cfg["mcpServers"].items() if isinstance(d, dict)]
    return {"path": MCP_JSON.replace(os.path.expanduser("~"), "~"), "servers": servers,
            "text": json.dumps(masked, indent=2, ensure_ascii=False)}


def unmask(new, old):
    """The page sends the masked values back as •••: put the real values in again."""
    old = old or {}
    out = {}
    for k, v in new.items():
        if k in ("env", "headers") and isinstance(v, dict):
            ov = old.get(k) if isinstance(old.get(k), dict) else {}
            out[k] = {kk: (ov.get(kk, "") if vv == SECRET else vv) for kk, vv in v.items()}
        elif k == "url" and isinstance(v, str) and SECRET in v:
            if mask_url(old.get("url")) != v:
                raise ValueError("The URL has ••• in it, but it is not the saved URL. Type the full URL again.")
            out[k] = old["url"]
        elif k == "args" and isinstance(v, list):
            oa = old.get("args") if isinstance(old.get("args"), list) else []
            res = []
            for i, a in enumerate(v):
                if isinstance(a, str) and SECRET in a:
                    m = mask_server({"args": oa}).get("args", [])
                    j = m.index(a) if a in m else -1
                    if j < 0:
                        raise ValueError("An argument has ••• in it. Type the full value again.")
                    a = oa[j]
                res.append(a)
            out[k] = res
        elif v == SECRET:
            if k not in old:
                raise ValueError(f"{k}: type the value.")
            out[k] = old[k]
        else:
            out[k] = v
    return out


def clean_def(d):
    """Check a server definition from the page. Returns the definition for mcp.json."""
    if not isinstance(d, dict):
        raise ValueError("expected a server definition")
    out = {}
    if d.get("type") == "http" or "url" in d:
        url = d.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url) or len(url) > 2000:
            raise ValueError("URL: type an http or https address, for example https://search.parallel.ai/mcp")
        out["url"] = url.strip()
        if d.get("headers"):
            out["headers"] = d["headers"]
    else:
        cmd = d.get("command")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 500:
            raise ValueError("Command: type the program that starts the server, for example npx")
        out["command"] = cmd.strip()
        args = d.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, str) and len(a) <= 2000 for a in args) or len(args) > 50:
            raise ValueError("Arguments: a list of texts")
        out["args"] = [a for a in args if a != ""]
        if d.get("env"):
            out["env"] = d["env"]
        if isinstance(d.get("cwd"), str) and d["cwd"].strip():
            out["cwd"] = d["cwd"].strip()
    for k in ("env", "headers"):
        if k in out:
            v = out[k]
            if not isinstance(v, dict) or not all(isinstance(a, str) and isinstance(b, str) and a for a, b in v.items()):
                raise ValueError(f"{k}: a list of names and values")
            out[k] = {a.strip(): b for a, b in v.items() if a.strip()}
            if not out[k]:
                del out[k]
    return out


def write_config(cfg):
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(MCP_JSON), exist_ok=True)
    if os.path.exists(MCP_JSON):
        with open(MCP_JSON, "rb") as f, open(BACKUP, "wb") as b:
            b.write(f.read())
        os.chmod(BACKUP, 0o600)
    tmp = MCP_JSON + ".lm-trace.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, MCP_JSON)


def save_server(req):
    """Add or change one server. req: {name, def, old?}: old is the name before a rename."""
    name, old_name = req.get("name"), req.get("old")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("Name: lowercase letters, digits, - and _ only, for example parallel-search. LM Studio uses it in the plugin id mcp/<name>.")
    with lock:
        cfg, err = read_config()
        if err:
            raise ValueError(err)
        servers = cfg["mcpServers"]
        if old_name not in (None, name) and old_name not in servers:
            raise ValueError(f"No server named {old_name}")
        if name in servers and old_name != name:
            raise ValueError(f"There is already a server named {name}")
        prev = servers.get(old_name if old_name else name)
        d = clean_def(unmask(req.get("def") or {}, prev))
        # Keep the keys of the old entry that the page does not edit.
        if isinstance(prev, dict):
            for k, v in prev.items():
                if k not in ("command", "args", "env", "cwd", "url", "headers", "type"):
                    d.setdefault(k, v)
        new = {}
        for n, v in servers.items():  # keep the order of the file; a renamed server keeps its place
            new[name if n in (old_name, name) else n] = d if n in (old_name, name) else v
        if name not in new:
            new[name] = d
        cfg["mcpServers"] = new
        write_config(cfg)
    return config_view()


def delete_server(name):
    with lock:
        cfg, err = read_config()
        if err:
            raise ValueError(err)
        if name not in cfg["mcpServers"]:
            raise ValueError(f"No server named {name}")
        del cfg["mcpServers"][name]
        write_config(cfg)
    return config_view()


def server_def(name):
    """The real definition of one server (with its secrets), for the client of this module only."""
    cfg, err = read_config()
    if err:
        raise ValueError(err)
    d = cfg["mcpServers"].get(name)
    if not isinstance(d, dict):
        raise ValueError(f"No server named {name} in mcp.json")
    return d


# ---------------------------------------------------------------- 2. The client of the Inspect view

def scrub(obj, secrets):
    """A recorded message, with every secret value replaced by •••."""
    text = json.dumps(obj, ensure_ascii=False)
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(json.dumps(s, ensure_ascii=False)[1:-1], SECRET)
    return json.loads(text)


class Conn:
    """One connection to one server, stdio or Streamable HTTP. Records each message as {dir, msg, ms, note}."""

    def __init__(self, d):
        self.d, self.t0, self.log, self.next_id = d, time.time(), [], 1
        self.secrets = [v for k in ("env", "headers") for v in (d.get(k) or {}).values()]
        if isinstance(d.get("url"), str):
            u = urllib.parse.urlsplit(d["url"])
            self.secrets += [p for p in u.path.split("/") if token_like(p)] + [v for _, v in urllib.parse.parse_qsl(u.query)]
        self.secrets += [a for a in d.get("args") or [] if token_like(a)]
        self.session, self.version, self.proc, self.stderr, self.auth = None, None, None, [], None
        if kind_of(d) == "stdio":
            env = {**os.environ, **(d.get("env") or {})}
            cmd = [d["command"], *(d.get("args") or [])]
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             env=env, cwd=os.path.expanduser(d["cwd"]) if d.get("cwd") else None, text=True, bufsize=1)
            except OSError as e:
                raise ValueError(f"Cannot start {d['command']}: {e}")
            self.lines = queue.Queue()
            threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines), daemon=True).start()
            threading.Thread(target=self._pump_err, daemon=True).start()
            self.note("start", "Host starts the server as a child process: " + " ".join(scrub(cmd, self.secrets)))

    def _pump(self, f, q):
        for line in f:
            q.put(line)
        q.put(None)

    def _pump_err(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())
            del self.stderr[:-40]

    def ms(self):
        return int((time.time() - self.t0) * 1000)

    def note(self, kind, text):
        self.log.append({"dir": "note", "kind": kind, "text": text, "ms": self.ms()})

    def record(self, direction, msg, extra=None):
        e = {"dir": direction, "msg": scrub(msg, self.secrets), "ms": self.ms()}
        if extra:
            e.update(extra)
        self.log.append(e)

    def send(self, method, params=None, notify=False, timeout=60):
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = self.next_id
            self.next_id += 1
        if params is not None:
            msg["params"] = params
        self.record("out", msg)
        if self.proc:
            return self._send_stdio(msg, notify, timeout)
        return self._send_http(msg, notify, timeout)

    def _send_stdio(self, msg, notify, timeout):
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ValueError("The server stopped. " + " ".join(self.stderr[-3:]))
        if notify:
            return None
        end = time.time() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                raise ValueError(f"No answer in {timeout} s. " + " ".join(self.stderr[-3:]))
            if line is None:
                raise ValueError("The server stopped. " + " ".join(self.stderr[-3:]))
            try:
                r = json.loads(line)
            except ValueError:
                self.note("stdout", "Not JSON on stdout (a server must never print there): " + line.strip()[:200])
                continue
            self.record("in", r)
            if r.get("id") == msg["id"]:
                return r

    def _send_http(self, msg, notify, timeout):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": "lm-trace-mcp-client/1.0", **(self.d.get("headers") or {})}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.d["url"], data=json.dumps(msg).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, ctype, body = r.status, r.headers.get("Content-Type", ""), r.read(2_000_000).decode("utf-8", "replace")
                sid = r.headers.get("Mcp-Session-Id")
        except urllib.error.HTTPError as e:
            body = e.read(4000).decode("utf-8", "replace")
            if e.code == 401:
                self.unauthorized(e.headers.get("WWW-Authenticate", ""), body)
            try:
                r = json.loads(body)
                self.record("in", r, {"http": e.code})
                if isinstance(r, dict) and "error" in r:
                    return r
            except ValueError:
                pass
            raise ValueError(f"HTTP {e.code}: {body[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ValueError(f"Cannot reach the server: {getattr(e, 'reason', e)}")
        if sid:
            self.session = sid
        if notify or not body.strip():
            if notify:
                self.note("http", f"HTTP {status}: no body (a notification gets no answer)")
            return None
        msgs = []
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        msgs.append(json.loads(line[5:].strip()))
                    except ValueError:
                        pass
        else:
            try:
                msgs = [json.loads(body)]
            except ValueError:
                raise ValueError(f"Not JSON: {body[:200]}")
        hit = None
        for r in msgs:
            self.record("in", r, {"sse": "text/event-stream" in ctype})
            if isinstance(r, dict) and r.get("id") == msg["id"]:
                hit = r
        if hit is None:
            raise ValueError("No answer to the request")
        return hit

    def meta(self):
        """In the 2026-07-28 version, each request says its protocol version and the client's capabilities."""
        if self.version != MODERN:
            return None
        return {"io.modelcontextprotocol/protocolVersion": MODERN, "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": CLIENT_INFO}

    def call(self, method, params=None):
        p = dict(params or {})
        m = self.meta()
        if m:
            p["_meta"] = m
        r = self.send(method, p or None)
        if "error" in r:
            raise ValueError(f"{method}: {r['error'].get('message', r['error'])}")
        return r.get("result") or {}

    def unauthorized(self, www, body):
        """HTTP 401: the server needs an access token. Read its protected resource metadata (RFC 9728), as an MCP client does."""
        self.record("in", {"http": 401, "WWW-Authenticate": www or None, "body": body[:200]}, {"http": 401})
        u = urllib.parse.urlsplit(self.d["url"])
        m = re.search(r'resource_metadata="([^"]+)"', www or "")
        urls = [m.group(1)] if m else []
        base = f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource"
        urls += [base + u.path.rstrip("/"), base] if u.path.strip("/") else [base]
        meta, used = None, None
        for url in urls:
            if not url.startswith("https://") and urllib.parse.urlsplit(url).hostname not in ("localhost", "127.0.0.1"):
                continue
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lm-trace-mcp-client/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    meta, used = json.loads(r.read(100_000)), url
                    break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                continue
        self.auth = {"wwwAuthenticate": www or None, "metadataUrl": used, "metadata": meta}
        if meta:
            self.note("auth", f"GET {used}: the server needs a token from {', '.join(meta.get('authorization_servers') or ['its authorization server'])}.")
        raise ValueError("HTTP 401: the server needs an access token.")

    def connect(self):
        """Modern first (server/discover), else the initialize handshake. Returns the server's capabilities and info."""
        r = self.send("server/discover", {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN,
                                                    "io.modelcontextprotocol/clientCapabilities": {},
                                                    "io.modelcontextprotocol/clientInfo": CLIENT_INFO}})
        res = r.get("result") if isinstance(r, dict) else None
        if res and MODERN in (res.get("supportedVersions") or []):
            self.version = MODERN
            self.note("version", f"The server knows version {MODERN}: no handshake, each request carries the version.")
            return {"version": MODERN, "mode": "discover", "capabilities": res.get("capabilities") or {},
                    "serverInfo": (res.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo") or res.get("serverInfo") or {},
                    "instructions": res.get("instructions")}
        self.note("version", "The server does not know server/discover: the client uses the initialize handshake of the older version.")
        r = self.send("initialize", {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": CLIENT_INFO})
        if "error" in r:
            raise ValueError(f"initialize: {r['error'].get('message', r['error'])}")
        res = r.get("result") or {}
        self.version = res.get("protocolVersion") or LEGACY
        self.send("notifications/initialized", notify=True)
        return {"version": self.version, "mode": "initialize", "capabilities": res.get("capabilities") or {},
                "serverInfo": res.get("serverInfo") or {}, "instructions": res.get("instructions")}

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
            self.note("stop", "Host closes the connection: the server process ends.")
        elif self.session:
            try:  # end the session, as the spec asks of a client that is done
                h = {"Mcp-Session-Id": self.session, "User-Agent": "lm-trace-mcp-client/1.0", **(self.d.get("headers") or {})}
                urllib.request.urlopen(urllib.request.Request(self.d["url"], headers=h, method="DELETE"), timeout=5).close()
            except Exception:
                pass


def inspect(name, op=None):
    """Connect to a server of mcp.json and list what it has. op: {"read": uri} or {"prompt": name, "arguments": {...}}."""
    d = server_def(name)
    c = Conn(d)
    out = {"name": name, "type": kind_of(d)}
    try:
        info = c.connect()
        out.update(info)
        caps = info["capabilities"]
        if op and op.get("read"):
            out["read"] = c.call("resources/read", {"uri": op["read"]})
        elif op and op.get("prompt"):
            out["prompt"] = c.call("prompts/get", {"name": op["prompt"], "arguments": op.get("arguments") or {}})
        else:
            for cap, method, key in (("tools", "tools/list", "tools"), ("resources", "resources/list", "resources"),
                                     ("resources", "resources/templates/list", "resourceTemplates"), ("prompts", "prompts/list", "prompts")):
                if cap in caps:
                    try:
                        out[key] = c.call(method).get(key, [])
                    except ValueError as e:
                        out.setdefault("errors", []).append(str(e))
    except ValueError as e:
        out["error"] = str(e)
    finally:
        c.close()
        out["log"] = c.log
        if c.auth:
            out["auth"] = c.auth
        if c.stderr:
            out["stderr"] = scrub(c.stderr[-20:], c.secrets)
    return out


# ---------------------------------------------------------------- 3. The MCP server of LM Trace (Streamable HTTP)

SERVER_INFO = {"name": "lm-trace", "title": "LM Trace tools", "version": "1.0"}
CAPS = {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}}
log = []  # [{t, dir, msg, peer}] the messages of the LM Trace server, newest last
log_lock = threading.Lock()
seq = [0]


def log_msg(direction, msg, ua=""):
    with log_lock:
        seq[0] += 1
        log.append({"n": seq[0], "t": int(time.time() * 1000), "dir": direction, "msg": msg, "ua": ua})
        del log[:-400]


def log_since(n):
    with log_lock:
        return [e for e in log if e["n"] > n]


def mcp_tool(t):
    """A tool of the Tools page, as an MCP tool: name, title, description and the JSON schema of its input."""
    props, req = {}, []
    for p in t.get("params") or []:
        props[p["name"]] = {"type": p.get("type", "string"), **({"description": p["description"]} if p.get("description") else {}),
                            **({"enum": p["enum"]} if p.get("enum") else {})}
        if p.get("required"):
            req.append(p["name"])
    return {"name": t["name"], "title": t.get("title") or t["name"], "description": t.get("description", ""),
            "inputSchema": {"type": "object", "properties": props, "required": req},
            "annotations": {"readOnlyHint": True, "openWorldHint": t.get("kind") == "url"}}


PROMPTS = [
    {"name": "price_outlook", "title": "Price outlook",
     "description": "The price of a coin now, and its value in 10 years at 10% a year. Needs the tools crypto_price and future_value.",
     "arguments": [{"name": "coin", "description": "The coin, for example Bitcoin or Ether", "required": True},
                   {"name": "currency", "description": "The currency of the price, for example CAD. Default: CAD", "required": False}],
     "text": "What is the price of {coin} in {currency} now? Then, what would it be worth in 10 years if it grows 10% a year? Show both numbers."},
    {"name": "convert", "title": "Convert money",
     "description": "Convert an amount from one currency to another with the latest rate. Needs the tool exchange_rate.",
     "arguments": [{"name": "amount", "description": "The amount, for example 100", "required": True},
                   {"name": "from", "description": "The currency to convert from, for example USD", "required": True},
                   {"name": "to", "description": "The currency to convert to, for example CAD", "required": True}],
     "text": "Convert {amount} {from} to {to} with the latest exchange rate. Give the rate and its date."},
]


def handle(msg, tools, run_tool, resources):
    """One JSON-RPC message to the LM Trace server → the answer, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method, p = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:
        return None  # notifications/initialized and others: nothing to answer
    ok = lambda res: {"jsonrpc": "2.0", "id": mid, "result": res}
    err = lambda code, text: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}
    if method == "initialize":
        v = p.get("protocolVersion")
        return ok({"protocolVersion": v if v in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05") else "2025-06-18",
                   "capabilities": CAPS, "serverInfo": SERVER_INFO,
                   "instructions": "The tools of the LM Trace Tools page. Each tool calls a free public API, or searches the saved facts of the Memory page."})
    if method == "server/discover":
        return ok({"resultType": "complete", "supportedVersions": [MODERN], "capabilities": CAPS,
                   "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO}, "ttlMs": 0, "cacheScope": "private"})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [mcp_tool(t) for t in tools]})
    if method == "tools/call":
        t = next((x for x in tools if x["name"] == p.get("name")), None)
        if not t:
            return err(-32602, f"Unknown tool: {p.get('name')}")
        r = run_tool(t, p.get("arguments") or {})
        return ok({"content": [{"type": "text", "text": r["result"]}], "isError": not r["ok"]})
    if method == "resources/list":
        return ok({"resources": [{k: v for k, v in r.items() if k != "read"} for r in resources]})
    if method == "resources/templates/list":
        return ok({"resourceTemplates": []})
    if method == "resources/read":
        r = next((x for x in resources if x["uri"] == p.get("uri")), None)
        if not r:
            return err(-32602, f"Resource not found: {p.get('uri')}")
        return ok({"contents": [{"uri": r["uri"], "mimeType": r["mimeType"], "text": r["read"]()}]})
    if method == "prompts/list":
        return ok({"prompts": [{k: v for k, v in x.items() if k != "text"} for x in PROMPTS]})
    if method == "prompts/get":
        x = next((x for x in PROMPTS if x["name"] == p.get("name")), None)
        if not x:
            return err(-32602, f"Unknown prompt: {p.get('name')}")
        args = p.get("arguments") or {}
        missing = [a["name"] for a in x["arguments"] if a.get("required") and not args.get(a["name"])]
        if missing:
            return err(-32602, f"Missing argument: {', '.join(missing)}")
        defaults = {"currency": "CAD"}
        text = re.sub(r"\{(\w+)\}", lambda m: str(args.get(m.group(1)) or defaults.get(m.group(1), "")), x["text"])
        return ok({"description": x["description"], "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
    return err(-32601, f"Method not found: {method}")


# ---------------------------------------------------------------- The official registry (Browse directory)

def registry(search, cursor=None):
    q = {"limit": "30", "version": "latest"}
    if search:
        q["search"] = search[:100]
    if cursor:
        q["cursor"] = cursor[:300]
    url = "https://registry.modelcontextprotocol.io/v0/servers?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lm-trace/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read(3_000_000))
