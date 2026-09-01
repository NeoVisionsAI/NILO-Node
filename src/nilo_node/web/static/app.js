const TOKEN_KEY = "nilo_setup_token";
const THEME_KEY = "nilo_theme";
const TITLES = {
  dashboard: "Panel",
  wifi: "WiFi",
  camera: "Cámara",
  bluetooth: "Audio inalámbrico",
  monitoring: "Monitorización",
  mqtt: "MQTT",
};

const CAM_STATE_LABELS = {
  disconnected: "Desconectada",
  connecting: "Conectando…",
  connected: "Conectada",
  error: "Error",
};

const MON_DAYS = [
  { id: "mon", label: "Lun" },
  { id: "tue", label: "Mar" },
  { id: "wed", label: "Mié" },
  { id: "thu", label: "Jue" },
  { id: "fri", label: "Vie" },
  { id: "sat", label: "Sáb" },
  { id: "sun", label: "Dom" },
];

const $ = (id) => document.getElementById(id);

let loadingCount = 0;
let camStatusCache = null;
let dashboardCache = null;
let monitoringWindows = [];
let monitoringEnabled = false;

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function startLoading() {
  loadingCount += 1;
  $("api-loading-bar").classList.add("active");
}

function stopLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) $("api-loading-bar").classList.remove("active");
}

function showToast(msg, type = "ok") {
  if (!msg) return;
  let host = $("toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-host";
    host.className = "toast-host";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = `toast ${type === "error" ? "error" : "ok"}`;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateX(12px)";
    el.style.transition = "opacity 0.25s ease, transform 0.25s ease";
    setTimeout(() => el.remove(), 280);
  }, 4500);
}

function applyTheme(theme) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  document.documentElement.style.colorScheme = next;
  localStorage.setItem(THEME_KEY, next);
  document.querySelectorAll(".theme-toggle").forEach((btn) => {
    btn.textContent = next === "dark" ? "☾" : "☀";
    btn.title = next === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
    btn.setAttribute("aria-label", btn.title);
  });
}

function initTheme() {
  applyTheme(localStorage.getItem(THEME_KEY) || "light");
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const goingDark = current !== "dark";
  applyTheme(goingDark ? "dark" : "light");
  showToast(goingDark ? "Tema oscuro activado" : "Tema claro activado", "ok");
}

async function apiRequest(path, options = {}) {
  const {
    silent = false,
    successMessage = null,
    jsonBody = true,
    method = "GET",
    body,
    headers: extraHeaders = {},
  } = options;

  startLoading();
  try {
    const headers = { ...authHeaders(), ...extraHeaders };
    if (jsonBody && body !== undefined && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(path, {
      method,
      body,
      headers,
    });

    if (res.status === 401) {
      logout();
      const msg = "Sesión expirada";
      if (!silent) showToast(msg, "error");
      throw new Error(msg);
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : res.statusText || `Error HTTP ${res.status}`;
      if (!silent) showToast(msg, "error");
      throw new Error(msg);
    }

    if (successMessage && !silent) showToast(successMessage, "ok");
    return res;
  } catch (err) {
    if (!silent && err instanceof TypeError) {
      showToast("Error de red — comprueba la conexión", "error");
    }
    throw err;
  } finally {
    stopLoading();
  }
}

async function api(path, options = {}) {
  const { body, method = "GET", ...rest } = options;
  const res = await apiRequest(path, {
    ...rest,
    method,
    body: body !== undefined ? body : undefined,
  });
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function showTab(name) {
  document.querySelectorAll(".page").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));
  $(`tab-${name}`).classList.remove("hidden");
  document.querySelector(`[data-tab="${name}"]`).classList.add("active");
  $("page-title").textContent = TITLES[name] || name;
  if (name === "camera") {
    loadCameraStatus({ silent: true }).catch(() => {});
  }
  if (name === "monitoring") {
    loadMonitoringTab({ silent: true }).catch(() => {});
  }
  if (name === "bluetooth") {
    loadBluetoothTab({ silent: true }).catch(() => {});
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  $("app").classList.add("hidden");
  $("login-screen").classList.remove("hidden");
}

function fillForm(form, data) {
  if (!data) return;
  for (const el of form.elements) {
    if (!el.name || data[el.name] === undefined) continue;
    if (el.type === "checkbox") el.checked = Boolean(data[el.name]);
    else el.value = data[el.name];
  }
}

function formToPatch(form) {
  const patch = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") patch[el.name] = el.checked;
    else if (el.value !== "") patch[el.name] = el.type === "number" ? Number(el.value) : el.value;
  }
  return patch;
}

async function saveSettings(section, patch, label) {
  const body = { [section]: patch };
  return api("/api/v1/setup/settings", {
    method: "PATCH",
    body: JSON.stringify(body),
    successMessage: `${label || "Configuración"} guardada y aplicada`,
  });
}

