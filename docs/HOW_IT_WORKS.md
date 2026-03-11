# Alkaline Hosting - How It Actually Works

## The Complete Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            YOUR DASHBOARD                                    │
│                        (runs on your PC/server)                             │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  SQLite Database (alkaline.db)                                      │   │
│   │  ├── devices (all modems + gateways)                               │   │
│   │  ├── hosters (people sharing internet)                             │   │
│   │  └── events (activity log)                                         │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                              ▲                                              │
│                              │ REST API                                     │
│                              │                                              │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │
          ┌────────────────────┴────────────────────┐
          │                                         │
          ▼                                         ▼
┌─────────────────────┐                   ┌─────────────────────┐
│   GATEWAY (Pi)      │                   │   GATEWAY (Pi)      │
│   Hoster: John      │                   │   Hoster: Mary      │
│   Location: 123 Oak │                   │   Location: 456 Pine│
│                     │                   │                     │
│   Runs:             │                   │   Runs:             │
│   alkaline_device.py│                   │   alkaline_device.py│
│   --mode gateway    │                   │   --mode gateway    │
│                     │                   │                     │
│   ┌───────────────┐ │                   │   ┌───────────────┐ │
│   │Connected:     │ │                   │   │Connected:     │ │
│   │ • Modem-A     │ │                   │   │ • Modem-D     │ │
│   │ • Modem-B     │ │                   │   │ • Modem-E     │ │
│   │ • Modem-C     │ │                   │   └───────────────┘ │
│   └───────────────┘ │                   │                     │
└──────────┬──────────┘                   └──────────┬──────────┘
           │ WiFi/Radio                              │ WiFi/Radio
     ┌─────┴─────┬─────────┐                   ┌─────┴─────┐
     ▼           ▼         ▼                   ▼           ▼
┌─────────┐ ┌─────────┐ ┌─────────┐     ┌─────────┐ ┌─────────┐
│MODEM-A  │ │MODEM-B  │ │MODEM-C  │     │MODEM-D  │ │MODEM-E  │
│Customer │ │Customer │ │Customer │     │Customer │ │Customer │
│$14.99/mo│ │$7.99/mo │ │$24.99/mo│     │$14.99/mo│ │$14.99/mo│
│         │ │         │ │         │     │         │ │         │
│Runs:    │ │Runs:    │ │Runs:    │     │Runs:    │ │Runs:    │
│alkaline_│ │alkaline_│ │alkaline_│     │alkaline_│ │alkaline_│
│device.py│ │device.py│ │device.py│     │device.py│ │device.py│
│--mode   │ │--mode   │ │--mode   │     │--mode   │ │--mode   │
│modem    │ │modem    │ │modem    │     │modem    │ │modem    │
└─────────┘ └─────────┘ └─────────┘     └─────────┘ └─────────┘
     │           │           │               │           │
     └───────────┴───────────┴───────────────┴───────────┘
                            │
                    Customer's devices
                    (phones, laptops, etc)
                    connect to MODEM's WiFi
```

---

## Step-by-Step: What Happens When

### 1. GATEWAY BOOTS UP

```python
# On Raspberry Pi running as GATEWAY:
python alkaline_device.py gateway --dashboard http://your-server:5000

# What happens:
1. Reads /etc/alkaline/device.json for identity (or creates it)
2. Gets unique device_id like "ALK-A1B2-C3D4"
3. POSTs to dashboard: /api/device/register
   {
     "device_id": "ALK-A1B2-C3D4",
     "mac_address": "AA:BB:CC:DD:EE:FF",
     "device_type": "gateway",
     "hostname": "gateway-john"
   }
4. Dashboard saves to SQLite, broadcasts SSE event
5. Gateway appears in your dashboard UI
6. Starts listening on UDP port 5555 for modem announcements
```

### 2. MODEM BOOTS UP

```python
# On Raspberry Pi running as MODEM:
python alkaline_device.py modem --dashboard http://your-server:5000

# What happens:
1. Reads /etc/alkaline/device.json for identity (or creates it)  
2. Gets unique device_id like "ALK-X7Y8-Z9W0"
3. POSTs to dashboard: /api/device/register
   {
     "device_id": "ALK-X7Y8-Z9W0",
     "mac_address": "11:22:33:44:55:66",
     "device_type": "modem",
     "hostname": "modem-customer1"
   }
4. Dashboard saves to SQLite, broadcasts SSE event
5. Modem appears in your dashboard UI (status: searching)
6. Broadcasts UDP announcement on port 5555: "I exist!"
```

### 3. GATEWAY SEES MODEM

```python
# Gateway receives UDP broadcast from Modem

# What happens:
1. Gateway's listener thread receives announcement
2. Gateway sends UDP response: "Welcome, connect to me"
3. Gateway POSTs to dashboard: /api/gateway/modem_connected
   {
     "gateway_id": "ALK-A1B2-C3D4",
     "modem_id": "ALK-X7Y8-Z9W0", 
     "modem_mac": "11:22:33:44:55:66"
   }
