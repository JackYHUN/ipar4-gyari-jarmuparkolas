/* Real Chromium, HTTP, and MQTT smoke test. Requires Playwright separately. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium } = require(process.env.MINI_YARD_PLAYWRIGHT || "playwright");

const python = process.env.MINI_YARD_PYTHON || "python";
const service = spawn(python, ["-B", "-X", "utf8", "-c", `
import json, shutil, tempfile, threading
from pathlib import Path
from mini_yard.runtime import Application
from mini_yard.server import make_server
from tests.integration import free_port

with tempfile.TemporaryDirectory(prefix="mini-yard-browser-") as tmp:
    root = Path(tmp)
    shutil.copytree("web", root / "web")
    app = Application({"car_count": 2, "slot_count": 4, "dt": 0.05, "heartbeat_timeout_s": 0.75},
                      root, mqtt_port=free_port(), initially_paused=True)
    try:
        app.start()
        with make_server(app, 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(server.server_port, flush=True)
            input()
            server.shutdown()
            thread.join()
    finally:
        app.close()
`], {stdio:["pipe", "pipe", "inherit"], windowsHide:true});

async function main() {
  let browser;
  try {
    const address = await Promise.race([
      once(service.stdout, "data").then(([data]) => {
        const port = Number(data.toString().trim());
        assert.ok(port > 0, data.toString());
        return `http://127.0.0.1:${port}/`;
      }),
      once(service, "exit").then(([code]) => { throw Error(`The test server exited: ${code}`); }),
    ]);
    browser = await chromium.launch(process.env.MINI_YARD_CHROME ? {executablePath:process.env.MINI_YARD_CHROME} : {});
    const context = await browser.newContext({viewport:{width:320,height:720}});
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.goto(address);
    await page.waitForFunction(() => document.querySelector("#project-subtitle").textContent.includes("2 autó · 4"));
    for (const width of [320, 390, 1280]) {
      await page.setViewportSize({width, height:720});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
                `Page overflows at ${width}px`);
      const reset = await page.locator("#reset").boundingBox();
      assert.ok(reset && reset.x >= 0 && reset.x + reset.width <= width, `Reset clipped at ${width}px`);
      assert.ok(await page.evaluate(() => {
        const card = document.querySelector(".inspector").getBoundingClientRect();
        return [...document.querySelectorAll("#car-steps li")].every(step => {
          const text = document.createRange();
          text.selectNodeContents(step);
          const bounds = text.getBoundingClientRect();
          return bounds.left >= card.left && bounds.right <= card.right;
        });
      }), `Parking phase clipped at ${width}px`);
    }
    const ax = await page.context().newCDPSession(page);
    const { nodes } = await ax.send("Accessibility.getFullAXTree");
    assert.ok(nodes.some(n => !n.ignored && n.role?.value === "button" && n.name?.value?.startsWith("CAR01 –")));
    const mapCar = page.locator("#yard [data-car='CAR02']");
    await mapCar.focus();
    await mapCar.press("Enter");
    assert.equal(await page.locator("#car-select").inputValue(), "CAR02");
    await page.locator("#spawn-parked").click();
    await page.waitForFunction(() => document.querySelector("#parked-status").textContent.includes("elhelyezve"));
    await page.locator("#remove-parked").click();
    await page.waitForFunction(() => document.querySelector("#parked-status").textContent.includes("eltávolítva"));
    await page.locator('[data-speed="4"]').click();
    await page.locator("#play").click();
    await page.waitForFunction(() => document.querySelector("#run-status").textContent.includes("Fut a"));
    assert.match(await page.locator("#map-status").textContent(), /80 névleges/);
    assert.match(await page.locator("#heartbeat-limit").textContent(), /0,75 s/);
    await page.waitForFunction(() => document.querySelector("#run-status").textContent.includes("befejezve"), null, {timeout:45000});
    const oldRun = await page.locator("#run-id").textContent();
    const messageButton = page.locator("#messages button").first();
    await messageButton.focus();
    await messageButton.press("Enter");
    assert.equal(await page.locator("#payload-details").getAttribute("open"), "");
    const secondPage = await context.newPage();
    await secondPage.goto(address);
    await secondPage.locator("#reset").click();
    await page.waitForFunction(run => document.querySelector("#run-id").textContent !== run, oldRun);
    assert.equal(await page.evaluate(() => document.activeElement.id), "message-filter");
    assert.equal(await page.locator("#payload-details").getAttribute("open"), null);
    await secondPage.close();
    await page.waitForFunction(() => document.querySelector("#run-status").textContent === "Szüneteltetve");
    await page.route("**/api/state", async route => {
      const live = await route.fetch();
      const state = await live.json();
      state.ready = false;
      state.mqtt.world_age_s = 99;
      await route.fulfill({json:state});
    });
    await page.waitForFunction(() => document.querySelector("#run-status").textContent === "Nincs friss adat");
    assert.equal(await page.locator("#play").isDisabled(), true);
    assert.match(await page.locator("#map-status").textContent(), /99\.0 s régi/);
    assert.deepEqual(pageErrors, []);
    process.stdout.write("Chromium OK: 320/390/1280 px, fázissor, AX, billentyűzet, futásváltás, MQTT-parkolás, frissesség.\n");
  } finally {
    if (browser) await browser.close();
    if (service.exitCode === null) {
      service.stdin.end("\n");
      await once(service, "exit");
    }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