function isoToDatetimeLocal(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function datetimeLocalToIso(value) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

async function loadSettingsForms() {
  const data = await api("/api/v1/setup/settings", { silent: true });
  const saved = data.settings || {};
  const merged = {
    camera: { ...data.live.camera, ...saved.camera },
    wifi: { ...data.live.wifi, ...saved.wifi },
    bluetooth: { ...data.live.bluetooth, ...saved.bluetooth },
    mqtt: { ...data.live.mqtt, ...saved.mqtt },
    monitoring: { ...(saved.monitoring || {}) },
  };
  fillForm($("camera-form"), merged.camera);
  fillForm($("wifi-form"), merged.wifi);
  fillForm($("bt-form"), merged.bluetooth);
  fillForm($("mqtt-form"), merged.mqtt);
  if ($("monitoring-form")) {
    const mon = normalizeMonitoringSettings({ ...merged.monitoring });
    monitoringWindows = mon.windows.map((w) => ({
      days: [...(w.days || [])],
      start_time: w.start_time || "09:00",
      end_time: w.end_time || "17:00",
    }));
    if (mon.period_start) mon.period_start = isoToDatetimeLocal(mon.period_start);
    if (mon.period_end) mon.period_end = isoToDatetimeLocal(mon.period_end);
    fillForm($("monitoring-form"), mon);
    toggleMonitoringScheduleFields(mon.schedule_mode || "always");
    renderMonitoringWindows();
    setMonitoringToggle(mon.enabled);
  }
  if ($("monitoring-model-form")) {
    const mon = normalizeMonitoringSettings({ ...merged.monitoring });
    fillForm($("monitoring-model-form"), mon);
    if ($("mon-api-host")) $("mon-api-host").value = mon.api_host || "nilomed.eu";
  }
  return merged;
}

function statCard(label, value, cls = "") {
  return `<div class="card"><div class="stat-label">${label}</div><div class="stat-value ${cls}">${value}</div></div>`;
}

function wifiStateLabel(wifi) {
  if (!wifi.enabled) return "Deshabilitado";
  if (wifi.running) return "Activo";
  if (wifi.host_ap_detected) return "Activo (host)";
  return "Parado";
}

function normalizeMonitoringSettings(mon = {}) {
  const out = { ...mon };
  if (out.schedule_mode === "fixed_window") {
    out.schedule_mode = "weekly_windows";
    out.windows = [
      {
        days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        start_time: out.daily_start_time || "00:00",
        end_time: out.daily_end_time || "23:59",
      },
    ];
  }
  if (!Array.isArray(out.windows) || !out.windows.length) {
    out.windows = [{ days: ["mon", "tue", "wed", "thu", "fri"], start_time: "09:00", end_time: "17:00" }];
  }
  if (!out.api_host) out.api_host = "nilomed.eu";
  if (!out.pose_fps) out.pose_fps = 30;
  return out;
}

function updateDashboardHero(d) {
  if ($("dash-node-id")) {
    $("dash-node-id").textContent = d.node_short_id || d.node_id || "—";
  }
  if ($("dash-temp")) {
    $("dash-temp").textContent = d.system?.temperature_label || "—";
  }
  if ($("dash-uptime")) {
    $("dash-uptime").textContent = d.system?.uptime_human || "—";
  }
}

function setMonitoringToggle(enabled) {
  monitoringEnabled = Boolean(enabled);
  const btn = $("mon-enabled-toggle");
  const hint = $("mon-toggle-hint");
  const label = $("mon-toggle-label");
  if (!btn) return;
  btn.setAttribute("aria-checked", monitoringEnabled ? "true" : "false");
  if (label) label.textContent = monitoringEnabled ? "ON" : "OFF";
  if (hint) {
    hint.textContent = monitoringEnabled
      ? "Activa — se registrarán datos según la configuración"
      : "Desactivada — no se registran datos";
  }
}

function renderMonitoringWindows() {
  const list = $("mon-windows-list");
  if (!list) return;
  list.innerHTML = monitoringWindows
    .map((win, idx) => {
      const dayChecks = MON_DAYS.map(
        (d) => `<label class="mon-day"><input type="checkbox" data-win="${idx}" data-day="${d.id}" ${win.days.includes(d.id) ? "checked" : ""} />${d.label}</label>`,
      ).join("");
      return `<div class="mon-window-item" data-index="${idx}">
        <header><span>Ventana ${idx + 1}</span>
          <button type="button" class="btn ghost mon-remove-window" data-index="${idx}">Eliminar</button>
        </header>
        <div class="mon-days">${dayChecks}</div>
        <div class="mon-window-times">
          <label>Desde <input type="time" class="mon-win-start" data-index="${idx}" value="${win.start_time || "09:00"}" /></label>
          <label>Hasta <input type="time" class="mon-win-end" data-index="${idx}" value="${win.end_time || "17:00"}" /></label>
        </div>
      </div>`;
    })
    .join("");

  list.querySelectorAll(".mon-day input").forEach((input) => {
    input.addEventListener("change", () => {
      const i = Number(input.dataset.win);
      const day = input.dataset.day;
      const days = new Set(monitoringWindows[i].days);
      if (input.checked) days.add(day);
      else days.delete(day);
      monitoringWindows[i].days = [...days];
    });
  });
  list.querySelectorAll(".mon-win-start").forEach((input) => {
    input.addEventListener("change", () => {
      monitoringWindows[Number(input.dataset.index)].start_time = input.value;
    });
  });
  list.querySelectorAll(".mon-win-end").forEach((input) => {
    input.addEventListener("change", () => {
      monitoringWindows[Number(input.dataset.index)].end_time = input.value;
    });
  });
  list.querySelectorAll(".mon-remove-window").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.index);
      monitoringWindows.splice(i, 1);
      if (!monitoringWindows.length) {
        monitoringWindows.push({ days: ["mon", "tue", "wed"], start_time: "09:00", end_time: "14:00" });
      }
      renderMonitoringWindows();
    });
  });
}

