"""
Alkaline Hosting - Unified Device Software
This runs on BOTH customer modems AND hoster gateways.
Devices announce themselves and communicate with dashboard automatically.

MODEM MODE:  Connects to gateway, announces to dashboard, routes user traffic
GATEWAY MODE: Accepts modems, shares internet, reports to dashboard
"""

import socket
import threading
import time
import requests
import json
import os
import subprocess
import platform
import uuid
import hashlib
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
from enum import Enum

# ============================================
# CONFIGURATION
# ============================================

class DeviceMode(Enum):
    MODEM = "modem"      # Customer device - connects TO gateway
    GATEWAY = "gateway"  # Hoster device - accepts modems

@dataclass
class DeviceConfig:
    """Configuration for this device."""
    mode: DeviceMode
    device_id: str
    dashboard_url: str = "https://dashboard.alkalinehosting.com"
    gateway_ip: Optional[str] = None  # Only for modems
    hoster_id: Optional[str] = None   # Only for gateways
    
    # Network settings
    wifi_interface: str = "wlan0"
    eth_interface: str = "eth0"
    
    # Communication ports
    announce_port: int = 5555      # UDP broadcast port
    control_port: int = 5556       # TCP control channel
    proxy_port: int = 8888         # HTTP proxy
    
    # Heartbeat
    heartbeat_interval: int = 30   # seconds

# ============================================
# DEVICE IDENTITY
# ============================================

class DeviceIdentity:
    """Manages device identity - persists across reboots."""
    
    CONFIG_PATH = "/etc/alkaline/device.json"
    
    def __init__(self):
        self.device_id = None
        self.mac_address = None
        self.serial = None
        self._load_or_create()
    
    def _get_mac(self, interface: str = "wlan0") -> str:
        """Get MAC address."""
        try:
            path = f"/sys/class/net/{interface}/address"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip().upper()
        except:
            pass
        
        # Fallback - generate from hostname
        return hashlib.md5(socket.gethostname().encode()).hexdigest()[:12]
    
    def _get_serial(self) -> str:
        """Get Raspberry Pi serial number."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        return line.split(':')[1].strip()
        except:
            pass
        return "UNKNOWN"
    
    def _load_or_create(self):
        """Load existing identity or create new one."""
        self.mac_address = self._get_mac()
        self.serial = self._get_serial()
        
        # Try to load existing config
        if os.path.exists(self.CONFIG_PATH):
            try:
                with open(self.CONFIG_PATH, 'r') as f:
                    data = json.load(f)
                    self.device_id = data.get('device_id')
                    return
            except:
                pass
        
        # Generate new device ID
        # Format: ALK-XXXX-XXXX (based on MAC + serial)
        unique = hashlib.sha256(f"{self.mac_address}{self.serial}".encode()).hexdigest()[:8].upper()
        self.device_id = f"ALK-{unique[:4]}-{unique[4:8]}"
        
        # Save it
        self._save()
    
    def _save(self):
        """Save identity to disk."""
        os.makedirs(os.path.dirname(self.CONFIG_PATH), exist_ok=True)
        with open(self.CONFIG_PATH, 'w') as f:
            json.dump({
                'device_id': self.device_id,
                'mac_address': self.mac_address,
                'serial': self.serial,
                'created': time.time()
            }, f, indent=2)

# ============================================
# DASHBOARD COMMUNICATION
# ============================================

class DashboardClient:
    """Handles all communication with the central dashboard."""
    
    def __init__(self, base_url: str, identity: DeviceIdentity):
        self.base_url = base_url.rstrip('/')
        self.identity = identity
        self.registered = False
        self.auth_token = None
    
    def register(self, mode: DeviceMode, hoster_id: str = None) -> bool:
        """Register this device with the dashboard."""
        try:
            payload = {
                "device_id": self.identity.device_id,
                "mac_address": self.identity.mac_address,
                "serial": self.identity.serial,
                "hostname": socket.gethostname(),
                "device_type": mode.value,
                "ip_address": self._get_ip(),
                "software_version": "1.0.0",
            }
            
            if hoster_id:
                payload["hoster_id"] = hoster_id
            
            response = requests.post(
                f"{self.base_url}/api/device/register",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.registered = True
                self.auth_token = data.get('auth_token')
                print(f"[DASHBOARD] Registered: {self.identity.device_id}")
                return True
            else:
                print(f"[DASHBOARD] Registration failed: {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError:
            print(f"[DASHBOARD] Cannot connect to {self.base_url}")
            return False
        except Exception as e:
            print(f"[DASHBOARD] Error: {e}")
            return False
    
    def heartbeat(self, stats: dict) -> dict:
        """Send heartbeat with stats, receive commands."""
        try:
            response = requests.post(
                f"{self.base_url}/api/device/heartbeat",
                json={
                    "device_id": self.identity.device_id,
                    "mac_address": self.identity.mac_address,
                    **stats
                },
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()  # May contain commands
            
        except:
            pass
        
        return {}
    
    def report_connected_modem(self, modem_id: str, modem_mac: str):
        """Gateway reports that a modem connected to it."""
        try:
            requests.post(
                f"{self.base_url}/api/gateway/modem_connected",
                json={
                    "gateway_id": self.identity.device_id,
                    "modem_id": modem_id,
                    "modem_mac": modem_mac,
                    "timestamp": time.time()
                },
                timeout=5
            )
        except:
            pass
    
    def _get_ip(self) -> str:
        """Get current IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "0.0.0.0"

