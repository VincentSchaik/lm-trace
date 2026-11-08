"""Serve LM Trace and proxy /v1/* to the LM Studio server (avoids CORS).

Also tails LM Studio's model log (`lms log stream`) so the page can show the exact
formatted prompt (chat template + special tokens) the model received.

Optional: with the `lmstudio` package installed, POST /tokenize returns the tokens of a text
(or, with "count": true, only the number of tokens of each text).
LM Studio has no tokenize endpoint over HTTP; only its SDK can tokenize.

POST /embed-rows reads the input embeddings of tokens (rows of the model's embedding table)
straight from the model's safetensors files. LM Studio does not expose them.

POST /tool-run runs one tool of the Tools page: a built-in tool (Python, below) or a URL tool
(a GET request to a public API, with the arguments in the URL). The page never calls other websites.

/agent-memory/* is the long-term memory of the Memory page: one .md file per fact plus a
MEMORY.md index (as Claude Code does it), and, with the optional `chromadb` package, the same
facts as vectors in a Chroma collection on disk. The page sends the vectors (from LM Studio).
"""
import atexit, glob, http.server, json, math, os, re, shutil, signal, struct, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error

LMS = os.environ.get("LMS_URL", "http://localhost:1234")
TOKEN = os.environ.get("LM_API_TOKEN")
PORT = int(os.environ.get("PORT", "8765"))
ROOT = os.path.dirname(os.path.abspath(__file__))
# Saved lists: user prompts and system prompts, one JSON file each.
LISTS = {k: os.path.join(ROOT, k.strip("/") + ".json") for k in ("/prompts", "/system-prompts", "/few-shot", "/schemas", "/presets",
                                                                   "/embed-texts", "/rag-docs", "/rag-questions", "/tools", "/model-formats", "/embed-prefixes")}
# Starting values of a list, until you save it. Model formats: where the thinking starts and ends in the raw text
# of the model, as each model wrote it in a real run. You can edit them in Settings.
LIST_START = {"/model-formats": [
    {"name": "google/gemma-4-12b", "thinkStart": "<|channel>thought", "thinkEnd": "<channel|>"},
    {"name": "zai-org/glm-4.6v-flash", "thinkStart": "<think>", "thinkEnd": "</think>"},
    {"name": "lmstudio-community/gpt-oss-20b", "thinkStart": "<|channel|>analysis<|message|>", "thinkEnd": "<|end|>"},
],
    # Embedding prefixes: the text before a query and before a document, as the makers of each model recommend.
    "/embed-prefixes": [
    {"name": "text-embedding-embeddinggemma-300m-qat", "query": "task: search result | query: ", "doc": "title: none | text: "},
    {"name": "text-embedding-nomic-embed-text-v1.5", "query": "search_query: ", "doc": "search_document: "},
]}


def embed_prefixes(model):
    """(query prefix, document prefix) of an embedding model, from Settings. The exact name first, else a saved
    name inside the model name. A model without an entry gets no prefix."""
    try:
        with open(LISTS["/embed-prefixes"]) as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = LIST_START["/embed-prefixes"]
    m = model.lower()
    row = next((r for r in rows if r.get("name", "").lower() == m), None) \
        or next((r for r in rows if r.get("name") and r["name"].lower() in m), None)
    return (row.get("query", ""), row.get("doc", "")) if row else ("", "")
PAGES = ("/index.html", "/embeddings.html", "/rag.html", "/memory.html", "/tools.html", "/settings.html")
MEM_DIR = os.path.join(ROOT, "agent-memory")  # long-term memory of the Memory page
LMS_CLI = shutil.which("lms") or os.path.expanduser("~/.lmstudio/bin/lms")
# Only local names: a different Host means DNS rebinding; a different Origin means another website.
LOCAL = ("localhost", "127.0.0.1", "[::1]")
HOSTS = {h for n in LOCAL for h in (n, f"{n}:{PORT}")}
ORIGINS = {f"http://{n}:{PORT}" for n in LOCAL}

inputs = []  # [{"t": ms received, "input": str, "model": str}], newest last
outputs = []  # [{"t": ms received, "output": str, "model": str}]: the raw text the model generated, with its special tokens
inputs_lock = threading.Lock()
log_ok = False
log_proc = None