function toggleMonitoringScheduleFields(mode) {
  const isWeekly = mode === "weekly_windows" || mode === "fixed_window";
  $("mon-period-fields")?.classList.toggle("hidden", !isWeekly);
  $("mon-windows-section")?.classList.toggle("hidden", !isWeekly);
}

function collectMonitoringWindowsFromDom() {
  return monitoringWindows.map((w) => ({
    days: [...(w.days || [])],
    start_time: w.start_time || "09:00",
    end_time: w.end_time || "17:00",
  }));
}

function renderMqttChannel(d) {
  const el = $("mqtt-channel");
  if (!el) return;
  const topic = d.mqtt?.subscribe_topic || `nilo/node:${d.node_short_id || ""}`;
  const events = d.mqtt?.events_topic || `${topic}/events`;
  el.innerHTML = [
    ["Canal comandos", topic],
    ["Canal eventos", events],
    ["ID nodo (8)", d.node_short_id || "—"],
    ["Plantilla", d.mqtt?.topic_template || "nilo/node:{node_short_id}"],
  ]
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`)
    .join("");
}

async function loadDashboard(options = {}) {
  const { silent = false } = options;
  const d = await api("/api/v1/setup/dashboard", {
    silent,
    successMessage: silent ? null : "Panel actualizado",
  });
  dashboardCache = d;
  $("node-id").textContent = d.node_short_id || d.node_id;
  updateDashboardHero(d);

  const camCls =
    d.camera.connection_state === "connected"
      ? "stat-ok"
      : d.camera.connection_state === "error"
        ? "stat-warn"
        : "stat-off";
  const camLabel = CAM_STATE_LABELS[d.camera.connection_state] || d.camera.connection_state;

  const wifiActive = d.wifi.running || d.wifi.host_ap_detected;
  $("dashboard-cards").innerHTML = [
    statCard("Cámara", camLabel, camCls),
    statCard("WiFi AP", wifiActive ? d.wifi.ssid : "Parado", wifiActive ? "stat-ok" : "stat-warn"),
    statCard("Audio BT", `${d.bluetooth.connected_count} conectado(s)`, ""),
    statCard("MQTT", d.mqtt?.connected ? "Conectado" : "Off", d.mqtt?.connected ? "stat-ok" : "stat-off"),
    statCard("Modelo pose", d.camera_model?.loaded ? (d.camera_model.backend || "OK") : "Sin cargar", d.camera_model?.loaded ? "stat-ok" : "stat-off"),
  ].join("");

  const wifiRows = [
    ["SSID", d.wifi.ssid || "—"],
    ["IP AP", d.wifi.ap_ip],
    ["Estado", wifiStateLabel(d.wifi)],
    ["Backend", d.wifi.backend || "—"],
    ["Mock", d.wifi.mock ? "Sí" : "No"],
  ];
  if (d.wifi.error) wifiRows.push(["Error", d.wifi.error]);
  $("wifi-status").innerHTML = wifiRows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");

  $("mqtt-info").textContent = JSON.stringify(d.mqtt, null, 2);
  renderMqttChannel(d);
  if (d.mqtt?.subscribe_topic) {
    $("mqtt-example").textContent = JSON.stringify(
      {
        topic: d.mqtt.subscribe_topic,
        token: "••••",
        action: "settings.get",
        request_id: "1",
        payload: {},
      },
      null,
      2,
    );
  }

  await loadSettingsForms();
}

function setCamBanner(message, type = "ok") {
  const el = $("cam-banner");
  if (!message) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = message;
  el.className = `status-banner ${type}`;
  el.classList.remove("hidden");
}

function updateCamConnectionIcon(s) {
  const icon = $("cam-connection-icon");
  const label = $("cam-connection-label");
  const hint = $("cam-connection-hint");
  if (!icon || !label || !hint) return;

  const state = s.connection_state || "disconnected";
  icon.className = `cam-icon ${state}`;
  label.textContent = CAM_STATE_LABELS[state] || state;

  if (state === "connected") {
    icon.textContent = "●";
    hint.textContent =
      s.pipeline_mode === "mock"
        ? "Modo simulación — Capturar RGB o ToF bajo demanda"
        : `Conectada (${s.connected_device_id || "OAK"}) — Capturar RGB o ToF`;
  } else if (state === "connecting") {
    icon.textContent = "◌";
    hint.textContent = "Estableciendo enlace con la cámara…";
  } else if (state === "error") {
    icon.textContent = "✕";
    hint.textContent = s.last_error || "Revisa PoE/USB e IP de la cámara";
  } else {
    icon.textContent = "○";
    hint.textContent = "Detectar → Conectar → Capturar RGB o ToF";
  }
}

function updateCamConnectButton(s) {
  const btn = $("cam-connect");
  if (!btn) return;
  const state = s.connection_state || "disconnected";
  if (state === "connected") {
    btn.textContent = "Refrescar";
    btn.className = "btn secondary";
  } else if (state === "connecting") {
    btn.textContent = "Conectando…";
    btn.className = "btn primary";
    btn.disabled = true;
    return;
  } else {
    btn.textContent = "Conectar";
    btn.className = "btn primary";
  }
  btn.disabled = false;
}

function renderModelStatus(model) {
  const el = $("cam-model-status");
  if (!el || !model) return;
  const rows = [
    ["En cámara OAK", model.loaded && model.placement === "device" ? "Sí" : model.loaded ? "Parcial" : "No"],
    ["Backend", model.backend || "—"],
    ["Estado", model.message || model.last_error || "—"],
  ];
  if (model.blob_path) rows.push(["Blob Myriad", "Listo"]);
  el.innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");
}

function renderCameraStatusSummary(s) {
  camStatusCache = s;
  const state = CAM_STATE_LABELS[s.connection_state] || s.connection_state;
  const rows = [
    ["Estado", state],
    ["DepthAI", s.depthai_available ? "Disponible" : "No instalado / no detectado"],
    ["Modo pipeline", s.pipeline_mode],
    ["Dispositivo", s.connected_device_id || "—"],
    ["Cámaras encontradas", String(s.available_devices?.length ?? 0)],
  ];
  if (s.model?.loaded) {
    rows.push(["Modelo cargado", `${s.model.backend} (${s.model.placement})`]);
  }
  if (s.last_error) rows.push(["Último error", s.last_error]);
  $("cam-status-summary").innerHTML = rows
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`)
    .join("");

  updateCamConnectionIcon(s);
  updateCamConnectButton(s);
  renderModelStatus(s.model);

  if (s.connection_state === "connected") {
    setCamBanner(
      s.pipeline_mode === "mock"
        ? "Conectado en modo simulación (mock) — captura bajo demanda"
        : `Cámara conectada (${s.connected_device_id || "OAK"}) — captura bajo demanda`,
      s.pipeline_mode === "mock" ? "warn" : "ok",
    );
  } else if (s.connection_state === "error") {
    setCamBanner(s.last_error || "Error al conectar la cámara", "err");
  } else if (s.connection_state === "connecting") {
    setCamBanner("Conectando con la cámara…", "warn");
  } else {
    setCamBanner("", "warn");
    $("cam-banner").classList.add("hidden");
  }
}

