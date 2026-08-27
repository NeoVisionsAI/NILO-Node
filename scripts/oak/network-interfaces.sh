#!/usr/bin/env bash
# List / pick Ethernet interface for OAK PoE link.
#
# Usage:
#   sudo ./scripts/oak/network-interfaces.sh list
#   sudo ./scripts/oak/network-interfaces.sh pick          # interactive → prints iface name
#   sudo POE_IFACE=enp2s0 ./scripts/oak/network-interfaces.sh pick
#
# Heuristics:
#   - PoE = cable directo OAK → inyector → puerto Ethernet del mini PC
#   - Suele tener cable conectado (carrier) y NO ser la ruta por defecto a Internet

set -euo pipefail

POE_IFACE="${POE_IFACE:-}"
POE_STATE_FILE="${POE_STATE_FILE:-}"

log() { printf '[net-ifaces] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

_iface_exists() {
  [[ -d "/sys/class/net/${1}" ]]
}

_iface_carrier() {
  local iface="$1"
  if [[ -f "/sys/class/net/${iface}/carrier" ]]; then
    cat "/sys/class/net/${iface}/carrier" 2>/dev/null || echo "?"
  else
    echo "?"
  fi
}

_iface_operstate() {
  local iface="$1"
  if [[ -f "/sys/class/net/${iface}/operstate" ]]; then
    cat "/sys/class/net/${iface}/operstate" 2>/dev/null || echo "?"
  else
    echo "?"
  fi
}

_iface_mac() {
  cat "/sys/class/net/${1}/address" 2>/dev/null || echo "?"
}

_iface_ips() {
  ip -4 -br addr show dev "$1" 2>/dev/null | awk '{print $3}' | tr '\n' ',' | sed 's/,$//'
}

_iface_is_wifi() {
  if [[ "$(cat "/sys/class/net/${1}/uevent" 2>/dev/null)" == *"DEVTYPE=wlan"* ]]; then
    return 0
  fi
  iw dev "$1" info >/dev/null 2>&1
}

_iface_is_virtual() {
  local iface="$1"
  [[ "${iface}" == lo ]] && return 0
  [[ "${iface}" == docker* ]] && return 0
  [[ "${iface}" == br-* ]] && return 0
  [[ "${iface}" == veth* ]] && return 0
  [[ "${iface}" == virbr* ]] && return 0
  return 1
}

_default_route_iface() {
  ip -4 route show default 2>/dev/null | awk '{print $5; exit}'
}

_nmcli_type() {
  local iface="$1" t=""
  if command -v nmcli >/dev/null 2>&1; then
    t="$(nmcli -t -f TYPE device show "${iface}" 2>/dev/null | head -1 || true)"
  fi
  if [[ -n "${t}" ]]; then
    echo "${t}"
  else
    echo "ethernet"
  fi
}

_guess_role() {
  local iface="$1" carrier="$2" def="$3"
  if _iface_is_wifi "${iface}"; then
    echo "WiFi (AP del nodo / cliente)"
    return
  fi
  if [[ "${iface}" == "${def}" ]]; then
    echo "Internet (ruta por defecto) — NO PoE"
    return
  fi
  if [[ "${carrier}" == "1" ]]; then
    echo "Candidata PoE (cable conectado)"
    return
  fi
  if [[ "${carrier}" == "0" ]]; then
    echo "Ethernet sin cable"
    return
  fi
  echo "Ethernet"
}

_collect_candidates() {
  CANDIDATES=()
  CAND_ROLES=()
  CAND_CARRIER=()
  CAND_IPS=()
  CAND_STATE=()
  CAND_MAC=()
  CAND_TYPES=()

  local def
  def="$(_default_route_iface)"

  while read -r iface; do
    [[ -n "${iface}" ]] || continue
    _iface_is_virtual "${iface}" && continue

    local carrier state ips mac role type_label
    carrier="$(_iface_carrier "${iface}")"
    state="$(_iface_operstate "${iface}")"
    ips="$(_iface_ips "${iface}")"
    mac="$(_iface_mac "${iface}")"
    [[ -z "${ips}" ]] && ips="-"

    if _iface_is_wifi "${iface}"; then
      type_label="wifi"
    else
      type_label="$(_nmcli_type "${iface}")"
      [[ "${type_label}" == "wifi" ]] && type_label="wifi" || type_label="ethernet"
    fi

    role="$(_guess_role "${iface}" "${carrier}" "${def}")"

    CANDIDATES+=("${iface}")
    CAND_ROLES+=("${role}")
    CAND_CARRIER+=("${carrier}")
    CAND_IPS+=("${ips}")
    CAND_STATE+=("${state}")
    CAND_MAC+=("${mac}")
    CAND_TYPES+=("${type_label}")
  done < <(ip -br link 2>/dev/null | awk '{print $1}')

  DEFAULT_PICK=-1
  local i
  for i in "${!CANDIDATES[@]}"; do
    [[ "${CAND_TYPES[$i]}" == "ethernet" ]] || continue
    [[ "${CAND_CARRIER[$i]}" == "1" ]] || continue
    [[ "${CAND_ROLES[$i]}" != *"Internet"* ]] || continue
    DEFAULT_PICK=$((i + 1))
    break
  done
}

print_interface_table() {
  _collect_candidates
  if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
    log "No se encontraron interfaces."
    return 1
  fi

  local def
  def="$(_default_route_iface)"
  log "Ruta por defecto (Internet): ${def:-ninguna}"
  log ""
  printf '%-4s %-12s %-10s %-8s %-6s %-22s %s\n' "#" "Interfaz" "Tipo" "Estado" "Cable" "IPv4" "Notas" >&2
  printf '%-4s %-12s %-10s %-8s %-6s %-22s %s\n' "----" "------------" "----------" "--------" "------" "----------------------" "-----" >&2

  local i
  for i in "${!CANDIDATES[@]}"; do
    local cable="${CAND_CARRIER[$i]}"
    [[ "${cable}" == "1" ]] && cable="sí" || [[ "${cable}" == "0" ]] && cable="no" || cable="?"
    printf '%-4s %-12s %-10s %-8s %-6s %-22s %s\n' \
      "$((i + 1))" \
      "${CANDIDATES[$i]}" \
      "${CAND_TYPES[$i]}" \
      "${CAND_STATE[$i]}" \
      "${cable}" \
      "${CAND_IPS[$i]}" \
      "${CAND_ROLES[$i]}" >&2
  done
  log ""
  log "PoE: OAK → inyector → puerto Ethernet del mini PC (sin Internet en ese cable)."
}

_load_saved_poe_iface() {
  local f="${POE_STATE_FILE}"
  [[ -z "${f}" ]] && f="/opt/nilo-node/config/poe.env"
  [[ -f "${f}" ]] || return 1
  # shellcheck disable=SC1090
  source "${f}"
  [[ -n "${POE_IFACE:-}" ]] && _iface_exists "${POE_IFACE}"
}

_save_poe_iface() {
  local iface="$1"
  local f="${POE_STATE_FILE}"
  [[ -z "${f}" ]] && f="/opt/nilo-node/config/poe.env"
  mkdir -p "$(dirname "${f}")"
  cat > "${f}" <<EOF
# Interfaz Ethernet hacia OAK PoE (generado por network-interfaces.sh)
POE_IFACE=${iface}
POE_CONFIGURED_AT=$(date -Iseconds)
EOF
  log "Guardado POE_IFACE=${iface} en ${f}"
}

pick_poe_interface() {
  if [[ -n "${POE_IFACE}" ]]; then
    echo "${POE_IFACE}"
    return 0
  fi

  if [[ -t 0 ]]; then
    print_interface_table

    if _load_saved_poe_iface; then
      log "Última interfaz PoE guardada: ${POE_IFACE}"
      read -r -p "¿Usar ${POE_IFACE}? [S/n]: " reuse || true
      if [[ ! "${reuse}" =~ ^[Nn]$ ]]; then
        echo "${POE_IFACE}"
        return 0
      fi
    fi

    _collect_candidates
    local choices=()
    local idx
    for idx in "${!CANDIDATES[@]}"; do
      [[ "${CAND_TYPES[$idx]}" == "ethernet" ]] && choices+=("${idx}")
    done

    if [[ ${#choices[@]} -eq 0 ]]; then
      die "No hay interfaces Ethernet disponibles."
    fi

    log ""
    log "Elige el puerto Ethernet conectado al inyector PoE de la cámara OAK:"
    read -r -p "Número [0=omitir, default=${DEFAULT_PICK:-?}]: " pick || true

    if [[ -z "${pick}" && "${DEFAULT_PICK}" -gt 0 ]]; then
      pick="${DEFAULT_PICK}"
    fi

    if [[ "${pick}" == "0" || -z "${pick}" ]]; then
      log "PoE omitido."
      return 1
    fi

    if ! [[ "${pick}" =~ ^[0-9]+$ ]]; then
      die "Selección inválida: ${pick}"
    fi

    local sel=$((pick - 1))
    if [[ "${sel}" -lt 0 || "${sel}" -ge ${#CANDIDATES[@]} ]]; then
      die "Número fuera de rango: ${pick}"
    fi

    if [[ "${CAND_TYPES[$sel]}" != "ethernet" ]]; then
      die "${CANDIDATES[$sel]} no es Ethernet — elige un puerto RJ45."
    fi

    POE_IFACE="${CANDIDATES[$sel]}"
    _save_poe_iface "${POE_IFACE}"
    echo "${POE_IFACE}"
    return 0
  fi

  # Non-interactive fallback: first ethernet with carrier, not default route
  _collect_candidates
  local i def
  def="$(_default_route_iface)"
  for i in "${!CANDIDATES[@]}"; do
    [[ "${CAND_TYPES[$i]}" == "ethernet" ]] || continue
    [[ "${CANDIDATES[$i]}" != "${def}" ]] || continue
    [[ "${CAND_CARRIER[$i]}" == "1" ]] && { echo "${CANDIDATES[$i]}"; return 0; }
  done
  for i in "${!CANDIDATES[@]}"; do
    [[ "${CAND_TYPES[$i]}" == "ethernet" ]] || continue
    [[ "${CANDIDATES[$i]}" != "${def}" ]] || continue
    echo "${CANDIDATES[$i]}"
    return 0
  done
  return 1
}

cmd="${1:-list}"
case "${cmd}" in
  list)
    print_interface_table
    ;;
  pick)
    pick_poe_interface
    ;;
  saved)
    if _load_saved_poe_iface; then
      echo "${POE_IFACE}"
    else
      exit 1
    fi
    ;;
  *)
    echo "Usage: $0 {list|pick|saved}" >&2
    exit 1
    ;;
esac
