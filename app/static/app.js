/* GTA Clips – Handy-App (ohne Build-Schritt, reines JavaScript) */
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
let tab = "live";
let timer = null;
let campaigns = [];
let streamers = [];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");
const eur = (n) => (Number(n) || 0).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " $";
const PLATFORMS = ["tiktok", "youtube", "instagram"];

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Nicht angemeldet");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data?.detail || data || res.statusText);
  return data;
}

/* ---------- Anmeldung ---------- */
function showLogin() {
  clearInterval(timer);
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-err").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("#login-pw").value } });
    $("#login").classList.add("hidden");
    $("#app").classList.remove("hidden");
    show(tab);
  } catch (err) {
    $("#login-err").textContent = err.message;
  }
});

/* ---------- Tabs ---------- */
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.tab))
);
$("#refresh").addEventListener("click", () => show(tab));

async function show(name) {
  tab = name;
  clearInterval(timer);
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  try {
    await { live: renderLive, clips: renderClips, campaigns: renderCampaigns, tracker: renderTracker, streamers: renderStreamers }[name]();
  } catch (err) {
    if (err.message !== "Nicht angemeldet") view.innerHTML = `<div class="card warn-box">${esc(err.message)}</div>`;
  }
}

/* ---------- Bottom-Sheet ---------- */
function openSheet(html) {
  $("#sheet-body").innerHTML = html;
  $("#sheet").classList.remove("hidden");
}
function closeSheet() {
  $("#sheet").classList.add("hidden");
  $("#sheet-body").innerHTML = "";
}
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

/* ---------- Live ---------- */
let chatLast = 0;
let chatTimer = null;

async function renderLive() {
  chatLast = 0;
  view.innerHTML = `<div id="live-top"></div>
    <h2>Gerade live</h2><div id="live-cards"></div>
    <div id="live-bottom"></div>`;
  await updateLive();
  clearInterval(timer);
  timer = setInterval(() => tab === "live" && !document.hidden && updateLive().catch(() => {}), 5000);
  clearInterval(chatTimer);
  chatTimer = setInterval(() => { if (tab !== "live") return clearInterval(chatTimer); if (!document.hidden) pollChat().catch(() => {}); }, 2000);
  pollChat().catch(() => {});
}

async function updateLive() {
  const top = $("#live-top");
  if (!top) return;
  const [s, sched] = await Promise.all([api("/api/status"), getSchedule()]);
  const live = s.streamers.filter((x) => x.live);
  const off = s.streamers.filter((x) => !x.live);
  const c = s.clips || {};
  top.innerHTML = `
    ${s.warnings.map((w) => `<div class="card warn-box">${esc(w)}</div>`).join("")}
    <div class="stats">
      <div class="stat"><b>${live.length}</b><span>live</span></div>
      <div class="stat"><b>${(c.queued || 0) + (c.cutting || 0)}</b><span>in Arbeit</span></div>
      <div class="stat"><b>${c.ready || 0}</b><span>Clips fertig</span></div>
    </div>`;
  $("#live-bottom").innerHTML = `
    ${off.length ? `<h2>Offline</h2><div class="card stack">${off.map((x) => { const sc = sched.find((y) => y.id === x.id); return `<div class="row" style="align-items:flex-start"><span class="dot" style="margin-top:6px"></span><div class="grow" style="min-width:0"><div class="row between"><span class="ellipsis">${esc(x.display_name || x.channel)}</span>${x.active ? `<span class="pill">${x.platform}</span>` : '<span class="pill warn">pausiert</span>'}</div>${sc && sc.streams ? `<div class="muted small">${esc(sc.summary)}${sc.next ? " · nächster Stream ca. <b style=\"color:var(--text)\">" + fmtNext(sc.next) + "</b>" : ""}</div>` : ""}</div></div>`; }).join("")}</div>` : ""}
    <h2>Letzte Hype-Momente</h2>
    ${s.events.length ? `<div class="card stack">${s.events.map((e) => `<div class="row between small"><span>${esc(e.channel || "?")} · ${fmtTime(e.ts)}</span><span class="pill ${e.score ? "ok" : ""}">${e.keywords?.[0] === "manuell" ? "manuell" : e.rate + "/s (normal " + e.baseline + ")"}</span></div>`).join("")}</div>` : '<div class="card muted small">Noch keine.</div>'}
    <p class="muted small">Letzte Abfrage: ${fmtTime(s.last_poll) || "–"}</p>`;

  // Live-Karten: bestehende nur aktualisieren, damit Chat/Stream offen bleiben
  const wrap = $("#live-cards");
  const ids = new Set(live.map((x) => String(x.id)));
  wrap.querySelectorAll("[data-card]").forEach((el) => { if (!ids.has(el.dataset.card)) el.remove(); });
  wrap.querySelector(".no-live")?.remove();
  if (!live.length) {
    wrap.insertAdjacentHTML("beforeend", `<div class="card muted no-live">Niemand von deiner Liste ist live. ${s.streamers.length ? "" : "Füge unter <b>Streamer</b> Kanäle hinzu."}</div>`);
  }
  live.forEach((x) => {
    let card = wrap.querySelector(`[data-card="${x.id}"]`);
    if (!card) {
      wrap.insertAdjacentHTML("beforeend", liveCard(x));
      card = wrap.querySelector(`[data-card="${x.id}"]`);
      bindLiveCard(card, x);
    }
    updateLiveCard(card, x);
  });
}

function streamEmbed(x) {
  const host = location.hostname;
  if (x.platform === "twitch") return `https://player.twitch.tv/?channel=${encodeURIComponent(x.channel)}&parent=${encodeURIComponent(host)}&muted=true&autoplay=true`;
  if (x.video_id) return `https://www.youtube.com/embed/${encodeURIComponent(x.video_id)}?autoplay=1&mute=1&playsinline=1`;
  return "";
}

function liveCard(x) {
  return `<div class="card live-card" data-card="${x.id}">
    <div class="row"><span class="dot live"></span><b class="grow ellipsis">${esc(x.display_name || x.channel)}</b><span class="pill" data-r="viewers"></span></div>
    <div class="muted small ellipsis" style="margin-top:4px" data-r="title"></div>
    <div class="row between small" style="margin-top:8px"><span data-r="rate"></span><span class="pill" data-r="factor"></span></div>
    <div class="meter"><div data-r="meter"></div></div>
    <div class="btns" style="margin-top:10px"><button class="btn small" data-clipnow="${x.id}">Jetzt clippen</button></div>
    <div class="btns" style="margin-top:8px">
      <button class="btn small ghost toggle" data-toggle="chat">💬 Chat</button>
      <button class="btn small ghost toggle" data-toggle="live">▶ Live</button>
    </div>
    <div class="media hidden">
      <div class="chat-box hidden" data-chat="${x.id}"><div class="chat-msgs"><div class="muted small chat-empty">Warte auf Nachrichten …</div></div></div>
      <div class="stream-box hidden"></div>
    </div>
  </div>`;
}

