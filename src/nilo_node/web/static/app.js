const TOKEN_KEY = "nilo_setup_token";
const TITLES = {
  dashboard: "Panel",
  wifi: "WiFi",
  camera: "Cámara",
  bluetooth: "Bluetooth",
  mqtt: "MQTT",
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

async function loadDashboard() {
  const d = await api("/api/v1/setup/dashboard");
  $("node-id").textContent = d.node_id;

  const camCls = d.camera.connection_state === "connected" ? "stat-ok" : "stat-off";
  $("dashboard-cards").innerHTML = [
    statCard("Cámara", d.camera.connection_state, camCls),
    statCard("WiFi AP", d.wifi.running ? d.wifi.ssid : "Parado", d.wifi.running ? "stat-ok" : "stat-warn"),
    statCard("Bluetooth", `${d.bluetooth.connected_count} mic(s)`, ""),
    statCard("MQTT", d.mqtt?.connected ? "Conectado" : "Off", d.mqtt?.connected ? "stat-ok" : "stat-off"),
  ].join("");

  $("wifi-status").innerHTML = [
    ["SSID", d.wifi.ssid || "—"],
    ["IP AP", d.wifi.ap_ip],
    ["Estado", d.wifi.running ? "Activo" : "Parado"],
    ["Mock", d.wifi.mock ? "Sí" : "No"],
  ].map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");

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

async function refreshCameraPreview() {
  const img = $("cam-preview");
  const ph = $("cam-preview-placeholder");
  try {
    const res = await fetch("/api/v1/camera/preview", { headers: authHeaders() });
    if (res.ok) {
      img.src = URL.createObjectURL(await res.blob());
      img.classList.remove("hidden");
      ph.classList.add("hidden");
    } else {
      img.classList.add("hidden");
      ph.classList.remove("hidden");
    }
  } catch {
    img.classList.add("hidden");
    ph.classList.remove("hidden");
  }
}

async function loadCameraStatus() {
  const s = await api("/api/v1/camera/status");
  $("cam-status").textContent = JSON.stringify(s, null, 2);
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
  await api("/api/v1/wifi/restart", { method: "POST" });
  showToast("WiFi reiniciado");
  await loadDashboard();
});

$("wifi-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("wifi", formToPatch(e.target));
  await loadDashboard();
});

$("camera-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("camera", formToPatch(e.target));
  await loadCameraStatus();
});

$("bt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("bluetooth", formToPatch(e.target));
  showToast("Preferencias Bluetooth guardadas");
});

$("mqtt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSettings("mqtt", formToPatch(e.target));
  showToast("MQTT guardado (reinicia el servicio para aplicar credenciales)");
});

$("cam-discover").addEventListener("click", async () => {
  const r = await api("/api/v1/camera/discover");
  $("cam-status").textContent = JSON.stringify(r, null, 2);
});

$("cam-connect").addEventListener("click", async () => {
  await api("/api/v1/camera/connect", { method: "POST", body: "{}" });
  await loadCameraStatus();
  await refreshCameraPreview();
});

$("cam-disconnect").addEventListener("click", async () => {
  await api("/api/v1/camera/disconnect", { method: "POST" });
  await loadCameraStatus();
});

$("bt-discover").addEventListener("click", async () => {
  const r = await api("/api/v1/bluetooth/discover");
  $("bt-list").innerHTML = r.devices
    .map(
      (d) =>
        `<li><div><div class="name">${d.name || "Dispositivo"}</div><div class="mac">${d.mac_address}</div></div><button class="btn secondary" data-mac="${d.mac_address}">Conectar</button></li>`,
    )
    .join("") || "<li>No se encontraron dispositivos</li>";
  $("bt-list").querySelectorAll("button[data-mac]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api("/api/v1/bluetooth/connect", {
        method: "POST",
        body: JSON.stringify({ mac_address: btn.dataset.mac }),
      });
      showToast("Micrófono conectado");
      await loadDashboard();
    });
  });
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
