// Browser test for the anova-cooker-card Lovelace card.
//
//   node tests/frontend/render.mjs
//
// Opens tests/frontend/harness.html over file:// in headless Chromium (Playwright), checks
// rendering and behaviour against the fake hass in the harness, and writes the screenshots
// docs/images/card-light.png and docs/images/card-dark.png. Exit code 1 when a check fails.
//
// Playwright is resolved from the local node_modules, or from the global one (npm root -g).

import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    const root = execSync("npm root -g").toString().trim();
    return require(path.join(root, "playwright"));
  }
}
const { chromium } = loadPlaywright();

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");
const HARNESS = pathToFileURL(path.join(HERE, "harness.html")).href;
const CARD_FILE = path.join(REPO, "custom_components", "anova_ble", "frontend", "anova-cooker-card.js");
const IMG_DIR = path.join(REPO, "docs", "images");

// ------------------------------------------------------------------ tiny test kit
let passed = 0;
const failures = [];
const consoleErrors = [];
const infoLogs = [];

function check(name, ok, detail = "") {
  if (ok) {
    passed += 1;
    console.log(`  PASS  ${name}`);
  } else {
    failures.push(name);
    console.log(`  FAIL  ${name}${detail ? "  -> " + detail : ""}`);
  }
}
const eq = (name, actual, expected) =>
  check(name, JSON.stringify(actual) === JSON.stringify(expected),
    `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
const near = (name, actual, expected, tol = 0.05) =>
  check(name, Math.abs(actual - expected) <= tol, `expected ${expected}, got ${actual}`);

const browser = await chromium.launch();

async function open({ query = "", viewport = { width: 800, height: 1400 }, clock = false, reducedMotion = "reduce" } = {}) {
  const context = await browser.newContext({ viewport, reducedMotion, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
    if (m.type() === "info") infoLogs.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
  if (clock) await page.clock.install({ time: 0 });
  await page.goto(HARNESS + query);
  await page.evaluate(() => window.__ready);
  if (clock) await page.clock.pauseAt(1000);
  return page;
}

const el = (page, card, name) => page.locator(`#c-${card} [data-el="${name}"]`);
const text = async (page, card, name) => (await el(page, card, name).textContent()).trim();
const visible = (page, card, name) => el(page, card, name).isVisible();
const calls = (page) => page.evaluate(() => window.__calls);
const clearCalls = (page) => page.evaluate(() => { window.__calls.length = 0; });
const settle = (page, ms = 80) => page.waitForTimeout(ms);
const visibleChips = (page, card) =>
  page.evaluate((c) => {
    const sr = document.querySelector(`#c-${c} anova-cooker-card`).shadowRoot;
    return [...sr.querySelectorAll(".chip")].filter((x) => !x.hidden).map((x) => x.dataset.el);
  }, card);
const pressed = (page, card, minutes) =>
  page.locator(`#c-${card} [data-min="${minutes}"]`).getAttribute("aria-pressed");

const SWEEP = 2 * Math.PI * 84 * 0.75;
const arcOffset = (page, card) =>
  page.evaluate((c) => parseFloat(document.querySelector(`#c-${c} anova-cooker-card`).shadowRoot
    .querySelector('[data-el="arcFill"]').style.strokeDashoffset), card);

// ------------------------------------------------------------------ 1. load, registration, config
console.log("\n[1] registration, config validation, stub config");
{
  const page = await open();
  const reg = await page.evaluate(() => ({
    card: !!customElements.get("anova-cooker-card"),
    editor: !!customElements.get("anova-cooker-card-editor"),
    cards: window.customCards,
  }));
  check("anova-cooker-card is defined", reg.card);
  check("anova-cooker-card-editor is defined", reg.editor);
  eq("window.customCards has one entry", reg.cards.length, 1);
  const c0 = reg.cards[0] || {};
  eq("customCards entry type/name/preview/docs", [c0.type, c0.name, c0.preview, c0.documentationURL],
    ["anova-cooker-card", "Anova Cooker Card", true, "https://github.com/gielk/ha-anova"]);
  check("customCards entry has a description", typeof c0.description === "string" && c0.description.length > 10);
  const banner = infoLogs.filter((t) => t.includes("ANOVA-COOKER-CARD"));
  check("one console.info line with the version", banner.length === 1 && banner[0].includes("0.1.0"), JSON.stringify(banner));

  await page.addScriptTag({ path: CARD_FILE }); // load the card a second time
  eq("double loading keeps one customCards entry", await page.evaluate(() => window.customCards.length), 1);
  eq("double loading logs the banner only once", infoLogs.filter((t) => t.includes("ANOVA-COOKER-CARD")).length, 1);

  const cfg = await page.evaluate(() => {
    const card = document.createElement("anova-cooker-card");
    const run = (c) => { try { card.setConfig(c); return null; } catch (e) { return String(e.message); } };
    return {
      missing: run({ type: "custom:anova-cooker-card" }),
      empty: run({ entity: "" }),
      notClimate: run({ entity: "light.kitchen" }),
      nullCfg: run(null),
      ok: run({ entity: "climate.anova", name: "x" }),
    };
  });
  check("setConfig throws without entity", /entity.*required/i.test(cfg.missing || ""), cfg.missing);
  check("setConfig throws for empty entity", !!cfg.empty);
  check("setConfig throws for a non climate entity", /climate/.test(cfg.notClimate || "") && /light\.kitchen/.test(cfg.notClimate), cfg.notClimate);
  check("setConfig throws for null", !!cfg.nullCfg);
  eq("setConfig accepts a climate entity", cfg.ok, null);

  const stub = await page.evaluate(() => {
    const C = customElements.get("anova-cooker-card");
    const st = (id) => ({ entity_id: id, state: "off", attributes: {} });
    const states = { "climate.other": st("climate.other"), "light.x": st("light.x"), "climate.anova": st("climate.anova") };
    const entities = { "climate.other": { platform: "other" }, "climate.anova": { platform: "anova_ble" } };
    const card = new C();
    return {
      own: C.getStubConfig({ states, entities }),
      fallback: C.getStubConfig({ states, entities: {} }),
      none: C.getStubConfig({ states: {}, entities: {} }),
      editor: C.getConfigElement().localName,
      size: card.getCardSize(),
      grid: card.getGridOptions(),
    };
  });
  eq("getStubConfig prefers the anova_ble climate", stub.own, { entity: "climate.anova" });
  eq("getStubConfig falls back to any climate", stub.fallback, { entity: "climate.other" });
  eq("getStubConfig without climates", stub.none, { entity: "" });
  eq("getConfigElement returns the editor", stub.editor, "anova-cooker-card-editor");
  check("getCardSize is a positive number", Number.isFinite(stub.size) && stub.size > 0);
  check("getGridOptions has columns and rows", stub.grid && stub.grid.columns > 0 && stub.grid.rows !== undefined, JSON.stringify(stub.grid));

  // ---------------------------------------------------------------- static rendering of the scenarios
  console.log("\n[2] static rendering: cooking, off, offline, warning");
  eq("cooking: name from friendly_name", await text(page, "cooking", "name"), "Anova Precision Cooker");
  eq("cooking: current temperature", await text(page, "cooking", "temp"), "54.2");
  eq("cooking: unit from the climate attributes", await text(page, "cooking", "unit"), "°C");
  eq("cooking: target", await text(page, "cooking", "target"), "56.5");
  eq("cooking: chips", await visibleChips(page, "cooking"), ["chipHeating", "chipTimer"]);
  eq("cooking: remaining time format", await text(page, "cooking", "remaining"), "1 h 05 min");
  eq("cooking: timer title", await text(page, "cooking", "timerTitle"), "Time remaining");
  eq("cooking: main button says Stop", await text(page, "cooking", "mainLbl"), "Stop");
  check("cooking: stop timer button visible", await visible(page, "cooking", "stopTimer"));
  near("cooking: arc progress from min temp to target", await arcOffset(page, "cooking"), SWEEP * (1 - (54.2 - 5) / (56.5 - 5)), 0.1);
  eq("cooking: 2 h preset is pressed (timer is 120)", await pressed(page, "cooking", 120), "true");
  eq("cooking: 1 h preset is not pressed", await pressed(page, "cooking", 60), "false");

  eq("off: current temperature", await text(page, "off", "temp"), "23.4");
  eq("off: chips", await visibleChips(page, "off"), ["chipOff"]);
  eq("off: main button says Start", await text(page, "off", "mainLbl"), "Start");
  eq("off: no timer text", await text(page, "off", "remaining"), "No timer");
  eq("off: timer title", await text(page, "off", "timerTitle"), "Timer");
  check("off: stop timer hidden", !(await visible(page, "off", "stopTimer")));
  near("off: arc progress", await arcOffset(page, "off"), SWEEP * (1 - (23.4 - 5) / (56.5 - 5)), 0.1);

  eq("offline: chips", await visibleChips(page, "offline"), ["chipOffline"]);
  eq("offline: temperature placeholder", await text(page, "offline", "temp"), "–");
  eq("offline: no unit shown", await text(page, "offline", "unit"), "");
  const offBtn = await page.evaluate(() => {
    const sr = document.querySelector("#c-offline anova-cooker-card").shadowRoot;
    return [...sr.querySelectorAll("button")].map((b) => b.disabled);
  });
  check("offline: every button is disabled", offBtn.length >= 6 && offBtn.every(Boolean), JSON.stringify(offBtn));
  check("offline: root has the offline class", await page.evaluate(() =>
    document.querySelector("#c-offline anova-cooker-card").shadowRoot.querySelector(".root").classList.contains("offline")));
  check("offline: arc is empty", await page.evaluate(() =>
    document.querySelector("#c-offline anova-cooker-card").shadowRoot.querySelector('[data-el="arcFill"]').classList.contains("empty")));

  eq("warning: chips (at temperature, timer inferred, warning)", await visibleChips(page, "warning"), ["chipAtTemp", "chipTimer", "chipWarn"]);
  eq("warning: chip text", await text(page, "warning", "chipWarn"), "Low water");
  eq("warning: remaining time", await text(page, "warning", "remaining"), "3 h 00 min");
  eq("warning: 4 h preset is pressed", await pressed(page, "warning", 240), "true");

  // accessible names via the accessibility tree (Playwright role selectors pierce shadow DOM)
  const roles = page.locator("#c-cooking");
  eq("cooking: plus button has an accessible name", await roles.getByRole("button", { name: "Increase target temperature" }).count(), 1);
  eq("cooking: minus button has an accessible name", await roles.getByRole("button", { name: "Decrease target temperature" }).count(), 1);
  eq("cooking: stop button has an accessible name", await roles.getByRole("button", { name: "Stop cooking" }).count(), 1);
  eq("cooking: preset exposes aria-pressed", await roles.getByRole("button", { name: "Timer 2 h", pressed: true }).count(), 1);
  eq("off: start button describes the action", await page.locator("#c-off").getByRole("button", { name: "Start cooking: 56.5 °C" }).count(), 1);
  eq("dial has an image role with a label", await page.locator("#c-cooking").getByRole("img", { name: /Water: 54\.2 °C, Target: 56\.5 °C/ }).count(), 1);

  // ---------------------------------------------------------------- editor
  console.log("\n[3] editor");
  const ed = await page.evaluate(() => {
    const form = document.querySelector("#editor ha-form");
    return {
      schema: form.schema,
      labels: form.schema.map((s) => form.computeLabel(s)),
      hasHass: form.hass === window.__scn.cooking.hass,
      data: form.data,
    };
  });
  eq("editor schema", ed.schema, [
    { name: "entity", required: true, selector: { entity: { domain: "climate", integration: "anova_ble" } } },
    { name: "name", selector: { text: {} } },
  ]);
  eq("editor labels are readable", ed.labels, ["Entity", "Name (optional)"]);
  check("editor passes hass to ha-form", ed.hasHass);
  eq("editor passes the config as data", ed.data.name, "Sous vide");
  await page.locator('#editor input[name="name"]').fill("Dinner");
  let last = (await page.evaluate(() => window.__configChanges)).at(-1);
  eq("config-changed carries the new config", last.config, { type: "custom:anova-cooker-card", entity: "climate.anova", name: "Dinner" });
  eq("config-changed bubbles and is composed", [last.bubbles, last.composed], [true, true]);
  await page.locator('#editor input[name="name"]').fill("");
  last = (await page.evaluate(() => window.__configChanges)).at(-1);
  eq("clearing the name removes the key", last.config, { type: "custom:anova-cooker-card", entity: "climate.anova" });
  await page.locator('#editor input[name="entity"]').fill("climate.other");
  last = (await page.evaluate(() => window.__configChanges)).at(-1);
  eq("changing the entity is reported", last.config.entity, "climate.other");

  // ---------------------------------------------------------------- motion
  console.log("\n[4] reduced motion");
  const dur = async () => page.evaluate(() => getComputedStyle(
    document.querySelector("#c-cooking anova-cooker-card").shadowRoot.querySelector('[data-el="arcFill"]')).transitionDuration);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  check("arc transition is animated by default", /0\.8s/.test(await dur()), await dur());
  await page.emulateMedia({ reducedMotion: "reduce" });
  check("prefers-reduced-motion turns the transition off", /^0s(, 0s)*$/.test(await dur()), await dur());
  await page.context().close();
}

// ------------------------------------------------------------------ 2. deterministic timing (fake clock)
console.log("\n[5] target stepper: debounce, clamp, press and hold, keyboard (fake clock)");
{
  const page = await open({ clock: true });
  const plus = el(page, "off", "plus");
  const minus = el(page, "off", "minus");
  const sent = async () => (await calls(page)).filter((c) => c.service === "set_temperature");

  await plus.click();
  eq("tap + updates the shown target at once", await text(page, "off", "target"), "56.6");
  eq("no service call yet", (await calls(page)).length, 0);
  await page.clock.runFor(799);
  eq("still no call after 799 ms", (await calls(page)).length, 0);
  await plus.click();
  await plus.click();
  eq("further taps accumulate", await text(page, "off", "target"), "56.8");
  await page.clock.runFor(799);
  eq("each tap restarts the 800 ms debounce", (await calls(page)).length, 0);
  await page.clock.runFor(2);
  eq("one set_temperature after the last tap", await sent(), [
    { scenario: "off", domain: "climate", service: "set_temperature", data: { entity_id: "climate.anova", temperature: 56.8 } },
  ]);
  eq("target still shown after HA reports it", await text(page, "off", "target"), "56.8");

  await clearCalls(page);
  await minus.click();
  await minus.click();
  eq("minus lowers by 0.1", await text(page, "off", "target"), "56.6");
  await page.clock.runFor(900);
  eq("minus sends the final value once", (await sent()).map((c) => c.data.temperature), [56.6]);

  // clamp at max_temp (99.9) and min_temp (5)
  await page.evaluate(() => window.__setState("off", "climate.anova", { attributes: { temperature: 99.8 } }));
  await clearCalls(page);
  for (let i = 0; i < 4; i++) await plus.click();
  eq("target is clamped at max_temp", await text(page, "off", "target"), "99.9");
  await page.clock.runFor(900);
  eq("clamped value is sent once", (await sent()).map((c) => c.data.temperature), [99.9]);
  await page.evaluate(() => window.__setState("off", "climate.anova", { attributes: { temperature: 5.1 } }));
  for (let i = 0; i < 4; i++) await minus.click();
  eq("target is clamped at min_temp", await text(page, "off", "target"), "5.0");
  await page.clock.runFor(900);

  // press and hold
  await page.evaluate(() => window.__setState("off", "climate.anova", { attributes: { temperature: 56.5 } }));
  await clearCalls(page);
  await plus.hover();
  await page.mouse.down();
  await page.clock.runFor(449);
  eq("hold: nothing before 450 ms", await text(page, "off", "target"), "56.5");
  await page.clock.runFor(2);
  eq("hold: first step at 450 ms", await text(page, "off", "target"), "56.6");
  await page.clock.runFor(270);
  eq("hold: repeats every 90 ms", await text(page, "off", "target"), "56.9");
  await page.mouse.up();
  eq("hold: releasing does not add a step (click swallowed)", await text(page, "off", "target"), "56.9");
  eq("hold: no call before the debounce", (await calls(page)).length, 0);
  await page.clock.runFor(800);
  eq("hold: one call with the final value", (await sent()).map((c) => c.data.temperature), [56.9]);

  await clearCalls(page);
  await page.mouse.down();
  await page.clock.runFor(451 + 90 * 16);
  near("hold: step grows x10 after a long hold", parseFloat(await text(page, "off", "target")), 59.5, 1e-6);
  await page.mouse.up();
  await page.clock.runFor(900);
  eq("long hold still sends one call", (await sent()).length, 1);

  // Start while a target change is still waiting for its debounce: start_cook carries it
  await clearCalls(page);
  await page.evaluate(() => window.__setState("off", "climate.anova", { attributes: { temperature: 56.5 } }));
  await plus.click();
  await el(page, "off", "main").click();
  eq("start with a pending target sends only start_cook with the shown value",
    (await calls(page)).map((c) => [c.service, c.data.temperature]), [["start_cook", 56.6]]);
  await page.clock.runFor(2000);
  eq("the pending set_temperature is cancelled", (await sent()).length, 0);
  await page.evaluate(() => window.__setState("off", "climate.anova", { state: "off" }));
  await page.evaluate(() => window.__setState("off", "binary_sensor.anova_cooking", { state: "off" }));

  // a normal tap right after a hold must work again
  await clearCalls(page);
  await page.evaluate(() => window.__setState("off", "climate.anova", { attributes: { temperature: 56.5 } }));
  await page.clock.runFor(1000);
  await plus.click();
  eq("tap after a hold works", await text(page, "off", "target"), "56.6");
  await page.clock.runFor(900);

  // keyboard: Enter on the focused button
  await clearCalls(page);
  await plus.focus();
  await page.keyboard.press("Enter");
  eq("keyboard Enter steps the target", await text(page, "off", "target"), "56.7");
  await page.keyboard.press("Space");
  eq("keyboard Space steps the target", await text(page, "off", "target"), "56.8");
  await page.clock.runFor(900);
  eq("keyboard steps are debounced too", (await sent()).map((c) => c.data.temperature), [56.8]);
  await page.context().close();
}

// ------------------------------------------------------------------ 3. cooking card interactions
console.log("\n[6] cooking card: timer presets, stop timer, stop");
{
  const page = await open();
  await clearCalls(page);

  await page.locator("#c-cooking [data-min='240']").click();
  await settle(page);
  eq("preset while cooking: set_value then start_timer", (await calls(page)).map((c) => [c.domain, c.service, c.data]), [
    ["number", "set_value", { entity_id: "number.anova_timer", value: 240 }],
    ["button", "press", { entity_id: "button.anova_start_timer" }],
  ]);
  eq("4 h preset is now pressed", await pressed(page, "cooking", 240), "true");
  eq("remaining time follows", await text(page, "cooking", "remaining"), "4 h 00 min");

  await clearCalls(page);
  await el(page, "cooking", "customBtn").click();
  check("custom opens the inline input", await visible(page, "cooking", "customInput"));
  check("custom input has focus", await page.evaluate(() =>
    document.querySelector("#c-cooking anova-cooker-card").shadowRoot.activeElement?.dataset.el === "customInput"));
  eq("custom input: bounds", await page.evaluate(() => {
    const i = document.querySelector("#c-cooking anova-cooker-card").shadowRoot.querySelector('[data-el="customInput"]');
    return [i.min, i.max, i.step, i.type];
  }), ["1", "6000", "1", "number"]);
  await el(page, "cooking", "customInput").fill("90");
  await el(page, "cooking", "customInput").press("Enter");
  await settle(page);
  eq("custom 90 min while cooking", (await calls(page)).map((c) => [c.service, c.data]), [
    ["set_value", { entity_id: "number.anova_timer", value: 90 }],
    ["press", { entity_id: "button.anova_start_timer" }],
  ]);
  eq("custom button shows as selected", await el(page, "cooking", "customBtn").getAttribute("aria-pressed"), "true");
  eq("remaining shows 1 h 30 min", await text(page, "cooking", "remaining"), "1 h 30 min");
  check("custom row closes after setting", !(await visible(page, "cooking", "customRow")));

  await clearCalls(page);
  await el(page, "cooking", "stopTimer").click();
  await settle(page);
  eq("stop timer presses the stop_timer button", (await calls(page)).map((c) => [c.domain, c.service, c.data]), [
    ["button", "press", { entity_id: "button.anova_stop_timer" }],
  ]);
  eq("timer chip disappears when the timer stops", await visibleChips(page, "cooking"), ["chipHeating"]);
  check("stop timer button hides", !(await visible(page, "cooking", "stopTimer")));

  await clearCalls(page);
  await el(page, "cooking", "main").click();
  await settle(page);
  eq("stop calls anova_ble.stop_cook", (await calls(page)).map((c) => [c.domain, c.service, c.data]), [
    ["anova_ble", "stop_cook", { entity_id: "climate.anova" }],
  ]);
  eq("button turns into Start after the state update", await text(page, "cooking", "mainLbl"), "Start");
  eq("chip becomes Off", await visibleChips(page, "cooking"), ["chipOff"]);
  await page.context().close();
}

// ------------------------------------------------------------------ 4. off card: presets, custom, start, focus
console.log("\n[7] off card: remembered preset, custom validation, start, focus retention");
{
  const page = await open();
  await clearCalls(page);

  await page.locator("#c-off [data-min='240']").click();
  await settle(page);
  eq("preset while off makes no service call", (await calls(page)).length, 0);
  eq("preset is remembered (pressed)", await pressed(page, "off", 240), "true");
  eq("planned duration is shown", await text(page, "off", "remaining"), "4 h 00 min");
  await page.locator("#c-off [data-min='240']").click();
  eq("tapping the chosen preset clears it", await pressed(page, "off", 240), "false");
  eq("planned duration is cleared", await text(page, "off", "remaining"), "No timer");

  // custom validation
  await el(page, "off", "customBtn").click();
  for (const bad of ["0", "6001", "-5", "1.5", ""]) {
    await el(page, "off", "customInput").fill(bad);
    await el(page, "off", "customSet").click();
    check(`custom rejects "${bad}"`, (await el(page, "off", "customInput").getAttribute("aria-invalid")) === "true"
      && (await visible(page, "off", "customHint")) && (await visible(page, "off", "customRow")));
  }
  eq("invalid custom values make no calls", (await calls(page)).length, 0);
  await el(page, "off", "customInput").fill("5");
  check("typing clears the validation message", !(await visible(page, "off", "customHint")));

  // focus survives hass updates (the DOM is patched, never rebuilt)
  await el(page, "off", "customInput").fill("45");
  await el(page, "off", "customInput").focus();
  const before = await page.evaluateHandle(() =>
    document.querySelector("#c-off anova-cooker-card").shadowRoot.querySelector('[data-el="customInput"]'));
  await page.evaluate(() => {
    window.__setState("off", "sensor.anova_status", { state: "stopped" });
    window.__setState("off", "sensor.anova_timer_remaining", { state: "3" });
    window.__setState("off", "climate.anova", { attributes: { current_temperature: 24.1 } });
  });
  const kept = await page.evaluate((b) => {
    const sr = document.querySelector("#c-off anova-cooker-card").shadowRoot;
    const input = sr.querySelector('[data-el="customInput"]');
    return { same: input === b, focused: sr.activeElement === input, value: input.value };
  }, before);
  eq("input element identity, focus and value survive hass updates", kept, { same: true, focused: true, value: "45" });
  eq("temperature text was patched meanwhile", await text(page, "off", "temp"), "24.1");

  await el(page, "off", "customInput").fill("90");
  await el(page, "off", "customSet").click();
  eq("valid custom is remembered, no call", (await calls(page)).length, 0);
  eq("planned custom duration shown", await text(page, "off", "remaining"), "1 h 30 min");
  eq("custom button selected", await el(page, "off", "customBtn").getAttribute("aria-pressed"), "true");

  await el(page, "off", "main").click();
  await settle(page);
  eq("start sends temperature and the duration object", (await calls(page)).map((c) => [c.domain, c.service, c.data]), [
    ["anova_ble", "start_cook", { entity_id: "climate.anova", temperature: 56.5, duration: { hours: 1, minutes: 30, seconds: 0 } }],
  ]);
  eq("button turns into Stop after the state update", await text(page, "off", "mainLbl"), "Stop");
  await page.context().close();
}

console.log("\n[8] start without a preset, busy state and inline error");
{
  const page = await open();
  await clearCalls(page);
  await page.evaluate(() => { window.__delay = 400; window.__fail = "boom: cooker not reachable"; });
  const main = el(page, "off", "main");
  await main.click();
  eq("busy: aria-busy is set while pending", await main.getAttribute("aria-busy"), "true");
  check("busy: button is disabled", await main.isDisabled());
  check("busy: spinner visible", await visible(page, "off", "spinner"));
  check("busy: no duplicate call on a second click", await (async () => { await main.click({ force: true }).catch(() => {}); return (await calls(page)).length === 1; })());
  await page.waitForFunction(() => document.querySelector("#c-off anova-cooker-card").shadowRoot
    .querySelector('[data-el="main"]').getAttribute("aria-busy") === "false");
  eq("failed start sends no duration key without a preset", (await calls(page))[0].data, { entity_id: "climate.anova", temperature: 56.5 });
  check("error text is shown inline", (await text(page, "off", "error")).includes("boom: cooker not reachable"), await text(page, "off", "error"));
  check("error is a live alert region", (await el(page, "off", "error").getAttribute("role")) === "alert");
  check("button is usable again", !(await main.isDisabled()));
  eq("button still says Start", await text(page, "off", "mainLbl"), "Start");

  await page.evaluate(() => { window.__delay = 0; window.__fail = null; });
  await main.click();
  await settle(page);
  check("error clears on the next action", !(await visible(page, "off", "error")));
  eq("start now works", await text(page, "off", "mainLbl"), "Stop");

  // a rejected target change shows the error and falls back to the real value
  await page.evaluate(() => { window.__fail = "denied"; });
  await el(page, "warning", "plus").click();
  eq("shown target moves immediately", await text(page, "warning", "target"), "56.6");
  await page.waitForFunction(() => document.querySelector("#c-warning anova-cooker-card").shadowRoot
    .querySelector('[data-el="error"]').textContent.includes("denied"), null, { timeout: 4000 });
  eq("rejected target change reverts to the reported value", await text(page, "warning", "target"), "56.5");

  // offline card: clicks do nothing
  await clearCalls(page);
  await page.evaluate(() => { window.__fail = null; });
  for (const name of ["main", "plus", "minus", "customBtn"]) await el(page, "offline", name).click({ force: true }).catch(() => {});
  await page.locator("#c-offline [data-min='60']").click({ force: true }).catch(() => {});
  await page.waitForTimeout(1000);
  eq("offline card never calls a service", (await calls(page)).length, 0);
  await page.context().close();
}

// ------------------------------------------------------------------ 5. patch only, no rebuild
console.log("\n[9] hass updates only patch the existing DOM");
{
  const page = await open();
  const r = await page.evaluate(() => {
    const card = document.querySelector("#c-cooking anova-cooker-card");
    const sr = card.shadowRoot;
    const main = sr.querySelector('[data-el="main"]');
    const records = [];
    const mo = new MutationObserver((rs) => records.push(...rs));
    mo.observe(sr, { subtree: true, childList: true, attributes: true, characterData: true });
    const drain = () => { records.push(...mo.takeRecords()); const n = records.length; records.length = 0; return n; };
    const out = {};
    card.hass = window.__scn.cooking.hass;
    out.sameHass = drain();
    window.__setState("cooking", "sensor.unrelated", { state: "2" });
    out.unrelated = drain();
    window.__setState("cooking", "climate.other", { state: "off" });
    out.otherClimate = drain();
    window.__setState("cooking", "climate.anova", { attributes: { current_temperature: 55.1 } });
    const changed = [...records, ...mo.takeRecords()];
    out.afterTemp = changed.length;
    out.onlyTextNodes = changed.filter((x) => x.type === "childList")
      .every((x) => [...x.addedNodes, ...x.removedNodes].every((n) => n.nodeType === 3));
    out.sameMain = sr.querySelector('[data-el="main"]') === main;
    mo.disconnect();
    return out;
  });
  eq("same hass again does no DOM work", r.sameHass, 0);
  eq("an unrelated entity change does no DOM work", r.unrelated, 0);
  eq("another climate entity does no DOM work", r.otherClimate, 0);
  check("a temperature change patches the DOM", r.afterTemp > 0);
  check("patching touches text, attributes and classes only (no elements added or removed)", r.onlyTextNodes);
  check("elements are the same objects after updates", r.sameMain);
  eq("temperature text updated", await text(page, "cooking", "temp"), "55.1");
  await page.context().close();
}

// ------------------------------------------------------------------ 6. layout checks
for (const width of [320, 360]) {
  console.log(`\n[10] layout at ${width}px: tap targets, overflow, accessible names`);
  const page = await open({ query: width === 320 ? "?width=320" : "", viewport: { width: width === 320 ? 320 : 800, height: 1800 } });
  // open the custom rows too, they have the widest content
  await el(page, "off", "customBtn").click();
  await el(page, "cooking", "customBtn").click();
  const res = await page.evaluate(() => {
    const out = { small: [], unnamed: [], overflow: [], count: 0 };
    for (const card of document.querySelectorAll("anova-cooker-card")) {
      const sr = card.shadowRoot;
      const id = card.parentElement.id;
      const root = sr.querySelector(".root");
      if (root.scrollWidth > root.clientWidth) out.overflow.push(`${id} root ${root.scrollWidth}>${root.clientWidth}`);
      for (const x of sr.querySelectorAll("button, input")) {
        if (!x.getClientRects().length) continue;
        out.count += 1;
        const b = x.getBoundingClientRect();
        if (b.width < 43.9 || b.height < 43.9) out.small.push(`${id} ${x.dataset.el || x.className} ${b.width.toFixed(1)}x${b.height.toFixed(1)}`);
        const name = x.getAttribute("aria-label") || x.textContent.trim();
        if (!name) out.unnamed.push(`${id} ${x.dataset.el || x.className}`);
        if (b.right > card.getBoundingClientRect().right + 0.5) out.overflow.push(`${id} ${x.dataset.el} sticks out`);
      }
    }
    out.docOverflow = document.documentElement.scrollWidth > window.innerWidth;
    return out;
  });
  check(`${res.count} interactive elements measured`, res.count > 30, String(res.count));
  eq("all tap targets are at least 44x44", res.small, []);
  eq("every button and input has an accessible name", res.unnamed, []);
  eq("nothing overflows its card", res.overflow, []);
  if (width === 320) check("no horizontal page scroll at 320px", !res.docOverflow);
  const h = await page.evaluate(() => Math.round(document.querySelector("#c-cooking anova-cooker-card").getBoundingClientRect().height));
  console.log(`  info  cooking card height at ${width}px: ${h}px (getCardSize units of 50px: ${(h / 50).toFixed(1)})`);
  await page.context().close();
}

// ------------------------------------------------------------------ 7. screenshots
console.log("\n[11] screenshots");
fs.mkdirSync(IMG_DIR, { recursive: true });
for (const theme of ["light", "dark"]) {
  const context = await browser.newContext({ viewport: { width: 800, height: 1400 }, reducedMotion: "reduce", deviceScaleFactor: 2 });
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
  await page.goto(HARNESS + (theme === "dark" ? "?theme=dark" : ""));
  await page.evaluate(() => window.__ready);
  await page.waitForTimeout(200);
  const file = path.join(IMG_DIR, `card-${theme}.png`);
  await page.locator("#stage").screenshot({ path: file });
  check(`wrote ${path.relative(REPO, file)}`, fs.existsSync(file) && fs.statSync(file).size > 10000, String(fs.existsSync(file) && fs.statSync(file).size));
  await context.close();
}

await browser.close();

// ------------------------------------------------------------------ result
console.log("");
eq("no console errors or page errors in any page", consoleErrors, []);
console.log(`\n${passed} checks passed, ${failures.length} failed`);
if (failures.length) {
  console.log("Failed:\n  " + failures.join("\n  "));
  process.exit(1);
}