function updateLiveCard(card, x) {
  const factor = x.baseline > 0 ? x.rate / x.baseline : 0;
  const set = (r, html) => { const el = card.querySelector(`[data-r="${r}"]`); if (el) el.innerHTML = html; };
  set("viewers", x.viewers ? x.viewers.toLocaleString("de-DE") + " Zuschauer" : x.platform);
  set("title", esc(x.game) + (x.game && x.title ? " · " : "") + esc(x.title));
  set("rate", `Chat: <b>${x.rate}/s</b> <span class="muted">(Ø ${x.baseline} · Clip ab ${x.threshold}/s)</span>`);
  const f = card.querySelector('[data-r="factor"]');
  const hot = x.threshold && x.rate >= x.threshold;
  f.className = "pill " + (hot ? "bad" : x.threshold && x.rate >= x.threshold * 0.6 ? "warn" : "");
  f.textContent = hot ? "HYPE" : factor.toFixed(1) + "×";
  card.querySelector('[data-r="meter"]').style.width = Math.min(100, x.threshold ? (x.rate / x.threshold) * 100 : 0) + "%";
}

function bindLiveCard(card, x) {
  card.querySelector("[data-clipnow]").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      await api(`/api/streamers/${x.id}/clip-now`, { method: "POST" });
      toast("Clip wird erstellt – dauert ca. 2 Minuten.");
    } catch (err) { toast(err.message); }
    b.disabled = false;
  });
  const media = card.querySelector(".media");
  const chat = card.querySelector(".chat-box");
  const stream = card.querySelector(".stream-box");
  const layout = () => {
    const c = !chat.classList.contains("hidden"), l = !stream.classList.contains("hidden");
    media.classList.toggle("hidden", !c && !l);
    media.classList.toggle("both", c && l);
    if (c) { const m = chat.querySelector(".chat-msgs"); m.scrollTop = m.scrollHeight; }
  };
  card.querySelectorAll("[data-toggle]").forEach((btn) => btn.addEventListener("click", () => {
    const isChat = btn.dataset.toggle === "chat";
    const box = isChat ? chat : stream;
    const open = box.classList.toggle("hidden") === false;
    btn.classList.toggle("active", open);
    if (!isChat) {
      if (open) {
        const src = streamEmbed(x);
        stream.innerHTML = src ? `<iframe src="${src}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>`
          : '<div class="muted small" style="padding:10px">Stream kann hier nicht angezeigt werden.</div>';
      } else {
        stream.innerHTML = ""; // Stream stoppen, spart Datenvolumen
      }
    }
    layout();
  }));
}

async function pollChat() {
  const grid = $("#live-cards");
  if (!grid) return;
  const r = await api(`/api/chat?after=${chatLast}`);
  chatLast = r.last;
  for (const [sid, msgs] of Object.entries(r.chats)) {
    const list = grid.querySelector(`[data-chat="${sid}"] .chat-msgs`);
    if (!list || !msgs.length) continue;
    list.querySelector(".chat-empty")?.remove();
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    for (const m of msgs) {
      const el = document.createElement("div");
      el.className = "chat-msg" + (m.k ? " kw" : "") + (m.sys ? " sys" : "");
      el.innerHTML = m.sys ? esc(m.m) : `<b style="color:${/^#[0-9a-f]{6}$/i.test(m.c) ? m.c : "var(--pink)"}">${esc(m.u)}</b> ${esc(m.m)}`;
      list.appendChild(el);
    }
    while (list.children.length > 80) list.firstChild.remove();
    if (atBottom) list.scrollTop = list.scrollHeight;
  }
}

/* ---------- Clips ---------- */
let clipFilter = "";
let clipSort = "new";
let openClipId = null;
async function renderClips() {
  const clips = await api(`/api/clips?sort=${clipSort}` + (clipFilter ? `&status=${clipFilter}` : ""));
  campaigns = await api("/api/campaigns");
  const busy = clips.some((c) => c.status === "queued" || c.status === "cutting");
  const filters = [["", "Alle"], ["new", "Neu"], ["ready", "Fertig"], ["posted", "Gepostet"], ["cutting", "Schneidet"], ["failed", "Fehler"]];
  view.innerHTML = `
    <div class="seg">${filters.map(([v, l]) => `<button class="btn small ${clipFilter === v ? "active" : ""}" data-filter="${v}">${l}</button>`).join("")}</div>
    <div class="seg"><button class="btn small ${clipSort === "new" ? "active" : ""}" data-sort="new">Neueste zuerst</button><button class="btn small ${clipSort === "best" ? "active" : ""}" data-sort="best">🔥 Bester Hype zuerst</button></div>
    <button class="btn ghost" id="manual" style="width:100%;margin-bottom:12px">+ Clip aus Link (VOD / Clip)</button>
    ${clips.length ? clips.map(clipMini).join("") : '<div class="card muted">Noch keine Clips. Sobald ein Chat eskaliert, erscheint hier automatisch ein Clip.</div>'}`;
  view.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { clipFilter = b.dataset.filter; renderClips(); }));
  view.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => { clipSort = b.dataset.sort; renderClips(); }));
  if (openClipId) { const c = clips.find((x) => x.id == openClipId); openClipId = null; if (c) openClip(c); }
  view.querySelectorAll("[data-clip]").forEach((el) => el.addEventListener("click", () => openClip(clips.find((c) => c.id == el.dataset.clip))));
  $("#manual").addEventListener("click", manualClipSheet);
  clearInterval(timer);
  if (busy) timer = setInterval(() => tab === "clips" && !document.hidden && renderClips().catch(() => {}), 6000);
}

const STATUS = { ready: ["Fertig", "ok"], cutting: ["Schneidet …", "warn"], queued: ["Wartet …", "warn"], failed: ["Fehler", "bad"] };
function clipMini(c) {
  const [label, cls] = STATUS[c.status] || [c.status, ""];
  return `<div class="card clip-mini" data-clip="${c.id}">
    ${c.thumb_url ? `<img src="${c.thumb_url}" alt="" loading="lazy">` : `<div class="ph">${c.status === "failed" ? "✗" : "…"}</div>`}
    <div class="stack" style="gap:4px;min-width:0">
      <div class="row between"><b class="ellipsis">${esc(c.display_name || c.channel || "Clip")}</b><span class="row" style="gap:4px">${c.status === "ready" && !c.seen ? '<span class="pill new">NEU</span>' : ""}${c.post_count ? '<span class="pill ok">gepostet</span>' : `<span class="pill ${cls}">${label}</span>`}</span></div>
      <div class="small ellipsis">${esc(c.hook || "")}</div>
      ${c.hype_rate ? `<div class="small" style="color:var(--orange)">🔥 Chat ${c.hype_rate}/s statt Ø ${c.hype_baseline}/s (${Math.round(c.hype_rate / Math.max(c.hype_baseline, 0.05))}×)</div>` : ""}
      <div class="muted small">${fmtTime(c.created_at)}${c.duration ? " · " + Math.round(c.duration) + "s" : ""}${c.campaign_name ? " · " + esc(c.campaign_name) : ""}</div>
      ${c.error ? `<div class="small" style="color:${c.status === "failed" ? "var(--red)" : "var(--orange)"}">${esc(c.error)}</div>` : ""}
    </div></div>`;
}

function campaignOptions(selected) {
  return `<option value="">– keine –</option>` + campaigns.map((k) => `<option value="${k.id}" ${k.id == selected ? "selected" : ""}>${esc(k.name)}${k.clips_allowed ? "" : " (keine Clips erlaubt)"}</option>`).join("");
}