async function showPreviewFromBlob(blob, { notify = false, message = "Frame capturado" } = {}) {
  const img = $("cam-preview");
  const ph = $("cam-preview-placeholder");
  if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
  const url = URL.createObjectURL(blob);
  img.dataset.objectUrl = url;
  img.src = url;
  img.classList.remove("hidden");
  ph.classList.add("hidden");
  if (notify) showToast(message, "ok");
  return true;
}

async function showPreviewFromResponse(res, { notify = false, message = "Frame capturado" } = {}) {
  const img = $("cam-preview");
  const ph = $("cam-preview-placeholder");
  if (res.ok) {
    const blob = await res.blob();
    return showPreviewFromBlob(blob, { notify, message });
  }
  img.classList.add("hidden");
  ph.classList.remove("hidden");
  const err = await res.json().catch(() => ({}));
  const msg = typeof err.detail === "string" ? err.detail : "Sin frame disponible";
  ph.textContent = msg;
  if (notify) showToast(msg, "error");
  return false;
}

function clearCameraPreview(message = "Sin captura — conecta la cámara y pulsa «Capturar RGB» o «Capturar ToF»") {
  const img = $("cam-preview");
  const ph = $("cam-preview-placeholder");
  if (img?.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
  if (img) {
    img.classList.add("hidden");
    img.removeAttribute("src");
    delete img.dataset.objectUrl;
  }
  if (ph) {
    ph.classList.remove("hidden");
    ph.textContent = message;
  }
}

async function loadCameraStatus(options = {}) {
  const { silent = false } = options;
  const s = await api("/api/v1/camera/status", { silent });
  $("cam-status").textContent = JSON.stringify(s, null, 2);
  renderCameraStatusSummary(s);
  return s;
}

function toggleMonitoringWindowFields(mode) {
  toggleMonitoringScheduleFields(mode);
}

async function loadMonitoringTab(options = {}) {
  const merged = await loadSettingsForms();
  const mon = normalizeMonitoringSettings(merged.monitoring || {});
  $("monitoring-summary").textContent = JSON.stringify(mon, null, 2);
  toggleMonitoringScheduleFields(mon.schedule_mode || "always");
  setMonitoringToggle(mon.enabled);
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").classList.add("hidden");
  try {
    const data = await api("/api/v1/setup/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("username").value.trim(),
        password: $("password").value,
      }),
      headers: {},
      jsonBody: true,
      successMessage: "Sesión iniciada",
    });
    localStorage.setItem(TOKEN_KEY, data.token);
    $("login-screen").classList.add("hidden");
    $("app").classList.remove("hidden");
    await loadDashboard({ silent: true });
  } catch (err) {
    $("login-error").textContent = err.message;
    $("login-error").classList.remove("hidden");
    showToast(err.message || "Error de inicio de sesión", "error");
  }
});

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.goto));
});

