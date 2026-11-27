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
async function renderLive() {
  const s = await api("/api/status");
  const live = s.streamers.filter((x) => x.live);
  const off = s.streamers.filter((x) => !x.live);
  const c = s.clips || {};
  view.innerHTML = `
    ${s.warnings.map((w) => `<div class="card warn-box">${esc(w)}</div>`).join("")}
    <div class="stats">
      <div class="stat"><b>${live.length}</b><span>live</span></div>
      <div class="stat"><b>${(c.queued || 0) + (c.cutting || 0)}</b><span>in Arbeit</span></div>
      <div class="stat"><b>${c.ready || 0}</b><span>Clips fertig</span></div>
    </div>
    <h2>Gerade live</h2>
    ${live.length ? live.map(liveCard).join("") : `<div class="card muted">Niemand von deiner Liste ist live. ${s.streamers.length ? "" : "Füge unter <b>Streamer</b> Kanäle hinzu."}</div>`}
    ${off.length ? `<h2>Offline</h2><div class="card stack">${off.map((x) => `<div class="row"><span class="dot"></span><span class="grow ellipsis">${esc(x.display_name || x.channel)}</span><span class="pill">${x.platform}</span>${x.active ? "" : '<span class="pill warn">pausiert</span>'}</div>`).join("")}</div>` : ""}
    <h2>Letzte Hype-Momente</h2>
    ${s.events.length ? `<div class="card stack">${s.events.map((e) => `<div class="row between small"><span>${esc(e.channel || "?")} · ${fmtTime(e.ts)}</span><span class="pill ${e.score ? "ok" : ""}">${e.keywords?.[0] === "manuell" ? "manuell" : e.rate + "/s (normal " + e.baseline + ")"}</span></div>`).join("")}</div>` : '<div class="card muted small">Noch keine.</div>'}
    <p class="muted small">Letzte Abfrage: ${fmtTime(s.last_poll) || "–"}</p>`;
  view.querySelectorAll("[data-clipnow]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api(`/api/streamers/${b.dataset.clipnow}/clip-now`, { method: "POST" });
        toast("Clip wird erstellt – dauert ca. 2 Minuten.");
      } catch (err) { toast(err.message); }
      b.disabled = false;
    })
  );
  timer = setInterval(() => tab === "live" && !document.hidden && renderLive().catch(() => {}), 5000);
}

function liveCard(x) {
  const factor = x.baseline > 0 ? x.rate / x.baseline : 0;
  const pct = Math.min(100, (factor / 3) * 100);
  return `<div class="card">
    <div class="row"><span class="dot live"></span><b class="grow ellipsis">${esc(x.display_name || x.channel)}</b><span class="pill">${x.viewers ? x.viewers.toLocaleString("de-DE") + " Zuschauer" : x.platform}</span></div>
    <div class="muted small ellipsis" style="margin-top:4px">${esc(x.game)}${x.game && x.title ? " · " : ""}${esc(x.title)}</div>
    <div class="row between small" style="margin-top:8px"><span>Chat: <b>${x.rate}/s</b> <span class="muted">(normal ${x.baseline})</span></span><span class="pill ${factor >= 3 ? "bad" : factor >= 2 ? "warn" : ""}">${factor >= 3 ? "HYPE" : factor.toFixed(1) + "×"}</span></div>
    <div class="meter"><div style="width:${pct}%"></div></div>
    <div class="btns" style="margin-top:10px"><button class="btn small" data-clipnow="${x.id}">Jetzt clippen</button></div>
  </div>`;
}

/* ---------- Clips ---------- */
let clipFilter = "";
async function renderClips() {
  const clips = await api("/api/clips" + (clipFilter ? `?status=${clipFilter}` : ""));
  campaigns = await api("/api/campaigns");
  const busy = clips.some((c) => c.status === "queued" || c.status === "cutting");
  const filters = [["", "Alle"], ["ready", "Fertig"], ["cutting", "Schneidet"], ["queued", "Wartet"], ["failed", "Fehler"]];
  view.innerHTML = `
    <div class="seg">${filters.map(([v, l]) => `<button class="btn small ${clipFilter === v ? "active" : ""}" data-filter="${v}">${l}</button>`).join("")}</div>
    <button class="btn ghost" id="manual" style="width:100%;margin-bottom:12px">+ Clip aus Link (VOD / Clip)</button>
    ${clips.length ? clips.map(clipMini).join("") : '<div class="card muted">Noch keine Clips. Sobald ein Chat eskaliert, erscheint hier automatisch ein Clip.</div>'}`;
  view.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { clipFilter = b.dataset.filter; renderClips(); }));
  view.querySelectorAll("[data-clip]").forEach((el) => el.addEventListener("click", () => openClip(clips.find((c) => c.id == el.dataset.clip))));
  $("#manual").addEventListener("click", manualClipSheet);
  if (busy) timer = setInterval(() => tab === "clips" && !document.hidden && renderClips().catch(() => {}), 6000);
}

