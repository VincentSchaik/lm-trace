// The Status control in the header of each page: is the LM Studio server running? A switch; a click starts or stops it.
// GET /lms-status every 5 s; POST /lms-server {"on": true|false} runs `lms server start` or `lms server stop`.
(() => {
  const theme = document.getElementById("theme");
  if (!theme) return;
  const css = document.createElement("style");
  css.textContent = `
  .lms-st { gap: 10px; margin-right: 4px; }  /* the button look: .hdr-btn in theme.css */
  .lms-st:disabled { cursor: default; opacity: .7; }
  /* All labels in one grid cell, only one visible: the control keeps the width of the longest label. */
  .lms-st .txt { display: inline-grid; text-align: left; }
  .lms-st .txt > span { grid-area: 1 / 1; visibility: hidden; }
  .lms-st .txt > span.show { visibility: visible; }
  /* The switch: grey and left when stopped, green and right when running. */
  .lms-st .lms-sw { position: relative; width: 28px; height: 16px; border-radius: 8px; background: var(--stone-2); transition: background .15s; flex: none; }
  .lms-st .lms-sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: #fff;
    box-shadow: 0 1px 2px rgba(0, 0, 0, .2); transition: translate .15s; }
  .lms-st.on .lms-sw { background: #22a559; }
  .lms-st.on .lms-sw::after { translate: 12px 0; }`;
  document.head.appendChild(css);

  const LABELS = ["Status: …", "Status: Running", "Status: Stopped", "Starting…", "Stopping…"];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "hdr-btn lms-st";
  btn.setAttribute("role", "switch");
  btn.innerHTML = '<span class="txt">' + LABELS.map(l => `<span>${l}</span>`).join("") + '</span><span class="lms-sw" aria-hidden="true"></span>';
  theme.before(btn);
  const label = l => btn.querySelectorAll(".txt > span").forEach(sp => sp.classList.toggle("show", sp.textContent === l));

  let running = null, busy = false;
  function show(r) {
    running = r;
    btn.classList.toggle("on", r === true);
    btn.setAttribute("aria-checked", r === true);
    label(r === null ? "Status: …" : r ? "Status: Running" : "Status: Stopped");
    btn.title = r ? "The LM Studio server runs. Click to stop it." : "The LM Studio server does not run. Click to start it.";
  }
  async function check() {
    if (busy) return;
    try { show((await (await fetch("/lms-status")).json()).running === true); } catch { show(false); }
  }
  btn.addEventListener("click", async () => {
    if (busy || running === null) return;
    const on = !running;
    busy = true; btn.disabled = true; label(on ? "Starting…" : "Stopping…");
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
