# Alkaline Network - How It Actually Works

## The Simple Truth

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│  Customer's     │  mesh   │  Gateway Host's │  WiFi   │  Gateway Host's │
│  Pinger Device  │ ──────► │  Gateway Device │ ──────► │  Home Router    │ ──► Internet
│  ($79 radio)    │  HaLow  │  ($79 radio)    │  ETH    │  (Comcast etc)  │
└─────────────────┘         └─────────────────┘         └─────────────────┘
                                    │
                                    │ The gateway device IS the 
                                    │ exit point to the internet
                                    ▼
                            Customer traffic goes
                            directly to internet
                            through host's connection
```

## What YOUR PC Does

Your PC is just for MANAGEMENT. It does NOT route any customer traffic.

```
YOUR PC (Management Only)
├── Website (alkalinehosting.com) - customers sign up here
├── Flash Tool - you provision new devices here  
├── Dashboard - you view customers/billing here
└── Database - stores who's paid, device keys, etc.
```

## What Happens When Customer Uses Internet

1. Customer connects laptop/phone to their Pinger's WiFi ("Alkaline-XXXXX")
2. Pinger encrypts traffic with WPA3
3. Pinger sends traffic over HaLow mesh to Gateway
4. Gateway decrypts and forwards to internet via host's router
5. Response comes back the same way

**Your PC is not involved in any of this.** The devices talk directly to each other.

## What Happens When You Flash a Device

1. You plug blank Heltec radio into your PC via Ethernet
2. Open Flash Tool, select pending order, click PINGER or GATEWAY
3. Flash Tool configures the device:
   - Sets mesh network ID + password (so devices find each other)
   - Sets customer WiFi name + password
   - Generates encryption keys
   - Saves device info to your database
4. Unplug device, ship to customer
5. Customer plugs in, it auto-connects to mesh, done

## What Happens When Customer Pays

1. Stripe charges their card
2. Webhook hits your website
3. Website updates database: customer status = "active"
4. Sync runs: active customers get added to "allowed devices" list
5. Gateway devices periodically download this list
6. If customer stops paying, they get removed from list, gateway blocks them

## The Three Encryption Layers (From Your Business Plan)

1. **WPA3-SAE** - Built into HaLow hardware. Encrypts mesh traffic automatically.
2. **Device Keys** - Each device has unique keys. Gateway only accepts known devices.
3. **TLS/HTTPS** - Websites encrypt their own traffic (nothing to do with us)

## Files On Your PC

| File | What It Does |
|------|--------------|
| `alkaline.db` | SQLite database - customers, gateways, payments |
| `pending_orders.json` | Orders waiting to be fulfilled |
| `network_config.json` | Mesh ID, passwords, provisioned device list |

## Files On Gateway Device

| File | What It Does |
|------|--------------|
| `/etc/alkaline/allowed_devices.json` | List of customer public keys allowed to connect |
| `/etc/alkaline/device_key` | This gateway's private key |
| `/etc/config/wireless` | OpenWrt WiFi/mesh configuration |

## The Automation

| Event | What Happens Automatically |
|-------|---------------------------|
| Customer signs up | Order created in pending_orders.json |
| Customer pays | Database updated, synced to allowed list |
| Customer stops paying | Removed from allowed list, loses internet |
| Device flashed | Registered in database, assigned to gateway |
| Customer plugs in device | Auto-connects to mesh, gets internet |

## What You Do Daily

1. Check flash tool for pending orders
2. Plug in device → click order → click Flash → ship
3. That's it

Everything else is automatic.