const STATUS = { ready: ["Fertig", "ok"], cutting: ["Schneidet …", "warn"], queued: ["Wartet …", "warn"], failed: ["Fehler", "bad"] };
function clipMini(c) {
  const [label, cls] = STATUS[c.status] || [c.status, ""];
  return `<div class="card clip-mini" data-clip="${c.id}">
    ${c.thumb_url ? `<img src="${c.thumb_url}" alt="" loading="lazy">` : `<div class="ph">${c.status === "failed" ? "✗" : "…"}</div>`}
    <div class="stack" style="gap:4px;min-width:0">
      <div class="row between"><b class="ellipsis">${esc(c.display_name || c.channel || "Clip")}</b><span class="pill ${cls}">${label}</span></div>
      <div class="small ellipsis">${esc(c.hook || "")}</div>
      <div class="muted small">${fmtTime(c.created_at)}${c.duration ? " · " + Math.round(c.duration) + "s" : ""}${c.campaign_name ? " · " + esc(c.campaign_name) : ""}${c.post_count ? " · gepostet" : ""}</div>
      ${c.error ? `<div class="small" style="color:${c.status === "failed" ? "var(--red)" : "var(--orange)"}">${esc(c.error)}</div>` : ""}
    </div></div>`;
}

function campaignOptions(selected) {
  return `<option value="">– keine –</option>` + campaigns.map((k) => `<option value="${k.id}" ${k.id == selected ? "selected" : ""}>${esc(k.name)}${k.clips_allowed ? "" : " (keine Clips erlaubt)"}</option>`).join("");
}

