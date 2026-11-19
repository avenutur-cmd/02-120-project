// Front end for the California Halibut simulator.
//
// Every slider change sends the current slider values to the two FastAPI
// endpoints (/api/leslie and /api/schaefer) with fetch(), waits for the JSON
// they return, and redraws the KPI cards, charts, and checks table from it.

const SLIDER_IDS = ["fishing_mortality", "size_limit_inches", "steepness", "years", "harvest"];
const LESLIE_FIELDS = ["fishing_mortality", "size_limit_inches", "steepness", "years"];

// Used before the first Schaefer response arrives: r = 0.2, K = 5,226,723.
const DEFAULT_MSY = 0.2 * 5_226_723 / 4;

const PRESETS = {
  unfished:    { fishing_mortality: 0,   size_limit_inches: 22, steepness: 0.7, years: 50,  harvest: 0,      tab: "leslie" },
  ca_limit:    { fishing_mortality: 0.3, size_limit_inches: 22, steepness: 0.7, years: 50,  harvest: 200000, tab: "leslie" },
  overfishing: { fishing_mortality: 0.6, size_limit_inches: 0,  steepness: 0.7, years: 100, harvest: 200000, tab: "leslie" },
  // harvest: null means 1.5 x MSY, computed when the button is pressed.
  msy_crash:   { fishing_mortality: 0.3, size_limit_inches: 22, steepness: 0.7, years: 50,  harvest: null,   tab: "schaefer" },
};

const state = { leslie: null, schaefer: null, controller: null, timer: null };

const $ = (id) => document.getElementById(id);
const fmtInt = (n) => Math.round(n).toLocaleString("en-US");
const fmtPct = (x, digits = 0) => `${(100 * x).toFixed(digits)}%`;
const inches = (x) => `${Number(x).toFixed(1).replace(/\.0$/, "")}″`;

const FORMATS = {
  fishing_mortality: (v) => `${v.toFixed(2)} /yr`,
  size_limit_inches: (v) => inches(v),
  steepness: (v) => v.toFixed(2),
  years: (v) => `${v} yr`,
  harvest: (v) => `${fmtInt(v)} /yr`,
};

// ---------- Talking to the Python API ----------

function readInputs() {
  // Slider ids match the API field names, so this is already the request body.
  return Object.fromEntries(SLIDER_IDS.map((id) => [id, Number($(id).value)]));
}

async function postJSON(url, body, signal) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    // FastAPI puts validation errors (422) in "detail".
    const error = await response.json().catch(() => ({}));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ")
      : response.statusText;
    throw new Error(`${url} returned ${response.status}: ${detail}`);
  }
  return response.json();
}

async function update() {
  // Cancel any request still in flight, so a slow old response can never
  // overwrite the charts after a newer one has been drawn.
  if (state.controller) state.controller.abort();
  const controller = new AbortController();
  state.controller = controller;

  const inputs = readInputs();
  const leslieBody = Object.fromEntries(LESLIE_FIELDS.map((k) => [k, inputs[k]]));
  const schaeferBody = { harvest: inputs.harvest, years: inputs.years };

  document.querySelector("main").classList.add("loading");
  setStatus("Updating…");
  try {
    const [leslie, schaefer] = await Promise.all([
      postJSON("/api/leslie", leslieBody, controller.signal),
      postJSON("/api/schaefer", schaeferBody, controller.signal),
    ]);
    state.leslie = leslie;
    state.schaefer = schaefer;
    render();
    setStatus("");
  } catch (error) {
    if (error.name === "AbortError") return;  // replaced by a newer request
    setStatus(error.message, true);
  } finally {
    if (state.controller === controller) document.querySelector("main").classList.remove("loading");
  }
}