$("logout-btn").addEventListener("click", () => {
  showToast("Sesión cerrada", "ok");
  logout();
});
$("logout-btn-top").addEventListener("click", () => {
  showToast("Sesión cerrada", "ok");
  logout();
});
$("theme-toggle")?.addEventListener("click", toggleTheme);
$("theme-toggle-login")?.addEventListener("click", toggleTheme);

$("dash-refresh").addEventListener("click", () => {
  loadDashboard().catch(() => logout());
});

$("wifi-restart").addEventListener("click", async () => {
  await api("/api/v1/wifi/restart", { method: "POST", successMessage: "WiFi reiniciado" });
  await loadDashboard({ silent: true });
});

$("wifi-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("wifi", formToPatch(e.target), "WiFi");
  await loadDashboard({ silent: true });
});

$("camera-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("camera", formToPatch(e.target), "Cámara");
  await loadCameraStatus({ silent: true });
});

$("bt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("bluetooth", formToPatch(e.target), "Audio inalámbrico");
});

$("mqtt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("mqtt", formToPatch(e.target), "MQTT");
  showToast("MQTT guardado — reinicia el servicio para aplicar credenciales", "ok");
});

$("cam-discover").addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/camera/discover", { successMessage: "Escaneo completado" });
    const n = r.devices?.length ?? 0;
    if (n === 0) {
      showToast("No se encontró ninguna cámara OAK", "error");
      setCamBanner(
        r.depthai_available
          ? "DepthAI OK pero sin dispositivos — revisa USB/PoE e IP"
          : "DepthAI no disponible en el contenedor",
        "warn",
      );
    } else {
      const names = r.devices.map((d) => d.device_id).join(", ");
      showToast(`${n} cámara(s) encontrada(s)`, "ok");
      setCamBanner(`Encontradas ${n}: ${names}`, "ok");
    }
    $("cam-status").textContent = JSON.stringify(r, null, 2);
    await loadCameraStatus({ silent: true });
  } catch (err) {
    setCamBanner(`Detectar: ${err.message}`, "err");
  }
});

$("cam-connect").addEventListener("click", async () => {
  const btn = $("cam-connect");
  try {
    if (camStatusCache?.connection_state === "connected") {
      await api("/api/v1/camera/refresh", {
        method: "POST",
        body: "{}",
        successMessage: "Estado actualizado",
      });
    } else {
      btn.disabled = true;
      btn.textContent = "Conectando…";
      const s = await api("/api/v1/camera/connect", {
        method: "POST",
        body: "{}",
      });
      if (s.connection_state === "error") {
        showToast(s.last_error || "Error al conectar", "error");
        setCamBanner(s.last_error || "Error al conectar", "err");
      } else if (s.connection_state === "connected") {
        showToast(
          s.pipeline_mode === "mock" ? "Conectado (modo mock)" : "Cámara conectada correctamente",
          "ok",
        );
      }
    }
    await loadCameraStatus({ silent: true });
  } catch (err) {
    setCamBanner(`Conectar: ${err.message}`, "err");
  } finally {
    if (camStatusCache) updateCamConnectButton(camStatusCache);
  }
});

async function captureSnapshot(stream, label) {
  const maxAttempts = 4;
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      if (attempt > 1) {
        setCamBanner(`Esperando frames (${attempt}/${maxAttempts})…`, "warn");
        await new Promise((r) => setTimeout(r, 1200));
      }
      const res = await apiRequest(`/api/v1/camera/snapshot?stream=${stream}`, {
        method: "POST",
        jsonBody: false,
        silent: attempt < maxAttempts,
      });
      const ok = await showPreviewFromResponse(res, { notify: false, message: `${label} capturado` });
      if (ok) {
        setCamBanner(`${label} capturado correctamente`, "ok");
        await loadCameraStatus({ silent: true });
        return;
      }
    } catch (err) {
      lastError = err;
      const retryable = /sin frame|no hay frame|sin respuesta/i.test(String(err.message || ""));
      if (!retryable || attempt === maxAttempts) break;
    }
  }
  const msg = lastError?.message || `Sin ${label} disponible — la cámara puede necesitar unos segundos tras conectar`;
  setCamBanner(msg, "err");
  showToast(msg, "error");
  await loadCameraStatus({ silent: true });
}

