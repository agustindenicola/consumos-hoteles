/* Consumos Hoteles – interfaz organizada por hotel */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const main = $("#main");
let META = null;
const STATE = { year: null };
const charts = [];

// ---------------------------------------------------------------- utilidades
const nf = (v, d = 0) => v == null || v === "" || Number.isNaN(v) ? "—" :
  Number(v).toLocaleString("es-AR", { minimumFractionDigits: d, maximumFractionDigits: d });
const money = v => v == null ? "—" : "$ " + nf(v, 0);
const pct = v => v == null ? "—" : (v > 0 ? "+" : "") + nf(v, 1) + " %";
const perDec = svc => svc === "electricidad" ? 2 : 3;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fdate = s => s ? s.split("-").reverse().join("/") : "—";
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const svcColor = svc => css("--" + svc);
const unit = svc => META.services[svc].unit;
const svcLabel = svc => META.services[svc].label;
const SVCS = () => Object.keys(META.services);
const SVC_ICON = { agua: "💧", electricidad: "⚡", gas: "🔥" };
const HOTEL_MARKERS = ["circle", "rect", "triangle", "rectRot"];
const hotelIdx = id => META.hotels.findIndex(h => h.id == id);
const hotelColor = id => css("--hotel" + ((Math.max(0, hotelIdx(id)) % 4) + 1));
const hotelById = id => META.hotels.find(h => h.id == id);
const initials = name => name.replace(/^Up\s+/i, "").split(/\s+/).filter(w => w.length > 2 || /^[A-Z]/.test(w)).slice(0, 2).map(w => w[0]).join("").toUpperCase();

// Aviso abajo a la derecha. Con `action` muestra un botón (p. ej. "Deshacer").
function toast(msg, ms = 3000, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.onclick = () => { t.classList.remove("show"); action.fn(); };
    t.appendChild(b);
    ms = Math.max(ms, 8000);
  }
  t.classList.toggle("has-action", !!action);
  t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
}

// Pregunta con botones claros (en vez del cartel feo del navegador). Devuelve true/false.
function ask(msg, { title = "¿Seguro?", ok = "Sí", cancel = "Cancelar", danger = false } = {}) {
  return new Promise(resolve => {
    const d = $("#ask");
    d.innerHTML = `<h2>${esc(title)}</h2><p class="ask-msg">${esc(msg).replace(/\n/g, "<br>")}</p>
      <div class="actions"><button class="btn" data-a="no">${esc(cancel)}</button><button class="btn ${danger ? "danger-fill" : "primary"}" data-a="si">${esc(ok)}</button></div>`;
    const done = v => { d.close(); resolve(v); };
    $("[data-a=si]", d).onclick = () => done(true);
    $("[data-a=no]", d).onclick = () => done(false);
    d.oncancel = e => { e.preventDefault(); done(false); };
    d.showModal();
    $("[data-a=si]", d).focus();
  });
}

async function api(url, opts = {}) {
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers = { "Content-Type": "application/json" };
    delete opts.json;
  }
  const r = await fetch(url, opts);
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (r.status === 422 && data?.confirm) { const e = new Error(data.confirm); e.confirm = data.confirm; throw e; }
  if (!r.ok) throw new Error(data?.error || `Error ${r.status}`);
  return data;
}

// Guarda; si el servidor ve un número raro, pregunta "¿Seguro?" y recién ahí guarda.
// `send(confirm)` hace el pedido. Devuelve null si ella eligió corregir.
async function withConfirm(send) {
  try { return await send(false); }
  catch (e) {
    if (!e.confirm) throw e;
    const ok = await ask(e.confirm, { title: "Revisá este dato", ok: "Está bien, guardar", cancel: "Corregir" });
    return ok ? await send(true) : null;
  }
}
function destroyCharts() { while (charts.length) { try { charts.pop().destroy(); } catch (_) {} } }

// Cualquier error inesperado: aviso amable + queda registrado en datos/errores.log
function reportClientError(msg) {
  try { fetch("/api/clientlog", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ msg, url: location.hash }) }); } catch (_) {}
}
window.addEventListener("error", e => { reportClientError(String(e.message) + " @ " + (e.filename || "") + ":" + (e.lineno || "")); toast("Algo no salió bien. Probá de nuevo; si sigue pasando, reiniciá la aplicación.", 5000); });
window.addEventListener("unhandledrejection", e => {
  const m = e.reason && e.reason.message ? e.reason.message : String(e.reason);
  if (/Failed to fetch|NetworkError/i.test(m)) toast("No hay conexión con la aplicación. ¿Se cerró la ventana negra? Volvé a abrir ConsumosHoteles.exe.", 7000);
  else { reportClientError(m); toast(m, 5000); }
});