function openClip(c) {
  openSheet(`
    <div class="clip card" style="margin:-4px 0 12px">
      ${c.video_url ? `<video src="${c.video_url}" controls playsinline preload="metadata" poster="${c.thumb_url}"></video>` : ""}
    </div>
    <div class="stack">
      <div class="row between"><b>${esc(c.display_name || c.channel || "Clip")}</b><span class="pill ${(STATUS[c.status] || [])[1] || ""}">${(STATUS[c.status] || [c.status])[0]}</span></div>
      ${c.error ? `<div class="card warn-box small" style="margin:0">${esc(c.error)}</div>` : ""}
      <label>Hook (Text oben im Clip – leer lassen = kein Text)<input id="c-hook" value="${esc(c.hook)}" maxlength="80"></label>
      <label>Layout<select id="c-layout"><option value="blur" ${c.layout !== "crop" ? "selected" : ""}>Ganzes Bild + unscharfer Hintergrund</option><option value="crop" ${c.layout === "crop" ? "selected" : ""}>Mitte zuschneiden (Vollbild)</option></select></label>
      <label>Kampagne<select id="c-camp">${campaignOptions(c.campaign_id)}</select></label>
      <button class="btn" id="c-rerender">Mit neuem Hook / Layout neu schneiden</button>
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
async function renderCampaigns() {
  campaigns = await api("/api/campaigns");
  view.innerHTML = `
    <div class="btns" style="margin-bottom:12px"><button class="btn primary" id="k-new">+ Kampagne</button><button class="btn ghost" id="k-import">kampagnen.json importieren</button></div>
    <input type="file" id="k-file" accept=".json,application/json" class="hidden">
    ${campaigns.length ? campaigns.map((k) => `<div class="card" data-camp="${k.id}">
      <div class="row between"><b class="ellipsis">${esc(k.name)}</b>${k.clips_allowed ? '<span class="pill ok">Clips erlaubt</span>' : '<span class="pill bad">keine Clips</span>'}</div>
      <div class="muted small" style="margin-top:4px">${esc(k.streamer)} · <b style="color:var(--text)">${eur(k.rate_per_1k)}</b> / 1.000 Aufrufe${k.min_views ? " · ab " + k.min_views.toLocaleString("de-DE") + " Aufrufen" : ""}${k.platforms.length ? " · " + k.platforms.join(", ") : ""}</div>
      ${k.required_tags.length || k.required_mention ? `<div class="small" style="margin-top:6px">Pflicht: ${esc([...k.required_tags, k.required_mention].filter(Boolean).join(" "))}</div>` : ""}
    </div>`).join("") : '<div class="card muted">Noch keine Kampagnen. Lege sie an oder importiere die <b>kampagnen.json</b> vom Laptop.</div>'}`;
  $("#k-new").addEventListener("click", () => campaignSheet());
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

function campaignSheet(k = {}) {
  const v = (x, d = "") => esc(x ?? d);
  openSheet(`<div class="stack">
    <h2>${k.id ? "Kampagne bearbeiten" : "Neue Kampagne"}</h2>
    <label>Name<input id="k-name" value="${v(k.name)}"></label>
    <label>Streamer<input id="k-streamer" value="${v(k.streamer)}"></label>
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

/* ---------- Tracker ---------- */
async function renderTracker() {
  const t = await api("/api/tracker");
  view.innerHTML = `
    <div class="stats">
      <div class="stat"><b>${t.total_views.toLocaleString("de-DE")}</b><span>Aufrufe</span></div>
      <div class="stat"><b>${eur(t.total_estimate)}</b><span>geschätzt</span></div>
      <div class="stat"><b>${eur(t.total_paid)}</b><span>ausgezahlt</span></div>
    </div>
    <a class="btn ghost small" href="/api/tracker.csv" style="display:block;text-align:center;text-decoration:none;margin-bottom:12px">Als Tabelle (CSV) herunterladen</a>
    ${t.posts.length ? t.posts.map((p) => `<div class="card stack" data-post="${p.id}">
      <div class="row between"><b class="ellipsis">${esc(p.channel || "Clip")} · ${esc(p.platform)}</b><span class="muted small">${fmtTime(p.posted_at)}</span></div>
      <div class="small muted ellipsis">${esc(p.campaign_name || "keine Kampagne")}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener" style="color:var(--pink)">Post öffnen</a>` : ""}</div>
      <div class="row"><label class="grow">Aufrufe<input type="number" inputmode="numeric" data-views value="${p.views || 0}"></label><label class="grow">Ausgezahlt $<input type="number" step="0.01" inputmode="decimal" data-paid value="${p.paid ?? ""}"></label></div>
      <div class="row between small"><span>Schätzung: <b>${eur(p.estimate)}</b></span><button class="btn small" data-save>Speichern</button></div>
    </div>`).join("") : '<div class="card muted">Noch nichts gepostet. Trag Clips nach dem Posten über „Regeln prüfen & als gepostet eintragen“ ein.</div>'}`;
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
  let cfg;
  [streamers, campaigns, cfg] = await Promise.all([api("/api/streamers"), api("/api/campaigns"), api("/api/settings")]);
  view.innerHTML = `
    <div class="card stack">
      <b>Streamer hinzufügen</b>
      <div class="row"><select id="s-platform" style="width:auto"><option value="twitch">Twitch</option><option value="youtube">YouTube</option></select><input id="s-channel" class="grow" placeholder="Kanalname, z. B. skilmasktimmy" autocapitalize="off"></div>
      <label>Kampagne<select id="s-camp">${campaignOptions()}</select></label>
      <button class="btn primary" id="s-add">Hinzufügen</button>
      <p class="muted small" style="margin:0">Nur Streamer, die Clips erlauben. Bei YouTube den @Handle eintragen.</p>
    </div>
    ${streamers.map((s) => `<div class="card stack" data-s="${s.id}">
      <div class="row between"><b class="ellipsis">${esc(s.display_name || s.channel)}</b><span class="pill">${s.platform}</span></div>
      <label class="check"><input type="checkbox" data-active ${s.active ? "checked" : ""}> beobachten</label>
      <label>Kampagne<select data-camp>${campaignOptions(s.campaign_id)}</select></label>
      <button class="btn danger small" data-del>Entfernen</button>
    </div>`).join("")}
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
    <button class="btn ghost" id="logout" style="width:100%;margin-top:8px">Abmelden</button>`;
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

/* ---------- Start ---------- */
(async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    await api("/api/status");
    $("#app").classList.remove("hidden");
    show("live");
  } catch {
    showLogin();
  }
})();
