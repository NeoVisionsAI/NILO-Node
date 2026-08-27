#!/usr/bin/env bash
# Diagnose NILO-Node WiFi AP failures (run on the mini PC as root).
set -euo pipefail

STA="${1:-}"
if [[ -z "${STA}" ]]; then
  STA="$(iw dev 2>/dev/null | awk '
    $1=="Interface" {
      n=$2
      if (n ~ /^uap/ || n ~ /-ap$/ || n == "niloap0") next
      print n; exit
    }')"
fi

echo "=== NILO-Node WiFi AP diagnostics ==="
echo "STA interface: ${STA:-?}"
echo ""

if curl -sf http://127.0.0.1:8080/api/v1/node/info >/dev/null 2>&1; then
  echo "--- API wifi status ---"
  curl -s http://127.0.0.1:8080/api/v1/node/info | python3 -m json.tool | sed -n '/"wifi"/,/^    }/p'
  echo ""
fi

echo "--- All wireless interfaces (iw dev) ---"
iw dev 2>/dev/null || echo "(iw failed)"
echo ""

echo "--- Driver concurrent limits (iw list) ---"
iw list 2>/dev/null | grep -A6 "valid interface combinations" || true
echo ""

if [[ -n "${STA}" ]]; then
  echo "--- STA ${STA} link (channel for AP) ---"
  iw dev "${STA}" link 2>/dev/null || true
  echo ""
fi

for ap in wlp3s0-ap niloap0 uap0; do
  echo "--- AP candidate ${ap} ---"
  iw dev "${ap}" info 2>/dev/null || echo "${ap}: not present"
  ip link show "${ap}" 2>/dev/null || true
  echo ""
done

echo "--- rfkill ---"
rfkill list wifi 2>/dev/null || true
echo ""

echo "--- hostapd / dnsmasq ---"
pgrep -af 'hostapd|dnsmasq' 2>/dev/null || echo "(none)"
echo ""

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
  echo "--- container /data/wifi/hostapd.conf ---"
  docker exec nilo-node cat /data/wifi/hostapd.conf 2>/dev/null || true
  echo ""
  echo "--- container /data/wifi/hostapd.log (last 40 lines) ---"
  docker exec nilo-node sh -c 'tail -40 /data/wifi/hostapd.log 2>/dev/null || echo "(empty)"'
  echo ""
  echo "--- container /data/wifi/hostapd-debug.log (last 60 lines) ---"
  docker exec nilo-node sh -c 'tail -60 /data/wifi/hostapd-debug.log 2>/dev/null || echo "(run wifi restart first to generate)"'
  echo ""
  echo "--- container logs (wifi/hostapd, last 30) ---"
  docker logs nilo-node 2>&1 | grep -iE 'wifi|hostapd|wlp3s0|nl80211' | tail -30 || true
  echo ""
fi

echo "--- kernel (dmesg wifi last 20) ---"
dmesg 2>/dev/null | grep -iE 'wlan|wifi|nl80211|hostapd|wlp3s0' | tail -20 || true
echo ""

echo "=== Manual hostapd debug (reproduces exact failure) ==="
echo "Run as root (stops NILO hostapd briefly):"
echo ""
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nilo-node; then
  CONF="/tmp/nilo-hostapd-debug.conf"
  docker exec nilo-node cat /data/wifi/hostapd.conf > "${CONF}" 2>/dev/null || true
  AP_IFACE="$(grep '^interface=' "${CONF}" 2>/dev/null | cut -d= -f2 || echo wlp3s0-ap)"
  echo "  sudo killall hostapd 2>/dev/null; sudo iw dev ${AP_IFACE} del 2>/dev/null; true"
  echo "  sudo hostapd -dd ${CONF} 2>&1 | tee /tmp/hostapd-manual-debug.log"
  echo ""
  echo "  # Success: Ctrl+C then restart NILO WiFi:"
  echo "  curl -X POST -H \"Authorization: Bearer \$NILO_LOCAL_API_TOKEN\" http://127.0.0.1:8080/api/v1/wifi/restart"
else
  echo "  (nilo-node container not running)"
fi