function openClip(c) {
  if (c.status === "ready" && !c.seen) {
    c.seen = 1;
    api(`/api/clips/${c.id}`, { method: "PATCH", body: { seen: 1 } }).catch(() => {});
  }
  openSheet(`
    <div class="clip card" style="margin:-4px 0 12px">
      ${c.video_url ? `<video src="${c.video_url}" controls playsinline preload="metadata" poster="${c.thumb_url}"></video>` : ""}
    </div>
    <div class="stack">
      <div class="row between"><b>${esc(c.display_name || c.channel || "Clip")}</b><span class="pill ${(STATUS[c.status] || [])[1] || ""}">${(STATUS[c.status] || [c.status])[0]}</span></div>
      ${c.error ? `<div class="card warn-box small" style="margin:0">${esc(c.error)}</div>` : ""}
      ${c.hype_sample && c.hype_sample.length ? `<div class="card small" style="margin:0;background:var(--card2)">
        <b>🔥 Was im Chat los war</b> <span class="muted">· ${c.hype_rate}/s statt Ø ${c.hype_baseline}/s${c.hype_keywords?.length ? " · " + esc(c.hype_keywords.join(", ")) : ""}</span>
        <div class="chat-msgs" style="max-height:140px;padding:6px 0 0">${c.hype_sample.map((m) => `<div class="chat-msg">${esc(m)}</div>`).join("")}</div></div>` : ""}
      <label>Hook (Text oben im Clip – leer lassen = kein Text)<input id="c-hook" value="${esc(c.hook)}" maxlength="80"></label>
      <div class="row"><label class="grow">Anfang kürzen (Sek.)<input id="c-trim-s" type="number" min="0" step="1" inputmode="numeric" value="${c.trim_start || 0}"></label>
        <label class="grow">Ende kürzen (Sek.)<input id="c-trim-e" type="number" min="0" step="1" inputmode="numeric" value="${c.trim_end || 0}"></label></div>
      <label>Layout<select id="c-layout"><option value="blur" ${c.layout !== "crop" ? "selected" : ""}>Ganzes Bild + unscharfer Hintergrund</option><option value="crop" ${c.layout === "crop" ? "selected" : ""}>Mitte zuschneiden (Vollbild)</option></select></label>
      <label>Kampagne<select id="c-camp">${campaignOptions(c.campaign_id)}</select></label>
      <label class="check"><input type="checkbox" id="c-capt" ${c.captions ? "checked" : ""}> 💬 Untertitel einbrennen <span class="muted small">(beim nächsten Neu-Schneiden, dauert ca. 1–2 Min. länger)</span></label>
      <button class="btn" id="c-rerender">Neu schneiden (Hook / Länge / Layout / Untertitel)</button>
      ${c.video_url ? `<div class="btns"><button class="btn primary" id="c-share">Teilen / Posten</button><a class="btn ghost" href="${c.video_url}" download="gta-clip-${c.id}.mp4" style="text-align:center;text-decoration:none">Download</a></div>` : ""}
      <h2>Posten mit Regel-Check</h2>
      <label>Plattform<select id="c-platform">${PLATFORMS.map((p) => `<option>${p}</option>`).join("")}</select></label>
      <label>Caption<textarea id="c-caption">${esc(c.caption)}</textarea></label>
      <button class="btn small ghost" id="c-copy">Caption kopieren</button>
      <ul class="checks" id="c-checks"></ul>
      <label>Link zum Post (optional, für den Tracker)<input id="c-url" placeholder="https://…"></label>
      <button class="btn primary" id="c-post">Regeln prüfen & als gepostet eintragen</button>
      <button class="btn danger" id="c-del">Clip löschen</button>
      <button class="btn ghost" onclick="closeSheet()">Schließen</button>
    </div>`);

  const save = () => api(`/api/clips/${c.id}`, { method: "PATCH", body: {
    hook: $("#c-hook").value, layout: $("#c-layout").value,
    trim_start: Math.max(0, Number($("#c-trim-s").value) || 0), trim_end: Math.max(0, Number($("#c-trim-e").value) || 0),
    captions: $("#c-capt").checked ? 1 : 0,
    campaign_id: $("#c-camp").value ? Number($("#c-camp").value) : null, caption: $("#c-caption").value } });
  const runCheck = async () => {
    await save();
    const r = await api(`/api/clips/${c.id}/check`, { method: "POST", body: { platform: $("#c-platform").value, caption: $("#c-caption").value } });
    $("#c-checks").innerHTML = r.checks.map((x) => `<li class="${x.ok ? "" : "no"}">${esc(x.msg)}</li>`).join("");
    return r;
  };
  $("#c-camp").addEventListener("change", () => runCheck().catch((e) => toast(e.message)));
  $("#c-platform").addEventListener("change", () => runCheck().catch((e) => toast(e.message)));
  runCheck().catch(() => {});

  $("#c-rerender").addEventListener("click", async () => {
    try { await save(); await api(`/api/clips/${c.id}/rerender`, { method: "POST" }); toast("Wird neu geschnitten …"); closeSheet(); renderClips(); }
    catch (e) { toast(e.message); }
  });
  $("#c-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#c-caption").value); toast("Caption kopiert"); } catch { toast("Kopieren nicht möglich"); }
  });
  $("#c-share")?.addEventListener("click", async () => {
    try {
      const r = await runCheck();
      if (!r.allowed && !confirm("Regel-Check nicht bestanden. Trotzdem teilen?")) return;
      try { await navigator.clipboard.writeText($("#c-caption").value); } catch {}
      const blob = await (await fetch(c.video_url)).blob();
      const file = new File([blob], `gta-clip-${c.id}.mp4`, { type: "video/mp4" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], text: $("#c-caption").value });
      } else {
        toast("Teilen geht hier nicht – nutze Download.");
      }
    } catch (e) { if (e.name !== "AbortError") toast(e.message); }
  });
  $("#c-post").addEventListener("click", async () => {
    try {
      const r = await runCheck();
      if (!r.allowed) return toast("Regel-Check nicht bestanden – Stopp.");
      await api(`/api/clips/${c.id}/posts`, { method: "POST", body: { platform: $("#c-platform").value, caption: $("#c-caption").value, url: $("#c-url").value } });
      toast("Im Tracker eingetragen");
      closeSheet(); renderClips();
    } catch (e) { toast(e.message); }
  });
  $("#c-del").addEventListener("click", async () => {
    if (!confirm("Clip wirklich löschen?")) return;
    await api(`/api/clips/${c.id}`, { method: "DELETE" });
    closeSheet(); renderClips();
  });
}

async function manualClipSheet() {
  streamers = await api("/api/streamers");
  openSheet(`<div class="stack">
    <h2>Clip aus Link</h2>
    <p class="muted small">Twitch-VOD, Twitch-Clip oder YouTube-Video. Bei einem Clip-Link Start/Ende leer lassen.</p>
    <label>Link<input id="m-url" placeholder="https://www.twitch.tv/videos/…" inputmode="url"></label>
    <div class="row"><label class="grow">Start (z. B. 1:23:45)<input id="m-start" placeholder="h:mm:ss"></label><label class="grow">Ende<input id="m-end" placeholder="h:mm:ss"></label></div>
    <label>Streamer<select id="m-streamer"><option value="">–</option>${streamers.map((s) => `<option value="${s.id}">${esc(s.display_name || s.channel)}</option>`).join("")}</select></label>
    <label>Kampagne<select id="m-camp">${campaignOptions()}</select></label>
    <button class="btn primary" id="m-go">Clip erstellen</button>
    <button class="btn ghost" onclick="closeSheet()">Abbrechen</button></div>`);
  const sec = (v) => {
    if (!v.trim()) return null;
    return v.trim().split(":").reduce((acc, p) => acc * 60 + Number(p), 0);
  };
  $("#m-go").addEventListener("click", async () => {
    try {
      await api("/api/clips", { method: "POST", body: {
        url: $("#m-url").value.trim(), start: sec($("#m-start").value), end: sec($("#m-end").value),
        streamer_id: $("#m-streamer").value ? Number($("#m-streamer").value) : null,
        campaign_id: $("#m-camp").value ? Number($("#m-camp").value) : null } });
      toast("Clip wird erstellt …"); closeSheet(); renderClips();
    } catch (e) { toast(e.message); }
  });
}

