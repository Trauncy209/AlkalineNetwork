# Hardware Guide

## Primary Hardware: Heltec HT-H7608

**Price: $79** | [Buy from Heltec](https://heltec.org/project/ht-h7608/) | [Amazon](https://www.amazon.com/dp/B0F2HT6ZFX)

The Heltec HT-H7608 is an all-in-one Wi-Fi HaLow router that handles everything:

### Specifications

| Spec | Value |
|------|-------|
| Wi-Fi HaLow | 802.11ah @ 902-928 MHz |
| Wi-Fi Standard | 802.11b/g/n @ 2.4 GHz |
| Range | Up to 1 km (line of sight) |
| Speed | 32 Mbps (close) / 150 kbps (max range) |
| Operating Temp | **-20°C to 70°C** (-4°F to 158°F) |
| Power | 5V/1-2A via USB-C |
| Size | 109 x 66 x 30.5 mm |
| Weight | ~102g |

### Key Features

- ✅ **Built-in 802.11s mesh support** - No custom firmware needed
- ✅ **Survives cold weather** - Works down to -4°F
- ✅ **Dual-band** - HaLow for mesh backhaul, 2.4GHz for customer devices
- ✅ **OpenWrt-based** - Full SSH access, customizable
- ✅ **Wall-mount design** - Easy indoor installation
- ✅ **OTA updates** - Update firmware via web UI

### What's In The Box

- HT-H7608 router
- Suction cup rubber rod antenna
- 5V 1A power adapter

---

## Why Heltec Over GL.iNet HaLowLink 2?

| Feature | Heltec HT-H7608 | GL.iNet HaLowLink 2 |
|---------|-----------------|---------------------|
| **Price** | **$79** | $130 |
| **Mesh Mode** | **Built into Web UI** | SSH only (maybe) |
| **Operating Temp** | **-20°C to 70°C** | 0°C to 40°C |
| **Documentation** | **Full mesh docs** | Sparse |
| **Amazon** | **Yes** | No |

**Heltec wins on price, features, and cold weather operation.**

---

## Hardware Requirements Per Deployment

### Gateway (Mesh Gate)

| Item | Cost | Notes |
|------|------|-------|
| Heltec HT-H7608 | $79 | The gateway device |
| Ethernet cable | $5 | To connect to host's router |
| **Total** | **$84** | |

### Pinger (Mesh Point)

| Item | Cost | Notes |
|------|------|-------|
| Heltec HT-H7608 | $79 | The customer device |
| **Total** | **$79** | Power adapter included |

---

## Full Network Cost Example

**9-customer network (1 gateway + 9 pingers):**

| Item | Qty | Unit Cost | Total |
|------|-----|-----------|-------|
| Heltec HT-H7608 | 10 | $79 | $790 |
| Ethernet cables | 1 | $5 | $5 |
| **Total Hardware** | | | **$795** |

**Break-even:**
- At $5.99/mo net per customer: ~15 months
- At $12.99/mo net per customer: ~7 months

---

## Installation Locations

### Gateway Placement

- **Inside** the host's home (900MHz penetrates walls well)
- Near a window facing customer direction (optional, for best signal)
- Connected via Ethernet to host's router
- Powered via included USB-C adapter

### Pinger Placement

- **Inside** customer's home
- Near a window facing gateway or nearest pinger
- Just needs power outlet
- Customer connects devices to its 2.4GHz WiFi

---

## Antenna Considerations

The included antenna works well for most deployments. For challenging situations:

| Situation | Solution |
|-----------|----------|
| Trees/obstacles | Higher mounting position |
| Long distance (800m+) | Point device toward peer |
| Multiple directions | Consider adding second gateway |

**Note:** External antenna mods are possible but usually unnecessary for typical 500m deployments.

---

## Power Requirements

| Mode | Power Draw |
|------|------------|
| Idle | ~1W |
| Active | ~1.5W |
| Max | ~2W |

- Standard USB-C phone charger works fine
- UPS recommended for gateways (keeps network up during outages)
- Solar power possible for remote installations

---

## Where To Buy

### United States

| Store | Price | Notes |
|-------|-------|-------|
| [Heltec Direct](https://heltec.org/project/ht-h7608/) | $79 | Ships from China, 5-10 days |
| [Amazon](https://www.amazon.com/dp/B0F2HT6ZFX) | ~$85 | Faster shipping |
| [Newegg](https://www.newegg.com/p/3C6-012R-00HT4) | ~$85 | Alternative |

### Bulk Orders

Contact Heltec directly for bulk pricing: support@heltec.cn

---

## Checklist

### Before First Deployment

```
☐ Purchase test units (minimum 2)
☐ Download flash_tool.py
☐ Install Python + paramiko
☐ Test mesh connection between 2 units
☐ Verify range at target deployment distance
```

### Per Gateway

```
☐ Heltec HT-H7608
☐ Ethernet cable
☐ Host willing to share internet
☐ Host willing to receive $2-4/customer/month
```

### Per Pinger

```
☐ Heltec HT-H7608
☐ Customer signed up + paying
☐ Flash with flash_tool.py
☐ Label with device ID
☐ Ship to customer
```
