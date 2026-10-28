/* Production UI regression checks, without a browser or a second simulation.
 * The real app.js is evaluated with a minimal DOM and no automatic polling.
 * This verifies rendering/control contracts, not visual appearance.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const checks = [];

class Element {
  constructor(id = "", dataset = {}) {
    this.id = id; this.dataset = dataset; this.hidden = false;
    this.disabled = false; this.textContent = ""; this.className = "";
    this.attributes = {}; this.listeners = {}; this.writes = 0;
    this.children = []; this._markup = ""; this.value = "";
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.clientWidth = 640; this.offsetHeight = 76;
    this.style = {setProperty() {}};
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  getBoundingClientRect() { return {top: this.rectTop || 0, bottom: 76, height: 76, width: 640}; }
  contains(element) { return this === element || this.children.includes(element); }
  closest(selector) { return selector === "[data-car]" && this.dataset.car ? this : null; }
  focus() { document.activeElement = this; }
  querySelector(selector) {
    return selector === "button" ? this.button || null
      : selector === ".progress-fill" ? this.fill || null : null;
  }
  querySelectorAll(selector) { return selector === "[data-car]" ? this.children : []; }
  get innerHTML() { return this._markup; }
  set innerHTML(markup) {
    this.writes++; this._markup = markup;
    this.children = [...markup.matchAll(/data-car="([^"]+)"/g)].map(match => {
      const child = new Element("", {car: match[1]});
      if (markup.includes("<button")) child.button = new Element();
      return child;
    });
  }
}
const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element(match[1])]));
const speedButtons = [...html.matchAll(/data-speed="([^"]+)"/g)].map(match => new Element("", {speed: match[1]}));
const actionButtons = [...html.matchAll(/data-action="([^"]+)"/g)].map(match => new Element("", {action: match[1]}));
const lifecycleSteps = [...html.matchAll(/data-step="([^"]+)"/g)].map(match => new Element("", {step: match[1]}));
const navLinks = ["main", "evidence"].map(id => {
  const link = new Element(); link.setAttribute("href", "#" + id); return link;
});
if (elements.has("parking-progress")) elements.get("parking-progress").fill = new Element();
const document = {
  activeElement: null,
  body: new Element("body"), documentElement: new Element("html"),
  getElementById(id) { return elements.get(id) || null; },
  querySelectorAll(selector) {
    return selector === "[data-speed]" ? speedButtons
      : selector === "[data-action]" ? actionButtons
      : selector === "#car-steps [data-step]" ? lifecycleSteps
      : selector === '.topbar nav a[href^="#"]' ? navLinks
      : selector === "[data-action], [data-speed], #play, #reset" ? [...actionButtons, ...speedButtons, elements.get("play"), elements.get("reset")]
      : [];
  },
  querySelector(selector) { return selector.startsWith("#") ? elements.get(selector.slice(1)) : null; },
  addEventListener() {},
  fonts: {ready: Promise.resolve()}
};
const window = {addEventListener() {}, innerHeight: 900, scrollY: 0, requestAnimationFrame(fn) { fn(); }};
let fixtureNow = Date.now();
const sandbox = {
  document, window, location: {search: "", hash: ""}, URLSearchParams,
  console, setTimeout() { return 1; }, clearTimeout() {},
  requestAnimationFrame(fn) { fn(); return 1; },
  AbortSignal, fetch: async () => { throw Error("No fixture response"); },
  ResizeObserver: class {observe() {}},
  Date: class extends Date { static now() { return fixtureNow; } },
};
vm.createContext(sandbox);
assert(/\bpoll\(\);\s*$/.test(source), "Poll entry point must remain explicit for fixture isolation.");
vm.runInContext(source.replace(/\bpoll\(\);\s*$/, "") + `
globalThis.production = {
  esc,num,fmt,stateClass,updateMarkup,renderMap,renderInspector,render,renderMessages,poll,control,
  lifecycleStep,renderProgress,settleControls,selectCar,updateNavigation,
  freeParkingSlots,removableParkedCars,renderParkingSetup,settleParkingSetup,spawnParked,removeParked,
  setState(value) { state = value; transportError = ""; settleParkingSetup(value); },
  select(value) { selectedCar = value; },
};`, sandbox, {filename: "web/app.js"});
const ui = sandbox.production;

async function check(name, fn) {
  try { await fn(); checks.push({name, passed: true}); }
  catch (error) { checks.push({name, passed: false, error: error.stack || String(error)}); }
}
function stateFixture() {
  const slots = [{id: "P01", x: 14, y: 7, state: "free"}, {id: "P02", x: 19, y: 7, state: "free"}];
  return {
    run_id: "ui-fixture", paused: false, connected: true, speed: 1, sim_time: 12.3,
    mqtt: {received: 15, heartbeat_age_s: .1, last_message_age_s: .1, port: 19085},
    controls: {pedestrian: false, sensor_failure: false, link_failure: false},
    events: [], messages: [], coordinator: {slots: slots.map(slot => ({...slot}))}, parking_setup: null,
    world: {parked_cars: [], metrics: {completed: 0, total: 2}, crossing: {active: false}, map: {slots}, cars: [
      {id: "CAR01", x: 20, y: 28, heading: 0, speed: 2, state: "driving", slot_id: "P01",
       route: [[8, 28], [56, 28], [56, 14], [14, 14], [14, 7]],
       sensor: {valid: true, pedestrian: false, range_m: 15, distance_to_stop_m: 5}, distance_m: 12, reason: "Halad."},
      {id: "CAR02", x: 14, y: 35, heading: 0, speed: 0, state: "waiting", route: [], sensor: {valid: true, pedestrian: false, range_m: 15}},
    ]}
  };
}
function preparkedFixture(runId = "removal-fixture", placementId = "placement-one") {
  const s = stateFixture(); s.run_id = runId;
  const car = {id:"PARK-P01",car_id:"PARK-P01",slot_id:"P01",x:14,y:7,heading:-90,
    state:"parked",origin:"preparked",placement_id:placementId};
  s.world.parked_cars = [car];
  Object.assign(s.world.map.slots[0],{state:"occupied",car_id:car.id,placement_id:placementId});
  Object.assign(s.coordinator.slots[0],{state:"occupied",car_id:car.id,placement_id:placementId});
  return s;
}
async function main() {
  await check("last navigation section remains current at the document scroll limit", () => {
    elements.get("main").rectTop = -2500;
    elements.get("evidence").rectTop = -1000;
    document.documentElement.scrollHeight = 5000;
    window.innerHeight = 844; window.scrollY = 4156;
    ui.updateNavigation();
    assert.equal(navLinks[1].getAttribute("aria-current"), "location");
    assert.equal(navLinks.filter(link => link.getAttribute("aria-current")).length, 1);
    window.scrollY = 2500;
    ui.updateNavigation();
    assert.equal(navLinks[1].getAttribute("aria-current"), "location");
  });
  await check("dynamic HTML identifiers remain unique and resolve", () => {
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(ids.length, new Set(ids).size, "Duplicate HTML id");
    const references = [...source.matchAll(/(?:\$|set)\("([^"]+)"/g)].map(match => match[1]);
    for (const id of references) assert(elements.has(id), `Missing static UI target #${id}`);
  });
  await check("untrusted labels and non-finite telemetry are handled", () => {
    assert.equal(ui.esc('<CAR & "x"\'>'), "&lt;CAR &amp; &quot;x&quot;&#39;&gt;");
    for (const value of [NaN, Infinity, -Infinity, "3", null, undefined]) {
      assert.equal(ui.fmt(value), "—"); assert.equal(ui.num(value, 7), 7);
    }
    assert.equal(ui.fmt(3.25, 2), "3.25");
    assert.equal(ui.stateClass("parked"), "done");
    for (const state of ["sensor_stop", "communication_stop", "pedestrian_stop"]) assert.equal(ui.stateClass(state), "stop");
    assert.equal(ui.stateClass("driving"), "");
  });
  await check("unchanged markup preserves existing focus and DOM", () => {
    const element = elements.get("fleet"); element.innerHTML = '<tr data-car="CAR01"><td><button>CAR01</button></td></tr>';
    document.activeElement = element.children[0];
    const active = document.activeElement, writes = element.writes;
    ui.updateMarkup("fleet", element.innerHTML);
    assert.equal(element.writes, writes); assert.equal(document.activeElement, active);
  });
  await check("changed telemetry restores focused car row", () => {
    const element = elements.get("fleet"); element.innerHTML = '<tr data-car="CAR01"><td><button>CAR01</button></td></tr>';
    document.activeElement = element.children[0];
    ui.updateMarkup("fleet", '<tr data-car="CAR02"><td><button>2</button></td></tr><tr data-car="CAR01"><td><button>1</button></td></tr>');
    assert.equal(document.activeElement, element.children.find(child => child.dataset.car === "CAR01").button);
    document.activeElement = null;
  });
  await check("map uses received positions and offers keyboard vehicle selection", () => {
    const s = stateFixture(); ui.select("CAR01"); ui.renderMap(s.world, s.coordinator.slots);
    const markup = elements.get("yard").innerHTML;
    assert(markup.includes('data-car="CAR01"')); assert(markup.includes('data-car="CAR02"'));
    assert(markup.includes("translate(200 280)")); assert(markup.includes("translate(140 350)"));
    assert.equal((markup.match(/class="car-hit"/g) || []).length, 2);
    assert.equal((markup.match(/tabindex="0" role="button"/g) || []).length, 2);
    assert(!/NaN|undefined/.test(markup));
    assert(markup.includes('80,280 560,280 560,140 140,140 140,70'), "Assigned route missing");
  });
  await check("map escapes broker-derived labels instead of creating elements", () => {
    const s = stateFixture(); s.world.cars[0].id = '<script>alert("car")</script>';
    ui.select(s.world.cars[0].id); ui.renderMap(s.world, s.coordinator.slots);
    const markup = elements.get("yard").innerHTML;
    assert(!markup.includes("<script>")); assert(markup.includes("&lt;script&gt;"));
    ui.select("CAR01");
  });
  await check("empty received fleet and unknown sensor values render without invented measurements", () => {
    const s = stateFixture(); ui.setState(s); ui.render();
    assert.equal(elements.get("car-speed").textContent, "2.0 m/s");
    s.world.cars = []; s.world.metrics = {completed: 0, total: 0};
    ui.setState(s); ui.render();
    assert(!/NaN|undefined/.test(elements.get("yard").innerHTML));
    assert(elements.get("messages").innerHTML.includes("colspan"));
    assert.equal(elements.get("car-speed").textContent, "—");
    assert.equal(elements.get("sensor").innerHTML, "");
    assert(lifecycleSteps.every(step => step.dataset.state === "upcoming"));
  });
  await check("complete lifecycle is classified separately from a stopped assigned car", () => {
    assert(ui.lifecycleStep, "Production lifecycle helper missing");
    for (const state of ["waiting", "requesting"]) assert.equal(ui.lifecycleStep({state}), 0);
    for (const state of ["driving", "pedestrian_stop", "communication_stop", "sensor_stop"]) assert.equal(ui.lifecycleStep({state, slot_id: "P01"}), 1);
    assert.equal(ui.lifecycleStep({state: "parked", slot_id: "P01"}), 2);
    assert.equal(ui.lifecycleStep({state: "communication_stop", slot_id: null}), 0);
    assert.equal(ui.lifecycleStep(null), -1);
  });
  await check("lifecycle display marks the current phase and clears previous current state", () => {
    const car = stateFixture().world.cars[0];
    ui.renderInspector(car);
    assert.deepEqual(lifecycleSteps.map(step => step.dataset.state), ["complete", "current", "upcoming"]);
    car.state = "parked"; ui.renderInspector(car);
    assert.deepEqual(lifecycleSteps.map(step => step.dataset.state), ["complete", "complete", "current"]);
    assert.equal(lifecycleSteps.filter(step => step.attributes["aria-current"] === "step").length, 1);
  });
  await check("parking progress is clamped for empty, invalid and out-of-range received counts", () => {
    const progress = elements.get("parking-progress");
    for (const [metrics, cars, expected, maximum, width] of [
      [{total: 3, completed: 2}, [], 2, 3, 200 / 3],
      [{total: 3, completed: 9}, [], 3, 3, 100],
      [{total: 3, completed: -1}, [], 0, 3, 0],
      [{total: 0, completed: 0}, [], 0, 1, 0],
      [{total: NaN, completed: Infinity}, [{}, {}], 0, 2, 0],
    ]) {
      ui.renderProgress(metrics, cars);
      assert.equal(progress.getAttribute("aria-valuenow"), String(expected));
      assert.equal(progress.getAttribute("aria-valuemax"), String(maximum));
      assert(Math.abs(parseFloat(progress.fill.style.width) - width) < 1e-8);
    }
  });
  await check("range illustration uses received metres and is absent outside the sensing approach", () => {
    const s = stateFixture(), car = s.world.cars[0]; ui.select("CAR01");
    ui.renderMap(s.world, s.coordinator.slots);
    assert.match(elements.get("yard").innerHTML, /data-range-m="15"/);
    assert.match(elements.get("yard").innerHTML, /A 150 150 /);
    car.sensor.range_m = 5; ui.renderMap(s.world, s.coordinator.slots);
    assert.match(elements.get("yard").innerHTML, /data-range-m="5"/);
    assert.match(elements.get("yard").innerHTML, /A 50 50 /);
    car.sensor.distance_to_stop_m = null; ui.renderMap(s.world, s.coordinator.slots); ui.renderInspector(car);
    assert(!elements.get("yard").innerHTML.includes("data-sensor-overlay"));
    assert.equal(elements.get("sensor").dataset.tone, "inactive");
    car.sensor.distance_to_stop_m = 5; car.sensor.valid = false;
    ui.renderMap(s.world, s.coordinator.slots); ui.renderInspector(car);
    assert(!elements.get("yard").innerHTML.includes("data-sensor-overlay"));
    assert.equal(elements.get("sensor").dataset.tone, "danger");
  });
  await check("map and inspector SVG definitions do not create duplicate document identifiers", () => {
    const s = stateFixture(); ui.renderMap(s.world, s.coordinator.slots); ui.renderInspector(s.world.cars[0]);
    const markup = elements.get("yard").innerHTML + elements.get("sensor").innerHTML;
    const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(ids.length, new Set(ids).size);
    const definitions = new Set(ids);
    for (const match of markup.matchAll(/url\(#([^\)]+)\)/g)) assert(definitions.has(match[1]), `Missing SVG definition ${match[1]}`);
  });
  await check("same pending toggle waits for received acknowledgement before another click", async () => {
    const s = stateFixture(); ui.setState(s); ui.settleControls(s);
    const button = actionButtons.find(button => button.dataset.action === "pedestrian");
    const requests = [];
    sandbox.fetch = async (_url, options) => {
      requests.push(JSON.parse(options.body)); return {ok: true, json: async () => ({queued: true})};
    };
    const first = button.listeners.click(), repeated = button.listeners.click();
    await Promise.all([first, repeated]);
    assert.equal(requests.length, 1); assert.equal(button.disabled, true);
    assert.equal(button.getAttribute("aria-busy"), "true");
    s.controls.pedestrian = true; ui.settleControls(s); ui.setState(s); ui.render();
    assert.equal(button.disabled, false); assert.equal(button.getAttribute("aria-pressed"), "true");
  });
  await check("different controls are sent in the order clicked", async () => {
    const s = stateFixture(); ui.setState(s); ui.settleControls(s);
    const requests = []; let release;
    sandbox.fetch = async (_url, options) => {
      requests.push(JSON.parse(options.body));
      if (requests.length === 1) await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => ({queued: true})};
    };
    const first = ui.control("speed", {value: 4});
    const second = ui.control("sensor_failure", {active: true});
    await Promise.resolve(); await Promise.resolve();
    assert.equal(requests.length, 1, "Second command bypassed pending first command");
    release(); await Promise.all([first, second]);
    assert.deepEqual(requests, [{action: "speed", value: 4,run_id:s.run_id}, {action: "sensor_failure", active: true,run_id:s.run_id}]);
  });
  await check("main UI contains neither the defense section, footer nor comparison links", () => {
    assert(!/<footer\b|id="learn"|href="#learn"|FELKÉSZÜLÉS A VÉDÉSRE/.test(html));
    assert(!/8765|href="\/compare"|Nagy változat|Változatok összehasonlítása/.test(html));
    assert(html.includes('class="map-footer"'), "Map sample receipt must remain visible");
    assert(html.includes('id="parked-status" class="parked-status" role="status" aria-live="polite"'));
  });
  await check("parked selection uses confirmed free slots and preserves user selection on unchanged polls", () => {
    const s = stateFixture(); s.run_id = "setup-choices";
    ui.setState({...s, world: {cars: [], metrics: {}}}); ui.render();
    assert.equal(elements.get("spawn-parked").disabled, true);
    assert.equal(elements.get("parked-slot").value, "");
    ui.setState(s); ui.render();
    assert.equal(elements.get("parked-slot").value, "P01");
    elements.get("parked-slot").value = "P02";
    document.activeElement = elements.get("parked-slot");
    const writes = elements.get("parked-slot").writes;
    ui.render();
    assert.equal(elements.get("parked-slot").value, "P02");
    assert.equal(elements.get("parked-slot").writes, writes);
    assert.equal(document.activeElement, elements.get("parked-slot"));
    s.coordinator.slots[1].state = "reserved"; ui.render();
    assert.equal(elements.get("parked-slot").value, "P01");
    assert(!elements.get("parked-slot").innerHTML.includes('value="P02"'));
    s.world.map.slots[0].state = "occupied"; ui.render();
    assert.equal(elements.get("spawn-parked").disabled, true);
    document.activeElement = null;
  });
  await check("HTTP acceptance does not draw a parked car; success needs matching ack and both received states", async () => {
    const s = stateFixture(); s.run_id = "setup-success"; ui.setState(s); ui.render();
    const requests = [];
    sandbox.fetch = async (_url, options) => {
      const body = JSON.parse(options.body); requests.push(body);
      return {ok: true, json: async () => ({request_id: body.request_id})};
    };
    await Promise.all([ui.spawnParked(), ui.spawnParked()]);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].action, "spawn_parked");
    assert.equal(requests[0].slot_id, "P01"); assert.equal(requests[0].run_id, s.run_id);
    assert(requests[0].request_id);
    assert.equal(elements.get("spawn-parked").disabled, true);
    assert.equal(elements.get("parked-count").textContent, "0 helyben álló");
    assert(!elements.get("yard").innerHTML.includes('data-preparked='));
    s.parking_setup = {kind:"parked_spawned",operation:"spawn",placement_id:requests[0].request_id,request_id:requests[0].request_id,run_id:s.run_id,slot_id:"P01",car_id:"PARK-P01"};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "true", "Ack alone must not settle");
    s.world.parked_cars = [{id:"PARK-P01",car_id:"PARK-P01",slot_id:"P01",x:14,y:7,heading:-90,state:"parked",origin:"preparked",placement_id:requests[0].request_id}];
    s.world.map.slots[0].state = "occupied";
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "true", "World sample alone must not settle");
    s.coordinator.slots[0] = {...s.coordinator.slots[0],state:"occupied",car_id:"PARK-P01",placement_id:requests[0].request_id};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "false");
    assert.equal(elements.get("parked-status").dataset.tone, "success");
    assert.equal(elements.get("parked-slot").value, "P02");
    assert.equal(elements.get("parked-count").textContent, "1 helyben álló");
    assert(elements.get("yard").innerHTML.includes('data-preparked="PARK-P01"'));
    assert(elements.get("yard").innerHTML.includes("translate(140 70) rotate(-90)"));
    assert(!elements.get("car-select").innerHTML.includes("PARK-P01"));
    assert(!elements.get("fleet").innerHTML.includes("PARK-P01"));
    assert(elements.get("kpi-completed").innerHTML.startsWith("0 "));
    s.world.map.slots[1].state = "occupied"; s.coordinator.slots[1].state = "occupied"; ui.render();
    assert.match(elements.get("parked-status").textContent, /eltávolításával helyet szabadíthatsz/);
  });
  await check("unrelated or wrong-run acknowledgements cannot settle a parked placement", async () => {
    const s = stateFixture(); s.run_id = "setup-mismatch"; ui.setState(s); ui.render(); let request;
    sandbox.fetch = async (_url, options) => {
      request = JSON.parse(options.body); return {ok:true,json:async () => ({request_id:request.request_id})};
    };
    await ui.spawnParked();
    s.parking_setup = {kind:"parking_setup_rejected",operation:"spawn",slot_id:request.slot_id,car_id:`PARK-${request.slot_id}`,placement_id:request.request_id,request_id:"other-request",run_id:s.run_id,message:"Unrelated"};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "true");
    s.parking_setup = {...s.parking_setup,request_id:request.request_id,run_id:"old-run"};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "true");
    s.parking_setup = {...s.parking_setup,run_id:s.run_id,message:'<img src=x onerror="alert(1)">A hely foglalt.'};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "false");
    assert.equal(elements.get("parked-status").textContent, s.parking_setup.message);
    assert.equal(elements.get("parked-status").dataset.tone, "error");
  });
  await check("parked placement handles HTTP rejection without optimistic mutation", async () => {
    const s = stateFixture(); s.run_id = "setup-http-error"; ui.setState(s); ui.render();
    sandbox.fetch = async () => ({ok:false,json:async () => ({error:"A futás időközben megváltozott."})});
    await ui.spawnParked();
    assert.equal(elements.get("spawn-parked").disabled, false);
    assert.equal(elements.get("parked-status").dataset.tone, "error");
    assert.match(elements.get("parked-status").textContent, /futás/);
    assert.equal(s.world.parked_cars.length, 0);
  });
  await check("new run clears pending placement and ignores the old HTTP response", async () => {
    const s = stateFixture(); s.run_id = "setup-reset-before"; ui.setState(s); ui.render(); let release;
    sandbox.fetch = async (_url, options) => {
      const request = JSON.parse(options.body); await new Promise(resolve => { release = resolve; });
      return {ok:true,json:async () => ({request_id:request.request_id})};
    };
    const old = ui.spawnParked(); await Promise.resolve(); await Promise.resolve();
    s.run_id = "setup-reset-after"; ui.setState(s); ui.render();
    assert.equal(elements.get("spawn-parked").getAttribute("aria-busy"), "false");
    release(); await old;
    assert.equal(elements.get("parked-status").dataset.tone, "");
    assert.equal(elements.get("parked-count").textContent, "0 helyben álló");
  });
  await check("missing placement acknowledgement times out and does not keep the UI blocked", async () => {
    const s = stateFixture(); s.run_id = "setup-timeout"; ui.setState(s); ui.render();
    sandbox.fetch = async (_url, options) => ({ok:true,json:async () => ({request_id:JSON.parse(options.body).request_id})});
    await ui.spawnParked(); fixtureNow += 8100; ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("spawn-parked").disabled, false);
    assert.equal(elements.get("parked-status").dataset.tone, "error");
    assert.match(elements.get("parked-status").textContent, /visszaigazolása nem érkezett/);
  });
  await check("parked map glyphs escape received identity and skip missing coordinates", () => {
    const s = stateFixture(); s.world.parked_cars = [
      {id:'<script>parked</script>',slot_id:'P01" onload="x',x:14,y:7,heading:-90},
      {id:"MISSING-POSITION",slot_id:"P02",x:NaN,y:7}
    ];
    ui.renderMap(s.world, s.coordinator.slots);
    const markup = elements.get("yard").innerHTML;
    assert(!markup.includes("<script>")); assert(markup.includes("&lt;script&gt;parked&lt;/script&gt;"));
    assert(markup.includes("&quot; onload=&quot;x"));
    assert(!markup.includes("MISSING-POSITION")); assert(!/NaN|undefined/.test(markup));
  });
  await check("removal lists only centrally confirmed manual placements and preserves selection", () => {
    const s = preparkedFixture("remove-choices");
    s.world.parked_cars.push({id:"PARK-P02",car_id:"PARK-P02",slot_id:"P02",state:"parked",origin:"preparked"});
    s.coordinator.slots[1] = {...s.coordinator.slots[1],state:"occupied",car_id:"CAR02"};
    ui.setState(s); ui.render();
    assert.equal(ui.removableParkedCars().length, 1);
    assert.equal(elements.get("remove-parked-slot").value, "P01");
    assert(!elements.get("remove-parked-slot").innerHTML.includes('value="P02"'));
    document.activeElement = elements.get("remove-parked-slot");
    const writes = elements.get("remove-parked-slot").writes; ui.render();
    assert.equal(elements.get("remove-parked-slot").writes, writes);
    assert.equal(document.activeElement, elements.get("remove-parked-slot"));
    s.coordinator.slots[0].placement_id = "newer-placement"; ui.render();
    assert.equal(ui.removableParkedCars().length, 0);
    assert.equal(elements.get("remove-parked").disabled, true);
    assert.equal(elements.get("remove-parked-slot").value, "");
    document.activeElement = null;
  });
  await check("removal waits for matching acknowledgement and both received states without deleting optimistically", async () => {
    const s = preparkedFixture("remove-success"); ui.setState(s); ui.render();
    const requests = [];
    sandbox.fetch = async (_url, options) => {
      const request = JSON.parse(options.body); requests.push(request);
      return {ok:true,json:async () => ({request_id:request.request_id})};
    };
    await Promise.all([ui.removeParked(),ui.removeParked(),ui.spawnParked()]);
    assert.equal(requests.length, 1);
    const request = requests[0];
    assert.equal(request.action, "remove_parked"); assert.equal(request.slot_id, "P01");
    assert.equal(request.placement_id, "placement-one"); assert.equal(request.run_id, s.run_id);
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "true");
    assert.equal(elements.get("spawn-parked").disabled, true);
    assert.equal(elements.get("parked-slot").disabled, true);
    assert.equal(elements.get("remove-parked-slot").disabled, true);
    assert(elements.get("yard").innerHTML.includes('data-preparked="PARK-P01"'));
    assert.equal(elements.get("parked-count").textContent, "1 helyben álló");
    s.parking_setup = {...request,operation:"remove",car_id:"PARK-P01",kind:"parked_removed"};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "true", "Ack alone cannot delete the car");
    s.world.parked_cars = []; ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "true", "World absence alone cannot settle removal");
    s.coordinator.slots[0] = {...s.coordinator.slots[0],state:"reserved",car_id:"CAR01",placement_id:null};
    s.world.map.slots[0] = {...s.world.map.slots[0],state:"reserved",car_id:"CAR01",placement_id:null};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "false");
    assert.equal(elements.get("parked-status").dataset.tone, "success");
    assert.match(elements.get("parked-status").textContent, /eltávolítva/);
    assert(!elements.get("yard").innerHTML.includes('data-preparked="PARK-P01"'));
    assert.equal(elements.get("parked-count").textContent, "0 helyben álló");
    assert(elements.get("kpi-completed").innerHTML.startsWith("0 "));
    assert.equal(elements.get("remove-parked").disabled, true);
  });
  await check("removal ignores a wrong placement token and handles matching rejection as text", async () => {
    const s = preparkedFixture("remove-mismatch"); ui.setState(s); ui.render(); let request;
    sandbox.fetch = async (_url, options) => {
      request = JSON.parse(options.body); return {ok:true,json:async () => ({request_id:request.request_id})};
    };
    await ui.removeParked();
    s.parking_setup = {...request,operation:"remove",car_id:"PARK-P01",kind:"parking_setup_rejected",placement_id:"different-placement",message:"Unrelated"};
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "true");
    s.parking_setup.placement_id = request.placement_id;
    s.parking_setup.message = '<img src=x onerror="alert(1)">A foglaltság megváltozott.';
    ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "false");
    assert.equal(elements.get("parked-status").dataset.tone, "error");
    assert.equal(elements.get("parked-status").textContent, s.parking_setup.message);
    assert(elements.get("yard").innerHTML.includes('data-preparked="PARK-P01"'));
  });
  await check("HTTP removal rejection preserves the observed parked car and releases shared controls", async () => {
    const s = preparkedFixture("remove-http-error"); ui.setState(s); ui.render();
    sandbox.fetch = async () => ({ok:false,json:async () => ({error:"A kézi autó példánya már megváltozott."})});
    await ui.removeParked();
    assert.equal(elements.get("remove-parked").disabled, false);
    assert.equal(elements.get("spawn-parked").disabled, false);
    assert.equal(elements.get("parked-status").dataset.tone, "error");
    assert.equal(s.world.parked_cars.length, 1);
    assert(elements.get("yard").innerHTML.includes('data-preparked="PARK-P01"'));
  });
  await check("new run cancels pending removal and ignores the delayed old HTTP failure", async () => {
    const s = preparkedFixture("remove-reset-before"); ui.setState(s); ui.render(); let release;
    sandbox.fetch = async () => {
      await new Promise(resolve => {release = resolve;});
      return {ok:false,json:async () => ({error:"Old run rejection"})};
    };
    const old = ui.removeParked(); await Promise.resolve(); await Promise.resolve();
    const next = stateFixture(); next.run_id = "remove-reset-after"; ui.setState(next); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "false");
    release(); await old;
    assert.equal(elements.get("parked-status").dataset.tone, "");
    assert.equal(elements.get("parked-count").textContent, "0 helyben álló");
  });
  await check("full capacity explains individual removal and missing acknowledgement does not freeze management", async () => {
    const s = preparkedFixture("remove-timeout");
    s.world.map.slots[1].state = "occupied"; s.coordinator.slots[1].state = "occupied";
    ui.setState(s); ui.render();
    assert.match(elements.get("parked-status").textContent, /eltávolításával helyet szabadíthatsz/);
    assert.equal(elements.get("spawn-parked").disabled, true);
    assert.equal(elements.get("remove-parked").disabled, false);
    sandbox.fetch = async (_url, options) => ({ok:true,json:async () => ({request_id:JSON.parse(options.body).request_id})});
    await ui.removeParked(); fixtureNow += 8100; ui.settleParkingSetup(s); ui.render();
    assert.equal(elements.get("remove-parked").getAttribute("aria-busy"), "false");
    assert.equal(elements.get("remove-parked").disabled, false);
    assert.equal(elements.get("parked-status").dataset.tone, "error");
    assert.equal(s.world.parked_cars.length, 1);
  });
  await check("queued ordinary controls remain bound to their run and late replies cannot affect a new run", async () => {
    const s = stateFixture(); s.run_id = "ordinary-old-run"; ui.setState(s); ui.settleControls(s); ui.render();
    const requests = []; let release;
    sandbox.fetch = async (_url, options) => {
      requests.push(JSON.parse(options.body));
      if (requests.length === 1) await new Promise(resolve => {release = resolve;});
      return {ok:false,json:async () => ({error:"Old run rejection"})};
    };
    const first = ui.control("speed", {value:4}), queued = ui.control("sensor_failure", {active:true});
    await Promise.resolve(); await Promise.resolve();
    const next = stateFixture(); next.run_id = "ordinary-new-run"; ui.setState(next); ui.settleControls(next); ui.render();
    release(); await Promise.all([first,queued]);
    assert.equal(requests.length, 1, "Queued old-run command was sent into a new run");
    assert.equal(requests[0].run_id, "ordinary-old-run");
    assert.equal(speedButtons[0].disabled, false);
    assert.equal(actionButtons.find(button => button.dataset.action === "sensor_failure").disabled, false);
    assert.notEqual(elements.get("error").textContent, "Old run rejection");
  });
  await check("transport failure visibly marks the last state as stale", async () => {
    ui.setState(preparkedFixture("stale-management")); ui.render();
    sandbox.fetch = async () => { throw Error("Fixture connection lost"); };
    await ui.poll();
    assert.equal(elements.get("error").hidden, false);
    assert.match(elements.get("run-status").textContent, /Nincs friss adat|Kapcsolat|Elavult/i);
    assert.match(elements.get("map-status").textContent, /utolsó|nincs friss|elavult|szünet/i);
    ui.selectCar("CAR02");
    assert.match(elements.get("map-status").textContent, /utolsó|nincs friss|elavult|szünet/i);
    assert.equal(elements.get("play").disabled, true);
    assert.equal(elements.get("spawn-parked").disabled, true);
    assert.equal(elements.get("remove-parked").disabled, true);
    assert.match(elements.get("parked-status").textContent, /Nincs friss kapcsolat/);
  });
  const failed = checks.filter(check => !check.passed);
  process.stdout.write(JSON.stringify({passed: !failed.length, checks_run: checks.length, checks}, null, 2) + "\n");
  process.exitCode = failed.length ? 1 : 0;
}
main().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