function scheduleUpdate() {
  // Wait until the slider stops moving for 150 ms instead of sending a request
  // for every pixel of the drag.
  clearTimeout(state.timer);
  state.timer = setTimeout(update, 150);
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

// ---------- Controls ----------

function showSliderValues() {
  for (const id of SLIDER_IDS) {
    document.querySelector(`output[for="${id}"]`).textContent = FORMATS[id](Number($(id).value));
  }
}

function applyPreset(name) {
  const preset = PRESETS[name];
  for (const id of SLIDER_IDS) {
    const value = id === "harvest" && preset.harvest === null
      ? 1.5 * (state.schaefer ? state.schaefer.msy : DEFAULT_MSY)
      : preset[id];
    $(id).value = value;
  }
  showSliderValues();
  markPreset(name);
  selectTab(preset.tab);
  update();
}

function markPreset(name) {
  document.querySelectorAll("[data-preset]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.preset === name));
  });
}

function selectTab(tab) {
  document.querySelectorAll("[role=tab]").forEach((button) => {
    const selected = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(selected));
    $(`tab-${button.dataset.tab}`).hidden = !selected;
  });
  // Plotly cannot measure a hidden div, so resize charts once their tab is visible.
  document.querySelectorAll(`#tab-${tab} .chart`).forEach((chart) => {
    if (chart.data) Plotly.Plots.resize(chart);
  });
}

// ---------- Rendering ----------

function theme() {
  const style = getComputedStyle(document.documentElement);
  const v = (name) => style.getPropertyValue(name).trim();
  return {
    surface: v("--surface-1"), text: v("--text-primary"), text2: v("--text-secondary"),
    muted: v("--text-muted"), grid: v("--grid"), axis: v("--axis"),
    s1: v("--series-1"), s2: v("--series-2"), good: v("--good"), critical: v("--critical"),
  };
}

function layout(t, { xaxis = {}, yaxis = {}, ...rest } = {}) {
  const axis = { gridcolor: t.grid, linecolor: t.axis, zeroline: false, tickfont: { color: t.muted } };
  // On narrow screens the legend wraps onto several rows, so give it a taller top margin.
  const narrow = window.innerWidth < 600;
  return {
    paper_bgcolor: t.surface,
    plot_bgcolor: t.surface,
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: t.text2, size: 12 },
    margin: { l: narrow ? 52 : 64, r: narrow ? 12 : 24, t: narrow ? 84 : 36, b: 48 },
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: t.text2 } },
    hoverlabel: { bgcolor: t.surface, bordercolor: t.axis, font: { color: t.text } },
    xaxis: { ...axis, ...xaxis },
    yaxis: { ...axis, rangemode: "tozero", ...yaxis },
    ...rest,
  };
}

const PLOT_CONFIG = { responsive: true, displayModeBar: false };

function render() {
  const t = theme();
  renderKpis();
  renderPopulation(t);
  renderAges(t);
  renderSchaefer(t);
  renderProduction(t);
  renderChecks();
}

function badge(kind, text) {
  return `<span class="badge ${kind}">${text}</span>`;
}

function renderKpis() {
  const { leslie, schaefer } = state;
  const limit = leslie.size_limit;
  const indiscriminate = leslie.indiscriminate;
  const unfishedTotal = leslie.unfished_age_distribution.reduce((a, b) => a + b, 0);
  const h = leslie.inputs.steepness;
  const collapseSpr = (1 - h) / (4 * h);

  $("kpi-population").textContent = fmtPct(limit.equilibrium.total_population / unfishedTotal);
  $("kpi-population-sub").textContent =
    `of unfished, ${inches(limit.size_limit_inches)} limit · indiscriminate: ` +
    fmtPct(indiscriminate.equilibrium.total_population / unfishedTotal);

  $("kpi-catch").textContent = fmtInt(limit.equilibrium.catch);
  $("kpi-catch-sub").textContent =
    `fish/yr at equilibrium · indiscriminate: ${fmtInt(indiscriminate.equilibrium.catch)}`;

  $("kpi-spr").textContent = fmtPct(limit.equilibrium.spawning_potential_ratio);
  $("kpi-spr-sub").innerHTML =
    (limit.equilibrium.persists ? badge("good", "Persists") : badge("critical", "Collapses")) +
    ` · collapses below ${fmtPct(collapseSpr, 1)}`;

  $("kpi-schaefer").textContent = schaefer.harvest_over_msy.toFixed(2);
  let outcome;
  if (schaefer.crashed) outcome = badge("critical", `Crashes in year ${schaefer.crash_year.toFixed(1)}`);
  else if (schaefer.msy_check.crash_expected) outcome = badge("warning", "Crash after horizon");
  else outcome = badge("good", "Sustainable");
  $("kpi-schaefer-sub").innerHTML = `${outcome} · MSY ${fmtInt(schaefer.msy)}/yr`;

  // Sidebar hints that depend on the response.
  const fished = limit.fished_ages;
  const lastAge = leslie.ages.at(-1);
  $("fished-ages-hint").textContent = fished.length === 0
    ? "No age class reaches this length: nothing is fished."
    : `Fished ages: ${fished[0]}–${lastAge}+ (average length ≥ ${inches(limit.size_limit_inches)}).`;
  $("msy-hint").textContent =
    `MSY = rK/4 = ${fmtInt(schaefer.msy)} fish/yr (H / MSY = ${schaefer.harvest_over_msy.toFixed(2)}).`;
}

