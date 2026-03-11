#!/bin/sh
#
# Alkaline Network - Auto-Start Script
# =====================================
#
# This script runs on boot on both Gateway and Pinger devices.
# It automatically:
#   1. Detects if this is a Gateway or Pinger
#   2. Starts the mesh discovery service
#   3. Connects to the network
#   4. Starts the encrypted tunnel
#
# Installation (run once):
#   chmod +x /opt/alkaline/alkaline_boot.sh
#   ln -s /opt/alkaline/alkaline_boot.sh /etc/init.d/alkaline
#   /etc/init.d/alkaline enable
#
# Or add to /etc/rc.local:
#   /opt/alkaline/alkaline_boot.sh start &
#

ALKALINE_DIR="/opt/alkaline"
CONFIG_FILE="/etc/alkaline/config.json"
LOG_FILE="/var/log/alkaline/boot.log"
PID_FILE="/var/run/alkaline.pid"

# Ensure directories exist
mkdir -p /etc/alkaline
mkdir -p /var/lib/alkaline
mkdir -p /var/log/alkaline
mkdir -p /opt/alkaline

# Logging
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
    echo "$1"
}

# Check if Python is available
check_python() {
    if command -v python3 > /dev/null 2>&1; then
        PYTHON="python3"
    elif command -v python > /dev/null 2>&1; then
        PYTHON="python"
    else
        log "ERROR: Python not found!"
        exit 1
    fi
    log "Using Python: $PYTHON"
}

# Read config
read_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # Parse JSON config (basic parsing for shell)
        MODE=$(grep -o '"mode"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
        SERVER_IP=$(grep -o '"server_ip"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
        SERVER_PORT=$(grep -o '"server_port"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_FILE" | grep -o '[0-9]*$')
        SERVER_PUBKEY=$(grep -o '"server_pubkey"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
        MAX_CUSTOMERS=$(grep -o '"max_customers"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_FILE" | grep -o '[0-9]*$')
        
        log "Config loaded: mode=$MODE"
    else
        log "No config file found, using defaults"
        MODE="pinger"
        SERVER_IP=""
        SERVER_PORT="51820"
        SERVER_PUBKEY=""
        MAX_CUSTOMERS="9"
    fi
}

# Detect mode from hardware/network
detect_mode() {
    # If we have an ethernet connection with internet, we're likely a gateway
    if ip link show eth0 | grep -q "state UP"; then
        # Check if we can reach the internet
        if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
            log "Detected: Gateway (has internet via ethernet)"
            MODE="gateway"
            return
        fi
    fi
    
    # Otherwise we're a pinger
    log "Detected: Pinger"
    MODE="pinger"
}

# Wait for network
wait_for_network() {
    log "Waiting for network..."
    TRIES=0
    while [ $TRIES -lt 30 ]; do
        if ip addr show | grep -q "inet.*scope global"; then
            log "Network is up"
            return 0
        fi
        TRIES=$((TRIES + 1))
        sleep 2
    done
    log "WARNING: Network not ready after 60 seconds"
    return 1
}

# Start Alkaline services
start_services() {
    log "Starting Alkaline services in $MODE mode..."
    
    cd "$ALKALINE_DIR"
    
    if [ "$MODE" = "gateway" ]; then
        # Start mesh manager as gateway
        $PYTHON alkaline_mesh.py --gateway \
            --max-customers "${MAX_CUSTOMERS:-9}" \
            --server-ip "$SERVER_IP" \
            --server-port "${SERVER_PORT:-51820}" \
            --server-pubkey "$SERVER_PUBKEY" \
            >> /var/log/alkaline/mesh.log 2>&1 &
        
        MESH_PID=$!
        echo "$MESH_PID" > "$PID_FILE"
        log "Mesh manager started (PID: $MESH_PID)"
        
    else
        # Start mesh manager as pinger (auto-connects)
        $PYTHON alkaline_mesh.py --pinger \
            --auto-connect \
            --server-ip "$SERVER_IP" \
            --server-port "${SERVER_PORT:-51820}" \
            --server-pubkey "$SERVER_PUBKEY" \
            >> /var/log/alkaline/mesh.log 2>&1 &
        
        MESH_PID=$!
        echo "$MESH_PID" > "$PID_FILE"
        log "Mesh manager started (PID: $MESH_PID)"
    fi
    
    log "Alkaline services started successfully"
}

# Stop services
stop_services() {
    log "Stopping Alkaline services..."
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            log "Stopped process $PID"
        fi
        rm -f "$PID_FILE"
    fi
    
    # Kill any remaining alkaline processes
    pkill -f "alkaline_mesh.py" 2>/dev/null
    pkill -f "alkaline_complete.py" 2>/dev/null
    
    log "Services stopped"
}

# Status check
status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Alkaline is running (PID: $PID)"
            return 0
        fi
    fi
    echo "Alkaline is not running"
    return 1
}

# Main
case "$1" in
    start)
        log "=========================================="
        log "Alkaline Network Boot Sequence"
        log "=========================================="
        
        check_python
        wait_for_network
        read_config
        
        # Auto-detect mode if not configured
        if [ -z "$MODE" ] || [ "$MODE" = "auto" ]; then
            detect_mode
        fi
        
        start_services
        ;;
    
    stop)
        stop_services
        ;;
    
    restart)
        stop_services
        sleep 2
        $0 start
        ;;
    
    status)
        status
        ;;
    
    enable)
        # Create init.d symlink
        if [ -f /etc/init.d/alkaline ]; then
            rm /etc/init.d/alkaline
        fi
        ln -s "$ALKALINE_DIR/alkaline_boot.sh" /etc/init.d/alkaline
        
        # Enable on OpenWrt
        if [ -f /etc/rc.d/S99alkaline ]; then
            rm /etc/rc.d/S99alkaline
        fi
        ln -s /etc/init.d/alkaline /etc/rc.d/S99alkaline
        
        log "Alkaline enabled on boot"
        ;;
    
    disable)
        rm -f /etc/init.d/alkaline
        rm -f /etc/rc.d/S99alkaline
        log "Alkaline disabled on boot"
        ;;
    
    *)
        echo "Usage: $0 {start|stop|restart|status|enable|disable}"
        exit 1
        ;;
esac

exit 0
