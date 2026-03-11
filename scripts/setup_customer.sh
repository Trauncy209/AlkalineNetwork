#!/bin/bash
# =============================================================================
# Alkaline Network - Customer Node Setup Script
# Run this on a HaLowLink device that will be a CUSTOMER (connects to gateway)
# =============================================================================

set -e

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        ALKALINE NETWORK - CUSTOMER NODE SETUP                 ║"
echo "║        This device will connect to a gateway                  ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo ./setup_customer.sh)"
    exit 1
fi

# =============================================================================
# GET GATEWAY KEY
# =============================================================================
if [ -z "$1" ]; then
    echo "Usage: ./setup_customer.sh <gateway_public_key>"
    echo ""
    echo "You need the gateway's public key. Ask your gateway operator for it."
    echo "It's a 64-character hex string like:"
    echo "  a1b2c3d4e5f6..."
    echo ""
    exit 1
fi

GATEWAY_KEY="$1"

# Validate key format (should be 64 hex chars)
if ! [[ "$GATEWAY_KEY" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "ERROR: Invalid gateway key format"
    echo "Key should be exactly 64 hexadecimal characters"
    exit 1
fi

echo "Gateway key: ${GATEWAY_KEY:0:16}..."
echo ""

# =============================================================================
# STEP 1: Install Dependencies
# =============================================================================
echo "[1/5] Installing dependencies..."

opkg update || apt-get update
opkg install python3 python3-pip || apt-get install -y python3 python3-pip
pip3 install pynacl pyserial

echo "✓ Dependencies installed"

# =============================================================================
# STEP 2: Create Alkaline Directory
# =============================================================================
echo "[2/5] Setting up Alkaline directory..."

mkdir -p /opt/alkaline
mkdir -p /opt/alkaline/keys
mkdir -p /opt/alkaline/logs

if [ -f "alkaline_node.py" ]; then
    cp -r *.py /opt/alkaline/
    cp -r src/ /opt/alkaline/ 2>/dev/null || true
fi

echo "✓ Directory created"

# =============================================================================
# STEP 3: Generate Identity
# =============================================================================
echo "[3/5] Generating node identity..."

python3 << PYEOF
import os
import sys
sys.path.insert(0, '/opt/alkaline')

try:
    from src.encryption import AlkalineEncryption, NACL_AVAILABLE
    if not NACL_AVAILABLE:
        print("ERROR: pynacl not installed")
        sys.exit(1)
    
    key_path = "/opt/alkaline/keys/identity"
    
    if not os.path.exists(key_path):
        crypto = AlkalineEncryption()
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, 'wb') as f:
            f.write(crypto.private_key)
        os.chmod(key_path, 0o600)
        print(f"Generated new identity: {crypto.public_key.hex()[:16]}...")
    else:
        print("Identity already exists")
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
PYEOF

echo "✓ Identity generated"

# =============================================================================
# STEP 4: Create Config with Gateway Key
# =============================================================================
echo "[4/5] Creating configuration..."

cat > /opt/alkaline/config.json << CONFIGEOF
{
    "mode": "client",
    "node_id": "",
    "gateway_public_key": "$GATEWAY_KEY",
    "radio_device": "/dev/ttyUSB0",
    "radio_baud": 115200,
    "local_interface": "br-lan",
    "local_ip": "10.42.0.100",
    "compression_level": 6,
    "max_packet_size": 250
}
CONFIGEOF

echo "✓ Configuration created with gateway key"

# =============================================================================
# STEP 5: Create Systemd Service
# =============================================================================
echo "[5/5] Creating startup service..."

cat > /etc/init.d/alkaline << 'INITEOF'
#!/bin/sh /etc/rc.common

START=99
STOP=10
USE_PROCD=1

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/python3 /opt/alkaline/alkaline_node.py --mode client --config /opt/alkaline/config.json
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param pidfile /var/run/alkaline.pid
    procd_close_instance
}
INITEOF

chmod +x /etc/init.d/alkaline
/etc/init.d/alkaline enable

echo "✓ Service created and enabled"

# =============================================================================
# DONE
# =============================================================================
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                 CUSTOMER NODE SETUP COMPLETE                  ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Your node is configured to connect to gateway:"
echo "  ${GATEWAY_KEY:0:16}..."
echo ""
echo "To start the node:"
echo "  /etc/init.d/alkaline start"
echo ""
echo "To check connection status:"
echo "  python3 /opt/alkaline/alkaline_node.py --status"
echo ""
echo "Once connected, devices on this network can access the internet"
echo "through the encrypted mesh."
echo ""