function renderPopulation(t) {
  const { leslie } = state;
  const limit = leslie.size_limit;
  const indiscriminate = leslie.indiscriminate;
  const unfishedTotal = leslie.unfished_age_distribution.reduce((a, b) => a + b, 0);
  const limitName = `${inches(limit.size_limit_inches)} size limit`;
  const hover = "%{y:,.0f} fish<extra>%{fullData.name}</extra>";

  $("population-caption").textContent = limit.size_limit_inches === 0
    ? "The size limit is 0″, so both lines are the same indiscriminate harvest."
    : `Both scenarios use F = ${leslie.inputs.fishing_mortality.toFixed(2)}; only who can be caught differs.`;

  const traces = [
    { x: leslie.years, y: leslie.years.map(() => unfishedTotal), name: "Unfished",
      mode: "lines", line: { color: t.muted, width: 2, dash: "dash" }, hovertemplate: hover },
    { x: leslie.years, y: indiscriminate.total_population, name: "Indiscriminate (0″)",
      mode: "lines", line: { color: t.s2, width: 2 }, hovertemplate: hover },
    { x: leslie.years, y: limit.total_population, name: limitName,
      mode: "lines", line: { color: t.s1, width: 2 }, hovertemplate: hover },
  ];
  Plotly.react("chart-population", traces, layout(t, {
    hovermode: "x unified",
    xaxis: { title: { text: "Year" } },
    yaxis: { title: { text: "Total fish" }, tickformat: "~s" },
  }), PLOT_CONFIG);
}

function renderAges(t) {
  const { leslie } = state;
  const limit = leslie.size_limit;
  const ages = leslie.ages;
  const labels = ages.map((a, i) => (i === ages.length - 1 ? `${a}+` : String(a)));
  const bar = (y, name, color) => ({
    type: "bar", x: ages, y, name,
    marker: { color, line: { color: t.surface, width: 1 } },
    hovertemplate: `Age %{x}: %{y:,.0f} fish<extra>${name}</extra>`,
  });

  const traces = [
    bar(limit.age_distribution.final, `${inches(limit.size_limit_inches)} size limit`, t.s1),
    bar(leslie.indiscriminate.age_distribution.final, "Indiscriminate (0″)", t.s2),
    { x: ages, y: leslie.unfished_age_distribution, name: "Unfished survivorship",
      mode: "lines+markers", line: { color: t.muted, width: 2, dash: "dash" },
      marker: { size: 8, color: t.muted, line: { color: t.surface, width: 2 } },
      hovertemplate: "Age %{x}: %{y:,.0f} fish<extra>Unfished</extra>" },
  ];

  // Dashed line between the last protected age and the first fished age.
  const shapes = [];
  const annotations = [];
  const firstFished = limit.fished_ages[0];
  if (firstFished !== undefined && firstFished > ages[0]) {
    shapes.push({ type: "line", xref: "x", yref: "paper", x0: firstFished - 0.5, x1: firstFished - 0.5,
                  y0: 0, y1: 1, line: { color: t.text2, width: 1, dash: "dot" } });
    annotations.push({ xref: "x", yref: "paper", x: firstFished - 0.5, y: 1, xanchor: "left", yanchor: "top",
                       text: ` ${inches(limit.size_limit_inches)} limit: fished →`, showarrow: false,
                       font: { color: t.text2, size: 11 } });
  }

  Plotly.react("chart-ages", traces, layout(t, {
    barmode: "group", bargap: 0.25, bargroupgap: 0.08, barcornerradius: 4,
    hovermode: "closest", shapes, annotations,
    xaxis: { title: { text: "Age (years)" }, tickvals: ages, ticktext: labels },
    yaxis: { title: { text: "Fish" }, tickformat: "~s" },
  }), PLOT_CONFIG);
}

