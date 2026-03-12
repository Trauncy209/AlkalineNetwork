# Alkaline Network

**The World's First Wi-Fi HaLow ISP — Open-source encrypted mesh internet for rural communities**

Wi-Fi HaLow (802.11ah) was standardized by IEEE in 2017, but consumer hardware only started shipping in 2022-2023. While everyone else is using it for IoT sensors and smart city infrastructure, we're doing something nobody else has done: **building a full ISP on top of it.**

This is the complete software stack to deploy a community mesh network that brings affordable internet to underserved rural areas. Uses long-range 900MHz radio that penetrates walls and trees — customers just put a box in their window. No dish. No roof mount. No frozen antennas.

---

## 🚀 Quick Start — Just Use `start.bat`

**For Windows users, this is all you need:**

```
Double-click start.bat
```

That's it. The GUI handles everything:
- Flash devices
- Manage customers
- View billing
- Configure network settings

**For testing the full system:**

```
Double-click start_tests.bat
```

This runs the complete test suite to verify everything works before deployment.

---

## How It Works

### The Network Architecture

```
                                    YOUR MANAGEMENT PC
                                    ┌─────────────────────────────────────┐
                                    │  start.bat → alkaline_app.py       │
                                    │  ├── Flash Tool (provision devices)│
                                    │  ├── Dashboard (manage customers)  │
                                    │  ├── Billing (Stripe integration)  │
                                    │  └── Settings (network config)     │
                                    └──────────────────┬──────────────────┘
                                                       │ REST API / SSH
                    ┌──────────────────────────────────┴──────────────────────────────────┐
                    │                                                                      │
                    ▼                                                                      ▼
    ┌───────────────────────────────┐                          ┌───────────────────────────────┐
    │      GATEWAY DEVICE           │                          │      GATEWAY DEVICE           │
    │      (Host: John)             │                          │      (Host: Mary)             │
    │                               │                          │                               │
    │  Hardware: Heltec HT-H7608    │                          │  Hardware: Heltec HT-H7608    │
    │  Software: alkaline_device.py │                          │  Software: alkaline_device.py │
    │  Mode: --gateway              │                          │  Mode: --gateway              │
    │                               │                          │                               │
    │  ┌─────────────────────────┐  │                          │  ┌─────────────────────────┐  │
    │  │ Connected Pingers:     │  │                          │  │ Connected Pingers:     │  │
    │  │  • Pinger-A (Customer) │  │                          │  │  • Pinger-D (Customer) │  │
    │  │  • Pinger-B (Customer) │  │                          │  │  • Pinger-E (Customer) │  │
    │  │  • Pinger-C (Customer) │  │                          │  └─────────────────────────┘  │
    │  └─────────────────────────┘  │                          │                               │
    │            │                  │                          │            │                  │
    │     Ethernet to Router        │                          │     Ethernet to Router        │
    │            ▼                  │                          │            ▼                  │
    │   [Host's Internet]           │                          │   [Host's Internet]           │
    │   (Starlink/Cable/DSL)        │                          │   (Starlink/Cable/DSL)        │
    └───────────────────────────────┘                          └───────────────────────────────┘
                    ▲                                                          ▲
                    │ 900 MHz HaLow                                            │ 900 MHz HaLow
                    │ (WPA3 + NaCl encrypted)                                  │ (WPA3 + NaCl encrypted)
                    │ ~500m through walls                                      │ ~500m through walls
                    │ ~1-3 miles line-of-sight                                 │ ~1-3 miles line-of-sight
                    │                                                          │
    ┌───────────────┴───────────────┐                          ┌───────────────┴───────────────┐
    │                               │                          │                               │
    ▼               ▼               ▼                          ▼               ▼               │
┌─────────┐   ┌─────────┐   ┌─────────┐                  ┌─────────┐   ┌─────────┐            │
│PINGER-A │   │PINGER-B │   │PINGER-C │                  │PINGER-D │   │PINGER-E │            │
│Customer │   │Customer │   │Customer │                  │Customer │   │Customer │            │
│         │   │         │   │         │                  │         │   │         │            │
│ Creates │   │ Creates │   │ Creates │                  │ Creates │   │ Creates │            │
│ 2.4GHz  │   │ 2.4GHz  │   │ 2.4GHz  │                  │ 2.4GHz  │   │ 2.4GHz  │            │
│ WiFi    │   │ WiFi    │   │ WiFi    │                  │ WiFi    │   │ WiFi    │            │
└────┬────┘   └────┬────┘   └────┬────┘                  └────┬────┘   └────┬────┘            │
     │             │             │                            │             │                 │
     ▼             ▼             ▼                            ▼             ▼                 │
  [Phone]       [Laptop]      [Smart TV]                   [Phone]       [Laptop]            │
  [Tablet]      [Desktop]     [Console]                    [Tablet]      [Desktop]           │
```

### Data Flow — Step by Step

**When a customer opens YouTube:**

