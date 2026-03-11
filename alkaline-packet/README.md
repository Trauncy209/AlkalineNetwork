# Alkaline Packet

## Ultra Long-Range Encrypted Communication

**Speed:** 0.3-50 kbps  
**Range:** 100+ miles  
**Best For:** Extremely rural, off-grid, emergency, preppers

---

## What Is This?

Alkaline Packet provides encrypted internet access over extreme distances using LoRa radio.

```
YOUR CABIN (middle of nowhere)
        ↓ radio (100+ miles)
GATEWAY (town 80 miles away)
        ↓ their internet
EMAIL, BASIC WEB, MESSAGING
```

**Slow, but works where nothing else does.**

---

## Realistic Expectations

### What Works

| Use Case | Works? | Notes |
|----------|--------|-------|
| Text messaging | ✅ Great | Instant |
| Email | ✅ Great | With attachments |
| Basic web browsing | ⚠️ Slow | Text-heavy sites OK |
| Wikipedia | ⚠️ Slow | Articles load in ~1 min |
| Images | ⚠️ Very slow | Small images only |
| Video | ❌ No | Impossible |
| Video calls | ❌ No | Impossible |
| Gaming | ❌ No | Too much latency |
| Large downloads | ❌ No | Would take days |

### Speed Reality

| Data Size | Time To Transfer |
|-----------|------------------|
| 1 KB (short email) | 3 seconds |
| 10 KB (long email) | 30 seconds |
| 100 KB (small webpage) | 5 minutes |
| 1 MB (large page) | 50 minutes |

**This is emergency/basic communication, not Netflix.**

---

## Why Use This?

### When Alkaline Mesh Won't Work

- No line of sight (mountains, forests)
- Distance over 30 miles to nearest gateway
- Extreme isolation

### Use Cases

1. **Off-grid cabins** - Basic email and web
2. **Emergency backup** - When all else fails
3. **Preppers** - Communication when grid is down
4. **Maritime** - Boats at sea
5. **Expeditions** - Remote wilderness

---

## How It Works

### The Radio

LoRa (Long Range) radio modules:
- Frequency: 915 MHz (US) / 868 MHz (EU)
- Range: 100+ miles with good antennas
- Encryption: Legal on these frequencies
- License: Not required

### The Network

```
Your Node
    ↓ LoRa radio
Relay Node (optional)
    ↓ LoRa radio
Gateway Node
    ↓ Internet
Websites
```

### Why So Far?

LoRa uses special modulation (chirp spread spectrum) that trades speed for range. The signal can travel over mountains and through forests where WiFi dies instantly.

---

## Hardware Setup

### Shopping List

| Item | Cost | Notes |
|------|------|-------|
| Raspberry Pi 4 | $55 | Or Pi Zero 2 W ($15) for basic |
| LoRa Module (SX1276) | $15 | 915 MHz for US |
| Antenna (Yagi or collinear) | $30-50 | Bigger = more range |
| Coax cable | $15 | Low-loss LMR-400 recommended |
| Weatherproof enclosure | $20 | For outdoor mounting |
| Solar panel (optional) | $50 | For true off-grid |
| Battery (optional) | $40 | For solar setup |
| **Total** | **$85-245** | |

### Recommended LoRa Modules