function referenceLine(t, y, text) {
  return {
    shape: { type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { color: t.muted, width: 1, dash: "dash" } },
    annotation: { xref: "paper", x: 1, y, xanchor: "right", yanchor: "bottom", text, showarrow: false,
                  font: { color: t.text2, size: 11 } },
  };
}

function renderSchaefer(t) {
  const { schaefer } = state;
  const K = schaefer.inputs.carrying_capacity;
  const lines = [referenceLine(t, schaefer.population_at_msy, "K/2: population at MSY")];
  if (schaefer.equilibria.stable !== null && schaefer.inputs.harvest > 0) {
    lines.push(referenceLine(t, schaefer.equilibria.stable, "Stable equilibrium"));
    lines.push(referenceLine(t, schaefer.equilibria.tipping_point, "Tipping point"));
  }

  const traces = [
    { x: schaefer.time, y: schaefer.population, name: "Population P", mode: "lines",
      line: { color: t.s1, width: 2 }, hovertemplate: "Year %{x:.1f}: %{y:,.0f} fish<extra></extra>" },
  ];
  if (schaefer.crashed) {
    traces.push({ x: [schaefer.crash_year], y: [0], name: "Crash", mode: "markers",
                  marker: { size: 10, color: t.critical, line: { color: t.surface, width: 2 } },
                  hovertemplate: "Crash at year %{x:.1f}<extra></extra>" });
  }

  Plotly.react("chart-schaefer", traces, layout(t, {
    showlegend: false,
    hovermode: "x",
    shapes: lines.map((l) => l.shape),
    annotations: lines.map((l) => l.annotation),
    xaxis: { title: { text: "Year" }, range: [0, schaefer.inputs.years] },
    yaxis: { title: { text: "Population P (fish)" }, tickformat: "~s",
             range: [0, 1.05 * Math.max(K, ...schaefer.population)] },
  }), PLOT_CONFIG);
}

function renderProduction(t) {
  const { schaefer } = state;
  const r = schaefer.inputs.growth_rate;
  const K = schaefer.inputs.carrying_capacity;
  const H = schaefer.inputs.harvest;
  const P = Array.from({ length: 201 }, (_, i) => (K * i) / 200);
  const production = P.map((p) => r * p * (1 - p / K));

  const traces = [
    { x: P, y: production, name: "Surplus production rP(1 − P/K)", mode: "lines",
      line: { color: t.s1, width: 2 }, hovertemplate: "P = %{x:,.0f}: %{y:,.0f} fish/yr<extra>Production</extra>" },
    { x: [0, K], y: [H, H], name: `Harvest H = ${fmtInt(H)}`, mode: "lines",
      line: { color: t.s2, width: 2 }, hovertemplate: "H = %{y:,.0f} fish/yr<extra></extra>" },
  ];
  const equilibria = [schaefer.equilibria.stable, schaefer.equilibria.tipping_point].filter((p) => p !== null);
  if (equilibria.length && H > 0) {
    traces.push({ x: equilibria, y: equilibria.map(() => H), name: "Equilibria (production = H)",
                  mode: "markers", marker: { size: 9, color: t.text, line: { color: t.surface, width: 2 } },
                  hovertemplate: "Equilibrium at P = %{x:,.0f}<extra></extra>" });
  }
  const msy = referenceLine(t, schaefer.msy, `MSY = rK/4 = ${fmtInt(schaefer.msy)}`);

  Plotly.react("chart-production", traces, layout(t, {
    hovermode: "closest",
    shapes: [msy.shape],
    annotations: [{ ...msy.annotation, xanchor: "left", x: 0 }],
    xaxis: { title: { text: "Population P (fish)" }, tickformat: "~s" },
    yaxis: { title: { text: "Fish per year" }, tickformat: "~s",
             range: [0, 1.15 * Math.max(schaefer.msy, H)] },
  }), PLOT_CONFIG);
}

