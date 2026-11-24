// The Status control in the header of each page: is the LM Studio server running? A click starts or stops it.
// GET /lms-status every 5 s; POST /lms-server {"on": true|false} runs `lms server start` or `lms server stop`.
(() => {
  const theme = document.getElementById("theme");
  if (!theme) return;
  const css = document.createElement("style");
  css.textContent = `
  .lms-st { gap: 7px; margin-right: 4px; }  /* the button look: .hdr-btn in theme.css */
  .lms-st:disabled { cursor: default; opacity: .7; }
  .lms-st .dot { width: 8px; height: 8px; border-radius: 50%; background: #a1a1aa; }
  .lms-st.on .dot { background: #22a559; }`;
  document.head.appendChild(css);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "hdr-btn lms-st";
  btn.innerHTML = '<span class="dot" aria-hidden="true"></span><span class="txt">Status: …</span>';
  theme.before(btn);
  const txt = btn.querySelector(".txt");

  let running = null, busy = false;
  function show(r) {
    running = r;
    btn.classList.toggle("on", r === true);
    txt.textContent = r === null ? "Status: …" : r ? "Status: Running" : "Status: Stopped";
    btn.title = r ? "The LM Studio server runs. Click to stop it." : "The LM Studio server does not run. Click to start it.";
  }
  async function check() {
    if (busy) return;
    try { show((await (await fetch("/lms-status")).json()).running === true); } catch { show(false); }
  }
  btn.addEventListener("click", async () => {
    if (busy || running === null) return;
    const on = !running;
    busy = true; btn.disabled = true; txt.textContent = on ? "Starting…" : "Stopping…";
    try {
      const r = await fetch("/lms-server", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) });
      const j = await r.json();
      show(j.running === true);
      if (j.error) btn.title = j.error.message;
      // The page loaded while LM Studio was stopped, so it has no model list: load the page again.
      if (on && j.running && [...document.querySelectorAll("select#model, select#chatModel")].some(s => !s.value)) location.reload();
    } catch (e) { btn.title = String(e.message || e); }
    busy = false; btn.disabled = false;
  });
  check();
  setInterval(check, 5000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
})();