/* ---------- Kampagnen ---------- */
let campSub = "mine";
let liveOnly = false;
function campTabs() {
  return `<div class="seg"><button class="btn small ${campSub === "mine" ? "active" : ""}" data-sub="mine">Meine Kampagnen</button><button class="btn small ${campSub === "search" ? "active" : ""}" data-sub="search">🔍 Kampagnen suchen</button></div>`;
}
function bindCampTabs() {
  view.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => { campSub = b.dataset.sub; renderCampaigns(); }));
}

async function renderCampaigns() {
  if (campSub === "search") return renderCampaignSearch();
  campaigns = await api("/api/campaigns");
  view.innerHTML = `
    ${campTabs()}
    <div class="btns" style="margin-bottom:12px"><button class="btn primary" id="k-new">+ Kampagne</button><button class="btn ghost" id="k-import">kampagnen.json importieren</button></div>
    <input type="file" id="k-file" accept=".json,application/json" class="hidden">
    ${campaigns.length ? campaigns.map((k) => `<div class="card" data-camp="${k.id}">
      <div class="row between"><b class="ellipsis">${esc(k.name)}</b>${k.clips_allowed ? '<span class="pill ok">Clips erlaubt</span>' : '<span class="pill bad">keine Clips</span>'}</div>
      <div class="muted small" style="margin-top:4px">${esc(k.streamer)} · <b style="color:var(--text)">${eur(k.rate_per_1k)}</b> / 1.000 Aufrufe${k.min_views ? " · ab " + k.min_views.toLocaleString("de-DE") + " Aufrufen" : ""}${k.platforms.length ? " · " + k.platforms.join(", ") : ""}</div>
      ${k.required_tags.length || k.required_mention ? `<div class="small" style="margin-top:6px">Pflicht: ${esc([...k.required_tags, k.required_mention].filter(Boolean).join(" "))}</div>` : ""}
    </div>`).join("") : '<div class="card muted">Noch keine Kampagnen. Lege sie an oder importiere die <b>kampagnen.json</b> vom Laptop.</div>'}`;
  bindCampTabs();
  $("#k-new").addEventListener("click", linkSheet);
  $("#k-import").addEventListener("click", () => $("#k-file").click());
  $("#k-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const r = await api("/api/campaigns/import", { method: "POST", body: JSON.parse(await f.text()) });
      toast(`${r.imported} Kampagnen importiert`); renderCampaigns();
    } catch (err) { toast("Import fehlgeschlagen: " + err.message); }
  });
  view.querySelectorAll("[data-camp]").forEach((el) => el.addEventListener("click", () => campaignSheet(campaigns.find((k) => k.id == el.dataset.camp))));
}

function linkSheet() {
  openSheet(`<div class="stack">
    <h2>Kampagnenlink</h2>
    <p class="muted small" style="margin:0">Link der Kampagne einfügen (z. B. von Content Rewards, Whop, heistclips). Den Rest füllt die App aus – du prüfst danach nur noch.</p>
    <input id="l-url" placeholder="https://…" inputmode="url" autocapitalize="off" autocomplete="off">
    <button class="btn primary" id="l-go">Weiter</button>
    <p id="l-msg" class="muted small" style="margin:0"></p>
    <button class="btn ghost small" id="l-manual">Ohne Link von Hand anlegen</button>
    <button class="btn ghost" onclick="closeSheet()">Abbrechen</button></div>`);
  $("#l-manual").addEventListener("click", () => campaignSheet());
  $("#l-go").addEventListener("click", async () => {
    const url = $("#l-url").value.trim();
    if (!url) return toast("Bitte einen Link einfügen");
    $("#l-go").disabled = true;
    $("#l-msg").textContent = "Seite wird gelesen …";
    try {
      const r = await api("/api/campaigns/from-link", { method: "POST", body: { url } });
      const labels = { name: "Name", twitch_channel: "Twitch-Kanal", rate_per_1k: "Rate", min_views: "Mindest-Aufrufe",
        max_payout: "Maximum", platforms: "Plattformen" };
      const missing = Object.keys(labels).filter((k) => !r.found.includes(k)).map((k) => labels[k]);
      let note = r.method === "claude" ? "Von Claude ausgefüllt – bitte kurz prüfen." : "Automatisch erkannt – bitte prüfen.";
      if (r.page_was_empty) note = "Die Seite lädt ihre Daten erst im Browser, deshalb konnte kaum etwas gelesen werden. Bitte die fehlenden Felder ergänzen (Angaben stehen auf der Kampagnenseite).";
      if (missing.length && !r.page_was_empty) note += " Nicht gefunden: " + missing.join(", ") + ".";
      campaignSheet({ ...r.fields, clips_allowed: 1 }, note);
    } catch (e) {
      $("#l-msg").textContent = e.message;
      $("#l-go").disabled = false;
    }
  });
}

