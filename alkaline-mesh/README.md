# Alkaline Mesh

## Fast, Encrypted Community Internet

**Speed:** 10-100 Mbps  
**Range:** 5-30 miles  
**Best For:** Towns, suburbs, neighborhoods

---

## What Is This?

Alkaline Mesh creates high-speed encrypted internet for communities using modified WiFi hardware.

```
YOUR HOUSE ←──radio──→ NEIGHBOR ←──radio──→ GATEWAY ←──internet──→ GOOGLE
   5 miles                10 miles              
```

**Fast enough for:** YouTube, Netflix, video calls, gaming, everything.

---

## How It Works

### The Hardware

Standard outdoor WiFi radios, running Alkaline firmware:

| Device | Price | Range | Speed |
|--------|-------|-------|-------|
| TP-Link CPE510 | $40 | 10-15 miles | 50+ Mbps |
| Ubiquiti NanoStation M5 | $80 | 15-30 miles | 100+ Mbps |
| Ubiquiti LiteBeam | $60 | 20+ miles | 100+ Mbps |

### The Network

Nodes automatically form a mesh:

```
      [Gateway]
          ↕
    [Relay Node]
      ↕     ↕
[Node A]   [Node B]
              ↕
          [Node C]
```

Traffic automatically finds the best path to a gateway.

### The Encryption

All traffic encrypted end-to-end:

```
Your device → Encrypted → Relay → Encrypted → Gateway → HTTPS → Internet
                 ↑                    ↑
           Can't read it        Can't read it
```

Uses NaCl/libsodium (same as Signal).

---

## Hardware Setup

### Shopping List

| Item | Cost | Link |
|------|------|------|
| Raspberry Pi 4 (4GB) | $55 | [Link](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) |
| TP-Link CPE510 | $40 | [Amazon](https://www.amazon.com/dp/B00N2RO63U) |
| Ethernet Cable (outdoor) | $15 | Amazon |
| PoE Injector | Included | With CPE510 |
| MicroSD Card (32GB) | $10 | Amazon |
| Weatherproof Box (optional) | $15 | Amazon |
| **Total** | **~$135** | |

### Wiring

```
[Raspberry Pi]
      ↓ Ethernet
[PoE Injector] ← Power outlet
      ↓ Ethernet (outdoor)
[CPE510 on roof/pole]
      ↓ Radio waves
[Other nodes]
```

---

## Installation

### Step 1: Flash The Image

Download: [alkaline-mesh-v1.0.img](releases)

Flash to SD card using [Balena Etcher](https://www.balena.io/etcher/).

### Step 2: Configure The Radio

1. Power on CPE510
2. Connect laptop to it via Ethernet
3. Go to 192.168.0.254 in browser
4. Flash Alkaline firmware (included in download)

### Step 3: Connect Everything

```
Pi ← Ethernet → PoE Injector ← Power
                    ↓
               Ethernet to CPE510
```

### Step 4: Boot It Up

1. Insert SD card into Pi
2. Power on
3. Wait 2 minutes
4. Connect phone to "Alkaline Network" WiFi

**Done.** It will automatically find other nodes and gateways.

---

## Running A Gateway

You have internet and want to share it?

### Same Setup As Above, Plus:

1. Plug Pi into your router via second Ethernet port
2. SSH into Pi: `ssh alkaline@alkaline.local`
3. Run: `alkaline-config --mode gateway`
4. Reboot

**You're now a gateway.** Other nodes will automatically discover you.

---

## Network Planning

### Coverage

Each node covers roughly:

| Antenna Type | Range | Coverage Area |
|--------------|-------|---------------|
| Built-in (CPE510) | 10-15 miles | ~700 sq miles |
| External dish | 20-30 miles | ~2,800 sq miles |

**Line of sight matters.** Mount high for best results.

### Minimum Network

```
[Gateway] ←── 15 miles ──→ [Your Node]
```

That's it. Two nodes = working network.

### Growing The Network

```
[Gateway]
    ↕
[Relay 1] ← adds 15 more miles of range
    ↕
[Relay 2] ← adds 15 more miles of range
    ↕
[Your Node] ← 45 miles from gateway!
```

More relays = more range.

---

## Technical Details

### Radio

- Frequency: 5.8 GHz (unlicensed)
- Protocol: 802.11n modified for long range
- Encryption: WPA2 + Alkaline overlay

### Mesh Routing

- Protocol: BATMAN-adv or Babel
- Auto-discovery: mDNS
- Failover: Automatic rerouting

### Encryption Stack

```
Application (your browser)
        ↓
    HTTPS (TLS)
        ↓
  Alkaline Tunnel (NaCl)
        ↓
   Mesh Routing
        ↓
   Radio Link (WPA2)
```

Three layers of encryption.

---

## Bandwidth Sharing

### Fair Usage

Gateway bandwidth is shared among all users. 

Example:
- Gateway has 100 Mbps
- 10 users connected
- Each gets ~10 Mbps average

### Priority System (Optional)

Gateways can prioritize:
1. Emergency traffic
2. Interactive (web, gaming)
3. Streaming
4. Bulk downloads

---

## Troubleshooting

### Can't Find Any Nodes

- Check antenna alignment
- Verify line of sight
- Try mounting higher
- Check frequency settings

### Slow Speeds

- Too many hops to gateway
- Gateway bandwidth saturated
- Interference from other WiFi networks

### Connection Drops

- Weather interference
- Antenna misalignment
- Power issues

---

## Specifications

| Spec | Value |
|------|-------|
| Frequency | 5.8 GHz |
| Bandwidth | 20/40 MHz channels |
| Range | 5-30 miles |
| Throughput | 10-100 Mbps |
| Latency | 5-50ms per hop |
| Encryption | NaCl (Curve25519, XSalsa20, Poly1305) |
| Power | 12V PoE, ~8W |

---

## License

MIT License.

---

## Links

- Main Project: [alkaline-network](https://github.com/AlkalineTech/alkaline-network)
- Long Range Version: [alkaline-packet](https://github.com/AlkalineTech/alkaline-packet)
- Core Library: [alkaline-core](https://github.com/AlkalineTech/alkaline-core)
