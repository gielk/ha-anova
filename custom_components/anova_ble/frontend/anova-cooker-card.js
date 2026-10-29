/**
 * Anova Cooker Card for the anova_ble integration (Anova Precision Cooker over Bluetooth).
 *
 * Plain Web Component, no build step, no CDN, no Lit. The DOM skeleton is built once,
 * every `set hass()` only patches text, attributes and classes (inputs keep focus) and
 * exits early when none of the entities the card depends on changed.
 *
 * Contract: docs/architecture.md (Entities, Services, Dashboard card).
 */
(() => {
  "use strict"; // also keeps this safe when the file is loaded as a classic script twice

  const CARD_VERSION = "0.1.0";
  const CARD_TAG = "anova-cooker-card";
  const EDITOR_TAG = "anova-cooker-card-editor";
  const PLATFORM = "anova_ble";

  const DEBOUNCE_MS = 800; // wait this long after the last +/- tap before calling the service
  const HOLD_DELAY_MS = 450; // press and hold: start repeating after this
  const HOLD_REPEAT_MS = 90;
  const HOLD_FAST_AFTER = 15; // repeats before the step grows x10
  const OVERRIDE_TTL_MS = 8000; // keep showing a sent target until HA reports it (or this passes)
  const AT_TEMP_BAND = 0.5; // |current - target| <= band counts as "at temperature"
  const DEFAULT_STEP = 0.1;
  const TIMER_MIN = 1;
  const TIMER_MAX = 6000;
  const PRESETS = [60, 120, 240]; // minutes
  const WARN_STATUS = { low_water: "warn", heater_error: "err", power_loss: "warn" };

  // Sibling entities on the same device: translation_key -> expected entity domain.
  const SIBLINGS = {
    timer: "number",
    timer_remaining: "sensor",
    start_timer: "button",
    stop_timer: "button",
    cooking: "binary_sensor",
    timer_running: "binary_sensor",
    status: "sensor",
  };

  // Gauge geometry: a 270 degree arc (open at the bottom) drawn with dash arrays so the
  // fill can be animated with a CSS transition on stroke-dashoffset.
  const RADIUS = 84;
  const CIRC = 2 * Math.PI * RADIUS;
  const SWEEP = CIRC * 0.75;
  const ARC_START_DEG = 135;

  // ---------------------------------------------------------------- translations
  const I18N = {
    en: {
      water: "Water", target: "Target",
      target_down: "Decrease target temperature", target_up: "Increase target temperature",
      start: "Start", stop: "Stop", start_aria: "Start cooking", stop_aria: "Stop cooking",
      timer: "Timer", remaining: "Time remaining", no_timer: "No timer",
      custom: "Custom", custom_aria: "Custom timer", custom_input: "Timer in minutes",
      set: "Set", min: "min", stop_timer: "Stop timer",
      chip_off: "Off", chip_offline: "Offline", chip_heating: "Heating",
      chip_at_temp: "At temperature", chip_above: "Above target", chip_timer: "Timer running",
      w_low_water: "Low water", w_heater_error: "Heater error", w_power_loss: "Power loss",
      err_range: "Enter 1 to 6000 minutes", err_failed: "Action failed",
      err_missing: "Entity not found",
    },
    nl: {
      water: "Water", target: "Doel",
      target_down: "Doeltemperatuur verlagen", target_up: "Doeltemperatuur verhogen",
      start: "Start", stop: "Stop", start_aria: "Koken starten", stop_aria: "Koken stoppen",
      timer: "Timer", remaining: "Resterende tijd", no_timer: "Geen timer",
      custom: "Eigen", custom_aria: "Eigen timer", custom_input: "Timer in minuten",
      set: "Instellen", min: "min", stop_timer: "Timer stoppen",
      chip_off: "Uit", chip_offline: "Offline", chip_heating: "Verwarmt",
      chip_at_temp: "Op temperatuur", chip_above: "Boven doel", chip_timer: "Timer loopt",
      w_low_water: "Laag waterniveau", w_heater_error: "Verwarmingsfout",
      w_power_loss: "Stroomonderbreking",
      err_range: "Voer 1 tot 6000 minuten in", err_failed: "Actie mislukt",
      err_missing: "Entiteit niet gevonden",
    },
  };
  const makeT = (lang) => {
    const d = I18N[String(lang || "en").slice(0, 2).toLowerCase()] || I18N.en;
    return (key) => d[key] || I18N.en[key] || key;
  };

  // ---------------------------------------------------------------- small helpers
  const num = (v) => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const numState = (st) => (st ? num(st.state) : null); // "unavailable" -> null
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const roundTo = (v, dec) => Math.round(v * 10 ** dec) / 10 ** dec;
  const decimalsOf = (step) => {
    const s = String(step);
    const i = s.indexOf(".");
    return i < 0 ? 0 : s.length - i - 1;
  };
  const fmtNum = (v, dec, lang) => {
    if (v === null || v === undefined) return "–";
    try {
      return v.toLocaleString(lang || undefined, {
        minimumFractionDigits: dec,
        maximumFractionDigits: dec,
      });
    } catch {
      return v.toFixed(dec);
    }
  };
  // 65 -> "1 h 05 min", 45 -> "45 min"
  const fmtDuration = (minutes) => {
    const m = Math.max(0, Math.round(minutes));
    const h = Math.floor(m / 60);
    return h > 0 ? `${h} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
  };
  const toDuration = (minutes) => ({
    hours: Math.floor(minutes / 60),
    minutes: minutes % 60,
    seconds: 0,
  });
  const normUnit = (u) => {
    if (typeof u !== "string" || !u.trim()) return null;
    const s = u.trim().toLowerCase().replace("°", "");
    if (s === "c" || s === "celsius") return "°C";
    if (s === "f" || s === "fahrenheit") return "°F";
    return u.trim();
  };

  // Patch helpers: remember the last value written per element and skip identical writes.
  const put = (el, key, val, apply) => {
    const c = el.__c || (el.__c = {});
    if (c[key] === val) return;
    c[key] = val;
    apply(el, val);
  };
  const setText = (el, v) => put(el, "t", v, (e, x) => { e.textContent = x; });
  const setHidden = (el, h) => put(el, "h", !!h, (e, x) => { e.hidden = x; });
  const setDisabled = (el, d) => put(el, "d", !!d, (e, x) => { e.disabled = x; });
  const setCls = (el, c, on) => put(el, "c:" + c, !!on, (e, x) => e.classList.toggle(c, x));
  const setStyle = (el, p, v) => put(el, "s:" + p, v, (e, x) => e.style.setProperty(p, x));
  const setAttr = (el, n, v) =>
    put(el, "a:" + n, v, (e, x) => (x === null || x === undefined ? e.removeAttribute(n) : e.setAttribute(n, x)));

  // ---------------------------------------------------------------- template
  const STYLE = `
    :host { display: block; }
    [hidden] { display: none !important; }
    *, *::before, *::after { box-sizing: border-box; }
    ha-card { display: block; overflow: hidden; }
    .root {
      --ac-text: var(--primary-text-color, #212121);
      --ac-muted: var(--secondary-text-color, #727272);
      --ac-line: var(--divider-color, rgba(127, 127, 127, 0.3));
      --ac-primary: var(--primary-color, #03a9f4);
      --ac-heat: var(--state-climate-heat-color, var(--state-active-color, #ff8100));
      --ac-ok: var(--success-color, #43a047);
      --ac-warn: var(--warning-color, #ffa600);
      --ac-err: var(--error-color, #db4437);
      --ac-accent: var(--ac-muted);
      display: flex; flex-direction: column; gap: 14px; padding: 16px; color: var(--ac-text);
    }
    .root.s-heating { --ac-accent: var(--ac-heat); }
    .root.s-attemp { --ac-accent: var(--ac-ok); }
    .root.s-active { --ac-accent: var(--ac-primary); }

    button {
      font: inherit; color: inherit; background: none; border: 0; margin: 0; padding: 0; cursor: pointer;
      touch-action: manipulation; -webkit-tap-highlight-color: transparent;
      user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
    }
    button:disabled { cursor: not-allowed; opacity: 0.4; }
    button:focus-visible, input:focus-visible { outline: 2px solid var(--ac-primary); outline-offset: 2px; }
    ha-icon { --mdc-icon-size: 24px; display: inline-flex; flex: none; }
    .name { font-size: 16px; font-weight: 500; line-height: 22px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* gauge */
    .dial { position: relative; width: 100%; max-width: 240px; margin: 0 auto; aspect-ratio: 200 / 172; container-type: inline-size; }
    .dial svg { display: block; width: 100%; height: 100%; overflow: visible; }
    .ring { fill: none; stroke-width: 14; stroke-linecap: round; stroke-dasharray: ${SWEEP.toFixed(2)} ${CIRC.toFixed(2)}; }
    .track { stroke: var(--ac-line); }
    .fill {
      stroke: var(--ac-accent); stroke-dashoffset: ${SWEEP.toFixed(2)}px;
      transition: stroke-dashoffset 0.8s cubic-bezier(0.4, 0, 0.2, 1), stroke 0.4s ease, opacity 0.3s ease;
    }
    .fill.empty { opacity: 0; }
    .root.s-off .fill { opacity: 0.45; }
    .knob {
      transform-origin: 100px 100px; transform: rotate(${ARC_START_DEG}deg);
      transition: transform 0.8s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease;
    }
    .knob circle { fill: var(--card-background-color, #fff); stroke: var(--ac-accent); stroke-width: 4; transition: stroke 0.4s ease; }
    .root.s-offline .knob { opacity: 0; }
    .center { position: absolute; left: 50%; top: 58.14%; transform: translate(-50%, -50%); text-align: center; }
    .cap { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ac-muted); }
    .temp { font-size: 46px; font-size: 19cqw; font-weight: 500; line-height: 1.1; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .unit { font-size: 0.4em; font-weight: 400; color: var(--ac-muted); margin-left: 2px; }
    .root.offline .dial { opacity: 0.5; filter: grayscale(1); }

    /* status chips */
    .chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; min-height: 30px; }
    .chip {
      --chip-c: var(--ac-muted);
      display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px 4px 8px; border-radius: 999px;
      font-size: 13px; line-height: 20px; color: var(--ac-text); border: 1px solid var(--ac-line);
    }
    .chip ha-icon { --mdc-icon-size: 16px; color: var(--chip-c); }
    @supports (color: color-mix(in srgb, red, blue)) {
      .chip { border-color: color-mix(in srgb, var(--chip-c) 45%, transparent); background: color-mix(in srgb, var(--chip-c) 14%, transparent); }
    }
    .chip.c-heat { --chip-c: var(--ac-heat); }
    .chip.c-ok { --chip-c: var(--ac-ok); }
    .chip.c-primary { --chip-c: var(--ac-primary); }
    .chip.c-warn { --chip-c: var(--ac-warn); }
    .chip.c-err { --chip-c: var(--ac-err); }

    /* target row */
    .target-row { display: grid; grid-template-columns: 48px 1fr 48px; align-items: center; gap: 12px; }
    .round {
      width: 48px; height: 48px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
      border: 1px solid var(--ac-line); transition: background-color 0.15s ease, transform 0.1s ease;
    }
    .round:hover:not(:disabled), .pill:hover:not(:disabled) { background: color-mix(in srgb, var(--ac-text) 8%, transparent); }
    .round:active:not(:disabled) { transform: scale(0.94); }
    .target { text-align: center; min-width: 0; }
    .target-val { display: block; font-size: 26px; font-weight: 500; line-height: 32px; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .root.offline .target { opacity: 0.5; }

    /* timer */
    .timer { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--ac-line); padding-top: 12px; }
    .timer-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
    .sec { font-size: 14px; color: var(--ac-muted); }
    .remaining { font-size: 18px; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .presets { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
    .pill {
      min-height: 44px; padding: 0 4px; border-radius: 10px; font-size: 14px; border: 1px solid var(--ac-line);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; transition: background-color 0.15s ease, border-color 0.15s ease;
    }
    .pill[aria-pressed="true"] { border-color: var(--ac-primary); font-weight: 600; background: color-mix(in srgb, var(--ac-primary) 16%, transparent); }
    .custom { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .custom input {
      flex: 1 1 90px; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 16px; color: var(--ac-text);
      background: var(--card-background-color, transparent); border: 1px solid var(--ac-line); border-radius: 10px;
    }
    .custom input[aria-invalid="true"] { border-color: var(--ac-err); }
    .custom .suffix { color: var(--ac-muted); }
    .hint { flex-basis: 100%; font-size: 13px; color: var(--ac-err); }
    .ghost, .small {
      min-height: 44px; padding: 0 14px; border-radius: 10px; border: 1px solid var(--ac-line);
      display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    }
    .small { min-width: 72px; border-color: var(--ac-primary); color: var(--ac-primary); font-weight: 500; }

    /* main action */
    .main {
      min-height: 48px; width: 100%; border-radius: 24px; padding: 0 20px; display: flex; align-items: center; justify-content: center; gap: 8px;
      font-size: 16px; font-weight: 500; letter-spacing: 0.02em; background: var(--ac-primary); color: var(--text-primary-color, #fff);
      transition: filter 0.15s ease, transform 0.1s ease;
    }
    .main.stop { background: var(--ac-err); }
    .main:hover:not(:disabled) { filter: brightness(1.08); }
    .main:active:not(:disabled) { transform: scale(0.985); }
    .spinner { width: 18px; height: 18px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: spin 0.8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes pulse { 50% { opacity: 0.7; } }
    .root.s-heating .fill { animation: pulse 2.4s ease-in-out infinite; }
    .error { font-size: 13px; line-height: 18px; text-align: center; color: var(--ac-err); overflow-wrap: anywhere; }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { transition: none !important; animation: none !important; }
    }
  `;

  const chip = (name, cls, icon) =>
    `<span class="chip ${cls}" data-el="${name}" hidden><ha-icon icon="${icon}"></ha-icon><span class="lbl"></span></span>`;

  const TEMPLATE = `
    <style>${STYLE}</style>
    <ha-card>
      <div class="root" data-el="root" hidden>
        <div class="name" data-el="name"></div>
        <div class="dial" data-el="dial" role="img">
          <svg viewBox="0 0 200 172" aria-hidden="true" focusable="false">
            <circle class="ring track" cx="100" cy="100" r="${RADIUS}" transform="rotate(${ARC_START_DEG} 100 100)"></circle>
            <circle class="ring fill empty" data-el="arcFill" cx="100" cy="100" r="${RADIUS}" transform="rotate(${ARC_START_DEG} 100 100)"></circle>
            <g class="knob" data-el="knob"><circle cx="${100 + RADIUS}" cy="100" r="9"></circle></g>
          </svg>
          <div class="center">
            <div class="cap" data-el="cap"></div>
            <div class="temp"><span data-el="temp"></span><span class="unit" data-el="unit"></span></div>
          </div>
        </div>
        <div class="chips" data-el="chips" aria-live="polite">
          ${chip("chipOffline", "", "mdi:bluetooth-off")}
          ${chip("chipOff", "", "mdi:power")}
          ${chip("chipHeating", "c-heat", "mdi:fire")}
          ${chip("chipAtTemp", "c-ok", "mdi:check-circle-outline")}
          ${chip("chipAbove", "c-primary", "mdi:arrow-up")}
          ${chip("chipTimer", "c-primary", "mdi:timer-outline")}
          ${chip("chipWarn", "c-warn", "mdi:alert-outline")}
        </div>
        <div class="target-row">
          <button class="round" data-el="minus" type="button"><ha-icon icon="mdi:minus"></ha-icon></button>
          <div class="target">
            <span class="sec" data-el="targetCap"></span>
            <span class="target-val"><span data-el="target"></span><span class="unit" data-el="targetUnit"></span></span>
          </div>
          <button class="round" data-el="plus" type="button"><ha-icon icon="mdi:plus"></ha-icon></button>
        </div>
        <section class="timer" data-el="timerSection">
          <div class="timer-head">
            <span class="sec" data-el="timerTitle"></span>
            <span class="remaining" data-el="remaining"></span>
          </div>
          <div class="presets" data-el="presets" role="group">
            ${PRESETS.map((m) => `<button class="pill" type="button" data-min="${m}" aria-pressed="false">${m / 60} h</button>`).join("")}
            <button class="pill" type="button" data-el="customBtn" aria-pressed="false" aria-expanded="false"></button>
          </div>
          <div class="custom" data-el="customRow" hidden>
            <input data-el="customInput" type="number" inputmode="numeric" min="${TIMER_MIN}" max="${TIMER_MAX}" step="1" aria-invalid="false" />
            <span class="suffix" data-el="customSuffix"></span>
            <button class="small" type="button" data-el="customSet"></button>
            <div class="hint" id="customHint" data-el="customHint" role="alert" hidden></div>
          </div>
          <button class="ghost" type="button" data-el="stopTimer" hidden>
            <ha-icon icon="mdi:timer-off-outline"></ha-icon><span data-el="stopTimerLbl"></span>
          </button>
        </section>
        <button class="main" type="button" data-el="main">
          <ha-icon data-el="mainIcon" icon="mdi:play"></ha-icon>
          <span class="spinner" data-el="spinner" hidden></span>
          <span data-el="mainLbl"></span>
        </button>
        <div class="error" data-el="error" role="alert" hidden></div>
      </div>
    </ha-card>
  `;

  // ---------------------------------------------------------------- the card
  class AnovaCookerCard extends HTMLElement {
    static getConfigElement() { return document.createElement(EDITOR_TAG); }

    static getStubConfig(hass) {
      const ids = Object.keys((hass && hass.states) || {}).filter((id) => id.startsWith("climate."));
      const reg = (hass && hass.entities) || {};
      const own = ids.find((id) => reg[id] && reg[id].platform === PLATFORM);
      return { entity: own || ids[0] || "" };
    }

    constructor() {
      super();
      this._config = null;
      this._hass = null;
      this._r = null; // element refs, built once
      this._m = null; // last view model (read by the event handlers)
      this._deps = null; // identities of everything the last render depended on
      this._dirty = true; // local UI state changed, render even when hass did not
      this._sibReg = undefined; this._sibEntity = null; this._sib = {};
      // local UI state
      this._preset = null; // {minutes} remembered while the cooker is not cooking
      this._customOpen = false; this._customInvalid = false;
      this._override = null; // {value, sent, until}: target shown before HA reports it
      this._busy = null; // "start" | "stop" | "timer" | "stoptimer" while a call is pending
      this._error = null;
      this._tm = {}; // timer handles: debounce, hold, repeat, expire, error
      this._swallowClickUntil = 0; this._holding = false; this._holdTicks = 0;
      this._warned = false;
    }

    setConfig(config) {
      if (!config || typeof config !== "object") throw new Error("anova-cooker-card: invalid configuration");
      const entity = config.entity;
      if (!entity || typeof entity !== "string") {
        throw new Error("anova-cooker-card: 'entity' is required, e.g. entity: climate.anova_precision_cooker");
      }
      if (!entity.startsWith("climate.")) {
        throw new Error(`anova-cooker-card: 'entity' must be a climate entity, got "${entity}"`);
      }
      if (this._config && this._config.entity !== entity) this._preset = this._override = this._error = null;
      this._config = { ...config };
      this._deps = null;
      this._sibReg = undefined;
      this._invalidate();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    get hass() { return this._hass; }

    // Masonry view: units of 50px (about 660px tall with the timer section).
    getCardSize() { return this._m && this._m.showTimer === false ? 9 : 13; }

    // Sections view: the height follows the content, so rows is "auto".
    getGridOptions() { return { columns: 6, min_columns: 4, rows: "auto" }; }

    connectedCallback() { this._invalidate(); }

    disconnectedCallback() {
      // A pending debounced temperature change is kept on purpose, the user did ask for it.
      this._stopHold();
      clearTimeout(this._tm.error);
      clearTimeout(this._tm.expire);
    }

    // ------------------------------------------------------------ rendering
    _invalidate() {
      this._dirty = true;
      this._render();
    }

    _render() {
      if (!this._config || !this._hass) return;
      try {
        this._ensureDom();
        const h = this._hass;
        const sib = this._siblings(h);
        const deps = [h.states[this._config.entity], sib, this._lang(h), this._unitSystem(h)];
        for (const k of Object.keys(SIBLINGS)) deps.push(sib[k] ? h.states[sib[k]] : undefined);
        if (!this._dirty && this._deps && deps.every((d, i) => d === this._deps[i])) return;
        this._deps = deps;
        this._dirty = false;
        this._m = this._model(h, sib);
        this._patch(this._m);
      } catch (err) {
        // never throw into the HA frontend; log once so the problem is still findable
        if (!this._warned) {
          this._warned = true;
          console.error("anova-cooker-card: render failed", err);
        }
      }
    }

    _lang(h) { return (h.locale && h.locale.language) || h.language || "en"; }

    _unitSystem(h) { return (h.config && h.config.unit_system && h.config.unit_system.temperature) || "°C"; }

    // Sibling entities of the climate entity: same device, platform anova_ble, matched by
    // translation_key. Cached until hass.entities (the registry) is replaced.
    _siblings(h) {
      const reg = h.entities;
      if (this._sibReg === reg && this._sibEntity === this._config.entity) return this._sib;
      const sib = {};
      const me = reg && reg[this._config.entity];
      if (me && me.device_id) {
        for (const id of Object.keys(reg)) {
          const e = reg[id];
          if (!e || e.platform !== PLATFORM || e.device_id !== me.device_id) continue;
          const dom = SIBLINGS[e.translation_key];
          if (dom && !sib[e.translation_key] && id.startsWith(dom + ".")) sib[e.translation_key] = id;
        }
      }
      this._sibReg = reg; this._sibEntity = this._config.entity; this._sib = sib;
      return sib;
    }

    // Everything the view needs, as plain values. Missing entities simply yield null/false.
    _model(h, sib) {
      const cfg = this._config;
      const S = (k) => (sib[k] ? h.states[sib[k]] : undefined);
      const st = h.states[cfg.entity];
      const a = (st && st.attributes) || {};
      const lang = this._lang(h);
      const t = makeT(lang);
      const missing = !st;
      const offline = missing || st.state === "unavailable" || st.state === "unknown";

      // The climate state is already in HA's unit system; the attribute is only a hint.
      const unit = normUnit(a.temperature_unit || a.unit_of_measurement || a.unit) || this._unitSystem(h);
      const min = num(a.min_temp) ?? (unit === "°F" ? 41 : 5);
      const max = num(a.max_temp) ?? (unit === "°F" ? 211.8 : 99.9);
      const step = num(a.target_temp_step) || DEFAULT_STEP;
      const dec = Math.max(1, decimalsOf(step));

      const current = offline ? null : num(a.current_temperature);
      let target = offline ? null : num(a.temperature);
      // Show the value the user just picked until HA reports it back.
      const ov = this._override;
      if (ov) {
        const reported = target !== null && Math.abs(target - ov.value) < 1e-6;
        if (offline || (ov.sent && (reported || Date.now() > ov.until))) this._override = null;
        else target = ov.value;
      }

      const cs = S("cooking");
      const cooking = !offline && (cs && (cs.state === "on" || cs.state === "off") ? cs.state === "on" : st.state === "heat");

      let state = "off";
      if (offline) state = "offline";
      else if (cooking) {
        state = "active";
        if (current !== null && target !== null) {
          if (current < target - AT_TEMP_BAND) state = "heating";
          else if (current > target + AT_TEMP_BAND) state = "above";
          else state = "attemp";
        }
      }

      // Arc: start at the minimum temperature of the cooker, full at the target.
      let progress = 0;
      if (current !== null && target !== null && !offline) {
        progress = target > min ? clamp((current - min) / (target - min), 0, 1) : current >= target ? 1 : 0;
      }

      // Timer. Prefer the timer_running binary sensor; without it, assume the timer
      // runs while cooking with time left.
      const tNum = S("timer");
      const tRem = S("timer_remaining");
      const tRun = S("timer_running");
      const timerSet = numState(tNum);
      const remaining = numState(tRem);
      const timerRunning =
        tRun && (tRun.state === "on" || tRun.state === "off")
          ? tRun.state === "on"
          : cooking && remaining !== null && remaining > 0;

      const statusSt = S("status");
      const warn = statusSt && WARN_STATUS[statusSt.state] ? statusSt.state : null;

      const name = cfg.name !== undefined && cfg.name !== null ? String(cfg.name) : a.friendly_name || "";
      const tempText = fmtNum(current, 1, lang);
      const targetText = fmtNum(target, dec, lang);
      const dialLabel = offline ? t("chip_offline") : `${t("water")}: ${tempText} ${unit}, ${t("target")}: ${targetText} ${unit}`;

      return {
        t, name, unit, dec, step, min, max, missing, offline, state, cooking, progress, current, target, tempText, targetText,
        sib, hasTimer: !!tNum, hasRemaining: !!tRem, hasStop: !!S("stop_timer"), showTimer: !!(tNum || tRem),
        timerSet, remaining, timerRunning, warn, dialLabel,
      };
    }

    _ensureDom() {
      if (this._r) return;
      const sr = this.shadowRoot || this.attachShadow({ mode: "open" });
      sr.innerHTML = TEMPLATE;
      const r = { presetBtns: Array.from(sr.querySelectorAll("[data-min]")) };
      sr.querySelectorAll("[data-el]").forEach((el) => { r[el.dataset.el] = el; });
      this._r = r;
      this._bind();
    }

    // Patch the existing DOM. Nothing is created or removed here.
    _patch(m) {
      const r = this._r;
      const t = m.t;
      this._t = t;
      const busy = this._busy;
      const dis = m.offline || !!busy;
      setHidden(r.root, false);

      for (const s of ["offline", "off", "heating", "attemp", "above", "active"]) setCls(r.root, "s-" + s, m.state === s);
      setCls(r.root, "offline", m.offline);

      // header + gauge
      setText(r.name, m.name);
      setHidden(r.name, !m.name);
      setText(r.cap, t("water"));
      setText(r.temp, m.tempText);
      setText(r.unit, m.current === null ? "" : m.unit);
      setAttr(r.dial, "aria-label", m.dialLabel);
      setStyle(r.arcFill, "stroke-dashoffset", `${(SWEEP * (1 - m.progress)).toFixed(2)}px`);
      setCls(r.arcFill, "empty", m.progress < 0.004);
      setStyle(r.knob, "transform", `rotate(${(ARC_START_DEG + 270 * m.progress).toFixed(2)}deg)`);

      // chips
      const chipOn = (el, on, label) => { setHidden(el, !on); if (on) setText(el.lastChild, label); };
      chipOn(r.chipOffline, m.offline, t("chip_offline"));
      chipOn(r.chipOff, !m.offline && !m.cooking, t("chip_off"));
      chipOn(r.chipHeating, m.state === "heating", t("chip_heating"));
      chipOn(r.chipAtTemp, m.state === "attemp", t("chip_at_temp"));
      chipOn(r.chipAbove, m.state === "above", t("chip_above"));
      chipOn(r.chipTimer, !m.offline && m.timerRunning, t("chip_timer"));
      chipOn(r.chipWarn, !m.offline && !!m.warn, m.warn ? t("w_" + m.warn) : "");
      if (m.warn) {
        setCls(r.chipWarn, "c-warn", WARN_STATUS[m.warn] === "warn");
        setCls(r.chipWarn, "c-err", WARN_STATUS[m.warn] === "err");
      }

      // target row
      setText(r.targetCap, t("target"));
      setText(r.target, m.targetText);
      setText(r.targetUnit, m.target === null ? "" : m.unit);
      for (const [btn, key] of [[r.minus, "target_down"], [r.plus, "target_up"]]) {
        setDisabled(btn, m.offline || m.target === null);
        setAttr(btn, "aria-label", t(key));
      }

      // timer section
      setHidden(r.timerSection, !m.showTimer);
      if (m.showTimer) this._patchTimer(m, dis);

      // main button
      const stopMode = m.cooking;
      const planned = this._preset ? `, ${fmtDuration(this._preset.minutes)}` : "";
      setCls(r.main, "stop", stopMode);
      setText(r.mainLbl, stopMode ? t("stop") : t("start"));
      setAttr(r.main, "aria-label", stopMode ? t("stop_aria") : `${t("start_aria")}: ${m.targetText} ${m.unit}${planned}`);
      setAttr(r.main, "aria-busy", busy === "start" || busy === "stop" ? "true" : "false");
      setAttr(r.mainIcon, "icon", stopMode ? "mdi:stop" : "mdi:play");
      setHidden(r.mainIcon, busy === "start" || busy === "stop");
      setHidden(r.spinner, !(busy === "start" || busy === "stop"));
      setDisabled(r.main, dis || (!stopMode && m.target === null));

      // inline error (service failure, or entity missing)
      const err = this._error || (m.missing ? `${t("err_missing")}: ${this._config.entity}` : "");
      setText(r.error, err);
      setHidden(r.error, !err);
    }

    _patchTimer(m, dis) {
      const { _r: r } = this;
      const t = m.t;
      // Selected preset: the cooker's timer while cooking, the local choice otherwise.
      const sel = m.cooking ? (m.timerSet > 0 ? m.timerSet : null) : this._preset ? this._preset.minutes : null;

      let right;
      if (m.cooking && m.hasRemaining) right = m.remaining > 0 ? fmtDuration(m.remaining) : t("no_timer");
      else if (m.cooking) right = m.timerSet > 0 ? fmtDuration(m.timerSet) : t("no_timer");
      else right = this._preset ? fmtDuration(this._preset.minutes) : t("no_timer");
      setText(r.timerTitle, m.cooking && m.remaining > 0 ? t("remaining") : t("timer"));
      setText(r.remaining, right);
      setHidden(r.presets, !m.hasTimer);
      setAttr(r.presets, "aria-label", t("timer"));
      for (const b of r.presetBtns) {
        setAttr(b, "aria-pressed", String(sel === Number(b.dataset.min)));
        setAttr(b, "aria-label", `${t("timer")} ${b.textContent}`);
        setDisabled(b, dis);
      }
      const customSel = sel !== null && !PRESETS.includes(sel);
      setText(r.customBtn, t("custom"));
      setAttr(r.customBtn, "aria-label", t("custom_aria"));
      setAttr(r.customBtn, "aria-pressed", String(customSel));
      setAttr(r.customBtn, "aria-expanded", String(this._customOpen));
      setDisabled(r.customBtn, dis);
      setHidden(r.customRow, !(m.hasTimer && this._customOpen));
      setAttr(r.customInput, "aria-label", t("custom_input"));
      setAttr(r.customInput, "aria-invalid", String(this._customInvalid));
      setAttr(r.customInput, "aria-describedby", this._customInvalid ? "customHint" : null);
      setText(r.customSuffix, t("min"));
      setText(r.customSet, t("set"));
      setDisabled(r.customSet, dis);
      setText(r.customHint, t("err_range"));
      setHidden(r.customHint, !this._customInvalid);
      setText(r.stopTimerLbl, t("stop_timer"));
      setHidden(r.stopTimer, !(m.timerRunning && m.hasStop));
      setDisabled(r.stopTimer, dis);
    }

    // ------------------------------------------------------------ events
    _bind() {
      const r = this._r;
      this._bindStepper(r.minus, -1);
      this._bindStepper(r.plus, 1);
      r.main.addEventListener("click", () => this._onMain());
      for (const b of r.presetBtns) b.addEventListener("click", () => this._onPreset(Number(b.dataset.min)));
      r.customBtn.addEventListener("click", () => this._toggleCustom());
      r.customSet.addEventListener("click", () => this._submitCustom());
      r.stopTimer.addEventListener("click", () => this._onStopTimer());
      r.customInput.addEventListener("keydown", (e) => {
        e.stopPropagation(); // keep dashboard hotkeys out of the field
        if (e.key === "Enter") {
          e.preventDefault();
          this._submitCustom();
        } else if (e.key === "Escape") {
          this._toggleCustom(false);
          r.customBtn.focus();
        }
      });
      r.customInput.addEventListener("input", () => {
        if (!this._customInvalid) return;
        this._customInvalid = false;
        this._invalidate();
      });
    }

    // +/- button: a tap steps once, press and hold repeats (and speeds up after a while).
    // The step happens on `click` so a swipe that starts on the button (scrolling the
    // dashboard) never changes the target; keyboard activation works for the same reason.
    _bindStepper(btn, dir) {
      btn.addEventListener("pointerdown", (e) => {
        if (e.pointerType === "mouse" && e.button !== 0) return;
        this._stopHold();
        this._holdTicks = 0;
        this._tm.hold = setTimeout(() => {
          this._holding = true; // the release must not add one more step through `click`
          this._stepTarget(dir);
          this._tm.repeat = setInterval(() => {
            this._holdTicks += 1;
            this._stepTarget(dir, this._holdTicks > HOLD_FAST_AFTER ? 10 : 1);
          }, HOLD_REPEAT_MS);
        }, HOLD_DELAY_MS);
      });
      btn.addEventListener("pointerup", () => {
        if (this._holding) this._swallowClickUntil = Date.now() + 400;
        this._stopHold();
      });
      for (const ev of ["pointercancel", "pointerleave"]) btn.addEventListener(ev, () => this._stopHold());
      btn.addEventListener("contextmenu", (e) => e.preventDefault());
      btn.addEventListener("click", () => {
        if (Date.now() < this._swallowClickUntil) return;
        this._stepTarget(dir);
      });
    }

    _stopHold() {
      this._holding = false;
      clearTimeout(this._tm.hold);
      clearInterval(this._tm.repeat);
    }

    // Change the shown target at once, send it to HA 800 ms after the last change.
    _stepTarget(dir, mult = 1) {
      const m = this._m;
      if (!m || m.offline || m.target === null) return;
      const v = clamp(roundTo(m.target + dir * mult * m.step, m.dec), m.min, m.max);
      if (v === m.target) return;
      this._override = { value: v, sent: false, until: Infinity };
      clearTimeout(this._tm.debounce);
      this._tm.debounce = setTimeout(() => this._sendTarget(), DEBOUNCE_MS);
      this._invalidate();
    }

    async _sendTarget() {
      const ov = this._override;
      this._tm.debounce = null;
      if (!ov || ov.sent) return;
      this._markSent(ov);
      try {
        await this._call("climate", "set_temperature", { entity_id: this._config.entity, temperature: ov.value });
      } catch (e) {
        if (this._override === ov) this._override = null;
        this._setError(e);
      }
      this._invalidate();
    }

    _markSent(ov) {
      ov.sent = true;
      ov.until = Date.now() + OVERRIDE_TTL_MS;
      clearTimeout(this._tm.expire);
      this._tm.expire = setTimeout(() => this._invalidate(), OVERRIDE_TTL_MS + 50);
    }

    _onMain() {
      const m = this._m;
      if (!m || m.offline || this._busy) return;
      const id = this._config.entity;
      if (m.cooking) {
        return this._run("stop", () => this._call(PLATFORM, "stop_cook", { entity_id: id }));
      }
      if (m.target === null) return;
      const data = { entity_id: id, temperature: m.target };
      if (this._preset) data.duration = toDuration(this._preset.minutes);
      clearTimeout(this._tm.debounce); // start_cook carries the temperature itself
      if (this._override && !this._override.sent) this._markSent(this._override);
      return this._run("start", async () => {
        try {
          await this._call(PLATFORM, "start_cook", data);
        } catch (e) {
          this._override = null;
          throw e;
        }
      });
    }

    _onPreset(minutes) {
      if (this._m && !this._m.offline && !this._busy) this._applyTimer(minutes, true);
    }

    // While cooking the timer is set (and started) on the cooker, otherwise it is only
    // remembered here and sent along with Start. Tapping the chosen preset again clears it.
    _applyTimer(minutes, toggle) {
      const m = this._m;
      if (m.cooking) {
        if (!m.sib.timer) return;
        this._customOpen = false;
        return this._run("timer", async () => {
          await this._call("number", "set_value", { entity_id: m.sib.timer, value: minutes });
          if (m.sib.start_timer) await this._call("button", "press", { entity_id: m.sib.start_timer });
        });
      }
      this._preset = toggle && this._preset && this._preset.minutes === minutes ? null : { minutes };
      this._customOpen = false;
      this._invalidate();
    }

    _onStopTimer() {
      const m = this._m;
      if (!m || m.offline || this._busy || !m.sib.stop_timer) return;
      return this._run("stoptimer", () => this._call("button", "press", { entity_id: m.sib.stop_timer }));
    }

    _toggleCustom(open) {
      const m = this._m;
      if (!m || m.offline) return;
      this._customOpen = open === undefined ? !this._customOpen : open;
      this._customInvalid = false;
      if (this._customOpen) {
        const cur = m.cooking ? m.timerSet : this._preset && this._preset.minutes;
        this._r.customInput.value = cur && !PRESETS.includes(cur) ? String(cur) : "";
      }
      this._invalidate();
      if (this._customOpen) this._r.customInput.focus();
    }

    _submitCustom() {
      const m = this._m;
      if (!m || m.offline || this._busy) return;
      const v = Number(this._r.customInput.value);
      if (!Number.isInteger(v) || v < TIMER_MIN || v > TIMER_MAX) {
        this._customInvalid = true;
        this._invalidate();
        this._r.customInput.focus();
        return;
      }
      this._applyTimer(v, false);
    }

    // Run one service action with a busy state and an inline error on failure.
    async _run(kind, fn) {
      if (this._busy) return;
      this._busy = kind;
      this._setError(null);
      this._invalidate();
      try {
        await fn();
      } catch (e) {
        this._setError(e);
      } finally {
        this._busy = null;
        this._invalidate();
      }
    }

    async _call(domain, service, data) { return this._hass.callService(domain, service, data); }

    _setError(e) {
      clearTimeout(this._tm.error);
      if (e === null) {
        this._error = null;
        return;
      }
      const t = this._t || makeT("en");
      const msg = e && typeof e === "object" ? e.message : typeof e === "string" ? e : "";
      this._error = msg ? `${t("err_failed")}: ${msg}` : t("err_failed");
      this._tm.error = setTimeout(() => { this._error = null; this._invalidate(); }, 10000);
    }
  }

  // ---------------------------------------------------------------- the editor
  const EDITOR_SCHEMA = [
    { name: "entity", required: true, selector: { entity: { domain: "climate", integration: PLATFORM } } },
    { name: "name", selector: { text: {} } },
  ];
  const EDITOR_LABELS = { entity: "Entity", name: "Name (optional)" };

  class AnovaCookerCardEditor extends HTMLElement {
    setConfig(config) { this._config = { ...config }; this._sync(); }

    set hass(hass) { this._hass = hass; this._sync(); }

    _sync() {
      if (!this._form) {
        const form = document.createElement("ha-form");
        form.schema = EDITOR_SCHEMA;
        form.computeLabel = (s) => EDITOR_LABELS[s.name] || s.name;
        form.addEventListener("value-changed", (ev) => {
          ev.stopPropagation();
          const config = { ...(ev.detail && ev.detail.value) };
          if (!config.name) delete config.name;
          this._config = config;
          this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
        });
        this._form = form;
        this.appendChild(form);
      }
      if (this._hass) this._form.hass = this._hass;
      if (this._config && this._form.data !== this._config) this._form.data = this._config;
    }
  }

  // ---------------------------------------------------------------- registration
  const firstLoad = !customElements.get(CARD_TAG);
  if (firstLoad) customElements.define(CARD_TAG, AnovaCookerCard);
  if (!customElements.get(EDITOR_TAG)) customElements.define(EDITOR_TAG, AnovaCookerCardEditor);

  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c && c.type === CARD_TAG)) {
    window.customCards.push({
      type: CARD_TAG,
      name: "Anova Cooker Card",
      description: "Control an Anova Precision Cooker: water temperature, target, timer and start/stop.",
      preview: true,
      documentationURL: "https://github.com/gielk/ha-anova",
    });
  }

  if (firstLoad) {
    console.info(
      `%c ANOVA-COOKER-CARD %c v${CARD_VERSION} `,
      "color:#fff;background:#d84315;font-weight:700;border-radius:3px 0 0 3px",
      "color:#d84315;background:#fbe9e7;font-weight:700;border-radius:0 3px 3px 0",
    );
  }
})();