function campaignSheet(k = {}, note = "") {
  const v = (x, d = "") => esc(x ?? d);
  openSheet(`<div class="stack">
    <h2>${k.id ? "Kampagne bearbeiten" : "Neue Kampagne"}</h2>
    ${note ? `<div class="card warn-box small" style="margin:0">${esc(note)}</div>` : ""}
    <label>Name<input id="k-name" value="${v(k.name)}"></label>
    <label>Streamer<input id="k-streamer" value="${v(k.streamer)}"></label>
    ${k.id ? "" : `<label>Twitch-Kanal (wird automatisch beobachtet)<input id="k-twitch" value="${v(k.twitch_channel)}" placeholder="z. B. darealboeshi" autocapitalize="off"></label>`}
    <label class="check"><input type="checkbox" id="k-allowed" ${k.clips_allowed === 0 ? "" : "checked"}> Streamer erlaubt Clips</label>
    <div class="row"><label class="grow">$ pro 1.000 Aufrufe<input id="k-rate" type="number" step="0.01" inputmode="decimal" value="${v(k.rate_per_1k, 1)}"></label><label class="grow">Mindest-Aufrufe<input id="k-min" type="number" inputmode="numeric" value="${v(k.min_views, 0)}"></label></div>
    <div class="row"><label class="grow">Max. Auszahlung/Clip<input id="k-cap" type="number" step="0.01" inputmode="decimal" value="${v(k.max_payout, 0)}"></label><label class="grow">Länge (s) von–bis<div class="row"><input id="k-smin" type="number" value="${v(k.min_seconds, 20)}"><input id="k-smax" type="number" value="${v(k.max_seconds, 60)}"></div></label></div>
    <label>Plattformen (leer = alle)<input id="k-plat" value="${v((k.platforms || []).join(", "))}" placeholder="tiktok, youtube, instagram"></label>
    <label>Pflicht-Tags<input id="k-tags" value="${v((k.required_tags || []).join(" "))}" placeholder="#gta6 #clip"></label>
    <label>Pflicht-Erwähnung in der Caption<input id="k-mention" value="${v(k.required_mention)}" placeholder="twitch.tv/…"></label>
    <label>Regeln<textarea id="k-rules">${v(k.rules)}</textarea></label>
    <label>Link zu den Regeln<input id="k-url" value="${v(k.url)}"></label>
    <button class="btn primary" id="k-save">Speichern</button>
    ${k.id ? '<button class="btn danger" id="k-del">Löschen</button>' : ""}
    <button class="btn ghost" onclick="closeSheet()">Abbrechen</button></div>`);
  $("#k-save").addEventListener("click", async () => {
    const body = {
      name: $("#k-name").value.trim() || "Kampagne", streamer: $("#k-streamer").value.trim(),
      clips_allowed: $("#k-allowed").checked, rate_per_1k: Number($("#k-rate").value) || 0,
      min_views: Number($("#k-min").value) || 0, max_payout: Number($("#k-cap").value) || 0,
      min_seconds: Number($("#k-smin").value) || 0, max_seconds: Number($("#k-smax").value) || 0,
      platforms: $("#k-plat").value.split(","), required_tags: $("#k-tags").value.split(/[\s,]+/),
      required_mention: $("#k-mention").value.trim(), rules: $("#k-rules").value, url: $("#k-url").value.trim(),
      twitch_channel: $("#k-twitch") ? $("#k-twitch").value.trim() : "",
    };
    try {
      await api(k.id ? `/api/campaigns/${k.id}` : "/api/campaigns", { method: k.id ? "PUT" : "POST", body });
      closeSheet(); renderCampaigns();
    } catch (e) { toast(e.message); }
  });
  $("#k-del")?.addEventListener("click", async () => {
    if (!confirm("Kampagne löschen?")) return;
    await api(`/api/campaigns/${k.id}`, { method: "DELETE" }); closeSheet(); renderCampaigns();
  });
}

async function renderCampaignSearch() {
  const [r, lv] = await Promise.all([api("/api/campaign-search"), api("/api/live-gta6").catch((e) => ({ error: e.message, streams: [] }))]);
  const running = r.status === "running";
  const results = (r.results || []).map((x, i) => ({ ...x, i })).filter((x) => !liveOnly || x.live_gta6);
  const mins = r.started_at ? Math.max(0, Math.round((Date.now() / 1000 - r.started_at) / 60)) : 0;
  const streams = lv.streams || [];
  view.innerHTML = `
    ${campTabs()}
    <h2>Gerade live in GTA 6 auf Twitch</h2>
    ${lv.error ? `<div class="card warn-box small">${esc(lv.error)}</div>` : lv.twitch_ready === false ? '<div class="card warn-box small">Trag zuerst die Twitch-Schlüssel ein (Streamer → Einstellungen).</div>' : ""}
    ${streams.length ? `<div class="card stack">${streams.map((s) => `
      <div class="row" style="align-items:flex-start">
        <span class="dot live" style="margin-top:6px"></span>
        <div class="grow" style="min-width:0">
          <div class="row between"><b class="ellipsis">${esc(s.name || s.channel)}</b><span class="muted small">${s.viewers.toLocaleString("de-DE")}</span></div>
          <div class="muted small ellipsis">${esc(s.title)}</div>
          <div class="small" style="margin-top:4px">${s.on_list ? `<span class="pill ok">auf deiner Liste${s.campaign_name ? " · " + esc(s.campaign_name) : ""}</span>` :
            s.campaign_match ? `<button class="btn small" data-addlive="${esc(s.channel)}" data-camp="${s.campaign_match_id}">Hinzufügen (Kampagne: ${esc(s.campaign_match)})</button>` :
            `<span class="pill">keine Kampagne bekannt</span> <button class="btn small ghost" data-addlive="${esc(s.channel)}">trotzdem beobachten</button>`}</div>
        </div>
      </div>`).join("")}</div>` : lv.twitch_ready ? '<div class="card muted">Gerade streamt niemand GTA 6 – oder Twitch kennt das Spiel noch nicht unter „Grand Theft Auto VI“.</div>' : ""}
    <p class="muted small">Nur Clips posten, wenn es für den Streamer eine Kampagne gibt oder er Clips ausdrücklich erlaubt.</p>

    <h2>Neue Kampagnen finden</h2>
    <div class="card stack">
      ${r.has_api_key ? `
        <button class="btn primary" id="cs-go" ${running ? "disabled" : ""}>${running ? `Suche läuft … (${mins} Min.)` : "Im Netz suchen (Claude)"}</button>
        ${running ? '<p class="muted small" style="margin:0">Dauert meist 1–4 Minuten.</p>' : ""}` : `
        <p class="small" style="margin:0"><b>Kostenlos:</b> Frag Claude im Chat <i>„Such mir GTA-6-Kampagnen“</i>. Du bekommst eine <b>kampagnen.json</b> – die importierst du unter <b>Meine Kampagnen → kampagnen.json importieren</b>.</p>
        <p class="muted small" style="margin:0">Mit einem Anthropic-API-Key (Streamer → Einstellungen) geht die Suche auch direkt hier per Knopf.</p>`}
      ${r.status === "error" ? `<div class="card warn-box small" style="margin:0">${esc(r.error)}</div>` : ""}
      ${r.finished_at && !running ? `<p class="muted small" style="margin:0">Letzte Suche: ${fmtTime(r.finished_at)}</p>` : ""}
    </div>
    ${(r.results || []).length ? `<label class="check" style="margin:0 0 12px"><input type="checkbox" id="cs-live" ${liveOnly ? "checked" : ""}> nur gerade live in GTA 6</label>` : ""}
    ${results.map((x) => `<div class="card stack">
      <div class="row between"><b class="ellipsis">${esc(x.name)}</b>${x.live_gta6 ? '<span class="pill bad">● LIVE GTA 6</span>' : x.live ? `<span class="pill warn">live: ${esc(x.game || "?")}</span>` : '<span class="pill">offline</span>'}</div>
      <div class="small"><b>twitch.tv/${esc(x.twitch_channel)}</b>${x.twitch_exists === false ? ' <span class="pill bad">Kanal nicht gefunden</span>' : ""}${x.viewers ? " · " + x.viewers.toLocaleString("de-DE") + " Zuschauer" : ""}</div>
      <div class="muted small"><b style="color:var(--text)">${eur(x.rate_per_1k)}</b> / 1.000 Aufrufe${x.min_views ? " · ab " + x.min_views.toLocaleString("de-DE") + " Aufrufen" : ""}${x.max_payout ? " · max. " + eur(x.max_payout) : ""}${x.platforms.length ? " · " + esc(x.platforms.join(", ")) : ""}</div>
      ${x.required_tags.length || x.required_mention ? `<div class="small">Pflicht: ${esc([...x.required_tags, x.required_mention].filter(Boolean).join(" "))}</div>` : ""}
      ${x.rules ? `<div class="small muted">${esc(x.rules)}</div>` : ""}
      ${x.url ? `<a class="small" href="${esc(x.url)}" target="_blank" rel="noopener" style="color:var(--pink)">Regeln / Kampagne öffnen</a>` : ""}
      ${x.already_added ? '<span class="pill ok" style="justify-self:start">schon auf deiner Liste</span>' : `<button class="btn small" data-adopt="${x.i}">Übernehmen & clippen</button>`}
    </div>`).join("")}`;
  bindCampTabs();
  $("#cs-go")?.addEventListener("click", async () => {
    try { await api("/api/campaign-search", { method: "POST" }); renderCampaignSearch(); } catch (e) { toast(e.message, 5000); }
  });
  $("#cs-live")?.addEventListener("change", (e) => { liveOnly = e.target.checked; renderCampaignSearch(); });
  view.querySelectorAll("[data-adopt]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await api("/api/campaign-search/adopt", { method: "POST", body: { index: Number(b.dataset.adopt) } });
      toast("Übernommen – wird jetzt beobachtet und geclippt"); renderCampaignSearch();
    } catch (e) { toast(e.message); b.disabled = false; }
  }));
  view.querySelectorAll("[data-addlive]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await api("/api/streamers", { method: "POST", body: { platform: "twitch", channel: b.dataset.addlive,
        campaign_id: b.dataset.camp ? Number(b.dataset.camp) : null } });
      api("/api/poll", { method: "POST" }).catch(() => {});
      toast("Wird jetzt beobachtet"); renderCampaignSearch();
    } catch (e) { toast(e.message); b.disabled = false; }
  }));
  clearInterval(timer);
  timer = setInterval(() => tab === "campaigns" && campSub === "search" && !document.hidden && renderCampaignSearch().catch(() => {}), running ? 5000 : 30000);
}

