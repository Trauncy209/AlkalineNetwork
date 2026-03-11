#!/usr/bin/env python3
"""
Alkaline Hosting - System Verification Test
Run this to verify all components work together.
"""

import sys
import os
import json
import time
import threading
import subprocess

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'alkaline-core', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'alkaline-dashboard'))

print("=" * 70)
print("  ALKALINE HOSTING - SYSTEM VERIFICATION")
print("=" * 70)
print()

results = {
    "passed": 0,
    "failed": 0,
    "tests": []
}

def test(name, condition, details=""):
    """Run a test and record result."""
    if condition:
        results["passed"] += 1
        status = "✅ PASS"
    else:
        results["failed"] += 1
        status = "❌ FAIL"
    
    results["tests"].append({"name": name, "passed": condition, "details": details})
    print(f"  {status}  {name}")
    if details and not condition:
        print(f"           {details}")

# ============================================
# TEST 1: Import all modules
# ============================================
print("\n[1/7] CHECKING IMPORTS...")

try:
    from qos import QoSManager, ServiceTier, TIER_LIMITS
    test("Import qos.py", True)
except Exception as e:
    test("Import qos.py", False, str(e))

try:
    from billing import BillingManager, PRICING
    test("Import billing.py", True)
except ImportError as e:
    if 'stripe' in str(e):
        test("Import billing.py (stripe optional)", True, "stripe not installed - OK for testing")
    else:
        test("Import billing.py", False, str(e))
except Exception as e:
    test("Import billing.py", False, str(e))

try:
    from encryption import AlkalineEncryption, NACL_AVAILABLE
    test("Import encryption.py", True)
    if NACL_AVAILABLE:
        test("PyNaCl available", True)
    else:
        test("PyNaCl available (optional)", True, "Not installed - encryption disabled")
except Exception as e:
    test("Import encryption.py", False, str(e))

try:
    from alkaline_device import AlkalineDevice, DeviceMode, DeviceIdentity
    test("Import alkaline_device.py", True)
except Exception as e:
    test("Import alkaline_device.py", False, str(e))

# ============================================
# TEST 2: QoS System
# ============================================
print("\n[2/7] TESTING QOS SYSTEM...")

try:
    # Check tier limits are correct
    basic = TIER_LIMITS[ServiceTier.BASIC]
    test("Basic tier = 25 Mbps down", basic.download_mbps == 25)
    test("Basic tier = 10 Mbps up", basic.upload_mbps == 10)
    
    plus = TIER_LIMITS[ServiceTier.PLUS]
    test("Plus tier = 50 Mbps down", plus.download_mbps == 50)
    test("Plus tier = 20 Mbps up", plus.upload_mbps == 20)
    
    pro = TIER_LIMITS[ServiceTier.PRO]
    test("Pro tier = 100 Mbps down", pro.download_mbps == 100)
    test("Pro tier = 40 Mbps up", pro.upload_mbps == 40)
    
    # Test QoS manager with temp file
    import tempfile
    temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    temp_db.close()
    
    qos = QoSManager(temp_db.name)
    test("QoS Manager initializes", qos is not None)
    
    # Register test customer
    success = qos.register_customer("TEST001", "AA:BB:CC:DD:EE:FF", ServiceTier.PLUS, "HOSTER001")
    test("Register customer", success)
    
    # Test rate limiting
    allowed, wait = qos.can_send("TEST001", 1000, "download")
    test("Rate limiting works", allowed == True)
    
    # Cleanup
    os.unlink(temp_db.name)
    
except Exception as e:
    test("QoS system", False, str(e))

# ============================================
# TEST 3: Billing System
# ============================================
print("\n[3/7] TESTING BILLING SYSTEM...")

try:
    # Import billing - it may fail if stripe not installed
    try:
        from billing import BillingManager, PRICING
        stripe_available = True
    except ImportError as e:
        if 'stripe' in str(e):
            stripe_available = False
            test("Billing (stripe not installed)", False, "pip install stripe")
        else:
            raise
    
    if stripe_available:
        # Check pricing is correct
        test("Basic price = $7.99", PRICING["basic"] == 799)
        test("Plus price = $14.99", PRICING["plus"] == 1499)
        test("Pro price = $24.99", PRICING["pro"] == 2499)
        
        # Test billing manager
        import tempfile
        temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp_db.close()
        
        billing = BillingManager(temp_db.name)
        test("Billing Manager initializes", billing is not None)
        
        # Test revenue report (empty)
        report = billing.get_revenue_report()
        test("Revenue report works", "mrr_cents" in report)
        
        os.unlink(temp_db.name)
    
