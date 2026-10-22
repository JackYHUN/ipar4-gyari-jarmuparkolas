/* Kijelző: kizárólag a szerver MQTT-megfigyelőjének fogadott mintáit rajzolja. */
"use strict";
const $ = (id) => document.getElementById(id);
const labels = {waiting:"Indulásra vár",requesting:"Helyet kér",driving:"Halad",pedestrian_stop:"Gyalogos miatt áll",communication_stop:"Kapcsolatra vár",sensor_stop:"Érzékelőhiba",parked:"Leparkolt"};
let state = null, selectedCar = "CAR01", messageFilter = "all", lastEvents = "", controlQueue = Promise.resolve();
let messageHistory = [], historyRun = null;
const seenMessages = new Set();
const pendingControls = new Map();
let controlError = "", transportError = "";
let pendingSpawn = null, parkingSetupRun = null, spawnSequence = 0;
let parkingSetupMessage = "", parkingSetupTone = "";
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = (v, fallback = 0) => typeof v === "number" && Number.isFinite(v) ? v : fallback;
const fmt = (v, digits = 1) => typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";
const set = (id, value) => { $(id).textContent = value; };
const stateClass = (code) => code === "parked" ? "done" : code?.endsWith("stop") ? "stop" : "";
const badge = (code) => `<span class="state-badge ${stateClass(code)}">${esc(labels[code] || code)}</span>`;