/* ---------- Tracker ---------- */
let statsBy = "by_streamer";
function statsHtml(st) {
  const list = st[statsBy] || [];
  const max = Math.max(1, ...list.map((e) => e.estimate || e.views / 1000));
  return `<h2>Auswertung</h2>
    <div class="card stack">
      <div class="small">Letzte 7 Tage: <b>${st.week.posts}</b> Posts · <b>${st.week.views.toLocaleString("de-DE")}</b> Aufrufe</div>
      <div class="seg" style="margin:0"><button class="btn small ${statsBy === "by_streamer" ? "active" : ""}" data-by="by_streamer">nach Streamer</button><button class="btn small ${statsBy === "by_campaign" ? "active" : ""}" data-by="by_campaign">nach Kampagne</button></div>
      ${list.length ? list.map((e, i) => `<div class="stat-row">
        <div class="row between small"><b class="ellipsis">${i === 0 ? "🏆 " : ""}${esc(e.name)}</b><span><b>${eur(e.estimate)}</b>${e.paid ? ` <span class="muted">(${eur(e.paid)} ausgezahlt)</span>` : ""}</span></div>
        <div class="meter" style="margin-top:4px"><div style="width:${Math.max(3, ((e.estimate || e.views / 1000) / max) * 100)}%"></div></div>
        <div class="muted small" style="margin-top:2px">${e.posts} Posts · ${e.views.toLocaleString("de-DE")} Aufrufe · Ø ${e.avg_views.toLocaleString("de-DE")} pro Post${e.clips !== undefined ? ` · ${e.clips} Clips fertig` : ""}</div>
      </div>`).join("") : '<div class="muted small">Noch keine Posts – sobald du Clips als gepostet einträgst und Aufrufe nachträgst, siehst du hier, was sich lohnt.</div>'}
    </div>
    ${st.top.length ? `<h2>Beste Clips</h2><div class="card stack">${st.top.map((c, i) => `<div class="row">
      <b style="width:22px">${["🥇", "🥈", "🥉", "4.", "5."][i]}</b>
      ${c.thumb_url ? `<img src="${c.thumb_url}" alt="" style="width:36px;aspect-ratio:9/16;object-fit:cover;border-radius:6px">` : ""}
      <div class="grow" style="min-width:0"><div class="small ellipsis"><b>${esc(c.channel || "Clip")}</b> · ${esc(c.platform)}</div><div class="muted small ellipsis">${esc(c.hook || "")}</div></div>
      <div class="small" style="text-align:right"><b>${Number(c.views).toLocaleString("de-DE")}</b><div class="muted">${eur(c.estimate)}</div></div>
    </div>`).join("")}</div>` : ""}`;
}

async function renderTracker() {
  const [t, st] = await Promise.all([api("/api/tracker"), api("/api/stats")]);
  view.innerHTML = `
    <div class="stats">
      <div class="stat"><b>${t.total_views.toLocaleString("de-DE")}</b><span>Aufrufe</span></div>
      <div class="stat"><b>${eur(t.total_estimate)}</b><span>geschätzt</span></div>
      <div class="stat"><b>${eur(t.total_paid)}</b><span>ausgezahlt</span></div>
    </div>
    <div id="stats">${statsHtml(st)}</div>
    <h2>Alle Posts</h2>
    <a class="btn ghost small" href="/api/tracker.csv" style="display:block;text-align:center;text-decoration:none;margin-bottom:12px">Als Tabelle (CSV) herunterladen</a>
    ${t.posts.length ? t.posts.map((p) => `<div class="card stack" data-post="${p.id}">
      <div class="row between"><b class="ellipsis">${esc(p.channel || "Clip")} · ${esc(p.platform)}</b><span class="muted small">${fmtTime(p.posted_at)}</span></div>
      <div class="small muted ellipsis">${esc(p.campaign_name || "keine Kampagne")}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener" style="color:var(--pink)">Post öffnen</a>` : ""}</div>
      <div class="row"><label class="grow">Aufrufe<input type="number" inputmode="numeric" data-views value="${p.views || 0}"></label><label class="grow">Ausgezahlt $<input type="number" step="0.01" inputmode="decimal" data-paid value="${p.paid ?? ""}"></label></div>
      <div class="row between small"><span>Schätzung: <b>${eur(p.estimate)}</b></span><button class="btn small" data-save>Speichern</button></div>
    </div>`).join("") : '<div class="card muted">Noch nichts gepostet. Trag Clips nach dem Posten über „Regeln prüfen & als gepostet eintragen“ ein.</div>'}`;
  const bindBy = () => view.querySelectorAll("[data-by]").forEach((b) => b.addEventListener("click", () => {
    statsBy = b.dataset.by; $("#stats").innerHTML = statsHtml(st); bindBy();
  }));
  bindBy();
  view.querySelectorAll("[data-post]").forEach((el) =>
    $("[data-save]", el).addEventListener("click", async () => {
      const paid = $("[data-paid]", el).value;
      await api(`/api/posts/${el.dataset.post}`, { method: "PATCH", body: { views: Number($("[data-views]", el).value) || 0, paid: paid === "" ? null : Number(paid) } });
      toast("Gespeichert"); renderTracker();
    })
  );
}