function renderChecks() {
  const { leslie, schaefer } = state;
  const c = leslie.checks;
  const close = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));
  const pass = (ok) => (ok ? badge("good", "Pass") : badge("critical", "Fail"));
  const rows = [];

  rows.push(["Unfished Leslie eigenvalue", "Tuned egg survival makes λ = 1 with F = 0",
             "1", c.unfished_eigenvalue.toFixed(10), pass(close(c.unfished_eigenvalue, 1))]);
  rows.push(["Egg survival", "Bisection vs. hand-derived 1 / (eggs per recruit)",
             c.analytic_egg_survival.toExponential(6), c.tuned_egg_survival.toExponential(6),
             pass(close(c.tuned_egg_survival, c.analytic_egg_survival, 1e-6))]);
  rows.push(["Stable age distribution", "Eigenvector vs. survivorship curve l(a) = exp(−ΣM)",
             "max difference 0", c.survivorship_curve.max_difference.toExponential(2),
             pass(c.survivorship_curve.max_difference < 1e-6)]);
  rows.push(["Beverton–Holt steepness", "R(0.2 E₀) / R₀ should equal h",
             c.steepness.expected.toFixed(4), c.steepness.model.toFixed(4),
             pass(close(c.steepness.model, c.steepness.expected))]);

  const limit = leslie.size_limit;
  const gap = limit.final_vs_equilibrium;
  const finalTotal = limit.total_population.at(-1);
  if (gap === null) {
    rows.push(["Simulation vs. analytic equilibrium", `${inches(limit.size_limit_inches)} limit; equilibrium is 0`,
               "0 fish", `${fmtInt(finalTotal)} fish in year ${leslie.inputs.years}`,
               finalTotal < 1 ? badge("good", "Pass") : badge("warning", "Still declining")]);
  } else {
    rows.push(["Simulation vs. analytic equilibrium", `${inches(limit.size_limit_inches)} limit, final year`,
               "within 1%", fmtPct(gap, 2),
               Math.abs(gap) < 0.01 ? badge("good", "Pass") : badge("warning", "Not settled yet")]);
  }

  const check = schaefer.msy_check;
  let result;
  if (check.crash_expected === check.crashed_within_horizon) result = badge("good", "Pass");
  else if (check.crash_expected) result = badge("warning", "Crash beyond horizon");
  else result = badge("critical", "Fail");
  rows.push(["Schaefer MSY prediction", "Crash iff H > rK/4 or P₀ below the tipping point",
             check.crash_expected ? "crash" : "no crash",
             check.crashed_within_horizon ? `crash (year ${schaefer.crash_year.toFixed(1)})` : "no crash",
             result]);

  $("checks-body").innerHTML = rows.map(([name, detail, expected, model, status]) =>
    `<tr><td>${name}<span class="detail">${detail}</span></td><td>${expected}</td><td>${model}</td><td>${status}</td></tr>`
  ).join("");
}

// ---------- Wiring ----------

for (const id of SLIDER_IDS) {
  $(id).addEventListener("input", () => {
    showSliderValues();
    markPreset(null);
    scheduleUpdate();
  });
}
document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => applyPreset(button.dataset.preset));
});
document.querySelectorAll("[role=tab]").forEach((button) => {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
});
// Redraw charts in the new colors when the system switches light/dark mode.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (state.leslie) render();
});
// Redraw with the other legend margin when the window crosses the narrow-screen width.
matchMedia("(max-width: 599px)").addEventListener("change", () => {
  if (state.leslie) render();
});

showSliderValues();
markPreset("ca_limit");
update();