function controlKey(action) {
  return action === "pause" || action === "resume" ? "paused" : action;
}
function syncPendingControls() {
  document.querySelectorAll("[data-action], [data-speed], #play, #reset").forEach(button => {
    const key = button.id === "play" ? "paused" : button.id === "reset" ? "reset"
      : button.dataset.speed ? "speed" : button.dataset.action;
    const pending = pendingControls.has(key) || pendingControls.has("reset");
    button.disabled = !state || Boolean(transportError) || pending;
    button.setAttribute("aria-busy", String(pending));
  });
  syncParkingSetup();
}
function freeParkingSlots(next = state) {
  const worldSlots = next?.world?.map?.slots, centralSlots = next?.coordinator?.slots;
  if (!Array.isArray(worldSlots) || !Array.isArray(centralSlots)) return [];
  const central = new Map(centralSlots.map(slot => [slot.id, slot]));
  return worldSlots.filter(slot => slot.state === "free" && central.get(slot.id)?.state === "free");
}
function syncParkingSetup() {
  const unavailable = !state || Boolean(transportError) || pendingControls.has("reset") || Boolean(pendingSpawn);
  $("parked-slot").disabled = unavailable || !freeParkingSlots().length;
  $("spawn-parked").disabled = unavailable || !freeParkingSlots().some(slot => slot.id === $("parked-slot").value);
  $("spawn-parked").setAttribute("aria-busy", String(Boolean(pendingSpawn)));
}
function settleParkingSetup(next) {
  if (parkingSetupRun !== next.run_id) {
    parkingSetupRun = next.run_id;
    pendingSpawn = null; parkingSetupMessage = ""; parkingSetupTone = "";
  }
  if (!pendingSpawn) return;
  const ack = next.parking_setup;
  if (ack?.run_id === pendingSpawn.runId && ack.request_id === pendingSpawn.requestId) {
    if (ack.kind === "parking_setup_rejected") {
      parkingSetupMessage = ack.message || "A parkolóhely már nem szabad. Válassz másik helyet.";
      parkingSetupTone = "error"; pendingSpawn = null;
    } else if (ack.kind === "parked_spawned" && ack.slot_id === pendingSpawn.slotId) {
      const observed = next.world?.parked_cars?.some(car => car.slot_id === ack.slot_id && (car.id || car.car_id) === ack.car_id);
      const occupied = next.coordinator?.slots?.some(slot => slot.id === ack.slot_id && slot.state === "occupied" && slot.car_id === ack.car_id);
      // HTTP acceptance and the acknowledgement alone do not invent an observed vehicle.
      if (observed && occupied) {
        parkingSetupMessage = `${ack.car_id} elhelyezve: ${ack.slot_id}. A hely foglalt.`;
        parkingSetupTone = "success"; pendingSpawn = null;
      }
    }
  }
  if (pendingSpawn && Date.now() - pendingSpawn.started > 8000) {
    pendingSpawn = null;
    parkingSetupMessage = "Az elhelyezés visszaigazolása nem érkezett meg. Ellenőrizd a foglaltságot új kérés előtt.";
    parkingSetupTone = "error";
  }
}
function renderParkingSetup() {
  const choices = freeParkingSlots(), select = $("parked-slot");
  const previous = select.value;
  const markup = choices.length ? choices.map(slot => `<option value="${esc(slot.id)}">${esc(slot.id)}</option>`).join("")
    : `<option value="">${state?.world?.map?.slots && state?.coordinator?.slots ? "Nincs közösen visszaigazolt szabad hely" : "Várakozás a fogadott állapotra…"}</option>`;
  if (select.innerHTML !== markup) {
    select.innerHTML = markup;
    select.value = choices.some(slot => slot.id === previous) ? previous : choices[0]?.id || "";
  }
  const parked = state?.world?.parked_cars;
  set("parked-count", Array.isArray(parked) ? `${parked.length} helyben álló` : "Még nincs minta");
  const message = transportError ? "Nincs friss kapcsolat. Az elhelyezéshez friss foglaltsági adat szükséges."
    : pendingControls.has("reset") ? "Új futás indul; a parkolóelrendezés törlődik."
    : pendingSpawn ? `${pendingSpawn.slotId}: elhelyezés kérése elküldve, MQTT-visszaigazolásra vár…`
    : parkingSetupMessage || (choices.length ? "Válassz helyet, majd helyezz el egy álló autót. Az új futás törli az elhelyezett autókat."
      : state?.world?.map?.slots && state?.coordinator?.slots ? "Nincs szabad hely. Új futással törölheted az elhelyezett autókat." : "A parkolókezelő és a világmodell első mintájára vár.");
  if ($("parked-status").textContent !== message) set("parked-status", message);
  $("parked-status").dataset.tone = transportError ? "error" : pendingSpawn ? "pending" : parkingSetupTone;
  syncParkingSetup();
}
function spawnParked() {
  const slotId = $("parked-slot").value;
  if (!state || transportError || pendingSpawn || pendingControls.has("reset") || !freeParkingSlots().some(slot => slot.id === slotId)) return controlQueue;
  const requestId = globalThis.crypto?.randomUUID?.() || `parked-${Date.now()}-${++spawnSequence}-${Math.random().toString(16).slice(2)}`;
  const pending = {runId:state.run_id, requestId, slotId, started:Date.now()};
  pendingSpawn = pending; parkingSetupMessage = ""; parkingSetupTone = "";
  renderParkingSetup();
  controlQueue = controlQueue.then(async () => {
    if (pendingSpawn !== pending || state?.run_id !== pending.runId) return;
    try {
      const response = await fetch("/api/control", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({action:"spawn_parked",slot_id:slotId,request_id:requestId,run_id:pending.runId}), signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Az autó elhelyezésének kérése nem sikerült.");
      if (result.request_id !== requestId) throw new Error("Az elhelyezési kérés azonosítója nem egyezik a válasszal.");
    } catch (error) {
      if (pendingSpawn !== pending) return;
      pendingSpawn = null; parkingSetupMessage = error.message; parkingSetupTone = "error";
      renderParkingSetup();
    }
  });
  return controlQueue;
}
function settleControls(next) {
  for (const [key, pending] of pendingControls) {
    const actual = key === "reset" ? next.run_id !== pending.runId
      : key === "paused" ? next.paused : key === "speed" ? next.speed : next.controls?.[key];
    if ((key === "reset" && actual) || (key !== "reset" && actual === pending.expected)) {
      pendingControls.delete(key);
    } else if (Date.now() - pending.started > 8000) {
      pendingControls.delete(key);
      controlError = "A vezérlés visszaigazolása nem érkezett meg. Ellenőrizd a futás állapotát.";
    }
  }
}
function control(action, data = {}) {
  const key = controlKey(action);
  if (!state || pendingControls.has(key) || pendingControls.has("reset")) return controlQueue;
  const expected = key === "paused" ? action === "pause" : key === "speed" ? data.value : data.active;
  pendingControls.set(key, {expected, runId:state.run_id, started:Date.now()});
  syncPendingControls();
  // Different controls stay ordered; the same control waits for the received acknowledgement.
  controlQueue = controlQueue.then(async () => {
    try {
      const response = await fetch("/api/control", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...data}),signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "A művelet nem sikerült.");
      controlError = "";
    } catch (error) {
      pendingControls.delete(key);
      controlError = error.message;
      showError(controlError);
      syncPendingControls();
    }
  });
  return controlQueue;
}
function showError(message) { $("error").hidden = false; $("error").textContent = message; }
$("play").addEventListener("click", () => control(state?.paused ? "resume" : "pause"));
$("reset").addEventListener("click", () => control("reset"));
$("spawn-parked").addEventListener("click", spawnParked);
$("parked-slot").addEventListener("change", syncParkingSetup);
document.querySelectorAll("[data-speed]").forEach(button => button.addEventListener("click", () => control("speed", {value:Number(button.dataset.speed)})));
document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => control(button.dataset.action, {active:!state?.controls?.[button.dataset.action]})));
$("car-select").addEventListener("change", event => selectCar(event.target.value));
$("message-filter").addEventListener("change", event => { messageFilter = event.target.value; renderMessages(); });
$("fleet").addEventListener("click", event => { const row = event.target.closest("[data-car]"); if (row) selectCar(row.dataset.car, true); });
$("yard").addEventListener("click", event => { const group = event.target.closest("[data-car]"); if (group) selectCar(group.dataset.car, true); });
$("yard").addEventListener("keydown", event => { if (["Enter"," "].includes(event.key)) { const group = event.target.closest("[data-car]"); if (group) {event.preventDefault();selectCar(group.dataset.car, true);} } });
$("messages").addEventListener("click", event => {
  const row = event.target.closest("[data-message]");
  if (!row) return;
  const record = messageHistory.find(r => r.payload.message_id === row.dataset.message);
  if (!record) return;
  $("payload").textContent = JSON.stringify(record, null, 2);
  $("payload-details").open = true;
});
function selectCar(id, showDetails = false) {
  selectedCar = id;
  if (state) render();
  if (showDetails && window.matchMedia("(max-width: 950px)").matches) {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    document.querySelector(".inspector").scrollIntoView({block:"start", behavior});
  }
}