1. **Customer's phone** connects to Pinger's 2.4GHz WiFi (standard WPA2)
2. **Pinger** captures the traffic, encrypts it with NaCl (X25519 + XSalsa20-Poly1305)
3. **Pinger** sends encrypted packet over 900MHz HaLow to Gateway (WPA3-SAE encrypted at radio level)
4. **Gateway** receives packet, decrypts NaCl layer, forwards to internet via host's router
5. **Response** comes back, Gateway encrypts with NaCl, sends back over HaLow
6. **Pinger** decrypts, forwards to customer's phone
7. **Customer watches video** — total latency: 20-50ms direct, 50-100ms through mesh relays

**What the Gateway host sees:** Encrypted blobs. They cannot decrypt customer traffic.

**What we see:** Nothing. Traffic doesn't touch our servers. Billing and management only.

---

## The Technology: Wi-Fi HaLow (802.11ah)

### Why HaLow?

| Spec | HaLow (802.11ah) | Regular WiFi (802.11ac/ax) |
|------|------------------|---------------------------|
| Frequency | **900 MHz** | 2.4 / 5 / 6 GHz |
| Range (outdoor) | **1-3 miles** | 300 feet |
| Through trees | **Excellent** | Poor |
| Through walls | **Excellent** | Degrades fast |
| Speed | 2-32 Mbps | 100-1000+ Mbps |
| Power consumption | **Very low** | Higher |
| License required | **No (ISM band)** | No |

**The tradeoff:** Lower speed for massively better range and penetration. For rural internet where the alternative is nothing, this is the right tradeoff.

### The Hardware

**Heltec HT-H7608 Wi-Fi HaLow Router** — $79/unit

- **Chip:** Morse Micro MM8108 (purpose-built for HaLow)
- **Frequency:** 902-928 MHz (US ISM band, no license needed)
- **Max TX Power:** 30 dBm (1 Watt)
- **Mesh:** Built-in 802.11s mesh support
- **Customer WiFi:** 2.4GHz 802.11n for local devices
- **OS:** OpenWrt (fully customizable)
- **Ports:** Ethernet, USB, GPIO

