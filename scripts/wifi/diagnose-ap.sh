#!/usr/bin/env bash
# Diagnose why the NILO-Node WiFi AP may not appear when scanning.
set -euo pipefail

IFACE="${1:-$(iw dev 2>/dev/null | awk '$1=="Interface"{print $2; exit}')}"

echo "=== NILO-Node WiFi AP diagnostics ==="
echo ""

if curl -sf http://127.0.0.1:8080/api/v1/node/info >/dev/null 2>&1; then
  echo "--- API wifi status ---"
  curl -s http://127.0.0.1:8080/api/v1/node/info | python3 -m json.tool | sed -n '/"wifi"/,/^    }/p'
  echo ""
else
  echo "WARN: API not reachable on :8080"
  echo ""
fi

if [[ -z "${IFACE}" ]]; then
  echo "No WiFi interface detected."
  exit 1
fi

echo "--- uap0 (virtual AP) ---"
iw dev uap0 info 2>/dev/null || echo "uap0 not present"
echo ""

echo "--- Interface ${IFACE} ---"
ip link show "${IFACE}" 2>/dev/null || true
echo ""
iw dev "${IFACE}" info 2>/dev/null || echo "iw: cannot read ${IFACE}"
echo ""

echo "--- rfkill ---"
rfkill list wifi 2>/dev/null || echo "rfkill unavailable"
echo ""

if command -v nmcli >/dev/null 2>&1; then
  echo "--- NetworkManager ---"
  nmcli -f GENERAL.STATE,GENERAL.CONNECTION device show "${IFACE}" 2>/dev/null || true
  nmcli -f WIFI-HW,WIFI device status 2>/dev/null || true
  echo ""
fi

echo "--- hostapd / dnsmasq processes ---"
pgrep -af 'hostapd|dnsmasq' 2>/dev/null || echo "(none)"
echo ""

if [[ -f /data/wifi/hostapd.conf ]]; then
  echo "--- /data/wifi/hostapd.conf (container path on host if bind-mounted) ---"
  cat /data/wifi/hostapd.conf 2>/dev/null || true
fi

CONF="$(docker exec nilo-node cat /data/wifi/hostapd.conf 2>/dev/null || true)"
if [[ -n "${CONF}" ]]; then
  echo "--- container /data/wifi/hostapd.conf ---"
  echo "${CONF}"
  echo ""
fi

echo "--- Recent container logs (wifi/hostapd) ---"
docker logs nilo-node 2>&1 | grep -iE 'wifi|hostapd|dnsmasq|NetworkManager|AP mode' | tail -20 || true

echo ""
echo "Expected after fix: iw dev ${IFACE} info -> type AP"
echo "If type is managed/station, run: sudo ./scripts/wifi/prepare-ap-interface.sh ${IFACE}"
echo "Then: curl -X POST -H \"Authorization: Bearer \$NILO_LOCAL_API_TOKEN\" http://127.0.0.1:8080/api/v1/wifi/restart"
