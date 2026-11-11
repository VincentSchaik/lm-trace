// The message box of LM Trace, shared by all pages: the model picker (Currently loaded with Eject, Your models
// with capability icons, Reasoning), the model hover card, and the round Send / Stop button.
// A picker works on a hidden <select> that the page owns: the page fills the options and reads .value;
// the picker sets .value and sends a "change" event when you choose a model.
(() => {
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const svg = (d, cls = "", w = 1.8) => `<svg${cls ? ` class="${cls}"` : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    chip: svg('<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>'),
    vec: svg('<path d="M4 20L20 4"/><path d="M13 4h7v7"/><circle cx="4" cy="20" r="1.5"/>'),
    updown: svg('<path d="M7 9l5-5 5 5M7 15l5 5 5-5"/>', "updown", 2),
    eject: svg('<path d="M12 5l7 8H5l7-8z"/><path d="M5 18h14"/>'),
    send: svg('<path d="M12 19V5M5 12l7-7 7 7"/>', "", 2.2),
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>',
  };
  // Capability icons, as LM Studio shows them: from the capabilities that LM Studio reports for each model.
  const CAPS = [
    ["tool", c => c.trained_for_tool_use, "Tool use", "The model learned tool calling in training. LM Studio puts the tools into its chat template, with its own tool tokens. A model without it gets the tools as a text note.",
      '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.1L3 17.7 6.3 21l6.3-6.3a4 4 0 0 0 5.1-5.4l-2.6 2.6-2.4-.5-.5-2.4z"/>'],
    ["eye", c => c.vision, "Vision", "The model can read images.", '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'],
    ["brain", c => c.reasoning, "Reasoning", "The model can think before it answers.", '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>'],
  ];
  const capIcons = m => CAPS.filter(([, has]) => has(m?.capabilities || {})).map(([cls, , title, tip, d]) =>
    `<span class="cap ${cls}" tabindex="-1" data-tip-title="${title}" data-tip="${esc(tip)}">${svg(d)}<span class="sr">${title}</span></span>`).join("");

  // ---- What LM Studio says about the models (GET /api/v1/models). One list for all pickers on the page.
  let models = [];
  const info = k => models.find(m => m.key === k);
  const isLoaded = k => !!info(k)?.loaded_instances?.length;
  async function load() {
    try { const r = await fetch("/api/v1/models"); if (r.ok) models = (await r.json()).models || []; } catch {}
    return models;
  }

  // ---- Reasoning, per model: LM Studio's default until you choose. Saved in this browser, for all pages.
  let reasonSet = {};
  try { reasonSet = JSON.parse(localStorage.getItem("lmtReasoning") || localStorage.getItem("toolsReasoning") || "{}"); } catch {}
  const reasonOf = k => { const r = info(k)?.capabilities?.reasoning; if (!r) return null; return r.allowed_options.includes(reasonSet[k]) ? reasonSet[k] : r.default; };
  // The request fields for the Reasoning setting. Tested: reasoning_effort "none" turns the thinking off (Qwen3, Gemma 4).
  const reasoningFields = k => reasonOf(k) === "off" ? { reasoning_effort: "none" } : {};
  // Set the Reasoning of a model from page code (a preset, Reset all). null: back to LM Studio's default.
  function setReason(k, v) {
    if (v == null) delete reasonSet[k]; else reasonSet[k] = v;
    try { localStorage.setItem("lmtReasoning", JSON.stringify(reasonSet)); } catch {}
    renderAll();
  }

  // ---- The model card: what LM Studio says about the model, and its pages on Hugging Face (fetched by the server).
  const cards = new Map();
  const fmtBytes = n => n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : Math.round(n / 1e6) + " MB";
  const fmtNum = n => n >= 1e6 ? (n / 1e6).toFixed(1) + " M" : n >= 1e3 ? Math.round(n / 1e3) + " k" : String(n);
  function cardHtml(key) {
    const m = info(key);
    if (!m) return "";
    const cap = m.capabilities || {}, tool = cap.trained_for_tool_use, ctx = m.loaded_instances?.[0]?.config?.context_length, llm = m.type !== "embedding";
    const spec = (k, v, tip) => v == null || v === "" ? "" : `<div class="spec"${tip ? ` tabindex="0" data-tip-title="${esc(k)}" data-tip="${esc(tip)}"` : ""}><span>${esc(k)}</span><b>${v}</b></div>`;
    const hf = cards.get(key);
    // A Hugging Face card can name a wrong base model; show it only when it is of the same family as the model.
    const family = (m.architecture || "").replace(/[\d_].*$/, "").toLowerCase();
    const base = hf?.base && family && hf.base.id.toLowerCase().includes(family) ? hf.base : null;
    const link = (h, label) => `<a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer">${esc(label || h.id)}</a>`;
    return `<div class="mcard-hd"><b>${esc(m.display_name || key)}</b><span class="pub">${esc(m.publisher || "")}</span>
        <span class="src">${hf && !hf.error ? "LM Studio · Hugging Face" : "LM Studio"}</span></div>
      <div class="mcard-bd">
        ${spec("Type", llm ? "Language model" : "Embedding model")}
        ${spec("Parameters", esc(m.params_string || ""))}
        ${spec("Architecture", esc(m.architecture || ""))}
        ${spec("Quantization", esc(m.quantization?.name || ""), "How many bits each weight of the model uses. Fewer bits: a smaller file, and a little less quality.")}
        ${spec("Format", esc((m.format || "").toUpperCase()))}
        ${spec("Size", m.size_bytes ? fmtBytes(m.size_bytes) : "")}
        ${spec("Context", m.max_context_length ? (ctx ? `${ctx.toLocaleString()} of ${m.max_context_length.toLocaleString()}` : `${m.max_context_length.toLocaleString()} max`) : "", "How many tokens fit in one request: the loaded setting, and the maximum of the model.")}
        ${llm ? spec("Tool use", tool ? "Native" : tool === false ? "Text note" : "Unknown") : ""}
        ${llm ? spec("Reasoning", cap.reasoning ? "Yes" : "No") : ""}
        ${llm ? spec("Vision", cap.vision ? "Yes" : "No") : ""}
        ${!cards.has(key) ? `<div class="spec"><span>Hugging Face</span><b class="ph">loading…</b></div>` : ""}
        ${hf && !hf.error ? spec("License", esc(hf.license || base?.license || "")) : ""}
        ${base ? spec("Base model", link(base), "The original model. The file in LM Studio is a converted copy of it.") : ""}
        ${base?.downloads ? spec("Downloads", fmtNum(base.downloads), "Downloads of the base model on Hugging Face in the last 30 days.") : ""}
        ${hf ? spec("Files", link(hf, hf.id.split("/")[0]), "The Hugging Face repository of the file that LM Studio downloaded.") : ""}
      </div>`;
  }
  async function fetchCard(key) {
    if (cards.has(key)) return;
    cards.set(key, null);
    try { const j = await fetch(`/model-card?key=${encodeURIComponent(key)}`).then(r => r.json()); cards.set(key, j.hf ? { ...j.hf, base: j.base } : null); }
    catch { cards.set(key, null); }
    pickers.forEach(p => p.select.value === key && p.renderCard());
  }

  // ---- A picker.
  // opts: select (the hidden <select>), mount (an empty element: the picker goes in it), label ("Chat model"),
  //       kind ("llm" | "embedding"), reasoning (show the Reasoning row), isBusy (() => bool), onError (msg => …),
  //       align ("right" | "left": the side the menu opens on), loadWord (the word of the page's run button),
  //       embed (optional: the hidden <select> of an embedding model; the menu then has an "Embedding model" section).
  const pickers = [];
  function picker(opts) {
    const { select, mount, label = "Model", kind = "llm", reasoning = false, isBusy = () => false, onError = () => {}, align = "right", loadWord = "Send", embed = null } = opts;
    const id = "mp" + pickers.length;
    mount.classList.add("mp-wrap"); if (align === "left") mount.classList.add("left");
    mount.innerHTML = `<button type="button" class="mp-btn" id="${id}Btn" aria-haspopup="menu" aria-expanded="false" aria-controls="${id}Menu" aria-label="${esc(label)}"></button>
      <div class="perm-menu mp-menu" id="${id}Menu" role="menu" aria-label="${esc(label)}" hidden></div>
      <div class="mcard pop" role="dialog" aria-label="Model card" hidden></div>`;
    const btn = mount.querySelector(".mp-btn"), menu = mount.querySelector(".mp-menu"), card = mount.querySelector(".mcard");
    const keys = () => [...select.options].map(o => o.value);
    const icon = kind === "embedding" ? ICON.vec : ICON.chip;
    const p = { select, btn, menu, card, kind };

    p.render = () => {
      const k = select.value, m = info(k), r = reasoning ? m?.capabilities?.reasoning : null;
      btn.innerHTML = k ? `${icon}<span class="nm">${esc(k)}</span>${isLoaded(k) ? '<span class="ld">loaded</span>' : ""}${r ? `<span class="ld">Reasoning ${esc(reasonOf(k))}</span>` : ""}${ICON.updown}`
        : `<span class="nm">No ${esc(label.toLowerCase())}</span>${ICON.updown}`;
      // Reasoning: always in the menu for a chat model, so it does not come and go as you pick a model.
      // A model with one setting (or none) shows it greyed out: Off for a model without reasoning.
      const seg = (opts, cur, dis) => `<span class="seg${dis ? " dis" : ""}" role="group" aria-label="Reasoning">${opts.map(o => `<button type="button"${dis ? " disabled" : ` data-reason="${esc(o)}"`} aria-pressed="${cur === o}">${o === "on" ? "On" : o === "off" ? "Off" : esc(o)}</button>`).join("")}</span>`;
      const rTip = (tip) => `<span tabindex="0" data-tip-title="Reasoning" data-tip="${tip}">Reasoning</span>`;
      const reasonRow = !reasoning || kind !== "llm" || !k ? ""
        : !r ? `<div class="frow">${rTip("This model does not think before it answers. LM Studio has no reasoning setting for it, so Reasoning is Off.")}${seg(["off", "on"], "off", true)}</div>`
        : r.allowed_options.length > 1 ? `<div class="frow">${rTip("The model thinks before it answers. LM Studio says which settings this model allows. Off sends reasoning_effort: none in the request.")}${seg(r.allowed_options, reasonOf(k), false)}</div>`
        : `<div class="frow">${rTip("This model always thinks before it answers. LM Studio allows no other setting.")}${seg(r.allowed_options[0] === "on" ? ["off", "on"] : r.allowed_options, r.allowed_options[0], true)}</div>`;
      // As LM Studio: the loaded models first, each with Eject; then the models that are not loaded.
      // A row: a chat model (data-model) or an embedding model (data-emb). Each list has its own selection.
      const all = keys(), eAll = embed ? [...embed.options].map(o => o.value) : [], ek = embed?.value;
      const isEmb = x => eAll.includes(x) && !all.includes(x);
      const row = x => { const e = isEmb(x), on = e ? x === ek : x === k;
        return `<button type="button" role="menuitemradio" aria-checked="${on}" ${e ? "data-emb" : "data-model"}="${esc(x)}">${e ? ICON.vec : icon}<span class="nmc">${esc(x)}</span><span></span><span class="caps">${capIcons(info(x))}</span></button>`; };
      const lrow = x => `<div class="lrow${(isEmb(x) ? x === ek : x === k) ? " sel" : ""}">${row(x)}<button type="button" class="eject" data-eject="${esc(info(x).loaded_instances[0].id)}" title="Unload this model from memory. LM Studio loads it again at the next ${esc(loadWord)}."${isBusy() ? " disabled" : ""}>${ICON.eject}<span>Eject</span></button></div>`;
      const loadedNow = [...all, ...eAll.filter(isEmb)].filter(isLoaded);
      const rest = all.filter(x => !isLoaded(x)), eRest = eAll.filter(x => !isLoaded(x));
      menu.innerHTML = `<div class="sec">Currently loaded <span class="n">(${loadedNow.length})</span></div>`
        + (loadedNow.length ? loadedNow.map(lrow).join("") : `<div class="none-l">No model is loaded. LM Studio loads the model at the next ${esc(loadWord)}.</div>`)
        // A loaded model shows once: under Currently loaded. It comes back to its list when you eject it.
        + (rest.length ? `<div class="sec">Your models</div><div class="list">` + rest.map(row).join("") + `</div>` : "")
        + (embed && eRest.length ? `<div class="sec"><span tabindex="0" data-tip-title="Embedding model" data-tip="A second, small model. It turns text into vectors (lists of numbers), so the search can find the text with the nearest meaning. It writes no text.">Embedding model</span></div><div class="list emb">` + eRest.map(row).join("") + `</div>` : "")
        + (reasonRow ? `<div class="foot">${reasonRow}</div>` : "");
    };
    p.renderCard = () => { card.innerHTML = cardHtml(select.value); };
    // A menu opens above the box; with too little room above (a box near the top of the page), below it.
    // On a narrow screen, a menu wider than the room at the side of the button moves into the screen.
    const place = el => {
      el.classList.remove("down"); el.style.transform = "";
      const r = btn.getBoundingClientRect(), h = el.offsetHeight + 8;
      if (r.top < h && innerHeight - r.bottom > r.top) el.classList.add("down");
      const m = el.getBoundingClientRect(), vw = document.documentElement.clientWidth, pad = 8;
      const dx = m.left < pad ? pad - m.left : m.right > vw - pad ? vw - pad - m.right : 0;
      if (dx) el.style.transform = `translateX(${Math.round(dx)}px)`;
    };
    p.place = place;
    const open = on => {
      menu.hidden = !on; btn.setAttribute("aria-expanded", on);
      if (on) { showCard(false); place(menu); menu.querySelector('[aria-checked="true"]')?.focus(); }
    };
    p.open = open;
    // Opening the menu reads the models again: LM Studio loads a model at a run, and you can eject one in LM Studio too.
    btn.onclick = () => {
      const on = menu.hidden; open(on);
      if (on) load().then(() => { if (!menu.hidden) { renderAll(); place(menu); menu.querySelector('[aria-checked="true"]')?.focus(); } });
    };
    menu.onclick = async e => {
      const ej = e.target.closest("[data-eject]");
      if (ej) {
        ej.disabled = true; ej.querySelector("span").textContent = "Ejecting…";
        try {
          const r = await fetch("/api/v1/models/unload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instance_id: ej.dataset.eject }) });
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error?.message || `HTTP ${r.status}`);
        } catch (err) { onError(`Eject failed: ${err.message}`); }
        await load(); renderAll(); menu.querySelector("button")?.focus();
        return;
      }
      const rb = e.target.closest("[data-reason]");
      if (rb) {
        reasonSet[select.value] = rb.dataset.reason;
        try { localStorage.setItem("lmtReasoning", JSON.stringify(reasonSet)); } catch {}
        p.render(); menu.querySelector(`[data-reason="${rb.dataset.reason}"]`)?.focus();
        return;
      }
      const eb = e.target.closest("[data-emb]");
      // Picking a model keeps the menu open, so you can set its Reasoning at once. Click outside or Esc closes it.
      if (eb && embed) { embed.value = eb.dataset.emb; embed.dispatchEvent(new Event("change", { bubbles: true })); place(menu); menu.querySelector(`[data-emb="${CSS.escape(eb.dataset.emb)}"]`)?.focus(); return; }
      const b = e.target.closest("[data-model]"); if (!b) return;
      select.value = b.dataset.model; select.dispatchEvent(new Event("change", { bubbles: true }));
      place(menu); menu.querySelector(`[data-model="${CSS.escape(b.dataset.model)}"]`)?.focus();
    };
    menu.addEventListener("keydown", e => {
      const items = [...menu.querySelectorAll("button")], i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { open(false); btn.focus(); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus(); }
    });
    // The model card: a hover card on the model button. It stays open while the pointer is on it, so its links work.
    let timer = null;
    const showCard = on => { clearTimeout(timer); card.hidden = !on; };
    const cardSoon = on => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (on && (!menu.hidden || !select.value || btn.disabled)) return;
        if (on) { p.renderCard(); fetchCard(select.value); }
        card.hidden = !on;
        if (on) place(card);
      }, on ? 350 : 200);
    };
    p.showCard = showCard;
    [btn, card].forEach(el => { el.addEventListener("mouseenter", () => cardSoon(true)); el.addEventListener("mouseleave", () => cardSoon(false)); });
    btn.addEventListener("focus", () => { if (menu.hidden) cardSoon(true); });
    btn.addEventListener("blur", e => { if (!card.contains(e.relatedTarget)) cardSoon(false); });
    // The page fills the options and sets the value; draw the button again then.
    select.addEventListener("change", () => { p.render(); if (!card.hidden) p.renderCard(); });
    new MutationObserver(() => p.render()).observe(select, { childList: true });
    if (embed) { embed.addEventListener("change", () => p.render()); new MutationObserver(() => p.render()).observe(embed, { childList: true }); }
    p.setDisabled = d => { btn.disabled = d; if (d) { open(false); showCard(false); } p.render(); };
    pickers.push(p);
    p.render();
    return p;
  }
  const renderAll = () => pickers.forEach(p => p.render());
  // composedPath: a click on Reasoning draws the menu again, so the target is no longer in the page.
  document.addEventListener("click", e => {
    const path = e.composedPath();
    pickers.forEach(p => { if (!path.includes(p.btn.parentElement)) p.open(false); });
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") pickers.forEach(p => p.showCard(false)); });

  // ---- The round Send / Stop button.
  function setSend(btn, busy, { send = "Send", stop = "Stop", key = "" } = {}) {
    btn.innerHTML = busy ? ICON.stop : ICON.send;
    btn.setAttribute("aria-label", busy ? stop : send);
    btn.title = busy ? stop : key ? `${send} (${key})` : send;
  }
  // The text box grows with its text, up to a limit.
  function autoGrow(ta, max = 240) {
    const fit = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, max) + "px"; ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden"; };
    ta.addEventListener("input", fit);
    fit();
    return fit;
  }

  window.Composer = { picker, load, models: () => models, info, isLoaded, reasonOf, reasoningFields, setReason, renderAll, setSend, autoGrow, ICON, capIcons };
})();
