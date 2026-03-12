# Alkaline Network

**Open-source encrypted mesh internet for rural communities using Wi-Fi HaLow**

Build your own community mesh network to bring affordable internet to underserved areas. Uses long-range 900MHz Wi-Fi HaLow technology that penetrates walls and trees - just put the box in a window.

## How It Works

```
[Internet] → [GATEWAY] ←--900MHz HaLow--→ [PINGER] ←--→ [PINGER] ←--→ [PINGER]
  Starlink     (Shares)     ~500m/hop       (Home 1)      (Home 2)      (Home 3)
                  ↓                            ↓             ↓             ↓
             2.4GHz WiFi                  2.4GHz WiFi   2.4GHz WiFi   2.4GHz WiFi
```

- **Gateway (Mesh Gate)**: Has internet, shares it. Earns $2/customer/month.
- **Pinger (Mesh Point)**: Needs internet, connects through mesh. Pays $7.99/mo.
- **Auto-discovery**: Pingers automatically find and connect to the best gateway.
- **Multi-hop**: Traffic can jump up to 3 hops (~500m each through obstacles).
- **Encrypted**: 3 layers - WPA3 on mesh, NaCl tunnel encryption, TLS on websites.

## Hardware

**Heltec HT-H7608 Wi-Fi HaLow Router** - $79/unit

- Wi-Fi HaLow (802.11ah) @ 902-928 MHz
- ~500m range through walls/trees (window placement)
- ~1-3 miles line-of-sight
- Built-in 802.11s mesh
- 2.4GHz WiFi for your devices
- OpenWrt-based

**Buy here:** [heltec.org/project/ht-h7608](https://heltec.org/project/ht-h7608/)

## Quick Start

### 1. Set Up Your Server

Run on your VPS or home server (needs static IP):

```bash
pip install pynacl
python alkaline_complete.py --server --port 51820
```

Copy the public key it prints - you'll need it.

### 2. Install Requirements

```bash
pip install requests paramiko pynacl
```

### 3. Flash Devices

```bash
python flash_tool.py
```

Enter your server IP and public key, then:
1. Plug in Heltec via Ethernet
2. Click **GATEWAY** or **PINGER**
3. Unplug and deploy

### 4. Run Dashboard

```bash
python alkaline_dashboard.py --port 8080
```

Open http://localhost:8080 to manage customers and billing.

## What's Included

| File | What It Does |
|------|--------------|
| `alkaline_complete.py` | Encrypted tunnel server/client (TUN + NaCl + compression) |
| `alkaline_mesh.py` | Auto-discovery, auto-connect, failover |
| `alkaline_dashboard.py` | Web dashboard for customer/billing management |
| `alkaline_control.py` | GUI control panel (run via `start.bat`) |
| `adaptive_bandwidth.py` | Auto-adjusts bandwidth based on signal strength |
| `flash_tool.py` | GUI to provision and deploy devices |
| `scripts/alkaline_boot.sh` | Auto-starts everything on device boot |

## Adaptive Bandwidth

The network automatically adjusts bandwidth (1/2/4/8 MHz) based on signal strength:

| Bandwidth | Speed | Range | RSSI Threshold |
|-----------|-------|-------|----------------|
| 8 MHz | 15-32 Mbps | <300m | -55 dBm or better |
| 4 MHz | 8-15 Mbps | 300-600m | -65 dBm or better |
| 2 MHz | 2-6 Mbps | 600-900m | -75 dBm or better |
| 1 MHz | 150Kbps-1Mbps | 900m-1km+ | -85 dBm or better |

**How it works:**
- Monitors signal strength every 5 seconds
- **Downgrade fast** (1 min): If signal drops, quickly reduce bandwidth to maintain connection
- **Upgrade slow** (5 min): Only increase bandwidth after sustained good signal
- **Hysteresis**: Requires 5dB margin above threshold to upgrade (prevents oscillation)

**GUI Control:** The Bandwidth tab in the Control Panel (`start.bat`) lets you:
- View current bandwidth and signal strength
- Manually set bandwidth
- Simulate different signal conditions for testing

```bash
# CLI usage
python adaptive_bandwidth.py --status      # Show current status
python adaptive_bandwidth.py --set 4       # Set to 4 MHz
python adaptive_bandwidth.py --monitor     # Run auto-adjustment
```

## Security

**Three layers of encryption:**

1. **WPA3-SAE** - Mesh backbone (automatic, hardware-level)
2. **NaCl (X25519 + XSalsa20-Poly1305)** - Tunnel encryption (same as Signal)
3. **TLS/HTTPS** - Website encryption (passthrough)

Gateway operators cannot see customer traffic - they only see encrypted blobs.

## Expected Performance

| Hops | Speed | Good For |
|------|-------|----------|
| 1 hop | 8-15 Mbps | HD streaming, video calls, gaming |
| 2 hops | 4-7 Mbps | SD streaming, video calls |
| 3 hops | 2-4 Mbps | Browsing, email, light video |

**With compression:** Web browsing feels like 20-30 Mbps (70-90% compression on text/HTML).

## Pricing Model

| Role | Pays | Earns |
|------|------|-------|
| Customer (Pinger) | $7.99/mo + $100 deposit | - |
| Customer (no deposit) | $14.99/mo | - |
| Gateway Operator | $0 | $2/customer/month |

## Project Structure

```
AlkalineNetwork/
├── alkaline_complete.py   # Tunnel + encryption + compression
├── alkaline_mesh.py       # Auto-discovery + failover
├── alkaline_dashboard.py  # Web dashboard
├── alkaline_control.py    # GUI control panel
├── adaptive_bandwidth.py  # Auto-adjust bandwidth based on signal
├── flash_tool.py          # Device provisioning GUI
├── start.bat              # Windows launcher for control panel
├── scripts/
│   └── alkaline_boot.sh   # Auto-start on boot (Linux/OpenWrt)
├── config_template.json   # Config template
└── docs/                  # Documentation
```

## Contributing

Pull requests welcome. The code is straightforward Python - no complex dependencies.

## License

MIT License - Use it however you want.

---

**Built for communities that got left behind by big telecom.**

**AlkalineTech** | [alkalinehosting.com](https://alkalinehosting.com)