**Buy here:** [heltec.org/project/ht-h7608](https://heltec.org/project/ht-h7608/)

### About the Technology Timeline

- **2017:** IEEE 802.11ah (HaLow) standard finalized
- **2020-2021:** First silicon (Morse Micro, Newracom)
- **2022-2023:** Consumer hardware starts shipping
- **2024-2025:** Rapid adoption for IoT, smart cities, industrial
- **NOW:** We're using it for something nobody else has — affordable rural ISP

By joining now, you're part of the first wave. As adoption grows and chips improve, speeds will increase. The infrastructure you build today will only get better.

---

## Security — Three Layers of Encryption

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Layer 3: TLS/HTTPS (Website Layer)                                              │
│ ┌─────────────────────────────────────────────────────────────────────────────┐ │
│ │ Layer 2: NaCl Tunnel (X25519 + XSalsa20-Poly1305)                          │ │
│ │ ┌─────────────────────────────────────────────────────────────────────────┐ │ │
│ │ │ Layer 1: WPA3-SAE (Radio Link)                                          │ │ │
│ │ │                                                                         │ │ │
│ │ │                    [Your actual data is here]                           │ │ │
│ │ │                                                                         │ │ │
│ │ └─────────────────────────────────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: WPA3-SAE (Radio Level)
- Built into the HaLow hardware
- Automatic, no configuration needed
- Protects the radio link between devices

### Layer 2: NaCl/libsodium (Our Tunnel)
- **X25519:** Elliptic curve key exchange (same as Signal, WhatsApp)
- **XSalsa20:** Stream cipher for encryption
- **Poly1305:** Authentication to detect tampering
- Pinger encrypts → Gateway decrypts → Gateway cannot read what's inside if it's HTTPS

### Layer 3: TLS/HTTPS (Website Level)
- Customer's browser encrypts to the destination website
- We can't see inside this layer
- Gateway host can't see inside this layer
- Nobody can see inside this layer except the customer and the website

**Result:** Gateway hosts literally cannot snoop on customer traffic. They see encrypted blobs in, encrypted blobs out.

---

## Adaptive Bandwidth

The network automatically adjusts based on signal strength:

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
- **Hysteresis:** Requires 5dB margin above threshold to upgrade (prevents oscillation)

---

## Expected Performance

| Hops | Speed | Latency | Good For |
|------|-------|---------|----------|
| 1 hop | 8-15 Mbps | 20-50ms | HD streaming, video calls, gaming |
| 2 hops | 4-7 Mbps | 50-80ms | SD streaming, video calls |
| 3 hops | 2-4 Mbps | 80-100ms | Browsing, email, light video |

### What 2-5 Mbps Actually Handles

| Activity | Bandwidth Needed | Works? |
|----------|------------------|--------|
| Online gaming (Fortnite, Minecraft, CoD) | 100-500 Kbps | ✅ Perfect |
| Zoom / Google Meet / Discord | 1.5 Mbps | ✅ Perfect |
| YouTube 720p | 2.5 Mbps | ✅ Works great |
| Netflix HD (720p) | 3 Mbps | ✅ Usually fine |
| YouTube 1080p | 5 Mbps | ⚠️ Works on good signal |
| 4K streaming | 25 Mbps | ❌ Not enough |
| Large downloads | Any speed | ⏳ Just takes longer |

**We're not competing with fiber.** We're bringing working internet to places that have none, at a price people can actually afford.

---

## File Structure

```
AlkalineNetwork/
│
├── start.bat                 # ⭐ WINDOWS USERS: DOUBLE-CLICK THIS
├── start_tests.bat           # ⭐ Run full test suite before deployment
│
├── alkaline_app.py           # Main GUI application (start.bat launches this)
│   ├── Flash Tab             # Provision gateway and pinger devices
│   ├── Dashboard Tab         # View connected devices, customers
│   ├── Billing Tab           # Stripe integration, payouts
│   └── Settings Tab          # Network configuration
│
├── alkaline_device.py        # Runs ON the Heltec devices
│   ├── GatewayDevice class   # Gateway mode: shares internet
│   ├── PingerDevice class    # Pinger mode: connects to gateway
│   ├── DeviceEncryption      # NaCl encryption
│   └── AlkalineCompression   # zlib compression for packet headers
│
├── alkaline_dashboard.py     # Web dashboard (alternative to GUI)
├── alkaline_billing.py       # Stripe payment processing
├── adaptive_bandwidth.py     # Signal-based bandwidth adjustment
├── flash_tool.py             # Standalone device flashing tool
├── provisioning.py           # Device setup automation
├── test_full_system.py       # Complete test suite
│
├── network_config.json       # Network configuration (auto-generated)
├── requirements.txt          # Python dependencies
│
├── scripts/
│   ├── alkaline_boot.sh      # Auto-start on device boot (Linux/OpenWrt)
│   ├── setup_gateway.sh      # Gateway device setup script
│   └── setup_customer.sh     # Customer device setup script
│
└── docs/
    ├── ARCHITECTURE.md       # Technical architecture details
    ├── HOW_IT_ALL_WORKS.md   # Complete system walkthrough
    ├── HOW_IT_WORKS.md       # Simplified explanation
    ├── HARDWARE.md           # Hardware specifications
    ├── ONE-CLICK-FLOW.md     # User experience flow
    ├── PAYMENT_SETUP.md      # Stripe configuration guide
    ├── CODE_AUDIT.md         # Security audit notes
    └── CONTRIBUTING.md       # Contribution guidelines
```

---

## Getting Started

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

Or manually:
```bash
pip install requests paramiko pynacl
```

### Step 2: Run the Application

**Windows:**
```
Double-click start.bat
```

**Linux/Mac:**
```bash
python alkaline_app.py
```

### Step 3: Configure Your Network

1. Go to the **Settings** tab
2. Set your mesh passphrase (32+ characters recommended)
3. Set your customer WiFi password
4. Save settings

### Step 4: Flash Your First Gateway

1. Go to the **Flash Devices** tab
2. Connect a Heltec HT-H7608 via Ethernet
3. Click **GATEWAY**
4. Wait for flash to complete
5. Unplug and deploy at host location

### Step 5: Flash Customer Pingers

1. Same process, but click **PINGER**
2. The pinger will auto-discover the gateway
3. Deploy at customer location

### Step 6: Test Everything

```
Double-click start_tests.bat
```

This runs `test_full_system.py` which verifies:
- Device communication
- Encryption/decryption
- Billing integration
- Dashboard functionality

---

## Pricing Model

| Role | Pays | Earns |
|------|------|-------|
| Customer (with deposit) | $7.99/mo + $100 refundable deposit | — |
| Customer (no deposit) | $14.99/mo | — |
| Gateway Host | $0 | $2/customer/month |

**Gateway hosts** provide the internet and earn passive income. Each gateway supports up to 9 customers = $18/month.

**Customers** get affordable internet at 1/15th the price of Starlink.

---

## Contributing

Pull requests welcome. The code is straightforward Python with minimal dependencies.

**Before submitting:**
1. Run `start_tests.bat` to verify nothing breaks
2. Test on actual hardware if possible
3. Update documentation if needed

---

## License

MIT License — Use it however you want. Build your own ISP. Fork it. Modify it. Sell it. We don't care. Just bring internet to people who need it.

---

## The Mission

Big telecom looked at rural America and said "not profitable."

Starlink said "here's internet for $120/month + $499 equipment."

We said "what if neighbors just shared?"

**Alkaline Network** is proof that community-owned infrastructure works. One person with Starlink can provide internet to 9 neighbors at $8/month each. Everyone wins.

This is the future of rural connectivity. And it's open source.

---

**Built for communities that got left behind by big telecom.**

**AlkalineTech** | [alkalinehosting.com](https://alkalinehosting.com)
