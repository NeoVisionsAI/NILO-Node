const TOKEN_KEY = "nilo_setup_token";
const TITLES = {
  dashboard: "Panel",
  wifi: "WiFi",
  camera: "Cámara",
  bluetooth: "Bluetooth",
  mqtt: "MQTT",
};

const CAM_STATE_LABELS = {
  disconnected: "Desconectada",
  connecting: "Conectando…",
  connected: "Conectada",
  error: "Error",
};

const $ = (id) => document.getElementById(id);

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function showToast(msg, type = "ok") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${type}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    logout();
    throw new Error("Sesión expirada");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail) || res.statusText);
  }
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
    loadCameraStatus().catch(() => {});
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

async function saveSettings(section, patch) {
  const body = { [section]: patch };
  const r = await api("/api/v1/setup/settings", { method: "PATCH", body: JSON.stringify(body) });
  showToast("Configuración guardada y aplicada");
  return r;
}

async function loadSettingsForms() {
  const data = await api("/api/v1/setup/settings");
  const saved = data.settings || {};
  const merged = {
    camera: { ...data.live.camera, ...saved.camera },
    wifi: { ...data.live.wifi, ...saved.wifi },
    bluetooth: { ...data.live.bluetooth, ...saved.bluetooth },
    mqtt: { ...data.live.mqtt, ...saved.mqtt },
  };
  fillForm($("camera-form"), merged.camera);
  fillForm($("wifi-form"), merged.wifi);
  fillForm($("bt-form"), merged.bluetooth);
  fillForm($("mqtt-form"), merged.mqtt);
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

async function loadDashboard() {
  const d = await api("/api/v1/setup/dashboard");
  $("node-id").textContent = d.node_id;

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
    statCard("Bluetooth", `${d.bluetooth.connected_count} mic(s)`, ""),
    statCard("MQTT", d.mqtt?.connected ? "Conectado" : "Off", d.mqtt?.connected ? "stat-ok" : "stat-off"),
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
  if (d.mqtt?.subscribe_topic) {
    $("mqtt-example").textContent = JSON.stringify(
      { token: "••••", action: "settings.get", request_id: "1", payload: {} },
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

function renderCameraStatusSummary(s) {
  const state = CAM_STATE_LABELS[s.connection_state] || s.connection_state;
  const rows = [
    ["Estado", state],
    ["DepthAI", s.depthai_available ? "Disponible" : "No instalado / no detectado"],
    ["Modo pipeline", s.pipeline_mode],
    ["Dispositivo", s.connected_device_id || "—"],
    ["Cámaras encontradas", String(s.available_devices?.length ?? 0)],
  ];
  if (s.last_error) rows.push(["Último error", s.last_error]);
  $("cam-status-summary").innerHTML = rows
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`)
    .join("");

  if (s.connection_state === "connected") {
    setCamBanner(
      s.pipeline_mode === "mock"
        ? "Conectado en modo simulación (mock) — no hay cámara OAK real"
        : `Cámara conectada correctamente (${s.connected_device_id || "OAK"})`,
      s.pipeline_mode === "mock" ? "warn" : "ok",
    );
  } else if (s.connection_state === "error") {
    setCamBanner(s.last_error || "Error al conectar la cámara", "err");
  } else if (s.connection_state === "connecting") {
    setCamBanner("Conectando con la cámara…", "warn");
  } else {
    setCamBanner("Cámara desconectada — pulsa Detectar y luego Conectar", "warn");
  }
}

async function showPreviewFromResponse(res) {
  const img = $("cam-preview");
  const ph = $("cam-preview-placeholder");
  if (res.ok) {
    const blob = await res.blob();
    if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
    const url = URL.createObjectURL(blob);
    img.dataset.objectUrl = url;
    img.src = url;
    img.classList.remove("hidden");
    ph.classList.add("hidden");
    return true;
  }
  img.classList.add("hidden");
  ph.classList.remove("hidden");
  const err = await res.json().catch(() => ({}));
  const msg = typeof err.detail === "string" ? err.detail : "Sin frame disponible";
  ph.textContent = msg;
  return false;
}

async function refreshCameraPreview() {
  try {
    const res = await fetch("/api/v1/camera/preview", { headers: authHeaders() });
    await showPreviewFromResponse(res);
  } catch {
    $("cam-preview").classList.add("hidden");
    $("cam-preview-placeholder").classList.remove("hidden");
    $("cam-preview-placeholder").textContent = "Error de red al obtener preview";
  }
}

async function loadCameraStatus() {
  const s = await api("/api/v1/camera/status");
  $("cam-status").textContent = JSON.stringify(s, null, 2);
  renderCameraStatusSummary(s);
  return s;
}

async function runCameraAction(label, fn) {
  try {
    const result = await fn();
    await loadCameraStatus();
    return result;
  } catch (err) {
    setCamBanner(`${label}: ${err.message}`, "err");
    showToast(`${label}: ${err.message}`, "error");
    throw err;
  }
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
    });
    localStorage.setItem(TOKEN_KEY, data.token);
    $("login-screen").classList.add("hidden");
    $("app").classList.remove("hidden");
    await loadDashboard();
  } catch (err) {
    $("login-error").textContent = err.message;
    $("login-error").classList.remove("hidden");
  }
});

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.goto));
});

$("logout-btn").addEventListener("click", logout);
$("dash-refresh").addEventListener("click", () => loadDashboard().catch(() => logout()));

$("wifi-restart").addEventListener("click", async () => {
  try {
    await api("/api/v1/wifi/restart", { method: "POST" });
    showToast("WiFi reiniciado");
    await loadDashboard();
  } catch (err) {
    showToast(`WiFi: ${err.message}`, "error");
  }
});

$("wifi-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveSettings("wifi", formToPatch(e.target));
    await loadDashboard();
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("camera-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveSettings("camera", formToPatch(e.target));
    await loadCameraStatus();
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("bt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveSettings("bluetooth", formToPatch(e.target));
    showToast("Preferencias Bluetooth guardadas");
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("mqtt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await saveSettings("mqtt", formToPatch(e.target));
    showToast("MQTT guardado (reinicia el servicio para aplicar credenciales)");
  } catch (err) {
    showToast(err.message, "error");
  }
});

$("cam-discover").addEventListener("click", async () => {
  await runCameraAction("Detectar", async () => {
    const r = await api("/api/v1/camera/discover");
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
      showToast(`${n} cámara(s) encontrada(s)`);
      setCamBanner(`Encontradas ${n}: ${names}`, "ok");
    }
    $("cam-status").textContent = JSON.stringify(r, null, 2);
    return r;
  });
});

$("cam-connect").addEventListener("click", async () => {
  await runCameraAction("Conectar", async () => {
    const s = await api("/api/v1/camera/connect", { method: "POST", body: "{}" });
    if (s.connection_state === "error") {
      showToast(s.last_error || "Error al conectar", "error");
      setCamBanner(s.last_error || "Error al conectar", "err");
    } else if (s.connection_state === "connected") {
      showToast(
        s.pipeline_mode === "mock" ? "Conectado (modo mock)" : "Cámara conectada correctamente",
      );
      await refreshCameraPreview();
    }
    return s;
  });
});

$("cam-snapshot").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/v1/camera/snapshot", {
      method: "POST",
      headers: authHeaders(),
    });
    const ok = await showPreviewFromResponse(res);
    if (ok) {
      showToast("Frame capturado");
      setCamBanner("Frame capturado correctamente", "ok");
    } else {
      showToast("No se pudo capturar frame", "error");
    }
    await loadCameraStatus();
  } catch (err) {
    setCamBanner(`Captura: ${err.message}`, "err");
    showToast(`Captura: ${err.message}`, "error");
  }
});

$("cam-disconnect").addEventListener("click", async () => {
  await runCameraAction("Desconectar", async () => {
    const s = await api("/api/v1/camera/disconnect", { method: "POST" });
    $("cam-preview").classList.add("hidden");
    $("cam-preview-placeholder").classList.remove("hidden");
    $("cam-preview-placeholder").textContent = "Desconectada";
    showToast("Cámara desconectada");
    return s;
  });
});

$("bt-discover").addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/bluetooth/discover");
    $("bt-list").innerHTML =
      r.devices
        .map(
          (d) =>
            `<li><div><div class="name">${d.name || "Dispositivo"}</div><div class="mac">${d.mac_address}</div></div><button class="btn secondary" data-mac="${d.mac_address}">Conectar</button></li>`,
        )
        .join("") || "<li>No se encontraron dispositivos</li>";
    $("bt-list").querySelectorAll("button[data-mac]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api("/api/v1/bluetooth/connect", {
            method: "POST",
            body: JSON.stringify({ mac_address: btn.dataset.mac }),
          });
          showToast("Micrófono conectado");
          await loadDashboard();
        } catch (err) {
          showToast(err.message, "error");
        }
      });
    });
  } catch (err) {
    showToast(err.message, "error");
  }
});

if (localStorage.getItem(TOKEN_KEY)) {
  $("login-screen").classList.add("hidden");
  $("app").classList.remove("hidden");
  loadDashboard().catch(logout);
}

setInterval(() => {
  if (!localStorage.getItem(TOKEN_KEY)) return;
  if (!$("tab-camera").classList.contains("hidden")) refreshCameraPreview();
}, 2500);