function updateMarkup(id, markup) {
  const element = $(id);
  if (element.innerHTML === markup) return;
  const active = document.activeElement;
  const carKey = element.contains(active) ? active.closest("[data-car]")?.dataset.car : null;
  element.innerHTML = markup;
  if (carKey) {
    const target = [...element.querySelectorAll("[data-car]")].find(e => e.dataset.car === carKey);
    (target?.querySelector("button") || target)?.focus({preventScroll:true});
  }
}
function lifecycleStep(car) {
  if (!car) return -1;
  if (car.state === "parked") return 2;
  return car.slot_id && !["waiting", "requesting"].includes(car.state) ? 1 : 0;
}
function renderProgress(metrics, cars) {
  const total = Math.max(0, num(metrics.total, cars.length));
  const completed = Math.max(0, Math.min(total, num(metrics.completed)));
  const progress = $("parking-progress");
  if (!progress) return;
  progress.setAttribute("aria-valuemax", String(total || 1));
  progress.setAttribute("aria-valuenow", String(completed));
  progress.setAttribute("aria-valuetext", `${completed} / ${total} autó leparkolt`);
  const fill = progress.querySelector(".progress-fill");
  if (fill) fill.style.width = `${total ? completed / total * 100 : 0}%`;
  if ($("parking-progress-label")) set("parking-progress-label", `${completed} / ${total} autó megérkezett`);
}
function render() {
  const world = state.world || {}, cars = world.cars || [], slots = state.coordinator?.slots || [];
  const metrics = world.metrics || {}, active = cars.find(c => !["parked","waiting"].includes(c.state));
  const complete = num(metrics.completed) === cars.length && cars.length > 0;
  set("run-status", state.paused ? "Szüneteltetve" : complete ? "Futás befejezve" : !state.connected ? "Szívverés hiányzik" : "Fut a szimuláció");
  $("status-dot").className = `dot ${state.paused ? "muted" : !state.connected ? "warning" : ""}`;
  set("sim-clock", `${fmt(state.sim_time)} s`); set("run-id", `#${state.run_id}`);
  set($("play-label") ? "play-label" : "play", pendingControls.has("paused") ? "Kérés…" : state.paused ? "Indítás" : "Szünet");
  const playIcon = $("play").querySelector(".icon");
  if (playIcon) playIcon.className = `icon icon-player-${state.paused ? "play" : "pause"}`;
  document.querySelectorAll("[data-speed]").forEach(b => b.setAttribute("aria-pressed", String(Number(b.dataset.speed) === state.speed)));
  document.querySelectorAll("[data-action]").forEach(b => b.setAttribute("aria-pressed", String(Boolean(state.controls?.[b.dataset.action]))));
  $("kpi-completed").innerHTML = `${num(metrics.completed)} <small>/ ${num(metrics.total, 3)}</small>`;
  set("kpi-progress", complete ? "Minden autó megérkezett" : active ? `${active.id} az aktív autó` : "Indulásra vár");
  $("kpi-free").innerHTML = `${slots.filter(s => s.state === "free").length} <small>/ ${slots.length || 8}</small>`;
  set("kpi-reserved", `${slots.filter(s => s.state === "reserved").length} lefoglalt · ${slots.filter(s => s.state === "occupied").length} parkolt`);
  set("kpi-stops", num(metrics.pedestrian_stops)); set("kpi-messages", num(state.mqtt?.received).toLocaleString("hu-HU"));
  set("kpi-heartbeat", `Szívverés: ${fmt(state.mqtt?.heartbeat_age_s, 2)} s ${state.connected ? "· rendben" : "· hiányzik"}`);
  set("broker-address", `TCP · 127.0.0.1:${state.mqtt?.port || 19085}`);
  set("log-file", state.log_file || "—");
  set("map-status", state.paused ? "Szünet · utolsó fogadott minta" : `${10 * state.speed} minta/s · ${state.speed}×`);
  if ($("map-receipt-label")) set("map-receipt-label", state.paused ? "MQTT-n fogadott minta · a futás szünetel" : "MQTT-n fogadott világállapot");
  renderProgress(metrics, cars);
  const choices = cars.map(c => c.id).join(",");
  if ($("car-select").dataset.choices !== choices) {
    $("car-select").dataset.choices = choices;
    $("car-select").innerHTML = cars.map(c => `<option value="${esc(c.id)}">${esc(c.id)}</option>`).join("");
  }
  if (!cars.some(c => c.id === selectedCar)) selectedCar = cars[0]?.id || "CAR01";
  $("car-select").value = selectedCar;
  renderMap(world, slots);
  renderParkingSetup();
  renderInspector(cars.find(c => c.id === selectedCar));
  const fleetMarkup = cars.map(c => `<tr data-car="${esc(c.id)}" class="${c.id === selectedCar ? "selected" : ""}"><td><button aria-label="${esc(c.id)} részletei">${esc(c.id)}</button></td><td>${badge(c.state)}</td><td class="mono">${esc(c.slot_id || "—")}</td><td class="mono">${fmt(c.speed)} m/s</td><td class="mono">${fmt(c.distance_m)} m</td></tr>`).join("");
  updateMarkup("fleet", fleetMarkup);
  const eventKey = `${state.run_id}:${(state.events || []).at(-1)?.seq}:${state.events?.length}`;
  if (eventKey !== lastEvents) {
    lastEvents = eventKey;
    $("events").innerHTML = state.events?.length ? [...state.events].reverse().map(e => `<li><time>${fmt(e.sim_time)} s</time><div><strong>${esc(e.car_id || "RENDSZER")} · ${esc(e.type)}</strong>${esc(e.message || "Állapotváltás")}</div></li>`).join("") : '<li class="empty">Indítsd el a futást az eseményekhez.</li>';
  }
  if (historyRun !== state.run_id) { historyRun = state.run_id; messageHistory = []; seenMessages.clear(); $("payload").textContent = "Kattints egy üzenetre a táblázatban."; }
  for (const record of state.messages || []) {
    const key = record.payload?.message_id;
    if (key && !seenMessages.has(key)) { seenMessages.add(key); messageHistory.push(record); }
  }
  // Local inspection window; authoritative full history remains in the JSONL file.
  if (messageHistory.length > 600) {
    for (const record of messageHistory.splice(0, messageHistory.length - 600)) seenMessages.delete(record.payload.message_id);
  }
  renderMessages();
  syncPendingControls();
  if (transportError) markStale();
}

