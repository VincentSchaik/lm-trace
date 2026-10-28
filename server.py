"""Serve LM Trace and proxy /v1/* to the LM Studio server (avoids CORS).

Also tails LM Studio's model log (`lms log stream`) so the page can show the exact
formatted prompt (chat template + special tokens) the model received.
"""
import atexit, http.server, json, os, shutil, signal, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error

LMS = os.environ.get("LMS_URL", "http://localhost:1234")
TOKEN = os.environ.get("LM_API_TOKEN")
PORT = int(os.environ.get("PORT", "8765"))
ROOT = os.path.dirname(os.path.abspath(__file__))
# Saved lists: user prompts and system prompts, one JSON file each.
LISTS = {k: os.path.join(ROOT, k.strip("/") + ".json") for k in ("/prompts", "/system-prompts", "/few-shot", "/schemas", "/presets")}
LMS_CLI = shutil.which("lms") or os.path.expanduser("~/.lmstudio/bin/lms")

inputs = []  # [{"t": ms received, "input": str, "model": str}], newest last
inputs_lock = threading.Lock()
log_ok = False
log_proc = None


def tail_model_log():
    """Keep `lms log stream` running and record every formatted model input."""
    global log_ok, log_proc
    while True:
        try:
            proc = log_proc = subprocess.Popen([LMS_CLI, "log", "stream", "-s", "model", "--filter", "input", "--json"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            log_ok = True
            for line in proc.stdout:
                try:
                    d = json.loads(line).get("data", {})
                except ValueError:
                    continue
                if d.get("type") == "llm.prediction.input":
                    with inputs_lock:
                        inputs.append({"t": int(time.time() * 1000), "input": d.get("input", ""),
                                       "model": d.get("modelIdentifier", "")})
                        del inputs[:-20]
        except OSError:
            pass
        log_ok = False
        time.sleep(3)  # lms missing or LM Studio restarted; retry


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

    def do_GET(self):
        if self.path in LISTS:
            try:
                with open(LISTS[self.path]) as f:
                    return self._json(200, json.load(f))
            except FileNotFoundError:
                return self._json(200, [])
        if self.path.startswith("/model-input"):
            # Newest formatted input received at or after `since` (ms epoch), if any.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            since = int(q.get("since", ["0"])[0])
            with inputs_lock:
                hit = next((x for x in reversed(inputs) if x["t"] >= since), None)
            return self._json(200, {"available": log_ok, "entry": hit})
        if self.path.startswith(("/v1/", "/api/v1/models")):  # api/v1/models lists reasoning options
            return self._proxy("GET")
        return super().do_GET()

    def do_PUT(self):
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
        if self.path.startswith("/v1/"):
            return self._proxy("POST")
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