def tail_model_log():
    """Keep `lms log stream` running and record every formatted model input and raw model output."""
    global log_ok, log_proc
    while True:
        try:
            proc = log_proc = subprocess.Popen([LMS_CLI, "log", "stream", "-s", "model", "--filter", "input,output", "--json"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            log_ok = True
            for line in proc.stdout:
                try:
                    d = json.loads(line).get("data", {})
                except ValueError:
                    continue
                kind = {"llm.prediction.input": "input", "llm.prediction.output": "output"}.get(d.get("type"))
                if kind:
                    log = inputs if kind == "input" else outputs
                    with inputs_lock:
                        log.append({"t": int(time.time() * 1000), kind: d.get(kind, ""), "model": d.get("modelIdentifier", "")})
                        del log[:-20]
        except OSError:
            pass
        log_ok = False
        time.sleep(3)  # lms missing or LM Studio restarted; retry


sdk_client = None
sdk_lock = threading.Lock()


def split_tokens(model, text):
    """Token ids of `text`, and the text of each token.

    The SDK returns ids only. The text of each token comes from the prefixes of the input:
    token k ends at the first prefix whose tokens start with the first k + 1 tokens of the full text.
    """
    prefixes = [text[:e] for e in range(len(text) + 1)]
    empty, *rest = model.tokenize(prefixes + [text]) if text else [model.tokenize("")] * 2
    full = rest[-1]
    lead = 0  # special tokens around every input, e.g. <bos> ... <eos>
    while lead < len(empty) and lead < len(full) and empty[lead] == full[lead]:
        lead += 1
    trail = len(empty) - lead
    cut = lambda ids: ids[lead:len(ids) - trail]
    content = cut(full)
    pieces = [{"id": i, "special": True, "text": ""} for i in full[:lead]]
    e = 0
    ends = [cut(ids) for ids in rest[:-1]]  # ends[e] = tokens of text[:e + 1]
    start = 0
    for k, tid in enumerate(content):
        want = content[:k + 1]
        while e < len(ends) and ends[e][:k + 1] != want:
            e += 1
        end = e + 1 if e < len(ends) else len(text)
        pieces.append({"id": tid, "special": False, "text": text[start:end], "start": start})
        start = end
    if pieces and start < len(text):  # never lose text: give the rest to the last token
        pieces[-1]["text"] += text[start:]
    pieces += [{"id": i, "special": True, "text": ""} for i in full[len(full) - trail:]]
    return {"ids": full, "pieces": pieces}


def lms_models():
    """`lms ls --json`, by model key."""
    try:
        out = subprocess.run([LMS_CLI, "ls", "--json"], capture_output=True, text=True, timeout=30).stdout
        return {m["modelKey"]: m for m in json.loads(out)}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {}


def find_dir(root, rel):
    """root/rel, ignoring case: model keys and folder names can differ in case."""
    d = root
    for part in rel.split("/"):
        try:
            d = next((os.path.join(d, n) for n in os.listdir(d) if n.lower() == part.lower()), None)
        except OSError:
            return None
        if d is None:
            return None
    return d


def model_dir(key):
    """Folder with the model's safetensors files, or None (not found, or GGUF)."""
    info = lms_models().get(key)
    if not info:
        return None
    try:
        with open(os.path.expanduser("~/.lmstudio/settings.json")) as f:
            root = json.load(f).get("downloadsFolder")
    except (OSError, ValueError):
        root = None
    root = root or os.path.expanduser("~/.lmstudio/models")
    candidates = [info["path"]]
    # A Hub model ("google/gemma-3-12b") names the downloaded files in its manifest.
    try:
        with open(os.path.expanduser(f"~/.lmstudio/hub/models/{info['path']}/manifest.json")) as f:
            candidates += [k for dep in json.load(f).get("dependencies", []) for k in dep.get("modelKeys", [])]
    except (OSError, ValueError):
        pass
    for rel in candidates:
        d = find_dir(root, rel)
        if d and glob.glob(os.path.join(d, "*.safetensors")):
            return d
    return None


class EmbedTable:
    """The embedding table of a safetensors model: one row of numbers per token ID.

    Plain (BF16, F16, F32) and MLX-quantized tables: each number is q * scale + bias,
    with a scale and a bias per group of numbers.
    """

    def __init__(self, d):
        self.dir = d
        tensors = {}
        for path in glob.glob(os.path.join(d, "*.safetensors")):
            with open(path, "rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                for k, v in json.loads(f.read(n)).items():
                    if k != "__metadata__":
                        tensors[k] = (path, 8 + n, v)
        keys = [k for k in tensors if k.endswith("embed_tokens.weight")]
        if not keys:
            raise ValueError("the model files have no embed_tokens table")
        key = next((k for k in keys if "language_model" in k), keys[0])
        prefix = key[:-len("weight")]
        self.weight = tensors[key]
        self.scales, self.biases = tensors.get(prefix + "scales"), tensors.get(prefix + "biases")
        with open(os.path.join(d, "config.json")) as f:
            config = json.load(f)
        text = config.get("text_config", config)
        self.vocab, self.hidden = self.weight[2]["shape"][0], text.get("hidden_size")
        self.layers, self.model_type = text.get("num_hidden_layers"), text.get("model_type") or config.get("model_type", "")
        q = config.get("quantization") or config.get("quantization_config") or {}
        self.bits, self.group = q.get("bits"), q.get("group_size")
        if self.scales:
            if self.weight[2]["dtype"] != "U32" or not self.bits or 32 % self.bits:
                raise ValueError("this quantization format is not supported")
            self.hidden = self.weight[2]["shape"][1] * 32 // self.bits
        elif self.weight[2]["dtype"] not in ("BF16", "F16", "F32"):
            raise ValueError(f"tensor type {self.weight[2]['dtype']} is not supported")
        else:
            self.hidden = self.weight[2]["shape"][1]
        self.names = {}  # token ID -> vocabulary entry, from tokenizer.json
        try:
            with open(os.path.join(d, "tokenizer.json")) as f:
                tok = json.load(f)
            vocab = tok["model"]["vocab"]
            self.names = {i: t for t, i in vocab.items()} if isinstance(vocab, dict) else {i: t[0] for i, t in enumerate(vocab)}
            self.names.update({t["id"]: t["content"] for t in tok.get("added_tokens", [])})
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _read(self, tensor, i, size):
        path, base, meta = tensor
        with open(path, "rb") as f:
            f.seek(base + meta["data_offsets"][0] + i * size)
            return f.read(size)

    @staticmethod
    def _floats(b, dtype):
        if dtype == "F32":
            return list(struct.unpack(f"<{len(b) // 4}f", b))
        if dtype == "F16":
            return list(struct.unpack(f"<{len(b) // 2}e", b))
        return [struct.unpack("<f", b"\x00\x00" + b[k:k + 2])[0] for k in range(0, len(b), 2)]  # BF16: the top half of an F32

    def row(self, i):
        meta = self.weight[2]
        if not self.scales:
            width = {"F32": 4, "F16": 2, "BF16": 2}[meta["dtype"]]
            return self._floats(self._read(self.weight, i, self.hidden * width), meta["dtype"])
        words = meta["shape"][1]
        w = struct.unpack(f"<{words}I", self._read(self.weight, i, words * 4))
        groups = self.scales[2]["shape"][1]
        sc = self._floats(self._read(self.scales, i, groups * 2), self.scales[2]["dtype"])
        bi = self._floats(self._read(self.biases, i, groups * 2), self.biases[2]["dtype"])
        per, mask, group = 32 // self.bits, (1 << self.bits) - 1, self.hidden // groups
        return [((w[j // per] >> (self.bits * (j % per))) & mask) * sc[j // group] + bi[j // group] for j in range(self.hidden)]


tables = {}  # model key -> EmbedTable


def embed_rows(key, ids):
    with sdk_lock:
        try:
            if key not in tables:
                d = model_dir(key)
                if not d:
                    return 404, {"error": {"message": f"Embeddings: no safetensors files found for {key}. "
                                                      "The page reads MLX (safetensors) models; GGUF models are not supported yet."}}
                tables[key] = EmbedTable(d)
            t = tables[key]
            if any(not 0 <= i < t.vocab for i in ids):
                return 400, {"error": {"message": "Embeddings: a token ID is outside the table"}}
            rows = [[round(x, 5) for x in t.row(i)] for i in ids]
        except (OSError, ValueError, KeyError, struct.error) as e:
            return 502, {"error": {"message": f"Embeddings: {e}"}}
    # Gemma multiplies the embeddings by sqrt(hidden size) before the first layer.
    gemma = t.model_type.startswith("gemma")
    return 200, {"model": key, "source": os.path.relpath(t.dir, os.path.dirname(os.path.dirname(t.dir))),
                 "vocab": t.vocab, "hidden": t.hidden, "layers": t.layers, "bits": t.bits if t.scales else None,
                 "multiplier": round(math.sqrt(t.hidden), 2) if gemma else None,
                 "names": [t.names.get(i, "") for i in ids], "rows": rows}


def tokenize(key, texts, count=False):
    global sdk_client
    try:
        import lmstudio
    except ImportError:
        return 501, {"error": {"message": "Tokenizer not available: install the LM Studio SDK with: pip install lmstudio"}}
    with sdk_lock:
        for attempt in (1, 2):  # the websocket closes when LM Studio restarts; connect again one time
            try:
                if sdk_client is None:
                    sdk_client = lmstudio.Client(urllib.parse.urlparse(LMS).netloc)
                kind = lms_models().get(key, {}).get("type")
                model = (sdk_client.embedding if kind == "embedding" else sdk_client.llm).model(key)
                if count:  # only the number of tokens of each text, without the special tokens around it
                    empty = len(model.tokenize(""))
                    return 200, {"model": key, "counts": [len(ids) - empty for ids in model.tokenize(texts)]}
                return 200, {"model": key, "results": [split_tokens(model, t) for t in texts]}
            except Exception as e:
                sdk_client = None
                if attempt == 2:
                    return 502, {"error": {"message": f"Tokenizer: {e}"}}


# ---- Long-term memory of the Memory page
mem_lock = threading.Lock()
chroma_col = None  # the Chroma collection, opened at first use


def mem_files():
    """The fact files: [{"name": "user-name.md", "type": ..., "description": ..., "body": ...}], oldest first."""
    out = []
    for path in sorted(glob.glob(os.path.join(MEM_DIR, "*.md")), key=os.path.getmtime):
        name = os.path.basename(path)
        if name == "MEMORY.md":
            continue
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        meta, body = {}, text
        m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
        if m:
            body = m.group(2)
            for line in m.group(1).splitlines():
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        out.append({"name": name, "type": meta.get("type", ""), "description": meta.get("description", ""), "body": body.strip()})
    return out


def mem_write_index(files):
    """MEMORY.md: one line per file. The Memory page puts this index into the system prompt."""
    lines = [f"- [{f['name'][:-3]}]({f['name']}) — {f['description']}" for f in files]
    tmp = os.path.join(MEM_DIR, "MEMORY.md.tmp")
    with open(tmp, "w") as f:
        f.write("# Memory index\n\n" + "\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, os.path.join(MEM_DIR, "MEMORY.md"))


def mem_state():
    files = mem_files()
    try:
        with open(os.path.join(MEM_DIR, "MEMORY.md")) as f:
            index = f.read()
    except OSError:
        index = ""
    return {"dir": MEM_DIR, "files": files, "index": index}


def mem_add(facts):
    """Write one file per new fact. A fact that is already saved (same text) is skipped."""
    os.makedirs(MEM_DIR, exist_ok=True)
    files = mem_files()
    known = {f["description"].lower() for f in files}
    added = []
    for x in facts:
        fact = " ".join(x["fact"].split())
        if not fact or fact.lower() in known:
            continue
        kind = re.sub(r"[^a-z]", "", x["type"].lower()) or "user"
        slug = "-".join(re.findall(r"[a-z0-9]+", fact.lower())[:6]) or "fact"
        name, n = f"{slug}.md", 2
        while os.path.exists(os.path.join(MEM_DIR, name)):
            name, n = f"{slug}-{n}.md", n + 1
        with open(os.path.join(MEM_DIR, name), "w") as f:
            f.write(f"---\nname: {name[:-3]}\ndescription: {fact}\ntype: {kind}\n---\n\n{fact}\n\n"
                    f"Saved by the Memory page on {time.strftime('%Y-%m-%d %H:%M')}.\n")
        known.add(fact.lower())
        added.append(name)
        time.sleep(0.01)  # distinct file times keep the order
    mem_write_index(mem_files())
    return added


def chroma():
    """The Chroma collection, or None if the chromadb package is not installed."""
    global chroma_col
    if chroma_col is None:
        try:
            import chromadb
        except ImportError:
            return None
        client = chromadb.PersistentClient(path=os.path.join(MEM_DIR, "chroma"))
        # embedding_function=None: Chroma stores the vectors that the page sends (from LM Studio) and downloads no model.
        chroma_col = client.get_or_create_collection("memories", embedding_function=None, metadata={"hnsw:space": "cosine"})
    return chroma_col


NO_CHROMA = {"error": {"message": "Chroma is not installed. Install it with: uv sync --extra memory"}}


def vec_ids(model):
    """{file name: {text, dims, head}} of the vectors that this embedding model made.
    head = the first numbers of the vector, so the page can show what Chroma stores."""
    col = chroma()
    if col is None:
        return None
    r = col.get(where={"model": model}, include=["documents", "metadatas", "embeddings"])
    return {m["file"]: {"text": d, "dims": len(e), "head": [round(float(x), 4) for x in e[:8]]}
            for d, m, e in zip(r["documents"], r["metadatas"], r["embeddings"])}


def vec_add(model, items):
    col = chroma()
    if col is None:
        return 501, NO_CHROMA
    # One id per file and model: the same fact can have vectors from two embedding models.
    col.upsert(ids=[f"{model}::{x['id']}" for x in items], embeddings=[x["embedding"] for x in items],
               documents=[x["text"] for x in items], metadatas=[{"model": model, "file": x["id"], "type": x["type"]} for x in items])
    return 200, {"count": col.count()}


def vec_query(model, embedding, k, kind):
    col = chroma()
    if col is None:
        return 501, NO_CHROMA
    where = {"model": model} if not kind else {"$and": [{"model": model}, {"type": kind}]}
    r = col.query(query_embeddings=[embedding], n_results=k, where=where, include=["documents", "distances", "metadatas"])
    hits = [{"id": m["file"], "text": d, "type": m["type"], "score": round(1 - dist, 4)}
            for d, dist, m in zip(r["documents"][0], r["distances"][0], r["metadatas"][0])]
    return 200, {"hits": hits, "count": col.count(), "path": os.path.join(MEM_DIR, "chroma")}


def mem_delete(name):
    path = os.path.join(MEM_DIR, os.path.basename(name))
    if name != os.path.basename(name) or not name.endswith(".md") or name == "MEMORY.md":
        return 400, {"error": {"message": "not a memory file"}}
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    mem_write_index(mem_files())
    col = chroma()
    if col is not None:
        col.delete(where={"file": name})
    return 200, mem_state()


def mem_clear():
    global chroma_col
    for path in glob.glob(os.path.join(MEM_DIR, "*.md")):
        os.remove(path)
    if chroma() is not None:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(MEM_DIR, "chroma"))
        client.delete_collection("memories")
        chroma_col = None
    if os.path.isdir(MEM_DIR):
        mem_write_index([])
    return 200, mem_state()


# ---- Model card of the Tools page: what LM Studio says about the model, plus its Hugging Face pages.
card_cache = {}


def model_card(key):
    info = lms_models().get(key)
    if not info:
        return 404, {"error": {"message": f"LM Studio has no model {key}"}}
    out = {"key": key, "hf": None, "base": None}
    d = model_dir(key)
    repo = "/".join(d.rstrip("/").split("/")[-2:]) if d else None  # the download folder is <user>/<repo> on Hugging Face
    if repo and repo not in card_cache:
        def hf(r):
            j = get_json("https://huggingface.co/api/models/" + urllib.parse.quote(r, safe="/"))
            card = j.get("cardData") or {}
            lic = card.get("license") or next((t[8:] for t in j.get("tags", []) if t.startswith("license:")), None)
            return {"id": j.get("id"), "url": f"https://huggingface.co/{j.get('id')}", "license": lic, "downloads": j.get("downloads"),
                    "likes": j.get("likes"), "updated": (j.get("lastModified") or "")[:10], "base_model": card.get("base_model")}
        try:
            h = hf(repo)
            base = h["base_model"][0] if isinstance(h["base_model"], list) and h["base_model"] else h["base_model"]
            b = hf(base) if isinstance(base, str) else None
            card_cache[repo] = (h, b)
        except Exception as e:
            card_cache[repo] = ({"id": repo, "url": f"https://huggingface.co/{repo}", "error": str(e)}, None)
    if repo:
        out["hf"], out["base"] = card_cache[repo]
    return 200, out


# ---- Tools of the Tools page. A tool returns text: that text is what the model gets back.
UA = "LM-Trace/0.1 (local learning tool)"
WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle",
       55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
       73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
       85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def public_url(url):
    """Only http(s) to public addresses: a URL tool must not reach this computer or the local network."""
    import ipaddress, socket
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))}
    except OSError:
        return False
    return all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def fetch(url, limit=200000):
    """GET a public URL. Returns (status, text). A redirect is checked again, so it cannot lead to a local address."""
    class NoLocalRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not public_url(newurl):
                raise urllib.error.URLError("redirect to a local address")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    if not public_url(url):
        raise ValueError("only public http(s) addresses are allowed")
    opener = urllib.request.build_opener(NoLocalRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    try:
        with opener.open(req, timeout=12) as r:
            return r.status, r.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")


def get_json(url):
    status, text = fetch(url)
    if status != 200:
        raise ValueError(f"HTTP {status}: {text[:200]}")
    return json.loads(text)


def num(v, name):
    """The model writes text; the book's multiply turns it into numbers with float()."""
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {json.dumps(v)}")


def t_multiply(a=None, b=None, **_):
    return str(num(a, "a") * num(b, "b"))  # Python's float result, as in the book: 3.1 * 6.5 = 20.150000000000002


def t_get_weather(city=None, **_):
    if not isinstance(city, str) or not city.strip():
        raise ValueError("city is required")
    g = get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(city.split(",")[0].strip()))
    if not g.get("results"):
        return json.dumps({"error": f"no place named {city}"})
    p = g["results"][0]
    w = get_json(f"https://api.open-meteo.com/v1/forecast?latitude={p['latitude']}&longitude={p['longitude']}"
                 "&current=temperature_2m,wind_speed_10m,weather_code&timezone=auto")["current"]
    return json.dumps({"place": ", ".join(x for x in (p.get("name"), p.get("admin1"), p.get("country")) if x),
                       "time": w["time"], "temperature": f"{w['temperature_2m']} °C", "wind": f"{w['wind_speed_10m']} km/h",
                       "conditions": WMO.get(w["weather_code"], f"code {w['weather_code']}")}, ensure_ascii=False)


def t_top_tech_news(count=3, **_):
    n = max(1, min(10, int(num(count, "count"))))
    ids = get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:n]
    items = [None] * n
    def one(i):
        items[i] = get_json(f"https://hacker-news.firebaseio.com/v0/item/{ids[i]}.json")
    threads = [threading.Thread(target=one, args=(i,)) for i in range(len(ids))]
    [t.start() for t in threads]
    [t.join(15) for t in threads]
    return json.dumps([{"title": x.get("title"), "points": x.get("score"), "url": x.get("url", "")} for x in items if x], ensure_ascii=False)


def lms_embed(model, texts):
    """Vectors from the embedding model in LM Studio, in the order of the texts."""
    body = json.dumps({"model": model, "input": texts}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(LMS + "/v1/embeddings", data=body,
                                                                 headers={"Content-Type": "application/json"}), timeout=60).read())
    return [x["embedding"] for x in sorted(r["data"], key=lambda x: x["index"])]


def t_search_memory(query=None, k=3, **_):
    """Retrieval as a tool: the saved facts of the Memory page nearest to the query.
    With Chroma: each fact is embedded once and its vector is stored; a search embeds only the query, and Chroma
    finds the nearest vectors. Without Chroma: each search embeds the query and all the facts (cosine, as the book)."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    files = [f for f in mem_files() if f["description"]]
    if not files:
        return "No facts are saved. Save facts on the Memory page first."
    models = json.loads(urllib.request.urlopen(LMS + "/v1/models", timeout=10).read())["data"]
    emb = [m["id"] for m in models if "embed" in m["id"].lower()]
    model = next((m for m in emb if "embeddinggemma" in m.lower()), emb[0] if emb else None)
    if not model:
        raise ValueError("LM Studio has no embedding model")
    q, d = embed_prefixes(model)  # from Settings, as the Memory page, so the vectors in Chroma match
    n = max(1, min(10, int(num(k, "k"))))
    stored = vec_ids(model)
    if stored is not None:
        # Facts without a vector (or with a changed text) get one now, once. Then only the query is embedded.
        miss = [f for f in files if stored.get(f["name"], {}).get("text") != f["description"]]
        if miss:
            vs = lms_embed(model, [d + f["description"] for f in miss])
            vec_add(model, [{"id": f["name"], "text": f["description"], "type": f.get("type") or "user", "embedding": v}
                            for f, v in zip(miss, vs)])
        _, r = vec_query(model, lms_embed(model, [q + query])[0], n, None)
        return "\n".join(h["text"] for h in r["hits"])
    vs = lms_embed(model, [q + query] + [d + f["description"] for f in files])
    cos = lambda a, b: sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a) * sum(y * y for y in b)) or 1)
    ranked = sorted(((cos(vs[0], v), f["description"]) for v, f in zip(vs[1:], files)), reverse=True)
    return "\n".join(text for _, text in ranked[:n])


BUILTIN = {"multiply": t_multiply, "get_weather": t_get_weather, "top_tech_news": t_top_tech_news, "search_memory": t_search_memory}


def tool_run(tool, args):
    """Run one tool call. Returns {"ok", "result", "request"}: request is what the server did, for the page to show."""
    t0, request = time.time(), ""
    try:
        if tool.get("kind") == "builtin":
            fn = BUILTIN.get(tool.get("builtin") or tool["name"])
            if not fn:
                raise ValueError(f"no built-in tool {tool.get('builtin')}")
            request = f"Python: {tool.get('builtin') or tool['name']}({', '.join(f'{k}={json.dumps(v)}' for k, v in args.items())})"
            if fn is t_search_memory:
                request += ("\nEmbeds the query only, then searches the stored fact vectors in Chroma." if chroma() is not None
                            else "\nEmbeds the query and every fact, then ranks the facts by cosine similarity (no Chroma).")
            result = fn(**args)
        elif tool.get("kind") == "url":
            tpl = tool.get("url")
            if not isinstance(tpl, str) or len(tpl) > 2000:
                raise ValueError("the tool has no URL")
            url = re.sub(r"\{(\w+)\}", lambda m: urllib.parse.quote(str(args.get(m.group(1), "")), safe=""), tpl)
            request = f"GET {url}"
            status, text = fetch(url)
            if status != 200:
                raise ValueError(f"HTTP {status}: {text[:300]}")
            result = text
        else:
            raise ValueError("unknown tool kind")
    except Exception as e:  # the error text goes back to the model, as LM Studio's SDK does
        return {"ok": False, "result": f"Error: {e}", "request": request, "ms": int((time.time() - t0) * 1000)}
    full = len(result)
    if full > 4000:
        result = result[:4000] + f"\n… (cut: {full} characters in total)"
    return {"ok": True, "result": result, "request": request, "ms": int((time.time() - t0) * 1000)}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # always serve the latest page
        super().end_headers()

    def _proxy(self, method):
        body = None
        if method == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        headers = {"Content-Type": "application/json"}
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
        req = urllib.request.Request(LMS + self.path, data=body, headers=headers, method=method)
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            status, data = e.code, e.read()
        except Exception as e:
            status, data = 502, ('{"error":{"message":"%s"}}' % str(e).replace('"', "'")).encode()
        else:
            # Stream the upstream body through as it arrives (SSE for stream=true).
            with r:
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while chunk := r.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client pressed Stop
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _blocked(self, write=False):
        """Send 403 and return True unless the request comes from the LM Trace page on this computer."""
        ok = self.headers.get("Host", "").lower() in HOSTS
        if ok and write:
            origin = self.headers.get("Origin")
            ok = (origin is None or origin in ORIGINS) and \
                self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"
        if not ok:
            self._json(403, {"error": {"message": "forbidden: LM Trace accepts requests from localhost only"}})
        return not ok

    def do_GET(self):
        if self._blocked():
            return
        if self.path in LISTS:
            try:
                with open(LISTS[self.path]) as f:
                    return self._json(200, json.load(f))
            except FileNotFoundError:
                return self._json(200, LIST_START.get(self.path, []))
        if self.path.startswith(("/model-input", "/model-output")):
            # Newest formatted input (or raw output) received at or after `since` (ms epoch), if any.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            since = int(q.get("since", ["0"])[0])
            log = outputs if self.path.startswith("/model-output") else inputs
            first = q.get("first") == ["1"]  # the oldest at or after `since`: the Tools page makes several calls in a row
            with inputs_lock:
                hit = next((x for x in (log if first else reversed(log)) if x["t"] >= since), None)
            return self._json(200, {"available": log_ok, "entry": hit})
        if self.path.startswith("/model-card?"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._json(*model_card(q.get("key", [""])[0]))
        if self.path.startswith("/agent-memory?") or self.path == "/agent-memory":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            with mem_lock:
                st = mem_state()
                try:
                    st["vectors"] = vec_ids(q.get("model", [""])[0])  # None: Chroma not installed
                except Exception as e:
                    st["vectors"], st["vectorError"] = None, str(e)
            return self._json(200, st)
        if self.path.startswith(("/v1/", "/api/v1/models")):  # api/v1/models lists reasoning options
            return self._proxy("GET")
        path = urllib.parse.urlparse(self.path).path
        if path in ("/",) + PAGES:
            self.path = path if path in PAGES else "/index.html"
            return super().do_GET()
        self.send_error(404)  # never serve .git, saved lists, logs or source files

    def do_PUT(self):
        if self._blocked(write=True):
            return
        # Saved lists: the page sends the whole list as [{"name": ..., <data>}, ...].
        if self.path not in LISTS:
            return self.send_error(404)
        try:
            items = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if not isinstance(items, list) or not all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in items):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "expected a list of objects with a name"})
        path = LISTS[self.path]
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)  # atomic: never a half-written file
        return self._json(200, items)

    def do_POST(self):
        if self._blocked(write=True):
            return
        if self.path.startswith("/v1/"):
            return self._proxy("POST")
        if self.path == "/tokenize":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                key, texts = req["model"], req["texts"]
                if not isinstance(key, str) or not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": {"message": "expected {\"model\": str, \"texts\": [str, ...]}"}})
            count = req.get("count") is True
            if count and (len(texts) > 300 or sum(map(len, texts)) > 60000):
                return self._json(400, {"error": {"message": "Token count: at most 300 texts of 60,000 characters in total"}})
            if not count and (len(texts) > 64 or any(len(t) > 4000 for t in texts)):
                return self._json(400, {"error": {"message": "Tokenizer: at most 64 texts of 4,000 characters each"}})
            return self._json(*tokenize(key, texts, count))
        if self.path == "/embed-rows":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                key, ids = req["model"], req["ids"]
                if not isinstance(key, str) or not isinstance(ids, list) or not all(type(i) is int for i in ids):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": {"message": "expected {\"model\": str, \"ids\": [int, ...]}"}})
            if len(ids) > 512:
                return self._json(400, {"error": {"message": "Embeddings: at most 512 tokens"}})
            return self._json(*embed_rows(key, ids))
        if self.path.startswith("/agent-memory/"):
            return self._memory(self.path[len("/agent-memory/"):])
        if self.path == "/tool-run":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                tool, args = req["tool"], req.get("args") or {}
                if not isinstance(tool, dict) or not isinstance(args, dict) or not isinstance(tool.get("name"), str):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": {"message": "expected {\"tool\": {...}, \"args\": {...}}"}})
            return self._json(200, tool_run(tool, args))
        self.send_error(404)

    def _memory(self, action):
        """Long-term memory: add, delete, clear (files and vectors); vectors/add and vectors/query (Chroma)."""
        bad = lambda: self._json(400, {"error": {"message": f"Memory: bad request for {action}"}})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return bad()
        isstr = lambda v, n: isinstance(v, str) and 0 < len(v) <= n
        vec = lambda v: isinstance(v, list) and 0 < len(v) <= 8192 and all(type(x) in (int, float) for x in v)
        with mem_lock:
            try:
                if action == "add":
                    facts = req.get("facts")
                    if not isinstance(facts, list) or len(facts) > 50 or \
                            not all(isinstance(x, dict) and isstr(x.get("fact"), 500) and isstr(x.get("type"), 40) for x in facts):
                        return bad()
                    if len(mem_files()) + len(facts) > 300:
                        return self._json(400, {"error": {"message": "Memory: at most 300 facts. Clear the memory first."}})
                    added = mem_add(facts)
                    return self._json(200, {**mem_state(), "added": added})
                if action == "delete":
                    return self._json(*mem_delete(req.get("name", ""))) if isstr(req.get("name"), 200) else bad()
                if action == "clear":
                    return self._json(*mem_clear())
                if action == "vectors/add":
                    items = req.get("items")
                    if not isstr(req.get("model"), 200) or not isinstance(items, list) or not 0 < len(items) <= 300 or \
                            not all(isinstance(x, dict) and isstr(x.get("id"), 200) and isstr(x.get("text"), 500)
                                    and isstr(x.get("type"), 40) and vec(x.get("embedding")) for x in items):
                        return bad()
                    return self._json(*vec_add(req["model"], items))
                if action == "vectors/query":
                    k, kind = req.get("k", 3), req.get("type") or ""
                    if not isstr(req.get("model"), 200) or not vec(req.get("embedding")) or type(k) is not int or not 1 <= k <= 20 \
                            or not isinstance(kind, str):
                        return bad()
                    return self._json(*vec_query(req["model"], req["embedding"], k, kind))
            except Exception as e:  # Chroma errors, for example vectors of a different length
                return self._json(502, {"error": {"message": f"Memory: {e}"}})
        self.send_error(404)


def stop_log():
    if log_proc and log_proc.poll() is None:
        log_proc.terminate()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)  # bind first: fail before spawning lms
    atexit.register(stop_log)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    threading.Thread(target=tail_model_log, daemon=True).start()
    print(f"LM Trace: http://localhost:{PORT}  (proxying {LMS})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
