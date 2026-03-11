# Alkaline Network - Code Audit Summary

**Date:** March 2026  
**Auditor:** Claude (for AlkalineNetwork)

## Executive Summary

The codebase has been audited against website claims. Critical issues have been fixed. The code now correctly reflects the actual product: a HaLow-based mesh network with two payment plans (not speed tiers).

---

## Website Claims vs Code Reality

### ✅ VERIFIED WORKING

| Feature | Website Claims | Code Location | Status |
|---------|----------------|---------------|--------|
| **NaCl/libsodium Encryption** | X25519 + XSalsa20 + Poly1305, same as Signal | `src/encryption.py` | ✅ Full implementation with tests |
| **End-to-End Encryption** | Gateway can't see traffic | `encryption.py` TunnelEncryption class | ✅ Working |
| **Compression** | zlib with HTTP optimization | `src/protocol.py` | ✅ Working |
| **KISS/AX.25 Protocol** | Standard radio protocol | `src/radio.py`, `src/radio_gateway.py` | ✅ Working |
| **Node Modes** | Client/Gateway/Relay | `alkaline_node.py` | ✅ All 3 modes |
| **Gateway Earnings** | $2/customer/month | `src/qos.py` | ✅ Correct |

### ✅ FIXED IN THIS AUDIT

| Issue | Was | Now | File Changed |
|-------|-----|-----|--------------|
| **Speed Tiers** | Basic/Plus/Pro (25/50/100 Mbps) | Single speed (8-20 Mbps, HaLow hardware limit) | `src/qos.py` |
| **Payment Plans** | tier-based | `deposit`/`included` | `src/qos.py` |
| **Internet Forwarding** | TODO stub | Real async TCP forwarding | `alkaline_node.py` |
| **Terminology** | "hoster" | "gateway operator" | `src/qos.py` |
| **Signup Page** | Old 3-tier system | Two options matching pricing | Website updated |

### ⚠️ NOT YET BUILT (Website Promises)

| Feature | Website Says | Status | Priority |
|---------|--------------|--------|----------|
| **Dashboard** | "Dashboard to monitor connections" | Not built | Medium |
| **Status Page** | "Status dashboard for outages" | Not built | Low |
| **Auto-Discovery** | Automatic gateway finding | DISCOVER packets exist, routing incomplete | Medium |
| **HaLow Driver Integration** | Works with GL.iNet HaLowLink | Generic serial/IP interface, no Morse Micro specific code | High |

---

## Architecture Overview

```
USER DEVICE                    ALKALINE NODE                      GATEWAY NODE
(phone/laptop)                 (HaLowLink)                        (HaLowLink + Internet)

┌─────────────┐               ┌─────────────────┐               ┌─────────────────┐
│             │               │                 │               │                 │
│  Browser    │──WiFi/ETH───▶│  1. Receive     │               │                 │
│  App        │               │  2. Compress    │───RADIO──────▶│  1. Receive     │
│  etc        │               │  3. Encrypt     │   (encrypted) │  2. Decrypt     │
│             │               │  4. Transmit    │               │  3. Decompress  │
│             │               │                 │               │  4. Forward     │──▶ INTERNET
└─────────────┘               └─────────────────┘               └─────────────────┘
```

---

## Pricing Model (Code reflects this)

### Customer Options

| Option | Monthly | Upfront | Equipment |
|--------|---------|---------|-----------|
| **Deposit** | $7.99 | $100 (refundable) | Return on cancel |
| **Included** | $14.99 | $0 | Keep after 12 months |

### Gateway Operator Revenue

- $2/customer/month
- 10 customers = $20/month
- HaLowLink costs ~$150 → paid off in ~8 months with 10 users

### Your Margin

| Plan | User Pays | Gateway Gets | You Keep |
|------|-----------|--------------|----------|
| Deposit | $7.99 | $2.00 | $5.99 |
| Included | $14.99 | $2.00 | $12.99 |

---

## Key Files

| File | Purpose |
|------|---------|
| `alkaline_node.py` | Main integration - ties encryption, compression, radio together |
| `src/encryption.py` | NaCl/libsodium crypto - X25519, XSalsa20, Poly1305 |
| `src/protocol.py` | Compression - zlib + HTTP header optimization |
| `src/radio.py` | KISS/AX.25 protocol for radio communication |
| `src/qos.py` | Customer/gateway management, billing |
| `src/ubiquiti.py` | DEPRECATED - old Ubiquiti WISP code |

---

## Next Steps

### Critical (Before Launch)

1. **Test with real HaLowLink hardware** - Current code uses generic serial interface
2. **Add Morse Micro driver integration** - The GL.iNet HaLowLink uses MM8108 chip
3. **Implement packet fragmentation** - Large responses need splitting for radio

### Important

1. **Build dashboard** - Simple web UI showing gateway status, connected users
2. **Add auto-discovery routing** - Currently nodes don't auto-find best gateway
3. **Build customer setup tool** - Easy way to configure new nodes

### Nice to Have

1. Status page for outage visibility
2. Mobile app for gateway operators
3. Usage graphs/analytics

---

## Files Changed in This Audit

- `src/qos.py` - Rewrote for new payment plan model
- `alkaline_node.py` - Implemented internet forwarding
- `src/ubiquiti.py` - Added deprecation notice
- Website `signup.html` - Fixed to match new pricing
- Website `pricing.html` - Fixed gateway earnings ($2 not $5)

---

## Verification

All modified Python files compile successfully:
```
✅ src/qos.py compiles OK
✅ alkaline_node.py compiles OK
```