| Module | Price | Range | Link |
|--------|-------|-------|------|
| RAK4631 | $30 | 100+ miles | [RAKwireless](https://store.rakwireless.com/) |
| LILYGO T-Beam | $25 | 50+ miles | [Amazon](https://www.amazon.com/) |
| Heltec LoRa 32 | $18 | 30+ miles | [Amazon](https://www.amazon.com/) |

### Wiring

```
[Raspberry Pi]
      ↓ SPI connection
[LoRa Module]
      ↓ Coax
[Antenna on roof/pole]
```

---

## Installation

### Step 1: Flash The Image

Download: [alkaline-packet-v1.0.img](releases)

Flash to SD card using [Balena Etcher](https://www.balena.io/etcher/).

### Step 2: Wire The LoRa Module

Connect to Pi GPIO:

| LoRa Pin | Pi Pin |
|----------|--------|
| VCC | 3.3V |
| GND | GND |
| MISO | GPIO 9 |
| MOSI | GPIO 10 |
| SCK | GPIO 11 |
| NSS | GPIO 8 |
| DIO0 | GPIO 4 |
| RST | GPIO 17 |

### Step 3: Connect Antenna

**NEVER power on without antenna connected.** You'll fry the module.

### Step 4: Boot

1. Insert SD card
2. Power on
3. Wait for "Alkaline Network" WiFi to appear
4. Connect and browse

---

## Running A Gateway

### Requirements

- Internet connection (any type)
- LoRa node (same hardware)
- High antenna placement (roof, tower)

### Setup

1. Same hardware setup as above
2. Connect Pi to router via Ethernet
3. SSH: `ssh alkaline@alkaline.local`
4. Run: `alkaline-config --mode gateway`
5. Reboot

### Antenna Placement

Higher = Better. Seriously.

| Height | Approximate Range |
|--------|-------------------|
| Ground level | 5-10 miles |
| Roof (1 story) | 20-30 miles |
| Roof (2 story) | 30-50 miles |
| Tower (50 ft) | 50-100 miles |
| Mountain top | 100+ miles |

---

## Range Planning

### Line of Sight

LoRa works best with line of sight, but CAN penetrate obstacles:

| Obstacle | Effect |
|----------|--------|
| Trees | Reduce range 50% |
| Hills | May block completely |
| Buildings | Reduce range 70% |
| Rain | Minimal effect |

### Relay Nodes

Can't reach a gateway directly? Add relays:

```
Your Cabin
    ↓ 40 miles
Relay on hilltop
    ↓ 60 miles
Gateway in town
```

Each relay adds latency but extends range.

---

## Optimizations

### Compression

All traffic is compressed before transmission:

| Data Type | Compression |
|-----------|-------------|
| Text/HTML | 90%+ reduction |
| JSON | 85% reduction |
| Images | 20-50% reduction |
| Already compressed | 0% reduction |

### Caching

Frequently accessed content is cached locally:
- DNS responses
- Common web assets
- Previous page loads

### Protocol Optimization

- HTTP headers stripped and rebuilt
- Connection pooling
- Request batching

---

## Power Options

### Grid Power

Standard 5V USB. Done.

### Solar Off-Grid

| Component | Size | Cost |
|-----------|------|------|
| Solar panel | 20W | $30 |
| Charge controller | 10A | $15 |
| Battery (LiFePO4) | 20Ah | $60 |
| **Total** | | **$105** |

Runs indefinitely with sun.

### Power Consumption

| Mode | Power |
|------|-------|
| Idle (listening) | 0.5W |
| Receiving | 1W |
| Transmitting | 3W |
| Average | 1W |

A 20Ah battery lasts ~4 days without sun.

---

## Emergency Use

### When Everything Else Is Down

- Cell towers: Down
- Internet: Down
- Power grid: Down
- Alkaline Packet: **Still working** (solar powered)

### Emergency Features

- Broadcast emergency messages
- Automatic relay of emergency traffic
- Priority routing for SOS
- Store-and-forward messaging

---

## Technical Details

### Radio Specs

| Parameter | Value |
|-----------|-------|
| Frequency | 915 MHz (US) |
| Bandwidth | 125/250/500 kHz |
| Spreading Factor | SF7-SF12 |
| TX Power | Up to 30 dBm |
| Sensitivity | -137 dBm |

### Protocol Stack

```
Application
    ↓
Alkaline Protocol (compression + encryption)
    ↓
LoRa MAC
    ↓
LoRa PHY
    ↓
Radio
```

### Encryption

Same as Alkaline Mesh:
- NaCl/libsodium
- Curve25519 key exchange
- XSalsa20 stream cipher
- Poly1305 authentication

---

## Legal

### United States

- 915 MHz is ISM band (unlicensed)
- Encryption: Legal
- Power limit: 1W (30 dBm)
- No license required

### Europe

- 868 MHz band
- Similar rules, lower power limit

### Other Regions

Check local regulations for ISM bands.

---

## Troubleshooting

### No Signal

- Check antenna connection
- Verify frequency settings
- Try higher antenna placement
- Check for local interference

### Very Slow

- Normal for LoRa
- Try higher spreading factor for more range
- Reduce distance to gateway

### Intermittent Connection

- Marginal signal - need better antenna
- Interference from other LoRa devices
- Weather effects (rare)

---

## Specifications Summary

| Spec | Value |
|------|-------|
| Frequency | 915 MHz (US) / 868 MHz (EU) |
| Range | 100+ miles |
| Throughput | 0.3-50 kbps |
| Latency | 500ms - 5s |
| Encryption | NaCl |
| Power | 1W average |
| License | None required |

---

## License

MIT License.

---

## Links

- Main Project: [alkaline-network](https://github.com/AlkalineTech/alkaline-network)
- Fast Version: [alkaline-mesh](https://github.com/AlkalineTech/alkaline-mesh)
- Core Library: [alkaline-core](https://github.com/AlkalineTech/alkaline-core)