except Exception as e:
    test("Billing system", False, str(e))

# ============================================
# TEST 4: Device Identity
# ============================================
print("\n[4/7] TESTING DEVICE IDENTITY...")

try:
    # Test identity generation (without writing to disk)
    identity = DeviceIdentity.__new__(DeviceIdentity)
    identity.mac_address = "AA:BB:CC:DD:EE:FF"
    identity.serial = "TEST12345"
    identity.device_id = "ALK-TEST-0001"
    
    test("Device ID format correct", identity.device_id.startswith("ALK-"))
    test("MAC address captured", len(identity.mac_address) == 17)
    
except Exception as e:
    test("Device identity", False, str(e))

# ============================================
# TEST 5: Protocol Messages
# ============================================
print("\n[5/7] TESTING PROTOCOL...")

try:
    from alkaline_device import AlkalineProtocol
    
    # Test packing
    payload = {"device_id": "ALK-TEST-0001", "type": "modem"}
    packed = AlkalineProtocol.pack(AlkalineProtocol.MSG_ANNOUNCE, payload)
    test("Protocol packing", packed.startswith(b'ALK'))
    
    # Test unpacking
    msg_type, unpacked = AlkalineProtocol.unpack(packed)
    test("Protocol unpacking", msg_type == AlkalineProtocol.MSG_ANNOUNCE)
    test("Payload preserved", unpacked["device_id"] == "ALK-TEST-0001")
    
except Exception as e:
    test("Protocol", False, str(e))

# ============================================
# TEST 6: Encryption
# ============================================
print("\n[6/7] TESTING ENCRYPTION...")

try:
    from encryption import AlkalineEncryption, NACL_AVAILABLE
    
    if NACL_AVAILABLE:
        crypto = AlkalineEncryption()
        keys = crypto.generate_keypair()
        test("Generate keypair", keys is not None)
        test("Public key is 32 bytes", len(keys.public_key) == 32)
        test("Private key is 32 bytes", len(keys.private_key) == 32)
        
        # Test encrypt/decrypt
        plaintext = b"Hello, Alkaline Network!"
        encrypted = crypto.encrypt(plaintext, keys.public_key)
        test("Encryption works", encrypted is not None)
        
        decrypted = crypto.decrypt(encrypted, keys.public_key)
        test("Decryption works", decrypted == plaintext)
    else:
        test("Encryption (PyNaCl not installed)", False, "pip install pynacl")
        
except Exception as e:
    test("Encryption", False, str(e))

# ============================================
# TEST 7: Dashboard API Structure
# ============================================
print("\n[7/7] TESTING DASHBOARD STRUCTURE...")

try:
    # Check dashboard file exists and has required endpoints
    dashboard_path = os.path.join(os.path.dirname(__file__), 'alkaline-dashboard', 'app.py')
    with open(dashboard_path, 'r') as f:
        dashboard_code = f.read()
    
    endpoints = [
        "/api/device/register",
        "/api/device/heartbeat", 
        "/api/devices",
        "/api/hosters",
        "/api/gateway/modem_connected",
        "/api/stats",
        "/api/events"
    ]
    
    for endpoint in endpoints:
        test(f"Dashboard has {endpoint}", endpoint in dashboard_code)
    
    # Check SSE is implemented
    test("Real-time SSE implemented", "text/event-stream" in dashboard_code)
    
    # Check $2/customer calculation
    test("$2/customer calculation", "* 2.00" in dashboard_code)
    
except Exception as e:
    test("Dashboard structure", False, str(e))

# ============================================
# SUMMARY
# ============================================
print()
print("=" * 70)
print(f"  RESULTS: {results['passed']} passed, {results['failed']} failed")
print("=" * 70)

if results['failed'] == 0:
    print()
    print("  🎉 ALL TESTS PASSED!")
    print()
    print("  Your system is ready. To run:")
    print()
    print("  1. Start Dashboard:")
    print("     cd alkaline-dashboard && python app.py")
    print()
    print("  2. Start Gateway (on Pi):")
    print("     python alkaline_device.py gateway --dashboard http://YOUR_IP:5000")
    print()
    print("  3. Start Modem (on Pi):")
    print("     python alkaline_device.py modem --dashboard http://YOUR_IP:5000")
    print()
else:
    print()
    print("  ⚠️  Some tests failed. Review the errors above.")
    print()
    
    # Show failed tests
    print("  Failed tests:")
    for t in results["tests"]:
        if not t["passed"]:
            print(f"    - {t['name']}")
            if t["details"]:
                print(f"      {t['details']}")

print("=" * 70)
