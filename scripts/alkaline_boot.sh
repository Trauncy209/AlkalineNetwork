#!/bin/sh
#
# Alkaline Network - Auto-Start Script v2.0
# ==========================================
#
# This script runs on boot on both Gateway and Pinger devices.
# 
# GATEWAY: Shares internet with pingers, enforces allowed device list
# PINGER: Connects to gateway, provides WiFi to customer
#
# No central server needed - devices talk directly to each other.
#
# Installation (run once):
#   chmod +x /opt/alkaline/alkaline_boot.sh
#   ln -s /opt/alkaline/alkaline_boot.sh /etc/init.d/alkaline
#   /etc/init.d/alkaline enable
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
        MODE=$(grep -o '"mode"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
        DEVICE_ID=$(grep -o '"device_id"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" | cut -d'"' -f4)
        MAX_CUSTOMERS=$(grep -o '"max_customers"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_FILE" | grep -o '[0-9]*$')
        
        log "Config loaded: mode=$MODE, device=$DEVICE_ID"
    else
        log "No config file found, auto-detecting mode"
        MODE=""
        DEVICE_ID=""
        MAX_CUSTOMERS="9"
    fi
}

# Detect mode from hardware/network
detect_mode() {
    # If we have ethernet with internet, we're a gateway
    if ip link show eth0 2>/dev/null | grep -q "state UP"; then
        if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
            log "Detected: GATEWAY (has internet via ethernet)"
            MODE="gateway"
            return
        fi
    fi
    
    # Otherwise we're a pinger
    log "Detected: PINGER"
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
    log "Starting Alkaline in $MODE mode..."
    
    cd "$ALKALINE_DIR"
    
    # Start the device software
    if [ "$MODE" = "gateway" ]; then
        $PYTHON alkaline_device.py --gateway \
            --device-id "${DEVICE_ID:-GW-AUTO}" \
            --max-customers "${MAX_CUSTOMERS:-9}" \
            >> /var/log/alkaline/device.log 2>&1 &
    else
        $PYTHON alkaline_device.py --pinger \
            --device-id "${DEVICE_ID:-PN-AUTO}" \
            >> /var/log/alkaline/device.log 2>&1 &
    fi
    
    DEVICE_PID=$!
    echo "$DEVICE_PID" > "$PID_FILE"
    log "Device software started (PID: $DEVICE_PID)"
    
    # Start adaptive bandwidth controller
    if [ -f "$ALKALINE_DIR/adaptive_bandwidth.py" ]; then
        $PYTHON adaptive_bandwidth.py --monitor \
            >> /var/log/alkaline/bandwidth.log 2>&1 &
        
        echo "$!" > "$PID_FILE.bandwidth"
        log "Adaptive bandwidth started"
    fi
    
    log "Alkaline services started successfully"
}

# Stop services
stop_services() {
    log "Stopping Alkaline services..."
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null
        rm -f "$PID_FILE"
    fi
    
    if [ -f "$PID_FILE.bandwidth" ]; then
        PID=$(cat "$PID_FILE.bandwidth")
        kill "$PID" 2>/dev/null
        rm -f "$PID_FILE.bandwidth"
    fi
    
    pkill -f "alkaline_device.py" 2>/dev/null
    pkill -f "adaptive_bandwidth.py" 2>/dev/null
    
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
        log "Alkaline Network Boot"
        log "=========================================="
        
        check_python
        wait_for_network
        read_config
        
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
        ln -sf "$ALKALINE_DIR/alkaline_boot.sh" /etc/init.d/alkaline
        ln -sf /etc/init.d/alkaline /etc/rc.d/S99alkaline 2>/dev/null
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