# ============================================
# DEVICE-TO-DEVICE COMMUNICATION
# ============================================

class AlkalineProtocol:
    """
    Protocol for modem <-> gateway communication.
    Uses UDP broadcast for discovery, TCP for control.
    """
    
    # Message types
    MSG_ANNOUNCE = 0x01      # Modem announces itself
    MSG_WELCOME = 0x02       # Gateway acknowledges modem
    MSG_HEARTBEAT = 0x03     # Periodic keepalive
    MSG_STATS = 0x04         # Usage statistics
    MSG_CONFIG = 0x05        # Configuration update
    MSG_DISCONNECT = 0x06    # Clean disconnect
    
    MAGIC = b'ALK'           # Packet identifier
    VERSION = 1
    
    @staticmethod
    def pack(msg_type: int, payload: dict) -> bytes:
        """Pack a message for transmission."""
        data = json.dumps(payload).encode('utf-8')
        header = AlkalineProtocol.MAGIC + bytes([
            AlkalineProtocol.VERSION,
            msg_type,
            (len(data) >> 8) & 0xFF,
            len(data) & 0xFF
        ])
        return header + data
    
    @staticmethod
    def unpack(data: bytes) -> tuple:
        """Unpack a received message. Returns (msg_type, payload) or (None, None)."""
        if len(data) < 8 or data[:3] != AlkalineProtocol.MAGIC:
            return None, None
        
        version = data[3]
        msg_type = data[4]
        length = (data[5] << 8) | data[6]
        
        if len(data) < 7 + length:
            return None, None
        
        try:
            payload = json.loads(data[7:7+length].decode('utf-8'))
            return msg_type, payload
        except:
            return None, None


class ModemAnnouncer:
    """Modem broadcasts its presence to find gateways."""
    
    def __init__(self, identity: DeviceIdentity, port: int = 5555):
        self.identity = identity
        self.port = port
        self.sock = None
        self.gateway_ip = None
        self.gateway_id = None
        self.connected = False
    
    def broadcast_announce(self) -> bool:
        """Broadcast announcement to find gateway."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(5)
            
            # Send announcement
            msg = AlkalineProtocol.pack(AlkalineProtocol.MSG_ANNOUNCE, {
                "device_id": self.identity.device_id,
                "mac": self.identity.mac_address,
                "hostname": socket.gethostname(),
                "type": "modem"
            })
            
            sock.sendto(msg, ('255.255.255.255', self.port))
            print(f"[MODEM] Broadcasting announcement...")
            
            # Wait for welcome response
            try:
                data, addr = sock.recvfrom(1024)
                msg_type, payload = AlkalineProtocol.unpack(data)
                
                if msg_type == AlkalineProtocol.MSG_WELCOME:
                    self.gateway_ip = addr[0]
                    self.gateway_id = payload.get('gateway_id')
                    self.connected = True
                    print(f"[MODEM] Connected to gateway {self.gateway_id} at {self.gateway_ip}")
                    return True
                    
            except socket.timeout:
                print(f"[MODEM] No gateway response")
                
            sock.close()
            
        except Exception as e:
            print(f"[MODEM] Announce error: {e}")
        
        return False
    
    def maintain_connection(self, callback=None):
        """Keep connection alive, call callback with stats."""
        while self.connected:
            # Send heartbeat to gateway
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                msg = AlkalineProtocol.pack(AlkalineProtocol.MSG_HEARTBEAT, {
                    "device_id": self.identity.device_id,
                    "uptime": time.time()
                })
                sock.sendto(msg, (self.gateway_ip, self.port))
                sock.close()
            except:
                pass
            
            time.sleep(30)


class GatewayListener:
    """Gateway listens for modem announcements."""
    
    def __init__(self, identity: DeviceIdentity, dashboard: DashboardClient, port: int = 5555):
        self.identity = identity
        self.dashboard = dashboard
        self.port = port
        self.connected_modems: Dict[str, dict] = {}
        self.running = False
    
    def start(self):
        """Start listening for modem announcements."""
        self.running = True
        thread = threading.Thread(target=self._listen_loop, daemon=True)
        thread.start()
        print(f"[GATEWAY] Listening for modems on port {self.port}")
    
    def _listen_loop(self):
        """Main listening loop."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', self.port))
        sock.settimeout(1)
        
        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                self._handle_message(data, addr, sock)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[GATEWAY] Listen error: {e}")
        
        sock.close()
    
    def _handle_message(self, data: bytes, addr: tuple, sock: socket.socket):
        """Handle incoming message from modem."""
        msg_type, payload = AlkalineProtocol.unpack(data)
        
        if msg_type == AlkalineProtocol.MSG_ANNOUNCE:
            # New modem announcing itself
            modem_id = payload.get('device_id')
            modem_mac = payload.get('mac')
            
            print(f"[GATEWAY] Modem announced: {modem_id} from {addr[0]}")
            
            # Send welcome response
            welcome = AlkalineProtocol.pack(AlkalineProtocol.MSG_WELCOME, {
                "gateway_id": self.identity.device_id,
                "gateway_ip": sock.getsockname()[0],
                "status": "accepted"
            })
            sock.sendto(welcome, addr)
            
            # Track connected modem
            self.connected_modems[modem_id] = {
                "mac": modem_mac,
                "ip": addr[0],
                "connected_at": time.time(),
                "last_seen": time.time()
            }
            
            # Report to dashboard
            self.dashboard.report_connected_modem(modem_id, modem_mac)
        
        elif msg_type == AlkalineProtocol.MSG_HEARTBEAT:
            # Update last seen
            modem_id = payload.get('device_id')
            if modem_id in self.connected_modems:
                self.connected_modems[modem_id]['last_seen'] = time.time()
    
    def stop(self):
        self.running = False
    
    def get_connected_count(self) -> int:
        """Get number of currently connected modems."""
        # Remove stale (>2 min no heartbeat)
        now = time.time()
        active = {k: v for k, v in self.connected_modems.items() 
                  if now - v['last_seen'] < 120}
        self.connected_modems = active
        return len(active)

