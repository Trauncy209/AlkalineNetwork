# Alkaline Network - Production Guide

## The Easy Way: Flash Tool

**Just run `flash_tool.py` - it has two buttons:**

```bash
python flash_tool.py
```

Or on Windows, double-click `start_flash_tool.bat`

### Button 1: GATEWAY
- Connect Heltec via Ethernet cable
- Click "🌐 GATEWAY"
- Wait ~1 minute
- Done - device is configured as Mesh Gate
- Deploy at a location WITH internet

### Button 2: PINGER
- Connect Heltec via Ethernet cable
- Click "📡 PINGER"
- Wait ~1 minute
- Unplug and ship to customer!

The tool automatically:
- Connects via SSH (default: root / heltec.org)
- Configures 802.11s mesh mode
- Sets mesh ID and encryption
- Configures customer WiFi (Alkaline-XX-XXX)
- Changes admin password
- Tracks all provisioned devices
- Saves config to `network_config.json`

---

## Requirements

```bash
pip install paramiko
```

That's it. Python 3.8+ required.

---

## Hardware

### What To Buy

| Item | Qty | Cost | Where |
|------|-----|------|-------|
| Heltec HT-H7608 | 2+ | $79 each | [Heltec](https://heltec.org/project/ht-h7608/) or [Amazon](https://www.amazon.com/dp/B0F2HT6ZFX) |
| Ethernet cable | 1 | ~$5 | Amazon |

**Start with 2 units:**
- 1 for gateway (your test location with internet)
- 1 for pinger (to test mesh connection)

### Heltec HT-H7608 Specs

- Wi-Fi HaLow: 802.11ah @ 902-928 MHz
- 2.4GHz WiFi for customer devices
- Built-in 802.11s mesh support
- Range: ~1km line of sight, ~500m through obstacles
- Operating temp: -20°C to 70°C
- Power: 5V USB-C (included)

---

## Network Architecture

```
[Host's Internet]
       |
   [GATEWAY]  ← Mesh Gate, has internet
       |
   HaLow Mesh (900MHz)
       |
   [PINGER 1] ← Mesh Point, customer #1
       |
   HaLow Mesh
       |
   [PINGER 2] ← Mesh Point, customer #2 (relay through #1)
       |
   HaLow Mesh
       |
   [PINGER 3] ← Mesh Point, customer #3 (max 3 hops)
```

### Limits

- **3 pingers max per gateway** (direct connections)
- **3 hops max** (deeper = too slow)
- **9 customers max per gateway** (3 chains × 3 hops)
- **~4.5 sq miles coverage** per gateway

### Expected Speeds

| Position | Speed | Works For |
|----------|-------|-----------|
| 1 hop | 8-15 Mbps | HD streaming |
| 2 hops | 4-7 Mbps | SD streaming, Zoom |
| 3 hops | 2-4 Mbps | Browsing, email |

---

## Step-by-Step Deployment

### Step 1: Provision Gateway

1. Plug Heltec into your PC via Ethernet
2. Run `python flash_tool.py`
3. Click **GATEWAY** button
4. Wait for completion message
5. Unplug device

### Step 2: Deploy Gateway

1. Take gateway to host location (someone with Starlink/AT&T/etc)
2. Connect gateway's Ethernet port to host's router
3. Plug in power
4. Gateway starts broadcasting mesh automatically

### Step 3: Provision Pingers

1. For each customer, plug fresh Heltec into your PC
2. Click **PINGER** button
3. Wait for completion
4. Label device with ID shown (e.g., PN-001)
5. Repeat for each customer

### Step 4: Ship to Customers

Customer receives device and:
1. Plugs in power (USB-C adapter included)
2. Connects phone/laptop to "Alkaline-PN-XXX" WiFi
3. Uses password shown during provisioning
4. Done!

---

## Pricing Reminder

### Customer Options

| Plan | Monthly | Upfront | Equipment |
|------|---------|---------|-----------|
| Deposit | $7.99 | $100 refundable | Return on cancel |
| Included | $14.99 | $0 | Keep after 12 months |

### Your Revenue Per Customer

| Plan | Customer Pays | Gateway Host | You Keep |
|------|---------------|--------------|----------|
| Deposit | $7.99 | $2.00 | $5.99 |
| Included | $14.99 | $2.00 | $12.99 |

### Break-Even

- **Hardware cost:** $79 per Heltec
- **Deposit plan:** ~13 months
- **Included plan:** ~6 months

---

## Configuration Files

### network_config.json

Auto-generated on first run:

```json
{
  "mesh_id": "AlkalineNet",
  "mesh_passphrase": "auto-generated-32-char-hex",
  "admin_password": "auto-generated",
  "customer_wifi_password": "auto-generated",
  "gateway_count": 1,
  "pinger_count": 5,
  "devices": [
    {"type": "gateway", "id": "GW-001", "mac": "AA:BB:CC:DD:EE:FF"},
    {"type": "pinger", "id": "PN-001", "mac": "11:22:33:44:55:66"}
  ]
}
```

**Keep this file safe!** It contains your network's encryption keys.

### On Each Device

After provisioning, each device has:

```
/etc/alkaline_device_id   # e.g., "GW-001" or "PN-003"
/etc/alkaline_mode        # "gateway" or "pinger"
```

---

## Troubleshooting

### Can't Connect to Device

1. Make sure Heltec is powered on (red light → yellow/green blinking)
2. Connect via Ethernet, not WiFi
3. Try both IPs: `10.42.0.1` and `192.168.100.1`
4. Default password: `heltec.org`
5. If password was changed: you may need to factory reset

### Factory Reset Heltec

1. Press and hold button with SIM needle for 10 seconds
2. Wait for white light, then release
3. Device resets to defaults

### Pinger Won't Connect to Gateway

1. Check distance (max ~1km with obstacles)
2. Ensure gateway is powered and has mesh enabled
3. Verify same mesh_id and mesh_passphrase
4. Try repositioning pinger near window

### Slow Speeds

- This is normal for 3-hop connections
- HaLow theoretical max: ~32 Mbps close, degrades with distance
- More customers = shared bandwidth
- Solution: Add more gateways to reduce hops

---

## Manual Configuration (Advanced)

If you need to configure without the flash tool:

### SSH Access

```bash
ssh root@10.42.0.1
# Password: heltec.org (or your admin password)
```

### UCI Commands

```bash
# Set mesh mode
uci set wireless.halow.mode="mesh"
uci set wireless.halow.mesh_id="AlkalineNet"
uci set wireless.halow.encryption="sae"
uci set wireless.halow.key="your-passphrase"

# For gateway, enable mesh gate announcements
uci set wireless.halow.mesh_gate_announcements="1"

# Apply
uci commit wireless
wifi reload
```

---

## Legal Checklist

Before launching:
- [ ] Register Michigan LLC (~$50)
- [ ] Get EIN (free, IRS website)
- [ ] Open business bank account
- [ ] Set up Stripe for payments
- [ ] Terms of Service on website
- [ ] Privacy Policy on website

---

## Support

- Website: https://alkalinenetwork.com