$("cam-snapshot-rgb").addEventListener("click", () => captureSnapshot("rgb", "Frame RGB"));
$("cam-snapshot-tof").addEventListener("click", () => captureSnapshot("tof", "Frame ToF"));

$("cam-disconnect").addEventListener("click", async () => {
  await api("/api/v1/camera/disconnect", {
    method: "POST",
    successMessage: "Cámara desconectada",
  });
  clearCameraPreview("Desconectada");
  await loadCameraStatus({ silent: true });
});

$("cam-model-load").addEventListener("click", async () => {
  try {
    const backend =
      $("camera-form").elements.pose_backend?.value ||
      dashboardCache?.camera?.pose_backend ||
      "mediapipe";
    const r = await api("/api/v1/camera/model/load", {
      method: "POST",
      body: JSON.stringify({ backend, placement: "device" }),
      successMessage: "Modelo cargado en la cámara",
    });
    renderModelStatus(r);
    await loadCameraStatus({ silent: true });
    await saveSettings("camera", { pose_backend: backend }, "Backend de pose");
  } catch (err) {
    setCamBanner(`Cargar modelo: ${err.message}`, "err");
  }
});

$("cam-model-unload").addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/camera/model/unload", {
      method: "POST",
      body: "{}",
      successMessage: "Modelo descargado",
    });
    renderModelStatus(r);
    $("cam-model-test-details")?.classList.add("hidden");
    await loadCameraStatus({ silent: true });
  } catch (err) {
    setCamBanner(`Quitar modelo: ${err.message}`, "err");
  }
});

$("cam-model-test").addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/camera/pose-test", {
      method: "POST",
      body: "{}",
    });
    if (r.annotated_jpeg_base64) {
      const raw = atob(r.annotated_jpeg_base64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      await showPreviewFromBlob(new Blob([bytes], { type: "image/jpeg" }), {
        notify: false,
        message: "Prueba completada",
      });
    }
    const details = { ...r };
    delete details.annotated_jpeg_base64;
    $("cam-model-test-json").textContent = JSON.stringify(details, null, 2);
    $("cam-model-test-details").classList.remove("hidden");

    if (r.engine_available) {
      const n = r.landmarks_detected ?? 0;
      const backend = r.model?.backend || r.engine_id || "modelo";
      const msg = r.pose_detected
        ? `${backend} OK — ${n} landmarks detectados`
        : r.message || `${backend} OK — sin cuerpo visible en el frame`;
      showToast(msg, r.pose_detected ? "ok" : "warn");
      setCamBanner(msg, r.pose_detected ? "ok" : "warn");
    }
    await loadCameraStatus({ silent: true });
  } catch (err) {
    setCamBanner(`Probar modelo: ${err.message}`, "err");
  }
});

$("mon-schedule-mode")?.addEventListener("change", (e) => {
  toggleMonitoringScheduleFields(e.target.value);
});

$("mon-add-window")?.addEventListener("click", () => {
  monitoringWindows.push({ days: ["mon", "tue", "wed"], start_time: "09:00", end_time: "14:00" });
  renderMonitoringWindows();
});