function sensorApproach(car) {
  return Boolean(car && !["waiting", "parked"].includes(car.state)
    && car.sensor?.distance_to_stop_m != null);
}

// Data-bound SVG: each unit is 0.1 m. The drawing does not add obstacles to the model.
function vehicleGlyph(color = "teal", prefix = "map") {
  return `<rect x="-16" y="-11" width="8" height="3" rx="1" fill="#273d4a"/>
    <rect x="9" y="-11" width="8" height="3" rx="1" fill="#273d4a"/>
    <rect x="-16" y="8" width="8" height="3" rx="1" fill="#273d4a"/>
    <rect x="9" y="8" width="8" height="3" rx="1" fill="#273d4a"/>
    <rect class="car-body" x="-21" y="-9" width="42" height="18" rx="5.5" fill="url(#${prefix}-car-${color})" stroke="#fff" stroke-width="1.2"/>
    <path d="M -17 -6 Q -19 0 -17 6 M 16 -6 Q 19 0 16 6" fill="none" stroke="#fff" opacity=".3" stroke-width=".8"/>
    <path d="M -8 -6 H 8 L 12 0 L 8 6 H -8 L -11 0 Z" fill="url(#${prefix}-car-glass)" stroke="#ffffff70" stroke-width=".5"/>
    <path d="M 7 -5 L 9 0 L 7 5 M -6 -5 L -8 0 L -6 5" fill="none" stroke="#294b5b" stroke-width="1.2"/>
    <path d="M -3 -5 V 5" stroke="#e9f6f6" opacity=".55" stroke-width=".7"/>
    <path d="M 18 -6 V -3 M 18 3 V 6" stroke="#fff2ce" stroke-width="2" stroke-linecap="round"/>
    <path d="M -19 -6 V -4 M -19 4 V 6" stroke="#e98577" stroke-width="1.5" stroke-linecap="round"/>`;
}
function carGradients(prefix = "map") {
  return `<linearGradient id="${prefix}-car-teal" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#468c99"/><stop offset=".5" stop-color="#126373"/><stop offset="1" stop-color="#24576c"/></linearGradient>
    <linearGradient id="${prefix}-car-amber" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#d9ad64"/><stop offset=".5" stop-color="#b67d30"/><stop offset="1" stop-color="#946432"/></linearGradient>
    <linearGradient id="${prefix}-car-navy" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#718799"/><stop offset=".5" stop-color="#405a70"/><stop offset="1" stop-color="#304958"/></linearGradient>
    <linearGradient id="${prefix}-car-glass" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#e2f3f6"/><stop offset="1" stop-color="#8eb3c5"/></linearGradient>`;
}
function renderMap(world, centralSlots) {
  const cars = world.cars || [], slots = world.map?.slots || [], crossing = Boolean(world.crossing?.active);
  const central = new Map(centralSlots.map(slot => [slot.id, slot]));
  const selected = cars.find(car => car.id === selectedCar);
  const slotMarkup = slots.map(slot => {
    const status = central.get(slot.id)?.state || slot.state;
    const paint = {free:["#f7fcf9","#8fae9f","#397763"],reserved:["#e8efff","#5d86c8","#3d6caf"],occupied:["#e0e8ee","#8098a9","#526d80"]}[status]
      || ["#eef2f4","#b2c0c9","#8496a0"];
    const x = num(slot.x) * 10;
    return `<g data-slot="${esc(slot.id)}" data-slot-state="${esc(status)}">
      <path d="M ${x-17} 103 V 139 H ${x+17} V 103" fill="#dfe6e9"/>
      <rect x="${x-21}" y="41" width="42" height="62" rx="5" fill="${paint[0]}" stroke="${paint[1]}" stroke-width="1.5"/>
      <path d="M ${x-17} 98 H ${x+17}" stroke="${paint[2]}" stroke-width="2.5"/>
      <text x="${x}" y="34" text-anchor="middle" font-size="10" fill="#496174" font-weight="650">${esc(slot.id)}</text>
      <rect x="${x-3}" y="47" width="6" height="2" rx="1" fill="${paint[2]}" opacity=".7"/>
    </g>`;
  }).join("");
  const routePoints = selected?.route?.map(point => `${num(point[0])*10},${num(point[1])*10}`).join(" ");
  const route = routePoints ? `<polyline points="${routePoints}" fill="none" stroke="#fff" stroke-width="4.5" stroke-linejoin="round" opacity=".85"/>
    <polyline points="${routePoints}" fill="none" stroke="#36899d" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"/>` : "";
  const vehicles = cars.map(car => {
    const x = num(car.x)*10, y = num(car.y)*10, chosen = car.id === selectedCar;
    const stopped = car.state?.endsWith("stop");
    const color = stopped ? "amber" : car.state === "parked" || car.state === "waiting" ? "navy" : "teal";
    const range = Math.max(0, num(car.sensor?.range_m));
    const radius = range * 10, side = radius * Math.sin(Math.PI/10), forward = radius * Math.cos(Math.PI/10);
    // This is a range illustration, not a camera or a geometric detection algorithm.
    const cone = chosen && sensorApproach(car) && car.sensor?.valid && range > 0
      ? `<g data-sensor-overlay="true" data-range-m="${range}" transform="translate(${x} ${y}) rotate(${num(car.heading)})">
          <path d="M 0 0 L ${forward} ${-side} A ${radius} ${radius} 0 0 1 ${forward} ${side} Z" fill="${car.sensor.pedestrian ? "#d8a264" : "#399795"}" opacity=".13"/>
          <path d="M ${forward} ${-side} A ${radius} ${radius} 0 0 1 ${forward} ${side}" fill="none" stroke="${car.sensor.pedestrian ? "#bc894a" : "#5eaaa5"}" stroke-width="1" stroke-dasharray="3 3"/>
        </g>` : "";
    const labelY = y + (car.state === "waiting" ? -20 : 30);
    return `${cone}<g class="car-hit" data-car="${esc(car.id)}" data-x-m="${num(car.x)}" data-y-m="${num(car.y)}" tabindex="0" role="button" aria-label="${esc(car.id)} – ${esc(labels[car.state] || car.state)}">
      <title>${esc(car.id)} · ${esc(labels[car.state] || car.state)}</title>
      ${chosen ? `<circle cx="${x}" cy="${y}" r="27" fill="#ffffff60" stroke="#4c9e9d" stroke-width="1.2" stroke-dasharray="3 3"/>` : ""}
      <g transform="translate(${x} ${y}) rotate(${num(car.heading)})" filter="url(#vehicle-shadow)">${vehicleGlyph(color)}</g>
      <rect x="${x-21}" y="${labelY-9}" width="42" height="13" rx="4" fill="${chosen ? "#164b5e" : "#f8fbfd"}" stroke="${chosen ? "#164b5e" : "#d5e1e7"}" stroke-width=".6"/>
      <text x="${x}" y="${labelY}" text-anchor="middle" fill="${chosen ? "#ffffff" : "#506a7c"}" font-size="8" font-weight="650">${esc(car.id)}</text>
    </g>`;
  }).join("");
  const parkedVehicles = (world.parked_cars || []).filter(car => Number.isFinite(car.x) && Number.isFinite(car.y)).map(car => {
    const x = car.x * 10, y = car.y * 10;
    return `<g class="preparked-car" data-preparked="${esc(car.id || car.car_id)}" data-x-m="${car.x}" data-y-m="${car.y}" role="img" aria-label="${esc(car.id || car.car_id)} · már itt parkoló autó · ${esc(car.slot_id)}">
      <title>${esc(car.id || car.car_id)} · ${esc(car.slot_id)} · már itt parkoló autó</title>
      <g transform="translate(${x} ${y}) rotate(${num(car.heading, -90)})" filter="url(#vehicle-shadow)">${vehicleGlyph("navy")}</g>
      <rect x="${x-19}" y="${y+36}" width="38" height="13" rx="4" fill="#e7eef2" stroke="#b5c8d2" stroke-width=".6"/>
      <text x="${x}" y="${y+45}" text-anchor="middle" font-size="7.3" font-weight="650" fill="#526b7a">Helyben</text>
    </g>`;
  }).join("");
  updateMarkup("yard", `<title id="yard-title">Gyári udvar · ${num(world.metrics?.completed)} leparkolt autó</title>
    <desc id="yard-desc">A parkolók felül, a gyártósori kijárat bal alul. A zebra ${crossing ? "foglalt" : "szabad"}. A kiválasztott autó ${esc(selectedCar)}.</desc>
    <defs>${carGradients()}
      <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".6" fill="#cbd7dc"/></pattern>
      <linearGradient id="yard-ground" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f2f6f6"/><stop offset="1" stop-color="#e9f0f1"/></linearGradient>
      <filter id="vehicle-shadow" x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="1.5" stdDeviation="1.1" flood-color="#253f50" flood-opacity=".24"/></filter>
    </defs>
    <rect width="640" height="380" fill="url(#yard-ground)"/><rect width="640" height="380" fill="url(#grid)"/>
    <rect x="109" y="15" width="414" height="93" rx="9" fill="#ffffff80" stroke="#d8e2e6" stroke-width=".8"/>
    <text x="120" y="10" font-size="8" letter-spacing="1.5" fill="#6d8391">PARKOLÓZÓNA</text>
    <path d="M 80 280 H 560 V 140 H 140" fill="none" stroke="#c5d0d7" stroke-width="64" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M 80 280 H 560 V 140 H 140" fill="none" stroke="#f8fafb" stroke-width="61" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M 80 280 H 560 V 140 H 140" fill="none" stroke="#dce4e9" stroke-width="55" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M 90 280 H 560 V 140 H 140" fill="none" stroke="#aebfc9" stroke-width="1.1" stroke-dasharray="7 6"/>
    <path d="M 193 280 h 14 l -5 -4 m 5 4 l -5 4 M 472 280 h 14 l -5 -4 m 5 4 l -5 4 M 560 212 v -14 l -4 5 m 4 -5 l 4 5 M 335 140 h -14 l 5 -4 m -5 4 l 5 4" fill="none" stroke="#8aa4b4" stroke-width="1.4"/>
    ${slotMarkup}
    <rect x="25" y="181" width="113" height="66" rx="7" fill="#dfe9ed" stroke="#bfced6"/>
    <rect x="30" y="186" width="103" height="56" rx="4" fill="none" stroke="#ffffff85"/>
    <path d="M 39 200 H 61 V 210 H 82 V 200 H 107 V 210 H 123" fill="none" stroke="#9db6c4" stroke-width="2"/>
    <text x="81" y="230" text-anchor="middle" font-size="10" fill="#4e6b7c" font-weight="650">GYÁRTÓSOR</text>
    <text x="38" y="369" font-size="8" fill="#738b9a" letter-spacing="1.2">INDULÁSI SOR</text>
    <text x="340" y="193" text-anchor="middle" font-size="10" fill="#7e96a3" letter-spacing="2">GYÁRI UDVAR</text>
    <text x="340" y="208" text-anchor="middle" font-size="8" fill="#879da9">rögzített útvonal · helyi érzékelés</text>
    <rect x="279" y="249" width="42" height="62" rx="2" fill="${crossing ? "#f4dcb5" : "#becdd6"}" stroke="${crossing ? "#be8b4e" : "#a6bac6"}"/>
    ${Array.from({length:7},(_,i)=>`<rect x="282" y="${253+i*8}" width="36" height="4" fill="#fff"/>`).join("")}
    <path d="M 250 254 V 306" stroke="${crossing ? "#bb7a36" : "#8ca5b5"}" stroke-width="3"/>
    <text x="246" y="246" text-anchor="end" fill="#6c8290" font-size="7" font-weight="650">STOP</text>
    <text x="300" y="237" text-anchor="middle" font-size="8" fill="${crossing ? "#985c22" : "#607d8c"}" font-weight="650">ZEBRA · ${crossing ? "FOGLALT" : "SZABAD"}</text>
    ${route}${parkedVehicles}${vehicles}
    ${crossing ? `<g transform="translate(300 279)"><circle r="12" fill="#fff7e9" stroke="#bc853f" stroke-width="1.4"/><circle cy="-4" r="2.4" fill="#a16a2a"/><path d="M 0 -1 v 6 m 0 -4 l -4 3 m 4 -3 l 4 3 M 0 5 l -3 4 m 3 -4 l 3 4" fill="none" stroke="#a16a2a" stroke-width="1.6" stroke-linecap="round"/></g>` : ""}
    <path d="M 500 363 H 600 M 500 360 V 366 M 600 360 V 366" stroke="#8ca4b1" stroke-width="1"/>
    <text x="550" y="357" text-anchor="middle" fill="#6d8796" font-size="7">10 m</text>`);
}

