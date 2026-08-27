# WiFi AP — mini PC (no ejecutar en PC de desarrollo)

NILO-Node expone un AP para tablet/Cardmed (`nilo-node-XXXXXXXX`) en **`192.168.50.1`**, inspirado en el stack NiloCardmed (uap0 + STA concurrente).

## Arquitectura

| Interfaz | Rol |
|----------|-----|
| `wlp3s0` / `wlan0` | STA — puede seguir conectada a otra WiFi |
| `uap0` | AP virtual — red `nilo-node-*` + portal `/setup/` |

Si el driver no soporta AP+STA, fallback automático a **modo dedicado** (AP en la tarjeta física, desconecta STA).

## Seguridad en desarrollo

- **`wifi.hardware_ap: false`** en `nilo-node.dev.yaml` — el contenedor local **no toca** nmcli/iw/hostapd.
- Scripts shell requieren **`NILO_WIFI_ALLOW_HOST_SCRIPTS=1`** — evita romper la WiFi del portátil por accidente.
- **No ejecutar** `prepare-ap-interface.sh` ni `wifi-ap-run.sh` en el PC de desarrollo.

## Despliegue en el mini PC (un solo comando)

```bash
cd /opt/nilo-node
sudo ./scripts/deploy.sh update    # rebuild + preparar uap0 + reiniciar AP + resumen
# o sin rebuild:
sudo ./scripts/deploy.sh reload
```

`deploy.sh` hace automáticamente:

1. Parche WiFi en config (`hardware_ap`, `uap0`, …)
2. `ensure-wifi-ap.sh` — uap0 unmanaged en NetworkManager
3. `prepare-ap-interface.sh` — crear/reutilizar uap0
4. Arrancar/reiniciar contenedor
5. `POST /api/v1/wifi/restart` — aplicar hostapd+dnsmasq
6. Imprimir SSID, modo y enlace al portal

Para omitir pasos WiFi: `sudo SKIP_WIFI_AP=1 ./scripts/deploy.sh update`

Reinicio manual (solo si hace falta):

```bash
source /opt/nilo-node/.env
curl -X POST -H "Authorization: Bearer $NILO_LOCAL_API_TOKEN" \
  http://127.0.0.1:8080/api/v1/wifi/restart
```

## Verificación

```bash
# API — buscar ap_mode: "concurrent" y ap_interface: "uap0"
curl -s http://127.0.0.1:8080/api/v1/node/info | python3 -m json.tool

# Solo lectura — seguro en mini PC
sudo ./scripts/wifi/diagnose-ap.sh wlp3s0

# Debe mostrar type AP en uap0
iw dev uap0 info
```

## Config YAML (`wifi.*`)

| Campo | Mini PC | Dev laptop |
|-------|---------|------------|
| `enabled` | `true` | `false` |
| `hardware_ap` | `true` | `false` |
| `ap_interface` | `uap0` | — |
| `concurrent_sta_ap` | `true` | — |
| `backend` | `container` (default) o `host` | — |

## Backend `host` (opcional, estilo NiloCardmed)

Si prefieres que hostapd corra en el host vía systemd:

1. En config: `wifi.backend: "host"`
2. `sudo systemctl enable --now nilo-node-wifi-ap` (instalado por `ensure-wifi-ap.sh`)
3. **No** usar `backend: host` y contenedor a la vez — elige uno.

### Error `Name not unique on network`

`uap0` ya existía de un arranque anterior. Tras `deploy update`, reinicia el AP:

```bash
curl -X POST -H "Authorization: Bearer $NILO_LOCAL_API_TOKEN" \
  http://127.0.0.1:8080/api/v1/wifi/restart
```

Si persiste, en el mini PC (no en el portátil dev):

```bash
sudo iw dev uap0 del 2>/dev/null || true
sudo NILO_WIFI_ALLOW_HOST_SCRIPTS=1 ./scripts/wifi/prepare-ap-interface.sh wlp3s0 uap0
```


| Síntoma | Causa | Acción |
|---------|-------|--------|
| API `running: true` pero no se ve la red | hostapd vivo, interfaz no en modo AP | `diagnose-ap.sh`; comprobar `iw dev uap0 info` |
| Solo modo `dedicated` | Driver sin AP+STA | Normal en algunos chipsets x86; Ethernet = internet |
| Tablet sin IP | dnsmasq | logs contenedor; `wifi-ap-run.sh repair` si backend host |
| WiFi del portátil rota | Script WiFi en dev | Usar `hardware_ap: false`; no exportar `NILO_WIFI_ALLOW_HOST_SCRIPTS` |