# ============================================
# MAIN DEVICE CLASS
# ============================================

class AlkalineDevice:
    """
    Main device class - runs on both modems and gateways.
    Handles all networking, dashboard communication, and device-to-device protocols.
    """
    
    def __init__(self, mode: DeviceMode, dashboard_url: str, hoster_id: str = None):
        self.mode = mode
        self.identity = DeviceIdentity()
        self.dashboard = DashboardClient(dashboard_url, self.identity)
        self.hoster_id = hoster_id
        
        self.running = False
        self.stats = {
            "bytes_down": 0,
            "bytes_up": 0,
            "uptime": 0,
            "connected_modems": 0  # Gateway only
        }
        
        # Mode-specific components
        if mode == DeviceMode.GATEWAY:
            self.listener = GatewayListener(self.identity, self.dashboard)
        else:
            self.announcer = ModemAnnouncer(self.identity)
    
    def start(self):
        """Start the device."""
        print(f"\n{'='*60}")
        print(f"  ALKALINE {self.mode.value.upper()}")
        print(f"  Device ID: {self.identity.device_id}")
        print(f"  MAC: {self.identity.mac_address}")
        print(f"{'='*60}\n")
        
        self.running = True
        self.start_time = time.time()
        
        # Register with dashboard
        while not self.dashboard.register(self.mode, self.hoster_id):
            print("[DEVICE] Retrying dashboard registration in 10s...")
            time.sleep(10)
        
        if self.mode == DeviceMode.GATEWAY:
            # Start listening for modems
            self.listener.start()
        else:
            # Find and connect to gateway
            while not self.announcer.broadcast_announce():
                print("[MODEM] Retrying gateway search in 10s...")
                time.sleep(10)
        
        # Start heartbeat loop
        self._heartbeat_loop()
    
    def _heartbeat_loop(self):
        """Send periodic heartbeats to dashboard."""
        while self.running:
            # Gather stats
            self.stats["uptime"] = int(time.time() - self.start_time)
            
            if self.mode == DeviceMode.GATEWAY:
                self.stats["connected_modems"] = self.listener.get_connected_count()
            
            # Send to dashboard
            response = self.dashboard.heartbeat(self.stats)
            
            # Handle any commands from dashboard
            if response.get('command'):
                self._handle_command(response['command'])
            
            time.sleep(30)
    
    def _handle_command(self, command: dict):
        """Handle command from dashboard."""
        cmd = command.get('type')
        
        if cmd == 'reboot':
            print("[COMMAND] Rebooting...")
            os.system('reboot')
        
        elif cmd == 'update_config':
            print("[COMMAND] Config update received")
            # Apply new configuration
        
        elif cmd == 'disconnect_modem':
            modem_id = command.get('modem_id')
            print(f"[COMMAND] Disconnecting modem {modem_id}")
            # Would block modem at firewall level
    
    def stop(self):
        """Stop the device."""
        self.running = False
        if self.mode == DeviceMode.GATEWAY:
            self.listener.stop()


# ============================================
# CLI ENTRY POINT
# ============================================

if __name__ == "__main__":
    import argparse
    import signal
    import sys
    
    parser = argparse.ArgumentParser(description="Alkaline Device Software")
    parser.add_argument("mode", choices=["modem", "gateway"], 
                        help="Device mode")
    parser.add_argument("--dashboard", default="http://localhost:5000",
                        help="Dashboard URL")
    parser.add_argument("--hoster-id", default=None,
                        help="Hoster ID (gateway mode only)")
    
    args = parser.parse_args()
    
    mode = DeviceMode.MODEM if args.mode == "modem" else DeviceMode.GATEWAY
    device = AlkalineDevice(mode, args.dashboard, args.hoster_id)
    
    def signal_handler(sig, frame):
        print("\n[DEVICE] Shutting down...")
        device.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    device.start()
