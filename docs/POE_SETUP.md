# OAK-D-SR-PoE — red directa al mini PC

Instalación final: **OAK → PoE injector → Ethernet del mini PC** (sin USB de datos).

## Por qué Ubuntu dice “conexión de red fallida”

Ese enlace **no tiene DHCP ni internet**. NetworkManager intenta obtener IP automáticamente, falla, y muestra el aviso — **aunque el LED ethernet esté verde** y la cámara esté en azul.

Eso es **normal**. Hay que configurar **IP estática** en el mini PC en ese puerto.

## IPs correctas (Luxonis, sin DHCP)

Según la [documentación oficial Luxonis](https://docs.luxonis.com/projects/hardware/en/latest/pages/guides/getting-started-with-poe/), cuando **no hay servidor DHCP** en el cable, la cámara PoE usa IP estática **`169.254.1.222`**.

| Equipo | IP |
|--------|-----|
| Mini PC (Ethernet PoE) | `169.254.1.10/16` (máscara `255.255.0.0`) |
| OAK PoE (sin DHCP) | `169.254.1.222` |

> **Importante:** Si haces `ping 192.168.1.10` y responde, probablemente estás haciendo ping **a tu propio mini PC** (IP que pusimos en el puerto), **no a la cámara**. La cámara no está en `.10` ni en `192.168.1.15` en este escenario.

Si la cámara estuvo antes en red de oficina con DHCP, puede tener otra IP (p. ej. `192.168.1.188`). En enlace directo sin DHCP vuelve al fallback `169.254.1.222`.

### Script automático (NetworkManager)

```bash
# Sustituye enp2s0 por tu puerto Ethernet del PoE
sudo POE_IFACE=enp2s0 ./scripts/oak/setup-poe-network.sh
ping -c 2 169.254.1.222
```

Variables opcionales:

```bash
sudo POE_IFACE=enp2s0 POE_HOST_IP=169.254.1.10 POE_PREFIX=16 POE_CAMERA_IP=169.254.1.222 ./scripts/oak/setup-poe-network.sh
```

Usa **otra interfaz** (WiFi u otro ethernet) para internet, Docker Hub y `apt`.

## Probar desde Docker

El contenedor usa `network_mode: host` — comparte la red del mini PC.

```bash
# Descubrir cámara
./scripts/oak/run-in-docker.sh discover

# ToF GUI (PoE)
sudo OAK_DEVICE_IP=169.254.1.222 ./scripts/oak/run-in-docker.sh tof

# O explícito:
sudo ./scripts/oak/run-in-docker.sh tof -- --device-ip 169.254.1.222 --prefer poe
```

## Config NILO-Node (`config/nilo-node.yaml`)

```yaml
camera:
  connection_mode: poe
  device_ip: "169.254.1.222"
  mock_when_unavailable: false
```

(`deploy.sh update` aplica esto automáticamente con `patch_camera_poe.py`.)

## Docker Compose

PoE **no requiere** montar `/dev/bus/usb`. Producción usa Ethernet + `network_mode: host`.

## Troubleshooting

| Síntoma | Acción |
|---------|--------|
| LED verde, aviso NM | Ejecutar `setup-poe-network.sh` |
| `ping 192.168.1.15` falla | Normal — usar `169.254.1.222` |
| `ping 192.168.1.10` OK | Eso es el mini PC, no la cámara |
| discover vacío | Probar `OAK_DEVICE_IP=169.254.1.222` |
| Varias ethernets | `POE_IFACE=` al puerto del PoE |

## Referencias

- [Luxonis PoE getting started](https://docs.luxonis.com/projects/hardware/en/latest/pages/guides/getting-started-with-poe/)
- [OAK-D-SR-PoE](https://shop.luxonis.com/products/oak-d-sr-poe)
- [DepthAI ToF example](https://docs.luxonis.com/software/depthai/examples/tof_depth)