$("mon-enabled-toggle")?.addEventListener("click", async () => {
  const next = !monitoringEnabled;
  const msg = next
    ? "¿Activar la monitorización? Se registrarán datos según la configuración guardada."
    : "¿Desactivar la monitorización? Dejará de registrar datos.";
  if (!window.confirm(msg)) return;
  try {
    await saveSettings("monitoring", { enabled: next }, next ? "Monitorización activada" : "Monitorización desactivada");
    setMonitoringToggle(next);
    showToast(next ? "Monitorización activada" : "Monitorización desactivada", "ok");
    await loadMonitoringTab({ silent: true });
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("mon-health-check")?.addEventListener("click", async () => {
  const host = ($("mon-api-host")?.value || "nilomed.eu").trim();
  const resultEl = $("mon-health-result");
  if (resultEl) {
    resultEl.classList.remove("hidden", "ok", "err");
    resultEl.textContent = "Comprobando conexión…";
  }
  try {
    const r = await api("/api/v1/setup/monitoring/health-check", {
      method: "POST",
      body: JSON.stringify({ host }),
      silent: true,
    });
    if (r.ok) showToast("Conexión correcta con el servidor", "ok");
    if (resultEl) {
      resultEl.classList.toggle("ok", Boolean(r.ok));
      resultEl.classList.toggle("err", !r.ok);
      resultEl.textContent = r.ok
        ? `✓ Conexión correcta (${r.url})`
        : `✕ Error: ${r.error || "No responde"}`;
    }
    if (r.ok) await saveSettings("monitoring", { api_host: host }, "Host API guardado");
  } catch (err) {
    if (resultEl) {
      resultEl.classList.add("err");
      resultEl.classList.remove("ok");
      resultEl.textContent = `✕ ${err.message}`;
    }
  }
});

$("monitoring-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const patch = formToPatch(e.target);
  if (patch.period_start) patch.period_start = datetimeLocalToIso(patch.period_start);
  if (patch.period_end) patch.period_end = datetimeLocalToIso(patch.period_end);
  patch.windows = collectMonitoringWindowsFromDom();
  patch.api_host = ($("mon-api-host")?.value || "nilomed.eu").trim();
  patch.enabled = monitoringEnabled;
  await saveSettings("monitoring", patch, "Intervalos de monitorización");
  await loadMonitoringTab({ silent: true });
});

$("monitoring-model-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const patch = formToPatch(e.target);
  patch.require_full_pose = e.target.elements.require_full_pose.checked;
  patch.api_host = ($("mon-api-host")?.value || "nilomed.eu").trim();
  patch.enabled = monitoringEnabled;
  await saveSettings("monitoring", patch, "Modelo y calidad de monitorización");
  await loadMonitoringTab({ silent: true });
});

const BT_RECORDING_MODE_LABELS = {
  continuous: "Permanente",
  interval: "Intervalos",
  on_demand: "Bajo demanda",
};

let btDiscoveredDevices = [];
let btSelectedDiscoverMac = null;
let btSelectedConnectedMac = null;
let btStatusCache = null;

function btEscape(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

function updateBtDetailVisibility(mode) {
  const intervalWrap = $("bt-interval-wrap");
  const demandWrap = $("bt-demand-wrap");
  if (intervalWrap) intervalWrap.classList.toggle("hidden", mode !== "interval");
  if (demandWrap) demandWrap.classList.toggle("hidden", mode !== "on_demand");
}

function renderBtDiscoveredList() {
  const list = $("bt-discovered-list");
  const actions = $("bt-discovered-actions");
  const connectedMacs = new Set(
    (btStatusCache?.mics || []).filter((m) => m.connected).map((m) => m.mac_address),
  );
  const items = btDiscoveredDevices.filter((d) => !connectedMacs.has(d.mac_address));
  if (!items.length) {
    list.innerHTML = "<li class='empty-item'>Ningún dispositivo nuevo — escanea de nuevo</li>";
    actions.classList.add("hidden");
    btSelectedDiscoverMac = null;
    return;
  }
  list.innerHTML = items
    .map((d) => {
      const label = d.label || d.display_name || d.name || d.mac_address;
      const selected = d.mac_address === btSelectedDiscoverMac ? " selected" : "";
      const meta = [
        d.mac_address,
        d.rssi != null ? `${d.rssi} dBm` : null,
        d.paired ? "emparejado" : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return `<li class="selectable${selected}" data-mac="${btEscape(d.mac_address)}">
        <div><div class="name">${btEscape(label)}</div><div class="mac">${btEscape(meta)}</div></div>
      </li>`;
    })
    .join("");
  list.querySelectorAll("li.selectable").forEach((li) => {
    li.addEventListener("click", () => {
      btSelectedDiscoverMac = li.dataset.mac;
      renderBtDiscoveredList();
      $("bt-discovered-actions").classList.remove("hidden");
    });
  });
  actions.classList.toggle("hidden", !btSelectedDiscoverMac);
}

function renderBtConnectedList() {
  const list = $("bt-connected-list");
  const connected = (btStatusCache?.mics || []).filter((m) => m.connected);
  if (!connected.length) {
    list.innerHTML = "<li class='empty-item'>No hay micrófonos conectados</li>";
    if (!btSelectedConnectedMac) $("bt-detail-card").classList.add("hidden");
    return;
  }
  list.innerHTML = connected
    .map((m) => {
      const selected = m.mac_address === btSelectedConnectedMac ? " selected" : "";
      const rec = m.record_enabled
        ? m.recording_mode === "on_demand" && !m.recording_active
          ? "activada (en espera)"
          : "grabando"
        : "sin grabación";
      return `<li class="selectable${selected}" data-mac="${btEscape(m.mac_address)}">
        <div>
          <div class="name">${btEscape(m.label || m.mac_address)}</div>
          <div class="mac">${btEscape(m.mac_address)} · ${btEscape(BT_RECORDING_MODE_LABELS[m.recording_mode] || m.recording_mode)} · ${btEscape(rec)}</div>
        </div>
      </li>`;
    })
    .join("");
  list.querySelectorAll("li.selectable").forEach((li) => {
    li.addEventListener("click", () => {
      btSelectedConnectedMac = li.dataset.mac;
      renderBtConnectedList();
      showBtMicDetail(btSelectedConnectedMac);
    });
  });
}

function showBtMicDetail(mac) {
  const mic = (btStatusCache?.mics || []).find((m) => m.mac_address === mac);
  if (!mic) {
    $("bt-detail-card").classList.add("hidden");
    return;
  }
  $("bt-detail-card").classList.remove("hidden");
  $("bt-detail-title").textContent = mic.label || mic.mac_address;
  fillForm($("bt-mic-form"), {
    display_name: mic.display_name || "",
    recording_mode: mic.recording_mode || "on_demand",
    recording_interval_sec: mic.recording_interval_sec || 60,
    record_enabled: mic.record_enabled,
    recording_active: mic.recording_active,
  });
  updateBtDetailVisibility(mic.recording_mode || "on_demand");
}

async function loadBluetoothTab(options = {}) {
  btStatusCache = await api("/api/v1/bluetooth/status", options);
  renderBtConnectedList();
  if (btSelectedConnectedMac) {
    const stillThere = (btStatusCache.mics || []).some(
      (m) => m.mac_address === btSelectedConnectedMac && m.connected,
    );
    if (stillThere) showBtMicDetail(btSelectedConnectedMac);
    else {
      btSelectedConnectedMac = null;
      $("bt-detail-card").classList.add("hidden");
    }
  }
  renderBtDiscoveredList();
}

$("bt-discover").addEventListener("click", async () => {
  const statusEl = $("bt-scan-status");
  statusEl.classList.remove("hidden");
  statusEl.textContent = "Escaneando… permanece en esta pantalla unos segundos.";
  btSelectedDiscoverMac = null;
  try {
    const r = await api("/api/v1/bluetooth/discover", {
      successMessage: "Escaneo completado",
    });
    btDiscoveredDevices = r.devices || [];
    statusEl.textContent = r.mock
      ? `Modo simulación — ${btDiscoveredDevices.length} dispositivo(s) de prueba`
      : `${btDiscoveredDevices.length} dispositivo(s) detectado(s)`;
    await loadBluetoothTab({ silent: true });
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
});

$("bt-connect-selected").addEventListener("click", async () => {
  if (!btSelectedDiscoverMac) return;
  const device = btDiscoveredDevices.find((d) => d.mac_address === btSelectedDiscoverMac);
  try {
    await api("/api/v1/bluetooth/connect", {
      method: "POST",
      body: JSON.stringify({
        mac_address: btSelectedDiscoverMac,
        device_name: device?.name || device?.label || null,
      }),
      successMessage: "Micrófono conectado",
    });
    btSelectedDiscoverMac = null;
    await loadBluetoothTab({ silent: true });
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("bt-mic-form").querySelector('[name="recording_mode"]').addEventListener("change", (e) => {
  updateBtDetailVisibility(e.target.value);
});

$("bt-save-mic").addEventListener("click", async () => {
  if (!btSelectedConnectedMac) return;
  const patch = formToPatch($("bt-mic-form"));
  patch.record_enabled = $("bt-mic-form").elements.record_enabled.checked;
  patch.recording_active = $("bt-mic-form").elements.recording_active.checked;
  try {
    await api(`/api/v1/bluetooth/mics/${encodeURIComponent(btSelectedConnectedMac)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
      successMessage: "Micrófono actualizado",
    });
    await loadBluetoothTab({ silent: true });
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("bt-disconnect-selected").addEventListener("click", async () => {
  if (!btSelectedConnectedMac) return;
  try {
    await api("/api/v1/bluetooth/disconnect", {
      method: "POST",
      body: JSON.stringify({ mac_address: btSelectedConnectedMac }),
      successMessage: "Desconectado",
    });
    btSelectedConnectedMac = null;
    $("bt-detail-card").classList.add("hidden");
    await loadBluetoothTab({ silent: true });
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("bt-unpair-selected").addEventListener("click", async () => {
  if (!btSelectedConnectedMac) return;
  if (!window.confirm("¿Desvincular este micrófono? Tendrás que emparejarlo de nuevo.")) return;
  const mac = btSelectedConnectedMac;
  try {
    await api("/api/v1/bluetooth/unpair", {
      method: "POST",
      body: JSON.stringify({ mac_address: mac }),
      successMessage: "Dispositivo desvinculado",
    });
    btSelectedConnectedMac = null;
    btDiscoveredDevices = btDiscoveredDevices.filter((d) => d.mac_address !== mac);
    $("bt-detail-card").classList.add("hidden");
    $("bt-test-player-wrap").classList.add("hidden");
    await loadBluetoothTab({ silent: true });
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("bt-test-record").addEventListener("click", async () => {
  if (!btSelectedConnectedMac) return;
  const btn = $("bt-test-record");
  btn.disabled = true;
  btn.textContent = "Grabando… (10 s)";
  try {
    const r = await api(
      `/api/v1/bluetooth/mics/${encodeURIComponent(btSelectedConnectedMac)}/test-recording`,
      {
        method: "POST",
        body: JSON.stringify({ duration_sec: 10 }),
        successMessage: "Prueba de grabación completada",
      },
    );
    const res = await apiRequest(r.playback_url, { silent: true, jsonBody: false });
    const blob = await res.blob();
    const player = $("bt-test-player");
    if (player.dataset.objectUrl) URL.revokeObjectURL(player.dataset.objectUrl);
    const url = URL.createObjectURL(blob);
    player.dataset.objectUrl = url;
    player.src = url;
    $("bt-test-player-wrap").classList.remove("hidden");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Probar grabación (10 s)";
  }
});

if (localStorage.getItem(TOKEN_KEY)) {
  $("login-screen").classList.add("hidden");
  $("app").classList.remove("hidden");
  loadDashboard({ silent: true }).catch(logout);
}

initTheme();