4. Dashboard updates modem's hoster_id field
5. Dashboard broadcasts SSE event
6. Your dashboard shows modem now "connected" to gateway
7. Modem's traffic now routes through gateway
```

### 4. CUSTOMER CONNECTS PHONE TO MODEM

```
Customer's phone → connects to Modem's WiFi → Modem routes to Gateway → Gateway routes to Internet
                                                                              ↓
                                                          Traffic stats reported to Dashboard
```

### 5. HEARTBEATS KEEP EVERYTHING UPDATED

```python
# Every 30 seconds, ALL devices send heartbeat:

# Modem sends:
POST /api/device/heartbeat
{
  "device_id": "ALK-X7Y8-Z9W0",
  "bytes_down": 52428800,  # 50 MB downloaded
  "bytes_up": 10485760,    # 10 MB uploaded
  "uptime": 3600           # 1 hour online
}

# Gateway sends:
POST /api/device/heartbeat  
{
  "device_id": "ALK-A1B2-C3D4",
  "bytes_down": 157286400,  # 150 MB (sum of all modems)
  "bytes_up": 31457280,     # 30 MB
  "connected_modems": 3,    # Currently has 3 modems
  "uptime": 86400           # 24 hours online
}

# Dashboard:
- Updates stats in database
- Marks devices as "online" 
- Can send commands back (reboot, update config, etc)
```

---

## The Actual Files

| File | Runs On | Purpose |
|------|---------|---------|
| `alkaline_device.py` | Both Modem & Gateway | Main software - handles registration, heartbeats, modem↔gateway protocol |
| `app.py` | Your Server | Dashboard - web UI, REST API, SQLite database |
| `qos.py` | Gateway | Bandwidth limiting (25/50/100 Mbps per customer tier) |
| `billing.py` | Your Server | Stripe integration, hoster payouts |
| `encryption.py` | Both | AES-256 encryption so Hosters can't see customer traffic |

---

## What You See in Dashboard

### Devices Tab:
```
📱 ALK-X7Y8-Z9W0  │  11:22:33:44:55:66  │  PLUS  │  John's Gateway  │  🟢 Online
📱 ALK-P4Q5-R6S7  │  AA:BB:CC:DD:EE:FF  │  BASIC │  John's Gateway  │  🟢 Online  
📱 ALK-M1N2-O3P4  │  12:34:56:78:9A:BC  │  PRO   │  Mary's Gateway  │  🟡 Offline
📡 ALK-A1B2-C3D4  │  FF:EE:DD:CC:BB:AA  │  -     │  (Gateway)       │  🟢 Online
```

### Hosters Tab:
```
🏠 John's Gateway  │  3 customers  │  $6.00/mo earnings
🏠 Mary's Gateway  │  1 customer   │  $2.00/mo earnings
```

### Stats:
```
📡 Devices Online: 4
🏠 Active Hosters: 2  
💰 Monthly Revenue: $62.96
💸 Hoster Payouts: $8.00
📊 Net Revenue: $54.96
```

---

## To Run This Right Now

### 1. Start Dashboard (on your PC):
```bash
cd AlkalineNetwork-Final/alkaline-dashboard
pip install flask flask-cors
python app.py
# Open http://localhost:5000
```

### 2. Start Gateway (on Raspberry Pi #1):
```bash
cd AlkalineNetwork-Final/alkaline-core/src
pip install requests
python alkaline_device.py gateway --dashboard http://YOUR_PC_IP:5000
```

### 3. Start Modem (on Raspberry Pi #2):
```bash
cd AlkalineNetwork-Final/alkaline-core/src
pip install requests
python alkaline_device.py modem --dashboard http://YOUR_PC_IP:5000
```

### 4. Watch the magic:
- Gateway appears in dashboard
- Modem appears in dashboard
- Modem connects to gateway (via UDP broadcast)
- Dashboard shows modem linked to gateway
- Stats update every 30 seconds

---

## What's Actually Routing Traffic?

The `alkaline_device.py` handles discovery and registration. For actual internet sharing:

**Gateway needs:**
- `hostapd` - Creates WiFi access point for modems to connect
- `dnsmasq` - DHCP server assigns IPs to modems
- `iptables` - NAT/masquerading shares internet connection
- Our software reports all this to dashboard

**Modem needs:**
- `hostapd` - Creates WiFi access point for customer devices
- `dnsmasq` - DHCP for customer devices
- `wpa_supplicant` - Connects to Gateway's WiFi
- Our software reports stats to dashboard

The actual traffic flows through standard Linux networking. Our software just:
1. Registers devices
2. Reports stats
3. Enables you to control everything from dashboard
