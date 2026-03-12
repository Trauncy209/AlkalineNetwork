# Alkaline Network - One-Click Provisioning Flow

## The Dream

```
Customer pays on website
        ↓
Order appears in your flash tool
        ↓
Plug in blank device, click order, click FLASH
        ↓
Device auto-configures, auto-registers
        ↓
Unplug, ship to customer
        ↓
Customer plugs in → internet works
```

## Current Status

✅ Website creates pending orders on signup
✅ Flash tool shows pending orders  
✅ Flash tool provisions devices with one click
✅ Provisioning system registers devices in database
✅ Billing system syncs active customers to tunnel
✅ Tunnel server only allows registered devices

## Setup (One-Time)

### 1. Directory Structure

Put everything in one place on your PC:

```
C:\AlkalineNetwork\
├── flash_tool.py           # Device provisioning GUI
├── provisioning.py         # Order management
├── alkaline_dashboard.py   # Network management web UI
├── alkaline_billing.py     # Stripe + billing
├── alkaline_complete.py    # Tunnel server (runs on VPS)
├── pending_orders.json     # Orders waiting to be fulfilled
├── alkaline.db             # SQLite database
├── clients.json            # Active customers (synced to VPS)
└── network_config.json     # Mesh credentials
```

### 2. Run Your VPS Tunnel Server

On your VPS (Linux):

```bash
# Copy alkaline_complete.py to VPS
scp alkaline_complete.py user@your-vps:/opt/alkaline/

# SSH to VPS and start server
ssh user@your-vps
cd /opt/alkaline
python3 alkaline_complete.py --server

# Note the PUBLIC KEY it prints - you need this for flash tool
```

### 3. Configure Flash Tool

Run `flash_tool.py` and enter:
- **Server IP:** Your VPS IP address
- **Server Public Key:** The key from step 2

These get saved in `network_config.json`.

### 4. Website Orders → Flash Tool

**Option A: Run website locally**
```batch
cd C:\AlkalineNetwork
python server.py
```
Website creates `pending_orders.json` in same directory - flash tool sees it.

**Option B: Production website (separate server)**
Configure website to POST orders to your PC, OR manually download `pending_orders.json` periodically.

## Daily Workflow

### New Customer Signup

1. Customer goes to alkalinehosting.com
2. Fills signup form, pays via Stripe
3. Order appears in `pending_orders.json`

### Fulfilling Orders

1. Double-click `start_production.bat` → Control Panel (flash tool)
2. Pending orders show at top of window
3. Click an order to select it
4. Plug in a blank HT-H7608 router via Ethernet
5. Click **PINGER** button (or GATEWAY for hosts)
6. Wait ~30 seconds for provisioning
7. See "Device ready! Unplug and deploy."
8. Box up device, print shipping label
9. Ship to customer address shown
10. Order automatically marked as "shipped"

### Customer Receives Device

1. Customer plugs in device
2. Device finds nearest gateway via mesh
3. Tunnel connection established
4. Customer connects to "Alkaline-XXXXX" WiFi
5. Internet works!

## What Happens Behind The Scenes

### On Signup

```
Website /api/submit
    ↓
Creates pending_orders.json entry
    ↓
Creates customer in alkaline.db (status: pending_device)
```

### On Flash

```
Flash tool reads pending_orders.json
    ↓
You select order, click PINGER
    ↓
Device gets: mesh credentials, tunnel config, WiFi SSID
    ↓
Device public key saved to database
    ↓
Customer assigned to nearest gateway
    ↓
Order status → "shipped"
```

### On Payment (Monthly)

```
Stripe webhook fires (invoice.paid)
    ↓
alkaline_billing.py updates subscription_status = 'active'
    ↓
sync_clients_json() runs
    ↓
Only active customers written to clients.json
    ↓
Tunnel server reloads clients.json
    ↓
Customer gets internet (or loses it if payment failed)
```

## Testing Without Hardware

You can test the whole flow without actual routers:

```bash
# Add test orders
python provisioning.py --add-test-order
python provisioning.py --add-gateway-order

# View pending orders
python provisioning.py --list-pending

# Run flash tool (will fail to connect to device, but shows the UI)
python flash_tool.py
```

## Files Created

| When | File | Contents |
|------|------|----------|
| Signup | `pending_orders.json` | Customer info, plan, address |
| Flash | `network_config.json` | Mesh ID, passphrases, device list |
| Flash | `provisioned_devices.json` | All devices ever provisioned |
| Flash | `alkaline.db` | Customers, gateways, billing |
| Payment | `clients.json` | Active customer public keys |

## Troubleshooting

**"No pending orders" in flash tool**
- Check `pending_orders.json` exists and has entries with `"status": "pending"`
- Make sure you're running flash tool from same directory as the JSON file

**Device won't connect to tunnel**
- Check `clients.json` has the device's public key
- Check customer is `subscription_status = 'active'` in database
- Make sure VPS tunnel server is running

**Customer can't get internet**
- Check gateway is online (dashboard shows status)
- Check customer device has good mesh signal
- Check tunnel server is running on VPS

## Adding Gateway Hosts

Same flow, but:
1. Click **GATEWAY** instead of PINGER
2. Ship to gateway host location
3. Host connects to their home internet via Ethernet
4. Gateway starts serving customers automatically
