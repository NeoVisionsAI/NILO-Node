# OAK-D-SR-PoE — red directa al mini PC

Instalación final: **OAK → PoE injector → Ethernet del mini PC** (sin USB de datos).

## Por qué Ubuntu dice “conexión de red fallida”

Ese enlace **no tiene DHCP ni internet**. NetworkManager intenta obtener IP automáticamente, falla, y muestra el aviso — **aunque el LED ethernet esté verde** y la cámara esté en azul.

Eso es **normal**. Hay que configurar **IP estática** en el mini PC en ese puerto.

## Configuración recomendada

| Equipo | IP |
|--------|-----|
| Mini PC (Ethernet PoE) | `192.168.1.10/24` |
| OAK-D-SR-PoE (fábrica Luxonis) | `192.168.1.15` |

Verifica la IP de fábrica de tu unidad en [Luxonis](https://docs.luxonis.com/) si el ping falla.

### Script automático (NetworkManager)

```bash
sudo ./scripts/oak/setup-poe-network.sh
ping -c 2 192.168.1.15
```

Variables opcionales:

```bash
sudo POE_IFACE=enp2s0 POE_HOST_IP=192.168.1.10 ./scripts/oak/setup-poe-network.sh
```

Usa **otra interfaz** (WiFi u otro ethernet) para internet, Docker Hub y `apt`.

## Probar desde Docker

El contenedor usa `network_mode: host` — comparte la red del mini PC.

```bash
# Descubrir cámara
./scripts/oak/run-in-docker.sh discover

# ToF GUI (PoE)
export OAK_DEVICE_IP=192.168.1.15   # si discover no lista la cámara
./scripts/oak/run-in-docker.sh tof

# O explícito:
./scripts/oak/run-in-docker.sh tof -- --device-ip 192.168.1.15 --prefer poe
```

## Config NILO-Node (`config/nilo-node.yaml`)

```yaml
camera:
  connection_mode: poe
  device_ip: "192.168.1.15"   # opcional si auto-discover funciona
  mock_when_unavailable: false
```

## Docker Compose

PoE **no requiere** montar `/dev/bus/usb`. Producción usa Ethernet + `network_mode: host`.

El montaje USB en compose es opcional (solo si también usas USB en desarrollo).

## Troubleshooting

| Síntoma | Acción |
|---------|--------|
| LED verde, aviso NM | Ejecutar `setup-poe-network.sh` |
| `ping 192.168.1.15` falla | Revisar cable, PoE injector, IP cámara |
| discover vacío | IP host en mismo /24; probar `OAK_DEVICE_IP` |
| install.sh lento | Internet debe ir por WiFi/otro NIC, no por PoE |

## Referencias

- [OAK-D-SR-PoE](https://shop.luxonis.com/products/oak-d-sr-poe)
- [DepthAI ToF example](https://docs.luxonis.com/software/depthai/examples/tof_depth)