function renderInspector(car) {
  if (!car) {
    for (const id of ["car-state","car-speed","car-slot","car-distance","car-stop-distance","car-reservation","inspector-car-name"]) {
      if ($(id)) set(id, "—");
    }
    set("car-reason", "A feliratkozó az első járműmintára vár.");
    set("sensor-result", "Még nincs járműminta");
    set("sensor-detail", "A fogadott érzékelési adat itt jelenik meg.");
    $("sensor").innerHTML = "";
    $("sensor").dataset.tone = "inactive";
    $("car-state").className = "state-badge";
    if ($("sensor-state-tag")) set("sensor-state-tag", "Még nincs minta");
    document.querySelectorAll("#car-steps [data-step]").forEach(step => { step.dataset.state = "upcoming"; step.removeAttribute("aria-current"); });
    return;
  }
  if ($("inspector-car-name")) set("inspector-car-name", car.id);
  set("car-state", labels[car.state] || car.state); $("car-state").className = `state-badge ${stateClass(car.state)}`;
  set("car-speed", `${fmt(car.speed)} m/s`); set("car-reason", car.reason);
  set("car-slot", car.slot_id || "Még nincs kiosztva"); set("car-distance", `${fmt(car.distance_m)} m`);
  set("car-stop-distance", car.sensor?.distance_to_stop_m == null ? "Másik útszakasz" : `${fmt(car.sensor.distance_to_stop_m)} m`);
  set("car-reservation", car.reservation_id || "—");
  const phase = lifecycleStep(car);
  document.querySelectorAll("#car-steps [data-step]").forEach((step,index) => {
    step.dataset.state = index < phase ? "complete" : index === phase ? "current" : "upcoming";
    if (index === phase) step.setAttribute("aria-current", "step"); else step.removeAttribute("aria-current");
  });
  const sensor = car.sensor || {}, failed = sensor.valid !== true, approach = sensorApproach(car), seen = Boolean(sensor.pedestrian);
  const tone = failed ? "danger" : !approach ? "inactive" : seen ? "warning" : "clear";
  $("sensor").dataset.tone = tone;
  if ($("sensor-state-tag")) { set("sensor-state-tag", failed ? "Hibás minta" : approach ? "Aktív megközelítés" : "Nem aktív útszakasz"); $("sensor-state-tag").dataset.tone = tone; }
  const color = failed ? "#bd6e67" : !approach ? "#8fa3b3" : seen ? "#bf8b43" : "#3a9893";
  const field = !failed && approach ? `<path d="M 150 132 L 80 28 A 125 125 0 0 1 220 28 Z" fill="${color}" opacity=".1"/>
    <path d="M 80 28 A 125 125 0 0 1 220 28 M 100 58 A 90 90 0 0 1 200 58 M 122 88 A 50 50 0 0 1 178 88" fill="none" stroke="${color}" opacity=".6" stroke-width="1" stroke-dasharray="3 4"/>
    <path d="M 150 132 L 80 28 M 150 132 L 220 28" stroke="${color}" opacity=".35" stroke-width="1"/>` : "";
  $("sensor").innerHTML = `<defs>${carGradients("sensor")}</defs>${field}
    <g transform="translate(150 121) rotate(-90)">${vehicleGlyph("teal", "sensor")}</g>
    ${seen && !failed && approach ? `<g transform="translate(150 47)"><circle r="15" fill="#fff4df" stroke="#c9944f"/><circle cy="-5" r="3" fill="#a47133"/><path d="M 0 -1 v 8 m 0 -5 l -5 4 m 5 -4 l 5 4 M 0 7 l -4 5 m 4 -5 l 4 5" stroke="#a47133" stroke-width="2" fill="none" stroke-linecap="round"/></g>` : ""}
    ${failed ? `<path d="M 141 42 l 18 18 m 0 -18 l -18 18" stroke="#bd6e67" stroke-width="2"/>` : ""}
    <text x="282" y="23" text-anchor="end" font-family="Consolas,monospace" font-size="10" fill="#6c8598">${fmt(sensor.range_m,0)} m</text>
    <text x="16" y="145" font-size="8" fill="#7f95a3">HATÓTÁV SZEMLÉLTETÉSE</text>`;
  set("sensor-result", failed ? "Érvénytelen érzékelőminta" : !approach ? "Zebraérzékelés ezen a szakaszon nincs" : seen ? "Gyalogos észlelve" : "Nincs észlelt gyalogos");
  set("sensor-detail", `Virtuális zebraérzékelő · ${fmt(sensor.range_m,0)} m · 10 Hz modellidőben`);
}