// Descarga de archivos con aviso si algo falla (en vez de mostrar una página de error)
async function download(url, btn, body) {
  const label = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "Generando…"; }
  try {
    const r = await fetch(url, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
    if (!r.ok) {
      let msg = `Error ${r.status}`;
      try { msg = (await r.json()).error || msg; } catch (_) {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
    const name = m ? decodeURIComponent(m[1]) : "archivo";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("Descargado: " + name);
  } catch (e) {
    toast(/Failed to fetch/i.test(e.message) ? "No hay conexión con la aplicación." : e.message, 6000);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = label; }
  }
}
document.addEventListener("click", e => {
  const el = e.target.closest("[data-dl]");
  if (!el) return;
  e.preventDefault();
  download(el.dataset.dl, el);
});

// Números pegados desde Excel: "3.639", "3,639", "3639", "1.234,5"
function parseNumber(v) {
  let t = String(v ?? "").trim().replace(/\s|\$/g, "");
  if (t === "") return null;
  if (/^-?\d{1,3}([.,]\d{3})+$/.test(t)) t = t.replace(/[.,]/g, "");
  else if (/^-?[\d.]+,\d+$/.test(t)) t = t.replace(/\./g, "").replace(",", ".");
  else t = t.replace(",", ".");
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

// Evita el doble clic en botones de guardar
async function busy(btn, fn) {
  if (btn.disabled) return;
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; }
}

function yearSelect(id = "year") {
  return `<label>Año<select id="${id}">${META.years.map(y => `<option ${y == STATE.year ? "selected" : ""}>${y}</option>`).join("")}</select></label>`;
}
function bindYear(rerender, id = "year") { $("#" + id).onchange = e => { STATE.year = +e.target.value; rerender(); }; }
function hotelSelect(id, all = false, sel) {
  return `<label>Hotel<select id="${id}">${all ? `<option value="">Todos</option>` : ""}${META.hotels.map(h => `<option value="${h.id}" ${h.id == sel ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select></label>`;
}
function serviceSelect(id, all = false, sel) {
  return `<label>Servicio<select id="${id}">${all ? `<option value="">Todos</option>` : ""}${SVCS().map(k => `<option value="${k}" ${k == sel ? "selected" : ""}>${svcLabel(k)}</option>`).join("")}</select></label>`;
}
function statusPill(value, ref) {
  if (value == null) return `<span class="pill neutral">sin datos</span>`;
  if (!ref) return `<span class="pill neutral">sin referencia</span>`;
  return value > ref ? `<span class="pill alto">▲ ${pct((value / ref - 1) * 100)} sobre ref.</span>` : `<span class="pill ok">✓ dentro de ref.</span>`;
}

async function loadMeta() {
  META = await api("/api/meta");
  if (!STATE.year) STATE.year = META.years.find(y => y <= new Date().getFullYear()) || META.years[0];
  $("#nav-hotels").innerHTML = META.hotels.map(h =>
    `<button data-route="hotel/${h.id}"><span class="hdot" style="background:${hotelColor(h.id)}"></span>${esc(h.name)}</button>`).join("");
  $$("#nav-hotels button").forEach(b => b.onclick = () => nav(b.dataset.route));
  $("#foot").innerHTML = `<b>${esc(META.settings.company || "")}</b><br>${nf(META.counts.invoices)} facturas cargadas`;
  markNav();
  refreshTodo();
  refreshAlerts();
}

// Contador de facturas que esperan revisión (al lado de "Cargar facturas")
async function refreshTodo() {
  try {
    const t = await api("/api/todo");
    STATE.todo = t;
    const n = t.inbox.length + t.review.length;
    const b = $("#todo-badge");
    b.textContent = n; b.hidden = !n;
    b.title = n ? `${n} ${n === 1 ? "factura espera" : "facturas esperan"} que la revises` : "";
    return t;
  } catch (_) { return null; }
}

// ---------------------------------------------------------------- navegación (#ruta)
function nav(route) { if (location.hash.slice(1) === route) render(); else location.hash = route; }
function markNav() {
  const r = location.hash.slice(1) || "inicio";
  $$("nav.side button").forEach(b => b.classList.toggle("active", r === b.dataset.route || r.startsWith(b.dataset.route + "/")));
}
$$("nav.side > button").forEach(b => b.onclick = () => nav(b.dataset.route));
window.addEventListener("hashchange", render);

async function render() {
  destroyCharts();
  markNav();
  const parts = (location.hash.slice(1) || "inicio").split("/");
  const view = parts[0];
  main.innerHTML = `<div class="empty">Cargando…</div>`;
  try {
    if (view === "hotel") await viewHotel(+parts[1], parts[2] || "resumen");
    else await (VIEWS[view] || VIEWS.inicio)();
  } catch (e) { main.innerHTML = `<div class="notice warn">Error: ${esc(e.message)}</div>`; console.error(e); }
  window.scrollTo(0, 0);
}
const VIEWS = {};

// ---------------------------------------------------------------- gráficos
Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
Chart.defaults.font.size = 12;
function baseOpts(yTitle, decimals = 0) {
  const grid = css("--grid"), ink = css("--text-2");
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false },
      tooltip: { backgroundColor: css("--tip-bg"), padding: 10, cornerRadius: 8,
        callbacks: { label: c => ` ${c.dataset.label}: ${c.raw == null ? "—" : nf(c.raw, decimals)}` } } },
    scales: {
      x: { grid: { display: false }, ticks: { color: ink }, border: { color: grid } },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false },
           ticks: { color: ink, callback: v => nf(v, decimals && v < 10 ? 2 : 0) }, title: { display: !!yTitle, text: yTitle, color: ink } },
    },
  };
}
const mkChart = (canvas, cfg) => { const c = new Chart(canvas, cfg); charts.push(c); return c; };
const legendHtml = items => `<div class="legend">${items.map(([label, color, kind]) =>
  `<span><i class="${kind || ""}" style="${kind === "dash" ? "" : `background:${color}`}"></i>${esc(label)}</span>`).join("")}</div>`;
const barDs = (label, data, color) => ({ label, data, backgroundColor: color, borderRadius: 4, categoryPercentage: .72, barPercentage: .92 });

function consumptionChart(canvas, rows, prev, svc, year) {
  const color = svcColor(svc), ds = [];
  if (prev.some(r => r.consumption)) ds.push(barDs(String(year - 1), prev.map(r => r.consumption), css("--prev")));
  ds.push(barDs(String(year), rows.map(r => r.consumption), rows.map(r => r.consumption != null && !r.complete ? color + "66" : color)));
  return mkChart(canvas, { type: "bar", data: { labels: META.months_short, datasets: ds }, options: baseOpts(unit(svc)) });
}
function perGuestChart(canvas, rows, prev, svc, year, ref) {
  const color = svcColor(svc), crit = css("--crit"), surface = css("--surface");
  const val = r => r.complete && r.per_guest != null ? r.per_guest : null;
  const ds = [];
  if (prev.some(val)) ds.push({ label: String(year - 1), data: prev.map(val), borderColor: css("--prev"), backgroundColor: css("--prev"), borderWidth: 2, pointRadius: 3 });
  ds.push({ label: String(year), data: rows.map(val), borderColor: color, backgroundColor: color, borderWidth: 2,
    pointRadius: rows.map(r => ref && val(r) > ref ? 6 : 4), pointBackgroundColor: rows.map(r => ref && val(r) > ref ? crit : color),
    pointBorderColor: surface, pointBorderWidth: 2 });
  if (ref) ds.push({ label: "Referencia", data: rows.map(() => ref), borderColor: css("--text-2"), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 });
  return mkChart(canvas, { type: "line", data: { labels: META.months_short, datasets: ds }, options: baseOpts(`${unit(svc)} por huésped`, perDec(svc)) });
}
function guestsChart(canvas, rows, prev, year, color) {
  const ds = [];
  if (prev.some(r => r.guests)) ds.push(barDs(String(year - 1), prev.map(r => r.guests), css("--prev")));
  ds.push(barDs(String(year), rows.map(r => r.guests), color || css("--guests")));
  return mkChart(canvas, { type: "bar", data: { labels: META.months_short, datasets: ds }, options: baseOpts("huéspedes") });
}
function sparkline(canvas, values, color, ref) {
  const ds = [{ data: values, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, tension: .3 }];
  if (ref) ds.push({ data: values.map(() => ref), borderColor: css("--muted"), borderDash: [3, 3], borderWidth: 1, pointRadius: 0 });
  return mkChart(canvas, { type: "line", data: { labels: META.months_short, datasets: ds },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, grace: "15%" } } } });
}

// ---------------------------------------------------------------- INICIO
VIEWS.inicio = async () => {
  const [d, todo, al] = await Promise.all([api(`/api/dashboard?year=${STATE.year}`), refreshTodo(), api("/api/reminders")]);
  const waiting = todo ? todo.inbox.length + todo.review.length : 0;
  const noBackup = todo?.backup?.level === "warn";
  const h = new Date().getHours();
  const saludo = h < 13 ? "Buenos días" : h < 20 ? "Buenas tardes" : "Buenas noches";
  const name = (META.settings.user_name || "").trim();
  const today = new Date().toLocaleDateString("es-AR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const totalGuests = d.cards.reduce((a, c) => a + (c.guests || 0), 0);
  const totalCost = d.cards.reduce((a, c) => a + SVCS().reduce((b, s) => b + (c.services[s].summary.cost || 0), 0), 0);
  main.innerHTML = `
    <div class="hello">
      <div><div class="date">${today}</div><h1>${saludo}${name ? ", " + esc(name) : ""}</h1>
        <p class="lead" style="margin:4px 0 0">Así vienen tus hoteles en ${STATE.year}.</p></div>
      <div class="toolbar" style="margin:0">${yearSelect()}<button class="btn primary" id="toUpload">⇪ Cargar facturas</button></div>
    </div>
    ${waiting ? `<div class="notice line"><span>📥 ${waiting === 1 ? "Hay 1 factura esperando" : `Hay ${waiting} facturas esperando`} que la revises.</span><a class="btn sm" href="#cargar">Ver</a></div>` : ""}
    ${noBackup ? `<div class="notice line"><span>💾 Tus datos están guardados solo en esta computadora. Conviene activar la copia de seguridad.</span><a class="btn sm" href="#config/respaldo">Activar</a></div>` : ""}
    ${META.counts.invoices === 0 ? `<div class="notice">Todavía no hay facturas. Empezá por <a href="#cargar">Cargar facturas</a> (podés arrastrar la carpeta entera) y después cargá los <a href="#huespedes">huéspedes por mes</a>.</div>` : ""}
    <div class="stat-strip three">
      <div class="kpi"><div class="v">${nf(totalGuests)}</div><div class="l">Huéspedes en ${STATE.year}</div></div>
      <div class="kpi"><div class="v">${money(totalCost || null)}</div><div class="l">Gasto en servicios ${STATE.year}</div><div class="s">agua, luz y gas de los ${META.hotels.length} hoteles</div></div>
      <div class="kpi click" data-go="alertas"><div class="v" style="color:${al.overdue ? "var(--crit)" : al.active ? "var(--warn)" : "inherit"}">${al.active}</div><div class="l">Alertas para atender</div>
        <div class="s">${al.overdue ? `<b style="color:var(--crit)">${al.overdue} ${al.overdue === 1 ? "vencida" : "vencidas"}</b> · ` : ""}${al.open.length} ${al.open.length === 1 ? "programada" : "programadas"} en total</div></div>
    </div>
    <div class="grid c4">${d.cards.map(c => `
      <div class="card hcard" tabindex="0" role="link" data-hotel="${c.hotel_id}" style="--hc:${hotelColor(c.hotel_id)}">
        <div class="top"><div class="mono">${esc(initials(c.hotel))}</div>
          <div><div class="name">${esc(c.hotel)}</div><div class="sub">${nf(c.guests)} huéspedes</div></div></div>
        <div class="rows">${SVCS().map(svc => {
          const s = c.services[svc].summary, ref = s.reference;
          const over = ref && s.per_guest != null ? (s.per_guest / ref - 1) * 100 : null;
          const chip = over == null ? "" : over > 0 ? `<span class="chip alto">+${nf(over, 0)} %</span>` : `<span class="chip ok">✓</span>`;
          return `<div class="svc-row simple"><span class="dot" style="background:${svcColor(svc)}"></span><div class="name">${svcLabel(svc)}</div>
            <div class="val">${nf(s.per_guest, perDec(svc))} <span class="muted small">${unit(svc)}/h</span></div>${chip}</div>`;
        }).join("")}</div>
        <div class="go">Ver hotel →</div>
      </div>`).join("")}
    </div>
    <p class="small muted" style="margin:8px 2px 0">Consumo por huésped en ${STATE.year}. <span class="chip ok">✓</span> dentro de la referencia · <span class="chip alto">+%</span> cuánto se pasó.</p>
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>Tus alertas</h3><button class="btn sm primary" data-new-alert>+ Nueva alerta</button></div>
      ${reminderTable(al.open.filter(r => r.active).concat(al.open.filter(r => !r.active)).slice(0, 8), { emptyMsg: "No tenés alertas. Creá una para acordarte de un plazo." })}
      ${al.open.length > 8 ? `<p style="margin:12px 0 0"><a href="#alertas">Ver todas las alertas →</a></p>` : ""}</div>`;
  bindYear(render);
  $("#toUpload").onclick = () => nav("cargar");
  $$("[data-hotel]").forEach(el => {
    const go = () => nav("hotel/" + el.dataset.hotel);
    el.onclick = go; el.onkeydown = e => { if (e.key === "Enter") go(); };
  });
  $$("[data-goto]").forEach(el => el.onclick = () => nav(el.dataset.goto));
  $$("[data-go]").forEach(el => el.onclick = () => nav(el.dataset.go));
  bindReminders(main, render);
};

// ---------------------------------------------------------------- ALERTAS (las crea ella)
// Cada alerta tiene título, hotel (o general), fecha límite y desde cuántos días antes avisa (2 a 7).
function addDays(iso, n) { const d = new Date(iso + "T12:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); }
function todayIso() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); }
function reminderInfo(r) {
  if (r.done_at) return ["ok", "✓ Resuelta"];
  const d = r.days_left;
  if (d < 0) return ["alto", `Vencida hace ${-d} ${d === -1 ? "día" : "días"}`];
  if (d === 0) return ["alto", "Vence hoy"];
  if (r.active) return ["warn", `Faltan ${d} ${d === 1 ? "día" : "días"}`];
  return ["neutral", `Avisa desde el ${fdate(addDays(r.due_date, -r.notify_days))}`];
}
function reminderTable(rows, { done = false, withHotel = true, emptyMsg } = {}) {
  if (!rows.length) return `<div class="empty" style="padding:14px">${emptyMsg || (done ? "No hay alertas resueltas en los últimos 60 días" : "✓ No hay alertas pendientes")}</div>`;
  return `<div class="table-wrap"><table class="data"><tbody>${rows.map(r => {
    const [cls, txt] = reminderInfo(r);
    return `<tr class="click" data-edit-alert="${r.id}">
      <td><b>${esc(r.title)}</b>${r.notes ? `<div class="small muted" style="white-space:pre-line">${esc(r.notes.length > 140 ? r.notes.slice(0, 140) + "…" : r.notes)}</div>` : ""}</td>
      ${withHotel ? `<td class="nowrap small">${r.hotel_id ? `<span class="dot" style="background:${hotelColor(r.hotel_id)}"></span> ${esc(r.hotel)}` : `<span class="muted">General</span>`}</td>` : ""}
      <td class="nowrap small">${fdate(r.due_date)}</td><td class="nowrap"><span class="pill ${cls}">${txt}</span></td>
      <td class="n nowrap">${r.done_at ? `<button class="btn sm ghost" data-undone="${r.id}">Reabrir</button>`
        : `<button class="btn sm primary" data-done="${r.id}">✓ Resuelta</button>`}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}
function bindReminders(root, after) {
  const cache = {};
  $$("[data-new-alert]", root).forEach(b => b.onclick = () => reminderDialog({ hotel_id: b.dataset.newAlert || "" }, after));
  $$("[data-edit-alert]", root).forEach(tr => tr.onclick = async e => {
    if (e.target.closest("button")) return;
    if (!cache[tr.dataset.editAlert]) { const d = await api("/api/reminders"); [...d.open, ...d.done].forEach(r => cache[r.id] = r); }
    const r = cache[tr.dataset.editAlert];
    if (r) reminderDialog(r, after);
  });
  const setDone = async (id, done) => { await api(`/api/reminders/${id}/done`, { method: "POST", json: { done } }); refreshAlerts(); after?.(); };
  $$("[data-done]", root).forEach(b => b.onclick = () => busy(b, async () => {
    const id = b.dataset.done;
    try { await setDone(id, true); toast("Alerta resuelta", 7000, { label: "Deshacer", fn: () => setDone(id, false) }); }
    catch (e) { toast(e.message, 5000); }
  }));
  $$("[data-undone]", root).forEach(b => b.onclick = () => busy(b, async () => {
    try { await setDone(b.dataset.undone, false); } catch (e) { toast(e.message, 5000); }
  }));
}
function reminderDialog(r, after) {
  const isNew = !r || !r.id;
  const x = { title: "", hotel_id: "", due_date: addDays(todayIso(), 7), notify_days: 3, notes: "", ...(r || {}) };
  const dlg = $("#dlg");
  dlg.innerHTML = `<h2>${isNew ? "Nueva alerta" : "Editar alerta"}</h2>
    <div class="form-grid">
      <label class="full">Qué hay que hacer<input type="text" id="r_title" maxlength="200" placeholder="Ej: Reclamar a Edesur por el medidor" value="${esc(x.title)}"></label>
      <label>Hotel<select id="r_hotel"><option value="">General (todos)</option>${META.hotels.map(h => `<option value="${h.id}" ${h.id == x.hotel_id ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select></label>
      <label>Fecha límite<input type="date" id="r_due" value="${x.due_date}"></label>
      <label class="full">Avisarme desde
        <select id="r_days">${[2, 3, 4, 5, 6, 7].map(n => `<option value="${n}" ${n == x.notify_days ? "selected" : ""}>${n === 7 ? "1 semana" : n + " días"} antes</option>`).join("")}</select></label>
      <label class="full">Seguimiento <span class="muted">(opcional: qué pasó, con quién hablaste…)</span><textarea id="r_notes" rows="4">${esc(x.notes || "")}</textarea></label>
    </div>
    <div class="actions">${!isNew ? `<button class="btn danger" id="r_del" style="margin-right:auto">Eliminar</button>` : ""}
      <button class="btn" id="r_cancel">Cancelar</button><button class="btn primary" id="r_ok">Guardar</button></div>`;
  dlg.showModal();
  $("#r_title").focus();
  $("#r_cancel").onclick = () => dlg.close();
  $("#r_ok").onclick = e => busy(e.currentTarget, async () => {
    const body = { title: $("#r_title").value.trim(), hotel_id: $("#r_hotel").value || null, due_date: $("#r_due").value,
                   notify_days: +$("#r_days").value, notes: $("#r_notes").value };
    if (!body.title) return toast("Escribí qué hay que hacer");
    if (!body.due_date) return toast("Elegí la fecha límite");
    try {
      await api(isNew ? "/api/reminders" : `/api/reminders/${r.id}`, { method: isNew ? "POST" : "PUT", json: body });
      dlg.close(); toast(isNew ? "Alerta creada" : "Guardado"); refreshAlerts(); after?.();
    } catch (err) { toast(err.message, 6000); }
  });
  if (!isNew) $("#r_del").onclick = e => busy(e.currentTarget, async () => {
    dlg.close();
    if (!await ask(`Se borra la alerta “${x.title}”.`, { title: "¿Eliminar la alerta?", ok: "Eliminar", danger: true })) return;
    try {
      await api(`/api/reminders/${r.id}`, { method: "DELETE" }); refreshAlerts(); after?.();
      toast("Alerta eliminada", 8000, { label: "Deshacer", fn: async () => {
        await api("/api/reminders", { method: "POST", json: x }); refreshAlerts(); after?.();
      } });
    } catch (err) { toast(err.message, 5000); }
  });
}
async function refreshAlerts() {
  try {
    const d = await api("/api/reminders");
    const b = $("#alert-badge"); b.textContent = d.active; b.hidden = !d.active;
    b.title = d.active ? `${d.active} ${d.active === 1 ? "alerta para atender" : "alertas para atender"}` : "";
    return d;
  } catch (_) { return null; }
}
VIEWS.alertas = async () => {
  const d = await api("/api/reminders");
  const act = d.open.filter(r => r.active), later = d.open.filter(r => !r.active);
  main.innerHTML = `
    <div class="hello"><div><h1>Alertas</h1>
      <p class="lead" style="margin:4px 0 0">Tus recordatorios con fecha. Cada uno empieza a avisar los días antes que elijas.</p></div>
      <div class="toolbar" style="margin:0"><button class="btn primary" data-new-alert>+ Nueva alerta</button></div></div>
    <div class="stat-strip three">
      <div class="kpi"><div class="v" style="color:${d.active ? "var(--warn)" : "var(--good)"}">${d.active}</div><div class="l">Para atender ahora</div></div>
      <div class="kpi"><div class="v" style="color:${d.overdue ? "var(--crit)" : "var(--good)"}">${d.overdue}</div><div class="l">Vencidas</div></div>
      <div class="kpi"><div class="v">${later.length}</div><div class="l">Programadas más adelante</div></div>
    </div>
    <h2>Para atender</h2><div class="card">${reminderTable(act, { emptyMsg: "✓ Nada para atender por ahora" })}</div>
    <h2>Más adelante</h2><div class="card">${reminderTable(later, { emptyMsg: "No hay alertas programadas" })}</div>
    <h2>Resueltas</h2><div class="card">${reminderTable(d.done, { done: true })}</div>
    <p class="small muted">Tocá una alerta para cambiarla o anotar cómo sigue. Las resueltas quedan acá 60 días.</p>`;
  bindReminders(main, render);
};
VIEWS.vencimientos = async () => nav("alertas");

// ---------------------------------------------------------------- PÁGINA DE HOTEL
const HOTEL_TABS = [["resumen", "Resumen"], ["agua", "💧 Agua"], ["electricidad", "⚡ Electricidad"], ["gas", "🔥 Gas"],
                    ["huespedes", "Huéspedes"], ["facturas", "Facturas"], ["informe", "Informe"]];

async function viewHotel(id, tab) {
  const h = hotelById(id);
  if (!h) return nav("inicio");
  const hc = hotelColor(id);
  main.innerHTML = `
    <div class="hotel-hero" style="--hc:${hc}">
      <div class="hero-top"><div class="mono">${esc(initials(h.name))}</div>
        <div class="meta"><h1>${esc(h.name)}</h1><div class="sub">${esc([h.legal_name, h.address].filter(Boolean).join(" · ") || "Hotel")}</div></div>
        <div class="toolbar" style="margin:0">${tab === "facturas" ? "" : yearSelect()}</div></div>
      <div class="tabs" role="tablist">${HOTEL_TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" class="${k === tab ? "active" : ""}">${l}</button>`).join("")}</div>
    </div>
    <div id="hbody" style="--hc:${hc}"></div>`;
  $$("[data-tab]").forEach(b => b.onclick = () => nav(`hotel/${id}/${b.dataset.tab}`));
  if ($("#year")) bindYear(render);
  const body = $("#hbody");
  if (tab === "resumen") await hotelResumen(body, h);
  else if (SVCS().includes(tab)) await hotelService(body, h, tab);
  else if (tab === "huespedes") await hotelGuests(body, h);
  else if (tab === "facturas") await invoicesList(body, { hotel: id });
  else if (tab === "informe") hotelReport(body, h);
}

async function hotelResumen(body, h) {
  const y = STATE.year;
  const [dash, al, ...stats] = await Promise.all([api(`/api/dashboard?year=${y}`), api("/api/reminders"),
    ...SVCS().map(s => api(`/api/stats?hotel=${h.id}&service=${s}&year=${y}`))]);
  const card = dash.cards.find(c => c.hotel_id == h.id);
  const rows = stats[0].rows, prev = stats[0].prev_rows;
  const gv = rows.map(r => r.guests).filter(Boolean);
  const gPrev = prev.reduce((a, r) => a + (r.guests || 0), 0);
  const mine = al.open.filter(r => r.hotel_id == h.id);
  body.innerHTML = `
    <div class="grid c3">${SVCS().map((svc, i) => {
      const s = stats[i].summary, cmp = stats[i].compare;
      return `<div class="card scard" style="--sc:${svcColor(svc)}" data-svc="${svc}" tabindex="0" role="link">
        <div class="head"><span class="t">${SVC_ICON[svc]} ${svcLabel(svc)}</span>${statusPill(s.per_guest, s.reference)}</div>
        <div class="big">${nf(s.per_guest, perDec(svc))} <small>${unit(svc)} por huésped${s.reference ? ` · ref. ${nf(s.reference, perDec(svc))}` : ""}</small></div>
        <div class="spark"><canvas id="sp-${svc}"></canvas></div>
        <div class="facts">
          <div><span>Consumo total</span><b>${nf(s.total)} ${unit(svc)}</b></div>
          <div><span>Promedio mensual</span><b>${nf(s.avg)}</b></div>
          <div><span>Mínimo</span><b>${nf(s.min)}</b> <span style="display:inline">${s.min_month ? "· " + s.min_month.slice(0, 3) : ""}</span></div>
          <div><span>Máximo</span><b>${nf(s.max)}</b> <span style="display:inline">${s.max_month ? "· " + s.max_month.slice(0, 3) : ""}</span></div>
          <div><span>Costo facturado</span><b>${money(s.cost)}</b></div>
          <div><span>vs ${y - 1}</span><b>${cmp && cmp.change != null ? pct(cmp.change) : "—"}</b></div>
        </div></div>`;
    }).join("")}</div>
    <div class="grid c2" style="margin-top:16px">
      <div class="card"><h3>Huéspedes por mes</h3>
        <div class="stat-strip" style="margin-bottom:10px">
          <div><div class="bignum">${nf(card.guests)}</div><div class="small muted">total ${y}${gPrev ? ` · ${nf(gPrev)} en ${y - 1}` : ""}</div></div>
          <div><div class="bignum">${gv.length ? nf(gv.reduce((a, b) => a + b, 0) / gv.length) : "—"}</div><div class="small muted">promedio mensual</div></div>
          <div><div class="bignum">${gv.length ? nf(Math.min(...gv)) + " – " + nf(Math.max(...gv)) : "—"}</div><div class="small muted">mínimo – máximo</div></div>
        </div>
        ${legendHtml([...(gPrev ? [[String(y - 1), css("--prev")]] : []), [String(y), css("--hc") || hotelColor(h.id)]])}
        <div class="chart-box" style="height:230px"><canvas id="gch"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Alertas</h3><button class="btn sm primary" data-new-alert="${h.id}">+ Nueva alerta</button></div>
        ${reminderTable(mine, { withHotel: false, emptyMsg: "No hay alertas para este hotel" })}</div>
    </div>`;
  SVCS().forEach((svc, i) => sparkline($(`#sp-${svc}`), stats[i].rows.map(r => r.complete ? r.per_guest : null), svcColor(svc), stats[i].reference));
  guestsChart($("#gch"), rows, prev, y, hotelColor(h.id));
  $$("[data-svc]").forEach(el => { const go = () => nav(`hotel/${h.id}/${el.dataset.svc}`); el.onclick = go; el.onkeydown = e => { if (e.key === "Enter") go(); }; });
  bindReminders(body, render);
}

async function hotelService(body, h, svc) {
  const y = STATE.year;
  const d = await api(`/api/stats?hotel=${h.id}&service=${svc}&year=${y}`);
  const s = d.summary, ref = d.reference, u = unit(svc), dec = perDec(svc), c = d.compare;
  body.innerHTML = `
    <div class="stat-strip">
      <div class="kpi"><div class="v">${nf(s.total)} <small>${u}</small></div><div class="l">Consumo total ${y}</div><div class="s">${c && c.change != null ? pct(c.change) + ` vs ${y - 1} (${c.months} meses comparables)` : "&nbsp;"}</div></div>
      <div class="kpi"><div class="v">${nf(s.avg)}</div><div class="l">Promedio mensual</div><div class="s">${s.months_complete} meses completos</div></div>
      <div class="kpi"><div class="v">${nf(s.min)}</div><div class="l">Mínimo mensual</div><div class="s">${s.min_month || "&nbsp;"}</div></div>
      <div class="kpi"><div class="v">${nf(s.max)}</div><div class="l">Máximo mensual</div><div class="s">${s.max_month || "&nbsp;"}</div></div>
      <div class="kpi"><div class="v">${nf(s.per_guest, dec)}</div><div class="l">${u} por huésped${ref ? ` (ref. ${nf(ref, dec)})` : ""}</div><div class="s">${statusPill(s.per_guest, ref)}</div></div>
      <div class="kpi"><div class="v">${money(s.cost)}</div><div class="l">Costo facturado</div><div class="s">${s.cost_per_guest ? money(s.cost_per_guest) + " por huésped" : "&nbsp;"}</div></div>
    </div>
    <div class="grid c2">
      <div class="card"><h3>Consumo mensual (${u})</h3>${legendHtml([...(d.prev_rows.some(r => r.consumption) ? [[String(y - 1), css("--prev")]] : []), [String(y), svcColor(svc)], ["Mes parcial", svcColor(svc) + "66"]])}<div class="chart-box"><canvas id="c1"></canvas></div></div>
      <div class="card"><h3>Consumo por huésped</h3>${legendHtml([...(d.prev_rows.some(r => r.per_guest) ? [[String(y - 1), css("--prev"), "line"]] : []), [String(y), svcColor(svc), "line"], ...(ref ? [["Referencia " + nf(ref, dec), "", "dash"], ["Sobre referencia", css("--crit")]] : [])])}<div class="chart-box"><canvas id="c2"></canvas></div></div>
      <div class="card full"><h3>Detalle mes a mes</h3><div class="table-wrap"><table class="data"><thead><tr><th>Mes</th><th class="n">Consumo (${u})</th><th class="n">${y - 1}</th><th class="n">Huéspedes</th><th class="n">${u} por huésped</th><th class="n">Costo</th><th>Estado</th></tr></thead><tbody>
        ${d.rows.map((r, i) => {
          const st = r.consumption == null ? (r.guests ? "falta factura" : "") : !r.complete ? "parcial" : r.guests == null ? "faltan huéspedes" : r.status || "";
          const pill = { alto: `<span class="pill alto">▲ sobre referencia</span>`, ok: `<span class="pill ok">✓ dentro de ref.</span>`, parcial: `<span class="pill warn">parcial</span>`,
                         "falta factura": `<span class="pill warn">falta factura</span>`, "faltan huéspedes": `<span class="pill warn">faltan huéspedes</span>` }[st] || "";
          return `<tr><td>${r.label}</td><td class="n">${nf(r.consumption)}</td><td class="n muted">${nf(d.prev_rows[i].consumption)}</td><td class="n">${nf(r.guests)}</td><td class="n ${r.status === "alto" && r.complete ? "alto" : ""}">${nf(r.per_guest, dec)}</td><td class="n">${money(r.amount)}</td><td>${pill}</td></tr>`;
        }).join("")}
        <tr class="total"><td>Total</td><td class="n">${nf(s.total)}</td><td class="n muted">${nf(d.prev_rows.reduce((a, r) => a + (r.consumption || 0), 0) || null)}</td><td class="n">${nf(s.guests)}</td><td class="n">${nf(s.per_guest, dec)}</td><td class="n">${money(s.cost)}</td><td></td></tr>
      </tbody></table></div>
      <p class="small muted">“Parcial”: todavía falta alguna factura que cubra todos los días del mes. Esos meses no entran en mínimos, máximos ni promedios.</p></div>
    </div>`;
  consumptionChart($("#c1"), d.rows, d.prev_rows, svc, y);
  perGuestChart($("#c2"), d.rows, d.prev_rows, svc, y, ref);
}

async function hotelGuests(body, h) {
  const years = [STATE.year, STATE.year - 1, STATE.year - 2];
  const data = await Promise.all(years.map(y => api(`/api/guests?year=${y}`)));
  body.innerHTML = `<div class="card">
    <p class="small muted" style="margin-top:0">Escribí la cantidad de huéspedes de cada mes: se guarda solo. También podés pegar una columna copiada de Excel.</p>
    <div class="table-wrap"><table class="data guests-grid"><thead><tr><th>Mes</th>${years.map(y => `<th class="n">${y}</th>`).join("")}</tr></thead><tbody>
      ${META.months.map((m, i) => `<tr><td>${m}</td>${years.map((y, k) => `<td><input type="number" min="0" data-y="${y}" data-m="${i + 1}" value="${data[k][h.id]?.[i + 1] ?? ""}" aria-label="${m} ${y}"></td>`).join("")}</tr>`).join("")}
      <tr class="total"><td>Total</td>${years.map(y => `<td class="n" data-tot="${y}"></td>`).join("")}</tr></tbody></table></div></div>`;
  const totals = () => years.forEach(y => $(`[data-tot="${y}"]`).textContent = nf($$(`input[data-y="${y}"]`).reduce((a, i) => a + (+i.value || 0), 0)));
  totals();
  bindGuestInputs(body, inp => ({ hotel_id: h.id, year: +inp.dataset.y, month: +inp.dataset.m }),
    (inp, r, c) => { const t = $(`input[data-y="${inp.dataset.y}"][data-m="${+inp.dataset.m + r}"]`); return c === 0 ? t : null; }, totals);
}

function bindGuestInputs(root, keyOf, cellAt, after) {
  $$(".guests-grid input", root).forEach(inp => {
    inp.dataset.prev = inp.value;
    inp.onchange = async () => {
      if (inp.value !== "" && (+inp.value < 0 || !Number.isFinite(+inp.value))) { toast("La cantidad de huéspedes tiene que ser un número positivo"); inp.value = inp.dataset.prev; return; }
      try {
        const res = await withConfirm(confirm => api("/api/guests", { method: "PUT", json: { items: [{ ...keyOf(inp), count: inp.value }], confirm } }));
        if (!res) { inp.value = inp.dataset.prev; inp.focus(); inp.select(); return; }
        inp.dataset.prev = inp.value;
        inp.classList.add("saved"); setTimeout(() => inp.classList.remove("saved"), 900); after(); refreshTodo();
      } catch (e) { inp.value = inp.dataset.prev; toast(e.message, 5000); }
    };
    inp.onpaste = async e => {
      const text = e.clipboardData.getData("text");
      if (!/[\n\t]/.test(text.trim())) return;
      e.preventDefault();
      const items = [], cells = [];
      text.trim().split(/\r?\n/).forEach((line, r) => line.split("\t").forEach((v, c) => {
        const t = cellAt(inp, r, c), num = parseNumber(v);
        if (t && num != null && num >= 0) { cells.push(t); t.value = Math.round(num); items.push({ ...keyOf(t), count: Math.round(num) }); }
      }));
      if (!items.length) return toast("No se reconocieron números en lo pegado");
      const undo = () => cells.forEach(t => t.value = t.dataset.prev);
      try {
        const res = await withConfirm(confirm => api("/api/guests", { method: "PUT", json: { items, confirm } }));
        if (!res) return undo();
        cells.forEach(t => t.dataset.prev = t.value);
        after(); refreshTodo(); toast(`${items.length} valores pegados`);
      } catch (err) { undo(); toast(err.message, 5000); }
    };
  });
}

function hotelReport(body, h) {
  reportBuilder(body, h.id);
}

// ---------------------------------------------------------------- CARGAR
VIEWS.cargar = async () => {
  main.innerHTML = `
    <h1>Cargar facturas</h1>
    <p class="lead">Arrastrá los PDF de AySA, Edesur o MetroGAS, o carpetas enteras. Se leen solos: período, consumo, importe y hotel. Los duplicados se descartan.</p>
    <div id="waiting"></div>
    <div class="drop" id="drop" tabindex="0" role="button" aria-label="Elegir archivos PDF">
      <div class="icon">📥</div><div class="big">Soltá acá las facturas</div>
      <div class="muted">o hacé clic para elegir archivos · también se pueden soltar carpetas</div>
      <input type="file" id="file" accept=".pdf,application/pdf" multiple hidden>
    </div>
    <div class="toolbar" style="margin-top:14px"><button class="btn" id="manual">✍ Cargar una factura a mano</button></div>
    <div id="prog" hidden><div class="progress"><div></div></div><div class="small muted" id="progtxt"></div></div>
    <div id="summary" style="margin-top:12px"></div>
    <div id="results" class="card" hidden></div>`;
  const drop = $("#drop"), input = $("#file");
  drop.onclick = () => input.click();
  drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onchange = () => uploadFiles([...input.files]);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = async e => {
    e.preventDefault(); drop.classList.remove("over");
    const entries = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    uploadFiles(entries.length ? (await Promise.all(entries.map(readEntry))).flat() : [...e.dataTransfer.files]);
  };
  $("#manual").onclick = () => invoiceDialog(null);
  waitingBox($("#waiting"));
};

// Facturas que quedaron esperando (sin hotel / no se pudieron leer) y las marcadas para revisar
async function waitingBox(box) {
  const t = await refreshTodo();
  if (!t || !box || !box.isConnected) return;
  const again = () => { loadMeta(); waitingBox(box); };
  box.innerHTML = `
    ${t.inbox.length ? `<div class="card waiting"><h3>📥 Quedaron esperando (${t.inbox.length})</h3>
      <p class="small muted" style="margin-top:-4px">No se supo de qué hotel son, o no se pudieron leer. Revisá los datos (con “Ver PDF” la abrís) y tocá Guardar.</p>
      ${t.inbox.map((p, k) => `<div class="inbox-item" data-k="${k}">
        <div><b>${esc(p.filename)}</b> <span class="pill ${p.kind === "error" ? "alto" : "warn"}">${p.kind === "error" ? "No se pudo leer" : "Elegí el hotel"}</span>
        <div class="small muted">${esc(p.message || "")}</div></div>${pendingForm({ token: p.token, data: p.data })}</div>`).join("")}</div>` : ""}
    ${t.review.length ? `<div class="card waiting"><h3>🔎 Para revisar (${t.review.length})</h3>
      <p class="small muted" style="margin-top:-4px">Se guardaron, pero el consumo es muy distinto al de siempre. Mirá el PDF: si está bien, tocá “Está bien”.</p>
      <div class="table-wrap"><table class="data"><tbody>${t.review.map(r => `<tr data-r="${r.id}">
        <td><span class="dot" style="background:${hotelColor(r.hotel_id)}"></span> ${esc(r.hotel)}</td><td>${svcLabel(r.service)}</td>
        <td>${fdate(r.period_start)} al ${fdate(r.period_end)}</td><td class="n">${nf(r.consumption)} ${unit(r.service)}</td>
        <td class="small" style="max-width:360px">${esc(r.review)}</td>
        <td class="n" style="white-space:nowrap">${r.file_stored ? `<a class="btn sm" href="/api/invoices/${r.id}/pdf" target="_blank">Ver PDF</a> ` : ""}<button class="btn sm" data-fix>Corregir</button> <button class="btn sm primary" data-ok>Está bien</button></td></tr>`).join("")}
      </tbody></table></div></div>` : ""}`;
  $$(".inbox-item", box).forEach(el => {
    const p = t.inbox[+el.dataset.k];
    bindPending(el, { token: p.token, file: p.filename }, null, again);
  });
  $$("tr[data-r]", box).forEach(tr => {
    const inv = t.review.find(r => r.id == tr.dataset.r);
    $("[data-ok]", tr).onclick = e => busy(e.currentTarget, async () => {
      try { await api(`/api/invoices/${inv.id}/ok`, { method: "POST" }); toast("Listo, marcada como revisada"); again(); } catch (err) { toast(err.message, 5000); }
    });
    $("[data-fix]", tr).onclick = () => invoiceDialog(inv, again);
  });
}
function readEntry(entry) {
  return new Promise(resolve => {
    if (entry.isFile) entry.file(f => resolve([f]), () => resolve([]));
    else if (entry.isDirectory) {
      const reader = entry.createReader(), all = [];
      const next = () => reader.readEntries(async ents => {
        if (!ents.length) resolve((await Promise.all(all.map(readEntry))).flat()); else { all.push(...ents); next(); }
      }, () => resolve([]));
      next();
    } else resolve([]);
  });
}
const STATUS = { guardada: ["ok", "✓ Guardada"], revisar: ["warn", "Guardada · revisar"], duplicada: ["neutral", "Ya estaba"], nota: ["neutral", "Nota informativa"],
  cuenta_desconocida: ["warn", "Elegí el hotel"], error: ["alto", "No se pudo leer"], ignorado: ["neutral", "Ignorado"] };

async function uploadFiles(files) {
  files = files.filter(f => /\.pdf$/i.test(f.name));
  if (!files.length) return toast("No hay archivos PDF");
  if (STATE.uploading) return toast("Esperá a que termine la carga en curso");
  STATE.uploading = true;
  const warn = e => { e.preventDefault(); e.returnValue = ""; };
  window.addEventListener("beforeunload", warn);
  const count = {};
  try {
  $("#prog").hidden = false;
  const bar = $("#prog .progress > div"), txt = $("#progtxt"), res = $("#results");
  res.hidden = false;
  res.innerHTML = `<div class="table-wrap"><table class="data"><thead><tr><th>Archivo</th><th>Estado</th><th>Hotel</th><th>Detalle</th></tr></thead><tbody></tbody></table></div>`;
  const tbody = $("tbody", res);
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    txt.textContent = `Leyendo ${i + 1} de ${files.length}: ${f.name}`;
    const fd = new FormData(); fd.append("file", f, f.name);
    let r;
    try { r = await api("/api/upload", { method: "POST", body: fd }); } catch (e) { r = { status: "error", file: f.name, message: e.message }; }
    count[r.status] = (count[r.status] || 0) + 1;
    const [cls, lbl] = STATUS[r.status] || ["neutral", r.status];
    const hid = META.hotels.find(h => h.name === r.hotel)?.id;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="small">${esc(r.file)}</td><td><span class="pill ${cls}">${lbl}</span></td><td>${r.hotel ? `<span class="dot" style="background:${hotelColor(hid)}"></span> ${esc(r.hotel)}` : ""}</td><td class="small">${esc(r.message || "")}</td>`;
    if (r.token) {
      const sub = document.createElement("tr");
      sub.innerHTML = `<td colspan="4">${pendingForm(r)}</td>`;
      tbody.prepend(sub); tbody.prepend(tr); bindPending(sub, r, tr);
    } else tbody.appendChild(tr);
    bar.style.width = `${((i + 1) / files.length) * 100}%`;
  }
  txt.textContent = `Listo: ${files.length} archivos procesados.`;
  } finally { STATE.uploading = false; window.removeEventListener("beforeunload", warn); }
  $("#summary").innerHTML = `<div class="notice">${[
    count.guardada && `<b>${count.guardada}</b> guardadas`, count.revisar && `<b>${count.revisar}</b> guardadas con un consumo muy distinto al habitual (miralas: dicen “revisar”)`,
    count.duplicada && `${count.duplicada} ya estaban cargadas`,
    count.nota && `${count.nota} notas informativas (no suman)`, count.cuenta_desconocida && `<b>${count.cuenta_desconocida}</b> esperan que elijas el hotel`,
    count.error && `<b>${count.error}</b> no se pudieron leer: completalas abajo`].filter(Boolean).join(" · ")}
    ${count.cuenta_desconocida || count.error ? `<div class="small" style="margin-top:6px">Si no las completás ahora no pasa nada: la próxima vez que entres a “Cargar facturas” van a estar esperando arriba de todo.</div>` : ""}</div>`;
  await loadMeta();
}
function pendingForm(r) {
  const d = r.data || {};
  return `<div class="pending-form">
    <label>Hotel<select name="hotel_id">${META.hotels.map(h => `<option value="${h.id}">${esc(h.name)}</option>`).join("")}</select></label>
    <label>Servicio<select name="service">${SVCS().map(k => `<option value="${k}" ${d.service === k ? "selected" : ""}>${svcLabel(k)}</option>`).join("")}</select></label>
    <label>Desde<input type="date" name="period_start" value="${d.period_start || ""}"></label>
    <label>Hasta<input type="date" name="period_end" value="${d.period_end || ""}"></label>
    <label>Consumo de la lectura<input type="number" step="any" name="consumption" value="${d.consumption ?? ""}"></label>
    <label>Importe ($)<input type="number" step="any" name="amount" value="${d.amount ?? ""}"></label>
    <label>N° de cuenta<input type="text" name="account" value="${esc(d.account || "")}"></label>
    <label style="justify-content:flex-end">${d.account ? `<span><input type="checkbox" name="remember" checked> Recordar esta cuenta</span>` : ""}</label>
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap"><button class="btn primary sm" data-save>Guardar</button><a class="btn sm" href="/api/pending/${r.token}/pdf" target="_blank">Ver PDF</a><button class="btn sm ghost" data-discard>Descartar</button></div></div>`;
}
function bindPending(row, r, statusRow, after) {
  const setStatus = html => { if (statusRow) $("td:nth-child(2)", statusRow).innerHTML = html; };
  $("[data-save]", row).onclick = e => busy(e.currentTarget, async () => {
    const v = n => $(`[name=${n}]`, row)?.value;
    if (!v("period_start") || !v("period_end") || v("consumption") === "") return toast("Completá las fechas y el consumo (están en la factura; podés abrirla con “Ver PDF”)", 6000);
    try {
      const res = await withConfirm(confirm => api(`/api/pending/${r.token}`, { method: "POST", json: { hotel_id: v("hotel_id"), remember: $("[name=remember]", row)?.checked, confirm,
        data: { service: v("service"), period_start: v("period_start"), period_end: v("period_end"), consumption: v("consumption"), amount: v("amount"), account: v("account") } } }));
      if (!res) return;
      row.remove();
      setStatus(`<span class="pill ok">✓ Guardada</span>`);
      if (statusRow) $("td:nth-child(3)", statusRow).textContent = hotelById(v("hotel_id")).name;
      toast("Factura guardada"); loadMeta(); after?.();
    } catch (e) {
      toast(e.message, 6000);
      if (/ya estaba cargada/.test(e.message)) { row.remove(); setStatus(`<span class="pill neutral">Ya estaba</span>`); refreshTodo(); after?.(); }
    }
  });
  $("[data-discard]", row).onclick = e => busy(e.currentTarget, async () => {
    if (!(await ask(`Se descarta “${r.file}”. La factura no se guarda (el PDF original sigue donde lo tenías).`, { title: "¿Descartar esta factura?", ok: "Descartar" }))) return;
    try { await api(`/api/pending/${r.token}`, { method: "DELETE" }); row.remove(); setStatus(`<span class="pill neutral">Descartada</span>`); refreshTodo(); after?.(); }
    catch (err) { toast(err.message, 5000); }
  });
}

// ---------------------------------------------------------------- FACTURAS (lista reutilizable)
async function invoicesList(box, fixed = {}) {
  const f = { service: "", year: STATE.year, ...(STATE.invFilter || {}), ...fixed };
  box.innerHTML = `<div class="toolbar">${fixed.hotel ? "" : hotelSelect("fh", true, f.hotel)}${serviceSelect("fs", true, f.service)}
      <label>Año<select id="fy"><option value="">Todos</option>${META.years.map(y => `<option ${y == f.year ? "selected" : ""}>${y}</option>`).join("")}</select></label>
      <label class="grow">Buscar<input type="search" id="fq" placeholder="N° de factura, cuenta, importe, fecha…" value="${esc(STATE.invQuery || "")}"></label>
      ${META.counts.trash ? `<a class="btn" href="#papelera">🗑 Papelera (${META.counts.trash})</a>` : ""}<button class="btn" id="add">+ Factura a mano</button></div>
    <div class="card"><div class="table-wrap" id="list"><div class="empty">Cargando…</div></div></div>`;
  const load = async () => {
    const q = { hotel: fixed.hotel || $("#fh")?.value || "", service: $("#fs").value, year: $("#fy").value };
    STATE.invFilter = { service: q.service, year: q.year };
    all = await api("/api/invoices?" + new URLSearchParams(q));
    show();
  };
  let all = [];
  const show = () => {
    const words = ($("#fq").value || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
    STATE.invQuery = $("#fq").value;
    const hay = r => [r.hotel, svcLabel(r.service), r.invoice_number, r.account_number, fdate(r.period_start), fdate(r.period_end), fdate(r.due_date),
      nf(r.amount), nf(r.consumption), r.amount, r.notes].join(" ").toLowerCase();
    const rows = words.length ? all.filter(r => { const t = hay(r); return words.every(w => t.includes(w)); }) : all;
    $("#list").innerHTML = rows.length ? `<table class="data"><thead><tr>${fixed.hotel ? "" : "<th>Hotel</th>"}<th>Servicio</th><th>Desde</th><th>Hasta</th><th class="n">Consumo</th><th>Tramo</th><th class="n">Importe</th><th>Vence</th><th>Cuenta</th><th>Factura</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr class="click" data-id="${r.id}">${fixed.hotel ? "" : `<td><span class="dot" style="background:${hotelColor(r.hotel_id)}"></span> ${esc(r.hotel)}</td>`}
        <td><span class="dot" style="background:${svcColor(r.service)}"></span> ${svcLabel(r.service)}</td><td>${fdate(r.period_start)}</td><td>${fdate(r.period_end)}</td>
        <td class="n">${nf(r.consumption)} ${unit(r.service)}${r.review ? ` <span class="pill warn" title="${esc(r.review)}">revisar</span>` : ""}</td><td>${r.split > 1 ? `${r.part} de ${r.split}` : ""}</td><td class="n">${money(r.amount)}</td>
        <td class="small" style="white-space:nowrap">${r.due_date ? fdate(r.due_date) : ""}</td>
        <td class="small">${esc(r.account_number || "")}</td><td class="small">${esc(r.invoice_number || (r.source === "manual" ? "a mano" : ""))}</td>
        <td>${r.file_stored ? `<a class="btn sm" href="/api/invoices/${r.id}/pdf" target="_blank" data-stop>PDF</a>` : ""}</td></tr>`).join("")}
      </tbody></table><p class="small muted">${rows.length} facturas${words.length ? ` (de ${all.length})` : ""} · hacé clic en una fila para corregirla</p>` : `<div class="empty">${words.length ? "No encontré facturas con eso. Probá con otra palabra o número." : "No hay facturas con esos filtros"}</div>`;
    $$("#list tr[data-id]").forEach(tr => tr.onclick = e => { if (!e.target.closest("[data-stop]")) invoiceDialog(rows.find(r => r.id == tr.dataset.id), load); });
  };
  ["#fh", "#fs", "#fy"].forEach(s => { if ($(s)) $(s).onchange = load; });
  let qt = null;
  $("#fq").oninput = () => { clearTimeout(qt); qt = setTimeout(show, 200); };
  $("#add").onclick = () => invoiceDialog(fixed.hotel ? { hotel_id: fixed.hotel, _new: true } : null, load);
  await load();
}

function invoiceDialog(inv, after) {
  const isNew = !inv || inv._new;
  const dlg = $("#dlg"), x = { service: "agua", split: 1, part: 1, hotel_id: META.hotels[0]?.id, ...(inv || {}) };
  dlg.innerHTML = `<h2>${isNew ? "Cargar factura a mano" : "Editar factura"}</h2>
    <div class="form-grid">
      ${hotelSelect("d_hotel", false, x.hotel_id)}${serviceSelect("d_service", false, x.service)}
      <label>Desde (lectura anterior)<input type="date" id="d_ps" value="${x.period_start || ""}"></label>
      <label>Hasta (lectura actual)<input type="date" id="d_pe" value="${x.period_end || ""}"></label>
      <label>Consumo de la lectura<input type="number" step="any" id="d_cons" value="${x.consumption ?? ""}"></label>
      <label>Importe total ($)<input type="number" step="any" id="d_amt" value="${x.amount ?? ""}"></label>
      <label>N° de cuenta<input type="text" id="d_acc" value="${esc(x.account_number || "")}"></label>
      <label>N° de factura<input type="text" id="d_num" value="${esc(x.invoice_number || "")}"></label>
      <label>Tramo (agua)<input type="number" min="1" id="d_part" value="${x.part || 1}"></label>
      <label>Cantidad de tramos<input type="number" min="1" id="d_split" value="${x.split || 1}"></label>
      <label>Vencimiento<input type="date" id="d_due" value="${x.due_date || ""}"></label>
      <label class="full">Notas<input type="text" id="d_notes" value="${esc(x.notes || "")}"></label>
      <p class="full small muted" style="margin:0">AySA cobra cada lectura bimestral en 2 tramos: cada tramo suma la mitad del consumo.</p>
    </div>
    <div class="actions">${!isNew ? `<button class="btn danger" id="d_del" style="margin-right:auto">Eliminar</button>` : ""}
      <button class="btn" id="d_cancel">Cancelar</button><button class="btn primary" id="d_ok">Guardar</button></div>`;
  dlg.showModal();
  $("#d_cancel").onclick = () => dlg.close();
  $("#d_ok").onclick = e => busy(e.currentTarget, async () => {
    const body = { hotel_id: +$("#d_hotel").value, service: $("#d_service").value, period_start: $("#d_ps").value, period_end: $("#d_pe").value,
      consumption: $("#d_cons").value, amount: $("#d_amt").value, invoice_number: $("#d_num").value || null,
      part: +$("#d_part").value || 1, split: +$("#d_split").value || 1, notes: $("#d_notes").value, due_date: $("#d_due").value || null };
    if (!body.period_start || !body.period_end || body.consumption === "") return toast("Completá fechas y consumo");
    if (+body.consumption < 0) return toast("El consumo no puede ser negativo");
    if (body.part > body.split) return toast("El tramo no puede ser mayor que la cantidad de tramos");
    try {
      const res = await withConfirm(confirm => !isNew
        ? api(`/api/invoices/${inv.id}`, { method: "PUT", json: { ...body, account_number: $("#d_acc").value, confirm } })
        : api("/api/invoices", { method: "POST", json: { ...body, account: $("#d_acc").value, confirm } }));
      if (!res) return;
      dlg.close(); toast("Guardado"); await loadMeta(); after?.();
    } catch (err) { toast(err.message, 6000); }
  });
  if (!isNew) $("#d_del").onclick = e => busy(e.currentTarget, async () => {
    try {
      await api(`/api/invoices/${inv.id}`, { method: "DELETE" }); dlg.close(); await loadMeta(); after?.();
      toast("Factura enviada a la papelera", 8000, { label: "Deshacer", fn: async () => {
        try { await api(`/api/trash/${inv.id}/restore`, { method: "POST" }); toast("Factura recuperada"); await loadMeta(); after?.(); }
        catch (err) { toast(err.message, 5000); }
      } });
    } catch (err) { toast(err.message, 5000); }
  });
}

// ---------------------------------------------------------------- HUÉSPEDES (todos los hoteles)
VIEWS.huespedes = async () => {
  const g = await api(`/api/guests?year=${STATE.year}`);
  main.innerHTML = `
    <h1>Huéspedes por mes</h1>
    <p class="lead">Todos los hoteles en una sola grilla. Se guarda al salir de cada casilla; se puede pegar desde Excel.</p>
    <div class="toolbar">${yearSelect()}<div class="grow"></div><button class="btn" id="imp">Importar desde Excel</button><input type="file" id="impf" accept=".xlsx" hidden></div>
    <div class="card"><div class="table-wrap"><table class="data guests-grid"><thead><tr><th>Mes</th>${META.hotels.map(h => `<th class="n"><span class="dot" style="background:${hotelColor(h.id)}"></span> ${esc(h.name)}</th>`).join("")}</tr></thead>
      <tbody>${META.months.map((m, i) => `<tr><td>${m}</td>${META.hotels.map(h => `<td><input type="number" min="0" data-h="${h.id}" data-m="${i + 1}" value="${g[h.id]?.[i + 1] ?? ""}" aria-label="${esc(h.name)} ${m}"></td>`).join("")}</tr>`).join("")}
      <tr class="total"><td>Total</td>${META.hotels.map(h => `<td class="n" data-tot="${h.id}"></td>`).join("")}</tr></tbody></table></div></div>
    <p class="small muted">El Excel a importar debe tener una columna “Mes” y una columna por hotel (como “Cantidad de Huespedes por Mes.xlsx”).</p>`;
  const totals = () => META.hotels.forEach(h => $(`[data-tot="${h.id}"]`).textContent = nf($$(`input[data-h="${h.id}"]`).reduce((a, i) => a + (+i.value || 0), 0)));
  totals();
  const ids = META.hotels.map(h => String(h.id));
  bindGuestInputs(main, inp => ({ hotel_id: +inp.dataset.h, year: STATE.year, month: +inp.dataset.m }),
    (inp, r, c) => $(`input[data-h="${ids[ids.indexOf(inp.dataset.h) + c]}"][data-m="${+inp.dataset.m + r}"]`), totals);
  bindYear(render);
  $("#imp").onclick = () => $("#impf").click();
  $("#impf").onchange = async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    const form = () => { const fd = new FormData(); fd.append("file", file); return fd; };
    try {
      const p = await api("/api/guests/import?preview=1", { method: "POST", body: form() });
      if (!p.new && !p.changed) return toast(`El Excel tiene ${p.imported} valores y ya estaban todos cargados igual. No hay nada que cambiar.`, 6000);
      const msg = [`El Excel tiene ${p.imported} valores de huéspedes:`, p.new && `• ${p.new} meses nuevos (no estaban cargados)`,
        p.changed && `• ${p.changed} meses que ya tenían un número: se reemplazan por el del Excel`, p.same && `• ${p.same} iguales a lo que ya está`].filter(Boolean).join("\n");
      if (!(await ask(msg, { title: "¿Importar estos huéspedes?", ok: "Importar" }))) return;
      const r = await api("/api/guests/import", { method: "POST", body: form() });
      toast(`Listo: ${r.new + r.changed} valores importados`); await loadMeta(); render();
    } catch (err) { toast(err.message, 6000); }
  };
};

// ---------------------------------------------------------------- COMPARAR
VIEWS.comparar = async () => {
  const svc = STATE.cmpService || "agua", y = STATE.year, u = unit(svc), dec = perDec(svc);
  const d = await api(`/api/compare?service=${svc}&year=${y}`);
  main.innerHTML = `
    <h1>Comparar hoteles</h1>
    <p class="lead">Consumo por huésped de cada hotel, mes a mes, frente a la referencia.</p>
    <div class="toolbar"><div class="seg" role="tablist">${SVCS().map(s => `<button data-s="${s}" class="${s === svc ? "active" : ""}">${SVC_ICON[s]} ${svcLabel(s)}</button>`).join("")}</div>${yearSelect()}</div>
    <div class="card"><h3>${svcLabel(svc)} · ${u} por huésped</h3>
      ${legendHtml([...d.hotels.map(h => [h.hotel, hotelColor(h.hotel_id), "line"]), ...(d.reference ? [["Referencia " + nf(d.reference, dec), "", "dash"]] : [])])}
      <div class="chart-box" style="height:320px"><canvas id="cc"></canvas></div></div>
    <div class="card" style="margin-top:16px"><div class="table-wrap"><table class="data"><thead><tr><th>Hotel</th><th class="n">Consumo (${u})</th><th class="n">Prom. mensual</th><th class="n">Mínimo</th><th class="n">Máximo</th><th class="n">Huéspedes</th><th class="n">Por huésped</th><th>Estado</th><th class="n">Costo</th></tr></thead><tbody>
      ${d.hotels.map(h => { const s = h.summary; return `<tr class="click" data-hotel="${h.hotel_id}"><td><span class="dot" style="background:${hotelColor(h.hotel_id)}"></span> ${esc(h.hotel)}</td><td class="n">${nf(s.total)}</td><td class="n">${nf(s.avg)}</td><td class="n">${nf(s.min)}</td><td class="n">${nf(s.max)}</td><td class="n">${nf(s.guests)}</td><td class="n">${nf(s.per_guest, dec)}</td><td>${statusPill(s.per_guest, d.reference)}</td><td class="n">${money(s.cost)}</td></tr>`; }).join("")}
    </tbody></table></div></div>`;
  $$("[data-s]").forEach(b => b.onclick = () => { STATE.cmpService = b.dataset.s; render(); });
  $$("tr[data-hotel]").forEach(tr => tr.onclick = () => nav(`hotel/${tr.dataset.hotel}/${svc}`));
  bindYear(render);
  const surface = css("--surface");
  const ds = d.hotels.map((h, i) => ({ label: h.hotel, data: h.rows.map(r => r.complete ? r.per_guest : null), borderColor: hotelColor(h.hotel_id), backgroundColor: hotelColor(h.hotel_id),
    borderWidth: 2, pointRadius: 5, pointStyle: HOTEL_MARKERS[hotelIdx(h.hotel_id) % 4], pointBorderColor: surface, pointBorderWidth: 1.5 }));
  if (d.reference) ds.push({ label: "Referencia", data: META.months_short.map(() => d.reference), borderColor: css("--text-2"), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 });
  mkChart($("#cc"), { type: "line", data: { labels: META.months_short, datasets: ds }, options: baseOpts(`${u} por huésped`, dec) });
};

// ---------------------------------------------------------------- INFORMES
// Armador de informes: ella elige qué lleva el PDF y lo ve al instante.
const RB_PARTS = [["portada", "Portada"], ["resumen", "Resumen y conclusiones"], ["costos", "Costos y huéspedes"],
  ["servicios_pag", "Una página por servicio"], ["anexo", "Detalle mes a mes"], ["facturas", "Lista de facturas"], ["metodologia", "Cómo se calcula (metodología)"]];
const RB_SHOW = [["graficos", "📈 Gráficos"], ["numeros", "🔢 Números y tablas"], ["texto", "📝 Textos y conclusiones"], ["importes", "💲 Importes en pesos"]];
const RB_ALL = Object.fromEntries([...RB_PARTS, ...RB_SHOW].map(([k]) => [k, true]));
const RB_PRESETS = {
  completo: { label: "Completo", opts: { ...RB_ALL } },
  corto: { label: "Resumen corto", opts: { ...RB_ALL, costos: false, servicios_pag: false, anexo: false, facturas: false, metodologia: false } },
  graficos: { label: "Solo gráficos", opts: { ...RB_ALL, numeros: false, texto: false, anexo: false, facturas: false, metodologia: false } },
  numeros: { label: "Solo números", opts: { ...RB_ALL, graficos: false, texto: false, metodologia: false } },
};
function rbLoad() {
  let o = null;
  try { o = JSON.parse(localStorage.getItem("informe_opciones") || "null"); } catch (_) { /* sin almacenamiento */ }
  return { ...RB_ALL, servicios: SVCS(), comentario: "", desde: 1, hasta: 12, ...(o || {}) };
}
function rbSave(o) { try { localStorage.setItem("informe_opciones", JSON.stringify(o)); } catch (_) { /* nada */ } }

function reportBuilder(box, fixedHotel) {
  const o = rbLoad();
  let hotel = fixedHotel || STATE.rbHotel || META.hotels[0]?.id;
  const chk = (k, l) => `<label class="rb-chk"><input type="checkbox" data-o="${k}" ${o[k] ? "checked" : ""}> ${l}</label>`;
  box.innerHTML = `<div class="rb">
    <div class="card rb-side">
      <h3>Armá tu informe</h3>
      <div class="rb-presets">${Object.entries(RB_PRESETS).map(([k, p]) => `<button class="btn sm" data-preset="${k}">${p.label}</button>`).join("")}</div>
      <div class="rb-group"><div class="rb-t">Período</div>
        <div class="rb-period"><label>Desde<select id="rb_m1">${META.months.map((m, i) => `<option value="${i + 1}" ${o.desde == i + 1 ? "selected" : ""}>${m}</option>`).join("")}</select></label>
          <label>Hasta<select id="rb_m2">${META.months.map((m, i) => `<option value="${i + 1}" ${o.hasta == i + 1 ? "selected" : ""}>${m}</option>`).join("")}</select></label></div>
        <div class="rb-presets"><button class="btn sm ghost" data-per="1-12">Año completo</button><button class="btn sm ghost" data-per="1-6">1er semestre</button><button class="btn sm ghost" data-per="7-12">2do semestre</button></div></div>
      <div class="rb-group"><div class="rb-t">Qué partes lleva</div>${RB_PARTS.map(([k, l]) => chk(k, l)).join("")}</div>
      <div class="rb-group"><div class="rb-t">Qué mostrar</div>${RB_SHOW.map(([k, l]) => chk(k, l)).join("")}</div>
      <div class="rb-group"><div class="rb-t">Servicios</div>${SVCS().map(k => `<label class="rb-chk"><input type="checkbox" data-svc="${k}" ${o.servicios.includes(k) ? "checked" : ""}> <span class="dot" style="background:${svcColor(k)}"></span> ${svcLabel(k)}</label>`).join("")}</div>
      <div class="rb-group"><div class="rb-t">Comentario para los dueños <span class="muted">(opcional)</span></div>
        <textarea id="rb_com" rows="4" placeholder="Por ejemplo: “En marzo se arregló una pérdida en el tanque de agua.”">${esc(o.comentario)}</textarea>
        <div class="small muted">Aparece en el resumen, en un recuadro.</div></div>
    </div>
    <div class="rb-main">
      <div class="toolbar" style="margin-bottom:12px">
        ${fixedHotel ? "" : hotelSelect("rb_h", false, hotel) + yearSelect("rb_y")}
        <div class="grow"></div>
        <button class="btn primary" id="rb_pdf">⬇ Descargar PDF</button>
        <button class="btn" id="rb_xls" title="El Excel trae todos los datos y gráficos; no usa estas opciones">⬇ Excel</button>
      </div>
      <div class="rb-view card"><div class="rb-loading" id="rb_load">Armando la vista previa…</div>
        <div class="rb-count small muted" id="rb_count"></div><div class="notice warn" id="rb_gaps" hidden></div><div class="rb-pages" id="rb_pages"></div></div>
      <p class="small muted" style="margin:8px 2px 0">Así va a quedar el PDF. Cada vez que cambiás una opción se actualiza solo. Tocá una página para verla grande.</p>
    </div></div>
    ${fixedHotel ? "" : `<div class="card" style="margin-top:16px">
      <h3>Todos los hoteles juntos</h3>
      <div class="toolbar" style="margin:0"><label>Formato<select id="rf"><option value="pdf">PDF</option><option value="xlsx">Excel</option><option value="ambos">PDF + Excel</option></select></label>
        <label class="rb-chk" style="flex-direction:row;align-self:center"><input type="checkbox" id="rb_cmp" checked> Sumar el comparativo entre hoteles</label>
        <button class="btn primary" id="dl">⬇ Descargar todo (.zip)</button></div>
      <p class="small muted" style="margin:8px 0 0">Un informe por hotel con las mismas opciones de arriba. El comparativo entre hoteles es siempre del año completo.</p></div>`}`;

  const read = () => {
    $$("[data-o]", box).forEach(i => o[i.dataset.o] = i.checked);
    o.servicios = $$("[data-svc]", box).filter(i => i.checked).map(i => i.dataset.svc);
    o.comentario = $("#rb_com", box).value;
    let a = +$("#rb_m1", box).value, b = +$("#rb_m2", box).value;
    if (a > b) { [a, b] = [b, a]; $("#rb_m1", box).value = a; $("#rb_m2", box).value = b; }
    o.desde = a; o.hasta = b;
    rbSave(o);
    return o;
  };
  const year = () => fixedHotel ? STATE.year : +$("#rb_y", box).value;
  let timer = null, seq = 0;
  const preview = async () => {
    const opts = read(), my = ++seq;
    if (!opts.servicios.length) { $("#rb_load", box).textContent = "Elegí al menos un servicio."; $("#rb_load", box).hidden = false; return; }
    $("#rb_load", box).textContent = "Armando la vista previa…"; $("#rb_load", box).hidden = false;
    try {
      const r = await fetch("/api/report", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hotel, year: year(), preview: true, opts }) });
      if (my !== seq) return;   // ya pidió otra versión
      if (!r.ok) { let m = `Error ${r.status}`; try { m = (await r.json()).error || m; } catch (_) {} throw new Error(m); }
      const { pages, gaps } = await r.json();
      if (my !== seq) return;
      $("#rb_gaps", box).hidden = !gaps;
      $("#rb_gaps", box).innerHTML = gaps ? `<b>Ojo, información incompleta:</b> ${esc(gaps)}` : "";
      $("#rb_count", box).textContent = `${pages.length} ${pages.length === 1 ? "página" : "páginas"}`;
      $("#rb_pages", box).innerHTML = pages.map((src, i) => `<figure><img src="${src}" alt="Página ${i + 1}" data-p="${i}"><figcaption>${i + 1}</figcaption></figure>`).join("");
      $$("#rb_pages img", box).forEach(img => img.onclick = () => {
        const d = $("#ask");
        d.innerHTML = `<div class="rb-big"><img src="${img.src}" alt=""><div class="actions"><button class="btn primary" data-a="si">Cerrar</button></div></div>`;
        d.classList.add("wide"); d.onclose = () => d.classList.remove("wide");
        $("[data-a=si]", d).onclick = () => d.close(); d.showModal(); d.scrollTop = 0;
      });
      $("#rb_load", box).hidden = true;
    } catch (e) {
      if (my !== seq) return;
      $("#rb_load", box).textContent = /Failed to fetch/i.test(e.message) ? "No hay conexión con la aplicación." : e.message;
    }
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(preview, 700); };
  $$("[data-o], [data-svc], #rb_m1, #rb_m2", box).forEach(i => i.onchange = later);
  $$("[data-per]", box).forEach(btn => btn.onclick = () => {
    const [a, b] = btn.dataset.per.split("-");
    $("#rb_m1", box).value = a; $("#rb_m2", box).value = b; preview();
  });
  $("#rb_com", box).oninput = () => { clearTimeout(timer); timer = setTimeout(preview, 1400); };
  $$("[data-preset]", box).forEach(b => b.onclick = () => {
    const p = RB_PRESETS[b.dataset.preset].opts;
    $$("[data-o]", box).forEach(i => i.checked = !!p[i.dataset.o]);
    $$("[data-svc]", box).forEach(i => i.checked = true);
    preview();
  });
  if ($("#rb_h", box)) $("#rb_h", box).onchange = e => { hotel = +e.target.value; STATE.rbHotel = hotel; preview(); };
  if ($("#rb_y", box)) $("#rb_y", box).onchange = e => { STATE.year = +e.target.value; preview(); };
  $("#rb_pdf", box).onclick = e => {
    const opts = read();
    if (!opts.servicios.length) return toast("Elegí al menos un servicio");
    download("/api/report", e.currentTarget, { hotel, year: year(), format: "pdf", opts });
  };
  $("#rb_xls", box).onclick = e => download(`/api/report?hotel=${hotel}&year=${year()}&format=xlsx`, e.currentTarget);
  if ($("#dl", box)) $("#dl", box).onclick = e => {
    const opts = read();
    toast("Generando los informes… puede tardar unos segundos", 5000);
    download("/api/report", e.currentTarget, { hotel: "all", year: year(), format: $("#rf", box).value, comparativo: $("#rb_cmp", box).checked, opts });
  };
  preview();
}

VIEWS.informes = async () => {
  main.innerHTML = `<h1>Informes</h1>
    <p class="lead">Elegí qué querés mostrarles a los dueños y mirá cómo queda antes de descargarlo.</p><div id="rbbox"></div>`;
  reportBuilder($("#rbbox"));
};

// ---------------------------------------------------------------- CONFIGURACIÓN
VIEWS.config = async () => {
  const s = META.settings;
  main.innerHTML = `
    <h1>Configuración</h1>
    <h2>General</h2>
    <div class="card"><div class="form-grid">
      <label>Tu nombre (para el saludo)<input type="text" id="user_name" value="${esc(s.user_name || "")}"></label>
      <label>Nombre de la empresa (aparece en el menú)<input type="text" id="company" value="${esc(s.company)}"></label>
      <label>Referencia agua (m³ por huésped)<input type="text" id="ref_agua" value="${esc(s.ref_agua)}"></label>
      <label>Referencia electricidad (kWh por huésped)<input type="text" id="ref_electricidad" value="${esc(s.ref_electricidad)}"></label>
      <label>Referencia gas (m³ por huésped) · vacío = sin referencia<input type="text" id="ref_gas" value="${esc(s.ref_gas)}"></label>
    </div><div class="actions"><button class="btn primary" id="saveset">Guardar</button></div>
    <p class="small muted" style="margin:0">Referencias UTGRA 2022: agua 0,23 m³/persona-noche · electricidad 11,65 kWh/persona-noche · gas sin datos para Argentina.</p></div>

    <h2>Hoteles</h2>
    <div class="card"><table class="data"><thead><tr><th></th><th>Nombre</th><th>Razón social</th><th>Dirección</th><th></th></tr></thead><tbody>
      ${META.hotels.map(h => `<tr data-h="${h.id}"><td><span class="dot" style="background:${hotelColor(h.id)}"></span></td><td><input type="text" name="name" value="${esc(h.name)}"></td><td><input type="text" name="legal_name" value="${esc(h.legal_name || "")}"></td><td><input type="text" name="address" value="${esc(h.address || "")}"></td><td><button class="btn sm" data-saveh>Guardar</button></td></tr>`).join("")}
      <tr><td></td><td><input type="text" id="nh" placeholder="Nuevo hotel"></td><td></td><td></td><td><button class="btn sm" id="addh">Agregar</button></td></tr>
    </tbody></table></div>

    <h2>Cuentas de servicio</h2>
    <p class="small muted">Con estos números la aplicación reconoce a qué hotel pertenece cada factura.</p>
    <div class="card"><table class="data"><thead><tr><th>Servicio</th><th>N° de cuenta / cliente</th><th>Hotel</th><th></th></tr></thead><tbody>
      ${META.accounts.map(a => `<tr><td><span class="dot" style="background:${svcColor(a.service)}"></span> ${svcLabel(a.service)}</td><td>${esc(a.number)}</td><td>${esc(a.hotel)}</td><td><button class="btn sm danger" data-dela="${a.id}">Quitar</button></td></tr>`).join("")}
      <tr><td><select id="na_s">${SVCS().map(k => `<option value="${k}">${svcLabel(k)}</option>`).join("")}</select></td><td><input type="text" id="na_n" placeholder="N° de cuenta"></td>
        <td><select id="na_h">${META.hotels.map(h => `<option value="${h.id}">${esc(h.name)}</option>`).join("")}</select></td><td><button class="btn sm" id="adda">Agregar</button></td></tr>
    </tbody></table></div>

    <h2 id="respaldo">Respaldo</h2>
    <div class="card" id="bk"><div class="empty">Cargando…</div></div>
    <div class="card" style="margin-top:14px">
      <h3>Otras opciones</h3>
      <p class="small" style="margin-top:0">Los datos se guardan en <code>${esc(META.data_dir)}</code>. Además, la aplicación hace sola una copia por día dentro de esa carpeta.</p>
      <div class="toolbar" style="margin:0"><button class="btn" data-dl="/api/backup">⬇ Descargar respaldo completo (.zip)</button><button class="btn" id="rest">Restaurar respaldo…</button><input type="file" id="restf" accept=".zip,.db" hidden>
        <a class="btn" href="#papelera">🗑 Papelera${META.counts.trash ? ` (${META.counts.trash})` : ""}</a></div></div>`;
  backupCard();
  if (location.hash.includes("respaldo")) setTimeout(() => $("#respaldo")?.scrollIntoView({ behavior: "smooth" }), 50);
  $("#saveset").onclick = async () => {
    const body = {}; ["user_name", "company", "ref_agua", "ref_electricidad", "ref_gas"].forEach(k => body[k] = $("#" + k).value.trim());
    try { await api("/api/settings", { method: "PUT", json: body }); await loadMeta(); toast("Configuración guardada"); }
    catch (err) { toast(err.message, 5000); }
  };
  $$("[data-saveh]").forEach(b => b.onclick = async () => {
    const tr = b.closest("tr"), v = n => $(`[name=${n}]`, tr).value;
    try { await api(`/api/hotels/${tr.dataset.h}`, { method: "PUT", json: { name: v("name"), legal_name: v("legal_name"), address: v("address") } }); await loadMeta(); toast("Hotel guardado"); }
    catch (err) { toast(err.message, 5000); }
  });
  $("#addh").onclick = async () => {
    if (!$("#nh").value.trim()) return toast("Escribí el nombre del hotel");
    try { await api("/api/hotels", { method: "POST", json: { name: $("#nh").value.trim() } }); await loadMeta(); render(); toast("Hotel agregado"); }
    catch (err) { toast(err.message, 5000); }
  };
  $$("[data-dela]").forEach(b => b.onclick = async () => {
    if (!(await ask("Las facturas ya cargadas no se borran. Solo que las facturas nuevas de esa cuenta te van a preguntar de qué hotel son.", { title: "¿Quitar esta cuenta?", ok: "Quitar" }))) return;
    try { await api(`/api/accounts/${b.dataset.dela}`, { method: "DELETE" }); await loadMeta(); render(); } catch (err) { toast(err.message, 5000); }
  });
  $("#adda").onclick = async () => {
    if (!$("#na_n").value.trim()) return;
    try { await api("/api/accounts", { method: "POST", json: { service: $("#na_s").value, number: $("#na_n").value, hotel_id: $("#na_h").value } }); await loadMeta(); render(); }
    catch (err) { toast(err.message, 5000); }
  };
  $("#rest").onclick = () => $("#restf").click();
  $("#restf").onchange = async e => {
    const file = e.target.files[0]; e.target.value = "";
    if (!file) return;
    if (!(await ask(`Los datos actuales se reemplazan por los de “${file.name}”. Por las dudas, se guarda una copia de los datos actuales antes de cambiar nada.`, { title: "¿Restaurar este respaldo?", ok: "Restaurar", danger: true }))) return;
    const fd = new FormData(); fd.append("file", file);
    toast("Restaurando… esperá unos segundos", 10000);
    try { await api("/api/restore", { method: "POST", body: fd }); await loadMeta(); toast("Respaldo restaurado"); nav("inicio"); } catch (err) { toast(err.message, 6000); }
  };
};

// Tarjeta de copia automática (Drive, OneDrive o pendrive)
async function backupCard() {
  const box = $("#bk");
  if (!box) return;
  let d;
  try { d = await api("/api/backup/status"); } catch (e) { box.innerHTML = `<div class="notice warn">${esc(e.message)}</div>`; return; }
  const st = d.status, on = !!st.folder;
  box.innerHTML = `
    <div class="bk-head"><div class="bk-ic ${st.level}">${st.level === "ok" && on ? "✓" : "!"}</div>
      <div><b>${esc(st.title)}</b><div class="small muted">${esc(on || st.level === "ok" ? st.detail : "Si la compu se rompe o se pierde, se pierden también los datos.")}</div></div></div>
    ${on ? `<div class="toolbar" style="margin:14px 0 0"><button class="btn primary" id="bk_now">Hacer la copia ahora</button>
        <button class="btn" id="bk_pick">Cambiar carpeta…</button><button class="btn ghost danger" id="bk_off">Dejar de copiar</button></div>`
      : `<p class="small" style="margin:14px 0 8px">Elegí dónde guardar la copia. Lo mejor es <b>Google Drive</b> u <b>OneDrive</b>: así queda en internet y no se pierde aunque se rompa la compu. Se hace sola, todos los días.</p>
        <div class="toolbar" style="margin:0">${d.places.map((p, i) => `<button class="btn primary" data-place="${i}">Usar ${esc(p.label)}</button>`).join("")}
          <button class="btn" id="bk_pick">${d.places.length ? "Elegir otra carpeta…" : "Elegir carpeta…"}</button></div>
        ${d.places.length ? "" : `<p class="small muted" style="margin:8px 0 0">No encontré Google Drive ni OneDrive en esta compu. Podés elegir un pendrive o instalar Google Drive para escritorio.</p>`}`}`;
  const use = async path => {
    try { await api("/api/settings", { method: "PUT", json: { backup_folder: path } }); toast("Listo: la copia se hace sola todos los días. La primera puede tardar unos minutos.", 6000); await loadMeta(); backupCard(); }
    catch (err) { toast(err.message, 6000); }
  };
  $$("[data-place]", box).forEach(b => b.onclick = () => busy(b, () => use(d.places[+b.dataset.place].path)));
  $("#bk_pick", box).onclick = e => busy(e.currentTarget, async () => {
    toast("Se abrió una ventana para elegir la carpeta (si no la ves, mirá en la barra de tareas)", 6000);
    try { const r = await api("/api/backup/pick", { method: "POST" }); if (r.path) await use(r.path); }
    catch (err) { toast(err.message, 6000); }
  });
  if (on) {
    $("#bk_now", box).onclick = e => busy(e.currentTarget, async () => {
      e.currentTarget.textContent = "Copiando…";
      try { await api("/api/backup/now", { method: "POST" }); toast("Copia hecha"); } catch (err) { toast(err.message, 7000); }
      await loadMeta(); backupCard();
    });
    $("#bk_off", box).onclick = async () => {
      if (!(await ask("La aplicación deja de hacer la copia automática. Lo que ya está copiado en esa carpeta no se borra.", { title: "¿Dejar de copiar?", ok: "Dejar de copiar" }))) return;
      try { await api("/api/settings", { method: "PUT", json: { backup_folder: "" } }); await loadMeta(); backupCard(); } catch (err) { toast(err.message, 5000); }
    };
  }
}

VIEWS.hacer = async () => nav("cargar");

// ---------------------------------------------------------------- PAPELERA
VIEWS.papelera = async () => {
  const rows = await api("/api/trash");
  main.innerHTML = `
    <h1>Papelera</h1>
    <p class="lead">Las facturas borradas quedan acá 90 días por si te equivocaste. Después se borran solas.</p>
    ${rows.length ? `<div class="toolbar"><div class="grow"></div><button class="btn danger" id="empty">Vaciar papelera</button></div>
    <div class="card"><div class="table-wrap"><table class="data"><thead><tr><th>Hotel</th><th>Servicio</th><th>Período</th><th class="n">Consumo</th><th>Borrada el</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr data-id="${r.id}"><td><span class="dot" style="background:${hotelColor(r.hotel_id)}"></span> ${esc(r.hotel)}</td><td>${svcLabel(r.service)}</td>
        <td>${fdate(r.period_start)} al ${fdate(r.period_end)}${r.split > 1 ? ` · tramo ${r.part} de ${r.split}` : ""}</td><td class="n">${nf(r.consumption)} ${unit(r.service)}</td>
        <td class="small">${fdate((r.deleted_at || "").slice(0, 10))}</td>
        <td class="n" style="white-space:nowrap"><button class="btn sm primary" data-rest>Recuperar</button> <button class="btn sm ghost danger" data-purge>Borrar para siempre</button></td></tr>`).join("")}
    </tbody></table></div></div>` : `<div class="card"><div class="empty">🗑 La papelera está vacía</div></div>`}`;
  $$("tr[data-id]").forEach(tr => {
    const id = tr.dataset.id;
    $("[data-rest]", tr).onclick = e => busy(e.currentTarget, async () => {
      try { await api(`/api/trash/${id}/restore`, { method: "POST" }); toast("Factura recuperada"); await loadMeta(); render(); } catch (err) { toast(err.message, 6000); }
    });
    $("[data-purge]", tr).onclick = e => busy(e.currentTarget, async () => {
      if (!(await ask("Esta factura se borra para siempre y no se va a poder recuperar.", { title: "¿Borrar para siempre?", ok: "Borrar", danger: true }))) return;
      try { await api(`/api/trash/${id}`, { method: "DELETE" }); await loadMeta(); render(); } catch (err) { toast(err.message, 5000); }
    });
  });
  if ($("#empty")) $("#empty").onclick = async () => {
    if (!(await ask(`Se borran para siempre las ${rows.length} facturas de la papelera.`, { title: "¿Vaciar la papelera?", ok: "Vaciar", danger: true }))) return;
    try { await api("/api/trash", { method: "DELETE" }); toast("Papelera vacía"); await loadMeta(); render(); } catch (err) { toast(err.message, 5000); }
  };
};

// ---------------------------------------------------------------- inicio
(async () => {
  try { await loadMeta(); render(); }
  catch (e) { main.innerHTML = `<div class="notice warn">No se pudo conectar con la aplicación. Cerrá esta pestaña y volvé a abrir <b>ConsumosHoteles.exe</b>.</div>`; }
})();
