#!/bin/bash
# =============================================================================
# Alkaline Network - Gateway Setup Script
# Run this on a HaLowLink device that will be a GATEWAY (has internet)
# =============================================================================

set -e

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        ALKALINE NETWORK - GATEWAY SETUP                       ║"
echo "║        This device will share internet with the mesh          ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo ./setup_gateway.sh)"
    exit 1
fi

# =============================================================================
# STEP 1: Install Dependencies
# =============================================================================
echo "[1/5] Installing dependencies..."

# Update package list
opkg update || apt-get update

# Install Python3 and pip
opkg install python3 python3-pip || apt-get install -y python3 python3-pip

# Install required Python packages
pip3 install pynacl pyserial

echo "✓ Dependencies installed"

# =============================================================================
# STEP 2: Create Alkaline Directory
# =============================================================================
echo "[2/5] Setting up Alkaline directory..."

mkdir -p /opt/alkaline
mkdir -p /opt/alkaline/keys
mkdir -p /opt/alkaline/logs

# Copy the code (assuming it's in current directory)
if [ -f "alkaline_node.py" ]; then
    cp -r *.py /opt/alkaline/
    cp -r src/ /opt/alkaline/ 2>/dev/null || true
else
    echo "WARNING: alkaline_node.py not found in current directory"
    echo "You'll need to copy the code manually to /opt/alkaline/"
fi

echo "✓ Directory created at /opt/alkaline"

# =============================================================================
# STEP 3: Generate Identity
# =============================================================================
echo "[3/5] Generating gateway identity..."

python3 << 'PYEOF'
import os
import sys
sys.path.insert(0, '/opt/alkaline')

try:
    from src.encryption import AlkalineEncryption, NACL_AVAILABLE
    if not NACL_AVAILABLE:
        print("ERROR: pynacl not installed")
        sys.exit(1)
    
    key_path = "/opt/alkaline/keys/identity"
    
    if os.path.exists(key_path):
        print(f"Identity already exists at {key_path}")
        with open(key_path, 'rb') as f:
            private_key = f.read(32)
        crypto = AlkalineEncryption(private_key)
    else:
        crypto = AlkalineEncryption()
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, 'wb') as f:
            f.write(crypto.private_key)
        os.chmod(key_path, 0o600)
        print(f"Generated new identity")
    
    print(f"\n{'='*60}")
    print(f"GATEWAY PUBLIC KEY (share this with customers):")
    print(f"{'='*60}")
    print(crypto.public_key.hex())
    print(f"{'='*60}\n")
    
    # Save public key to file for easy sharing
    with open("/opt/alkaline/keys/public_key.txt", 'w') as f:
        f.write(crypto.public_key.hex())
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
PYEOF

echo "✓ Identity generated"

# =============================================================================
# STEP 4: Create Config
# =============================================================================
echo "[4/5] Creating configuration..."

cat > /opt/alkaline/config.json << 'CONFIGEOF'
{
    "mode": "gateway",
    "node_id": "",
    "radio_device": "/dev/ttyUSB0",
    "radio_baud": 115200,
    "local_interface": "br-lan",
    "local_ip": "10.42.0.1",
    "compression_level": 6,
    "max_packet_size": 250
}
CONFIGEOF

echo "✓ Configuration created at /opt/alkaline/config.json"

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
    procd_set_param command /usr/bin/python3 /opt/alkaline/alkaline_node.py --mode gateway --config /opt/alkaline/config.json
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
echo "║                    GATEWAY SETUP COMPLETE                     ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Your gateway public key is saved at:"
echo "  /opt/alkaline/keys/public_key.txt"
echo ""
echo "Customers need this key to connect to your gateway."
echo ""
echo "To start the gateway now:"
echo "  /etc/init.d/alkaline start"
echo ""
echo "To view logs:"
echo "  logread -f | grep alkaline"
echo ""
echo "To check status:"
echo "  python3 /opt/alkaline/alkaline_node.py --status"
echo ""