function renderMessages() {
  if ($("messages").contains(document.activeElement)) return;
  const items = messageHistory.filter(r => messageFilter === "all" || r.relative_topic.startsWith(messageFilter)).slice(-20).reverse();
  $("messages").innerHTML = items.map(r => `<tr data-message="${esc(r.payload.message_id)}"><td>${fmt(r.payload.sim_time)}</td><td><button aria-label="${esc(r.relative_topic)} csomag megnyitása">${esc(r.relative_topic)}</button></td><td>${num(r.qos)}</td><td>${fmt(r.latency_ms,2)}</td></tr>`).join("") || '<tr><td colspan="4">Még nincs ilyen fogadott üzenet az ablakban.</td></tr>';
}

async function poll() {
  try {
    const response = await fetch("/api/state", {cache:"no-store",signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const next = await response.json();
    if (!next.run_id) throw new Error("Az alkalmazás még indul.");
    state = next;
    transportError = next.error ? `A futás leállt: ${next.error}` : "";
    settleControls(next);
    settleParkingSetup(next);
    render();
  } catch (error) {
    transportError = `A műszerfal nem kap friss állapotot: ${error.message}`;
  } finally {
    document.body.classList.toggle("data-stale", Boolean(transportError));
    const error = transportError || controlError;
    if (error) showError(error); else $("error").hidden = true;
    if (transportError) markStale();
    if (state) settleParkingSetup(state);
    renderParkingSetup();
    syncPendingControls();
    setTimeout(poll, 300);
  }
}
function markStale() {
    $("status-dot").className = "dot warning"; set("run-status", "Nincs friss adat");
    set("map-status", "Utolsó ismert állapot · nincs friss kapcsolat");
    if ($("map-receipt-label")) set("map-receipt-label", "Nincs friss kapcsolat · a rajz korábbi mintát mutat");
    if ($("sensor-state-tag")) set("sensor-state-tag", "Korábbi észlelés");
}
function updateNavigation() {
  const links = [...document.querySelectorAll('.topbar nav a[href^="#"]')];
  const guide = (document.querySelector(".topbar")?.getBoundingClientRect().height || 0) + 80;
  let current = links[0];
  for (const link of links) {
    const target = document.querySelector(link.getAttribute("href"));
    if (target && target.getBoundingClientRect().top <= guide) current = link;
  }
  // The final section cannot always align under the header at the end of the page.
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = links.at(-1);
  links.forEach(link => {
    link.classList.toggle("is-active", link === current);
    if (link === current) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current");
  });
}
window.addEventListener("scroll", updateNavigation, {passive:true});
window.addEventListener("resize", updateNavigation);
window.addEventListener("hashchange", updateNavigation);
updateNavigation();
if (new URLSearchParams(location.search).has("embed")) document.body.classList.add("embed");
poll();
