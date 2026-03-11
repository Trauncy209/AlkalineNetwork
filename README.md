# Alkaline Network

**Open-source mesh internet for rural communities using Wi-Fi HaLow**

Build your own community mesh network to bring affordable internet to underserved rural areas. Uses long-range 900MHz Wi-Fi HaLow technology that goes through trees and covers 1-3 miles per node.

## How It Works

```
[Internet Source] → [GATEWAY] ←--900MHz HaLow--→ [NODE] ←--→ [NODE] ←--→ [NODE]
   Starlink/DSL        (Mesh Gate)              (Mesh Point)  (Mesh Point)  (Mesh Point)
                            ↓                        ↓             ↓             ↓
                      2.4GHz WiFi              2.4GHz WiFi   2.4GHz WiFi   2.4GHz WiFi
                            ↓                        ↓             ↓             ↓
                      Local devices             Home 1         Home 2         Home 3
```

- **Gateway (Mesh Gate)**: Placed at a location WITH existing internet. Shares connection via 900MHz HaLow.
- **Node (Mesh Point)**: Placed at homes WITHOUT internet. Connects to gateway or relays through other nodes.
- **Multi-hop mesh**: Nodes can relay through other nodes (recommended max 3 hops)
- **Coverage**: ~1-3 miles per node depending on terrain

## Hardware

**Heltec HT-H7608 Wi-Fi HaLow Router** - ~$79/unit

- Wi-Fi HaLow (802.11ah) @ 902-928 MHz (sub-GHz, long range)
- 2.4GHz WiFi for end-user devices
- Built-in 802.11s mesh support
- 1km+ range, penetrates walls and trees
- Operating temp: -20°C to 70°C (outdoor rated)
- OpenWrt-based, fully configurable via SSH

**Where to buy:**
- [Heltec Official Store](https://heltec.org/project/ht-h7608/) - $79
- [Amazon](https://www.amazon.com/dp/B0F2HT6ZFX) - ~$85

## Quick Start

### 1. Install Requirements

```bash
pip install requests paramiko
```

### 2. Run Flash Tool

```bash
python flash_tool.py
```

A GUI will open with two buttons: GATEWAY and NODE.

### 3. Provision Devices

1. Connect Heltec device to your PC via Ethernet cable
2. Click **GATEWAY** for devices going to locations with internet
3. Click **NODE** for devices going to locations without internet
4. Device reboots with your mesh config
5. Unplug and deploy!

### 4. Deploy

**Gateway setup:**
1. Place at location with internet
2. Connect Ethernet port to existing router
3. Power on

**Node setup:**
1. Place at location needing internet
2. Power on
3. Connect devices to the "Alkaline-PN-XXX" WiFi network
4. Done - mesh connects automatically

## Expected Performance

| Position | Typical Speed | Good For |
|----------|---------------|----------|
| 1 hop from gateway | 8-15 Mbps | HD streaming, video calls, gaming |
| 2 hops | 4-7 Mbps | SD streaming, video calls |
| 3 hops | 2-4 Mbps | Browsing, email, light video |

This is NOT fiber. It's functional internet for communities that have nothing. Good for Netflix, Zoom, gaming - just not 4K on multiple devices simultaneously.

## Network Architecture

**Recommended limits for best performance:**
- 3-5 nodes per gateway
- 3 hops maximum
- Higher placement = better range

**Security:**
- WPA3-SAE encryption on mesh backbone
- WPA2-PSK on user-facing WiFi
- All credentials auto-generated and stored in `network_config.json`
- Admin passwords changed on all devices

## Project Structure

```
AlkalineNetwork/
├── flash_tool.py          # Main provisioning tool (GUI + CLI)
├── network_config.json    # Auto-generated after first run
├── start_flash_tool.bat   # Windows launcher
├── docs/
│   ├── HARDWARE.md        # Hardware details
│   └── HOW_IT_WORKS.md    # Technical deep dive
└── alkaline-core/         # Core networking code
```

## CLI Usage

```bash
# Provision as gateway
python flash_tool.py gateway

# Provision as node
python flash_tool.py pinger

# Show your network config
python flash_tool.py --show-config

# Find a device by MAC (for tracking)
python flash_tool.py --find-device AA:BB:CC:DD:EE:FF
```

## Customization

Edit the top of `flash_tool.py`:

```python
MESH_ID = "YourNetworkName"           # Your mesh network name
CUSTOMER_WIFI_PREFIX = "YourNetwork-" # What users see as WiFi
```

Or edit `network_config.json` after first run to change credentials.

## Use Cases

- **Rural communities** without cable/fiber access
- **Disaster relief** temporary networks  
- **Events/festivals** temporary coverage
- **Farms/ranches** covering large properties
- **Developing regions** low-cost infrastructure

## Contributing

Pull requests welcome! See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

MIT License - Use it however you want. See [LICENSE](LICENSE).

---

**Built for communities that got left behind by big telecom.**
---

**Built by AlkalineTech** | [AlkalineHosting.com](https://alkalinehosting.com)