/* ---------- Streamer ---------- */
async function renderStreamers() {
  let cfg, upd, sched;
  [streamers, campaigns, cfg, upd, sched] = await Promise.all([api("/api/streamers"), api("/api/campaigns"), api("/api/settings"),
    api("/api/update").catch(() => ({})), getSchedule()]);
  view.innerHTML = `
    <div class="card stack">
      <b>Streamer hinzufügen</b>
      <div class="row"><select id="s-platform" style="width:auto"><option value="twitch">Twitch</option><option value="youtube">YouTube</option></select><input id="s-channel" class="grow" placeholder="Kanalname, z. B. skilmasktimmy" autocapitalize="off"></div>
      <label>Kampagne<select id="s-camp">${campaignOptions()}</select></label>
      <button class="btn primary" id="s-add">Hinzufügen</button>
      <p class="muted small" style="margin:0">Nur Streamer, die Clips erlauben. Bei YouTube den @Handle eintragen.</p>
    </div>
    <div class="row between"><h2 style="margin:6px 0">Deine Streamer</h2><button class="btn small ghost" id="sched-refresh">🗓️ Zeitpläne aktualisieren</button></div>
    ${streamers.map((s) => { const sc = sched.find((y) => y.id === s.id); return `<div class="card stack" data-s="${s.id}">
      <div class="row between"><b class="ellipsis">${esc(s.display_name || s.channel)}</b><span class="pill">${s.platform}</span></div>
      ${sc && sc.streams ? `<div class="small"><b>🗓️ ${esc(sc.summary)}</b><span class="muted"> · ca. ${sc.hours_per_week} Std./Woche${sc.next && !sc.live ? " · nächster Stream ca. " + fmtNext(sc.next) : ""}</span></div>
      <details><summary class="small muted">Wochenübersicht anzeigen</summary>${heatmapHtml(sc.heat)}<div class="muted small">Je kräftiger, desto öfter live (letzte 4 Wochen).</div></details>` :
      '<div class="muted small">🗓️ Zeitplan: noch keine Daten – füllt sich mit der Zeit bzw. über „Zeitpläne aktualisieren“.</div>'}
      <label class="check"><input type="checkbox" data-active ${s.active ? "checked" : ""}> beobachten</label>
      <label>Kampagne<select data-camp>${campaignOptions(s.campaign_id)}</select></label>
      <button class="btn danger small" data-del>Entfernen</button>
    </div>`; }).join("")}
    <h2>Einstellungen</h2>
    <div class="card stack">
      <span class="pill ${cfg.twitch_ready ? "ok" : "bad"}" style="justify-self:start">${cfg.twitch_ready ? "Twitch verbunden" : "Twitch-Schlüssel fehlen"}</span>
      <label>Twitch Client-ID<input id="set-tid" value="${esc(cfg.twitch_client_id)}" autocapitalize="off" autocomplete="off"></label>
      <label>Twitch Client-Secret<input id="set-tsec" placeholder="${cfg.twitch_client_secret ? esc(cfg.twitch_client_secret) + " (gespeichert)" : "einfügen"}" autocapitalize="off" autocomplete="off"></label>
      <label>Anthropic API-Key (optional)<input id="set-akey" placeholder="${cfg.anthropic_api_key ? esc(cfg.anthropic_api_key) + " (gespeichert)" : "optional"}" autocapitalize="off" autocomplete="off"></label>
      <label>Neues App-Passwort (optional, mind. 8 Zeichen)<input id="set-pw" type="password" autocomplete="new-password"></label>
      <button class="btn primary" id="set-save">Einstellungen speichern</button>
      <p class="muted small" style="margin:0">Twitch-Schlüssel bekommst du kostenlos unter dev.twitch.tv/console/apps. Leere Felder bleiben unverändert.</p>
    </div>
    <h2>Hype-Erkennung</h2>
    <div class="card stack">
      <p class="muted small" style="margin:0">Ein Clip entsteht nur, wenn der Chat deutlich schneller ist als sein Durchschnitt – und das ein paar Sekunden anhält.</p>
      <label>Chat muss so viel schneller sein als der Durchschnitt
        <select id="h-factor">${[3, 4, 5, 6, 8].map((f) => `<option value="${f}" ${cfg.hype_factor == f ? "selected" : ""}>${f}× ${f === 4 ? "(empfohlen)" : f >= 6 ? "(sehr streng)" : f === 3 ? "(mehr Clips)" : ""}</option>`).join("")}</select></label>
      <label>Mindestens so viele Nachrichten pro Sekunde
        <select id="h-min">${[0.5, 1, 2, 3, 5].map((f) => `<option value="${f}" ${cfg.hype_min_rate == f ? "selected" : ""}>${String(f).replace(".", ",")} / s ${f === 1 ? "(empfohlen)" : f < 1 ? "(auch kleine Streams)" : ""}</option>`).join("")}</select></label>
      <label>Pause nach einem Clip (pro Streamer)
        <select id="h-cool">${[120, 300, 600, 900, 1800].map((f) => `<option value="${f}" ${cfg.hype_cooldown == f ? "selected" : ""}>${f / 60} Minuten</option>`).join("")}</select></label>
      <label>Höchstens so viele Clips pro Stunde (pro Streamer)
        <select id="h-max">${[2, 4, 6, 10, 20].map((f) => `<option value="${f}" ${cfg.hype_max_per_hour == f ? "selected" : ""}>${f}</option>`).join("")}</select></label>
      <button class="btn" id="h-save">Hype-Einstellungen speichern</button>
    </div>
    <h2>Benachrichtigungen</h2>
    <div class="card stack">
      <p class="muted small" style="margin:0">Dein Handy meldet sich, sobald ein neuer Clip fertig ist.</p>
      <div class="small" id="push-state">…</div>
      <div class="btns"><button class="btn primary" id="push-on">Aktivieren</button><button class="btn ghost" id="push-test">Test</button></div>
      <button class="btn ghost small" id="push-off">Auf diesem Handy ausschalten</button>
    </div>
    <h2>Speicher</h2>
    <div class="card stack">
      <div class="small">Frei: <b>${cfg.disk.free_gb} GB</b> von ${cfg.disk.total_gb} GB · Clips: ${cfg.disk.clips_gb} GB · Rohmaterial: ${cfg.disk.raw_gb} GB</div>
      <div class="meter"><div style="width:${Math.min(100, Math.round((1 - cfg.disk.free_gb / Math.max(cfg.disk.total_gb, 1)) * 100))}%"></div></div>
      <label>Nicht gepostete Clips automatisch löschen nach
        <select id="ret">${[[3, "3 Tagen"], [7, "7 Tagen (empfohlen)"], [14, "14 Tagen"], [30, "30 Tagen"], [0, "nie"]].map(([v, l]) => `<option value="${v}" ${cfg.clip_retention_days == v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <p class="muted small" style="margin:0">Gepostete Clips bleiben immer. Rohmaterial wird nach 3 Tagen gelöscht, fehlgeschlagene Clips nach 2 Tagen. Wird der Speicher knapp, gehen die ältesten nicht geposteten Clips zuerst.</p>
      <div class="btns"><button class="btn" id="ret-save">Speichern</button><button class="btn ghost" id="clean-now">Jetzt aufräumen</button></div>
    </div>
    <h2>App-Update</h2>
    <div class="card stack" id="upd-card">
      <div class="small">Version: <b>${esc(upd.version || "unbekannt")}</b></div>
      ${upd.message ? `<div class="small ${upd.state === "error" ? "err" : "muted"}">${esc(upd.message)}${upd.at ? " · " + fmtTime(upd.at) : ""}</div>` : ""}
      <button class="btn" id="upd-go" ${upd.requested || upd.state === "running" ? "disabled" : ""}>${upd.requested ? "Update angefordert …" : upd.state === "running" ? "Update läuft …" : "Nach Updates suchen & installieren"}</button>
      <p class="muted small" style="margin:0">Updates kommen außerdem jede Nacht automatisch. Während der Installation ist die App 2–5 Minuten nicht erreichbar.</p>
    </div>
    <button class="btn ghost" id="logout" style="width:100%;margin-top:8px">Abmelden</button>`;
  $("#h-save").addEventListener("click", async () => {
    try {
      await api("/api/settings", { method: "PUT", body: { hype_factor: Number($("#h-factor").value), hype_min_rate: Number($("#h-min").value),
        hype_cooldown: Number($("#h-cool").value), hype_max_per_hour: Number($("#h-max").value) } });
      toast("Gespeichert – gilt sofort");
    } catch (e) { toast(e.message); }
  });
  $("#ret-save").addEventListener("click", async () => {
    try { await api("/api/settings", { method: "PUT", body: { clip_retention_days: Number($("#ret").value) } }); toast("Gespeichert"); }
    catch (e) { toast(e.message); }
  });
  $("#clean-now").addEventListener("click", async () => {
    try {
      const r = await api("/api/cleanup", { method: "POST" });
      toast(`Aufgeräumt: ${r.removed.clips} Clips, ${r.removed.raw} Rohdateien · ${r.disk.free_gb} GB frei`, 4000);
      renderStreamers();
    } catch (e) { toast(e.message); }
  });
  setupPush(cfg.push_key);
  $("#upd-go").addEventListener("click", async () => {
    try {
      await api("/api/update", { method: "POST" });
      toast("Update angefordert – startet innerhalb einer Minute", 4000);
      renderStreamers();
    } catch (e) { toast(e.message); }
  });
  $("#set-save").addEventListener("click", async () => {
    const body = { twitch_client_id: $("#set-tid").value.trim() || null, twitch_client_secret: $("#set-tsec").value.trim() || null,
      anthropic_api_key: $("#set-akey").value.trim() || null, new_password: $("#set-pw").value || null };
    try { await api("/api/settings", { method: "PUT", body }); toast("Gespeichert"); renderStreamers(); }
    catch (e) { toast(e.message, 5000); }
  });
  $("#s-add").addEventListener("click", async () => {
    try {
      await api("/api/streamers", { method: "POST", body: { platform: $("#s-platform").value, channel: $("#s-channel").value,
        campaign_id: $("#s-camp").value ? Number($("#s-camp").value) : null } });
      api("/api/poll", { method: "POST" }).catch(() => {});
      toast("Hinzugefügt"); renderStreamers();
    } catch (e) { toast(e.message); }
  });
  $("#sched-refresh").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Lädt …";
    await getSchedule(true); renderStreamers();
  });
  view.querySelectorAll("[data-s]").forEach((el) => {
    const id = el.dataset.s;
    $("[data-active]", el).addEventListener("change", (e) => api(`/api/streamers/${id}`, { method: "PATCH", body: { active: e.target.checked } }).then(() => toast("Gespeichert")));
    $("[data-camp]", el).addEventListener("change", (e) => api(`/api/streamers/${id}`, { method: "PATCH", body: { campaign_id: e.target.value ? Number(e.target.value) : null } }).then(() => toast("Gespeichert")));
    $("[data-del]", el).addEventListener("click", async () => {
      if (!confirm("Streamer entfernen?")) return;
      await api(`/api/streamers/${id}`, { method: "DELETE" }); renderStreamers();
    });
  });
  $("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); showLogin(); });
}

/* ---------- Zeitplan ---------- */
let schedCache = { at: 0, data: [] };
async function getSchedule(refresh = false) {
  if (!refresh && Date.now() - schedCache.at < 5 * 60 * 1000) return schedCache.data;
  schedCache = { at: Date.now(), data: await api("/api/schedule" + (refresh ? "?refresh=1" : "")).catch(() => []) };
  return schedCache.data;
}
const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
function fmtNext(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  const day = d.toDateString() === now.toDateString() ? "heute" : d.toDateString() === tomorrow.toDateString() ? "morgen" : DAYS[(d.getDay() + 6) % 7];
  return `${day} ${d.getHours()} Uhr`;
}
function heatmapHtml(heat) {
  const nowD = new Date(), today = (nowD.getDay() + 6) % 7, hour = nowD.getHours();
  let html = '<div class="heat"><span></span>' + Array.from({ length: 24 }, (_, h) => `<span class="hl">${h % 6 === 0 ? h : ""}</span>`).join("");
  heat.forEach((row, d) => {
    html += `<span class="dl">${DAYS[d]}</span>` + row.map((v, h) =>
      `<span class="hc${d === today && h === hour ? " now" : ""}" style="background:rgba(255,61,139,${v ? 0.15 + v * 0.85 : 0.06})" title="${DAYS[d]} ${h} Uhr: ${Math.round(v * 100)}%"></span>`).join("");
  });
  return html + "</div>";
}

/* ---------- Push-Benachrichtigungen ---------- */
function urlB64ToUint8(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((ch) => ch.charCodeAt(0)));
}

async function setupPush(key) {
  const state = $("#push-state");
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (!supported) {
    state.innerHTML = isIOS && !standalone
      ? "Auf dem iPhone geht das nur, wenn die App auf dem <b>Home-Bildschirm</b> liegt (Teilen → „Zum Home-Bildschirm“) und von dort geöffnet wird."
      : "Dieser Browser unterstützt keine Benachrichtigungen.";
    ["#push-on", "#push-test", "#push-off"].forEach((s) => $(s).classList.add("hidden"));
    return;
  }
  const reg = await navigator.serviceWorker.ready;
  const refresh = async () => {
    const sub = await reg.pushManager.getSubscription();
    state.innerHTML = sub ? '<span class="pill ok">Aktiv auf diesem Handy</span>' :
      Notification.permission === "denied" ? '<span class="pill bad">Blockiert</span> – in den Handy-Einstellungen für diese App erlauben.' : '<span class="pill">Aus</span>';
    $("#push-on").classList.toggle("hidden", !!sub);
    $("#push-off").classList.toggle("hidden", !sub);
    $("#push-test").classList.toggle("hidden", !sub);
  };
  $("#push-on").addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") { toast("Benachrichtigungen nicht erlaubt"); return refresh(); }
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(key) });
      await api("/api/push/subscribe", { method: "POST", body: sub.toJSON() });
      toast("Benachrichtigungen aktiv");
    } catch (e) { toast("Fehler: " + e.message, 5000); }
    refresh();
  });
  $("#push-off").addEventListener("click", async () => {
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api("/api/push/unsubscribe", { method: "POST", body: sub.toJSON() }).catch(() => {});
      await sub.unsubscribe();
    }
    refresh();
  });
  $("#push-test").addEventListener("click", async () => {
    try { const r = await api("/api/push/test", { method: "POST" }); toast(r.sent ? "Test gesendet" : "Kein Gerät erreicht – bitte neu aktivieren"); }
    catch (e) { toast(e.message); }
  });
  refresh();
}

/* ---------- Start ---------- */
(async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    await api("/api/status");
    $("#app").classList.remove("hidden");
    const m = location.hash.match(/^#clip-(\d+)$/);
    if (m) { openClipId = Number(m[1]); history.replaceState(null, "", "/"); show("clips"); } else show("live");
  } catch {
    showLogin();
  }
})();
