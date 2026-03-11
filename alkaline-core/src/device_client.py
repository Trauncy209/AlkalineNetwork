"""
Alkaline Hosting - Device Client
Runs on customer modems/gateways to auto-register and report stats.
"""

import socket
import requests
import time
import uuid
import subprocess
import platform
import threading
import json
import os

class AlkalineClient:
    """
    Client that runs on each Alkaline device (modem/gateway).
    Automatically registers with the dashboard and sends heartbeats.
    """
    
    def __init__(self, server_url: str = "http://localhost:5000", 
                 device_type: str = "user"):
        self.server_url = server_url.rstrip('/')
        self.device_type = device_type  # "user" or "gateway"
        self.device_id = None
        self.mac_address = self.get_mac_address()
        self.hostname = self.get_hostname()
        self.running = False
        
        # Stats tracking
        self.bytes_down = 0
        self.bytes_up = 0
        self.last_bytes_down = 0
        self.last_bytes_up = 0
    
    def get_mac_address(self) -> str:
        """Get the device's MAC address."""
        try:
            # Try to get the MAC of the primary interface
            if platform.system() == "Windows":
                output = subprocess.check_output("getmac", shell=True).decode()
                for line in output.split('\n'):
                    if '-' in line:
                        mac = line.split()[0].replace('-', ':')
                        if mac != 'N/A':
                            return mac.upper()
            else:
                # Linux/Mac
                output = subprocess.check_output("ip link show", shell=True).decode()
                for line in output.split('\n'):
                    if 'link/ether' in line:
                        mac = line.split()[1].upper()
                        if mac != '00:00:00:00:00:00':
                            return mac
        except:
            pass
        
        # Fallback: generate a pseudo-MAC based on machine ID
        machine_id = hex(uuid.getnode())[2:].upper()
        return ':'.join(machine_id[i:i+2] for i in range(0, 12, 2))
    
    def get_hostname(self) -> str:
        """Get the device hostname."""
        try:
            return socket.gethostname()
        except:
            return "Unknown"
    
    def get_ip_address(self) -> str:
        """Get the device's IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "0.0.0.0"
    
    def get_signal_strength(self) -> int:
        """Get WiFi signal strength (if applicable)."""
        try:
            if platform.system() == "Linux":
                output = subprocess.check_output(
                    "iwconfig 2>/dev/null | grep 'Signal level'", 
                    shell=True
                ).decode()
                # Parse signal level
                if 'Signal level=' in output:
                    level = output.split('Signal level=')[1].split()[0]
                    return int(level.replace('dBm', ''))
        except:
            pass
        return 0
    
    def get_network_stats(self) -> tuple:
        """Get bytes sent/received since last check."""
        try:
            if platform.system() == "Linux":
                with open('/proc/net/dev', 'r') as f:
                    lines = f.readlines()
                
                total_rx = 0
                total_tx = 0
                
                for line in lines[2:]:  # Skip headers
                    parts = line.split()
                    if len(parts) >= 10:
                        iface = parts[0].rstrip(':')
                        if iface not in ['lo']:  # Exclude loopback
                            total_rx += int(parts[1])
                            total_tx += int(parts[9])
                
                # Calculate delta
                rx_delta = total_rx - self.last_bytes_down
                tx_delta = total_tx - self.last_bytes_up
                
                self.last_bytes_down = total_rx
                self.last_bytes_up = total_tx
                
                # First call, no delta
                if self.bytes_down == 0:
                    return 0, 0
                
                return max(0, rx_delta), max(0, tx_delta)
        except:
            pass
        
        return 0, 0
    
    def register(self) -> bool:
        """Register this device with the dashboard."""
        try:
            response = requests.post(
                f"{self.server_url}/api/device/register",
                json={
                    "mac_address": self.mac_address,
                    "ip_address": self.get_ip_address(),
                    "hostname": self.hostname,
                    "device_type": self.device_type
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    self.device_id = data.get('device_id')
                    is_new = data.get('is_new', False)
                    print(f"[REGISTERED] Device ID: {self.device_id} (new: {is_new})")
                    return True
            
            print(f"[ERROR] Registration failed: {response.text}")
            return False
            
        except requests.exceptions.ConnectionError:
            print(f"[ERROR] Cannot connect to {self.server_url}")
            return False
        except Exception as e:
            print(f"[ERROR] Registration error: {e}")
            return False
    
    def heartbeat(self) -> bool:
        """Send heartbeat with current stats."""
        bytes_down, bytes_up = self.get_network_stats()
        
        try:
            response = requests.post(
                f"{self.server_url}/api/device/heartbeat",
                json={
                    "mac_address": self.mac_address,
                    "bytes_down": bytes_down,
                    "bytes_up": bytes_up,
                    "signal_strength": self.get_signal_strength()
                },
                timeout=5
            )
            return response.status_code == 200
        except:
            return False
    
    def run(self, heartbeat_interval: int = 30):
        """Start the client - register and send periodic heartbeats."""
        print(f"[ALKALINE CLIENT] Starting...")
        print(f"  MAC: {self.mac_address}")
        print(f"  Hostname: {self.hostname}")
        print(f"  Type: {self.device_type}")
        print(f"  Server: {self.server_url}")
        print()
        
        self.running = True
        
        # Register
        while self.running:
            if self.register():
                break
            print("[RETRY] Retrying registration in 10 seconds...")
            time.sleep(10)
        
        # Heartbeat loop
        while self.running:
            if self.heartbeat():
                print(f"[HEARTBEAT] OK - {self.device_id}")
            else:
                print(f"[HEARTBEAT] Failed - will retry")
            
            time.sleep(heartbeat_interval)
    
    def stop(self):
        """Stop the client."""
        self.running = False


class AlkalineGateway(AlkalineClient):
    """
    Gateway client - for Hosters.
    Extends the basic client with gateway-specific features.
    """
    
    def __init__(self, server_url: str = "http://localhost:5000"):
        super().__init__(server_url, device_type="gateway")
        self.connected_clients = {}
    
    def get_connected_clients(self) -> list:
        """Get list of devices connected through this gateway."""
        # This would integrate with the actual network stack
        # For now, return empty list
        return list(self.connected_clients.keys())


# Daemon mode for running as a service
def run_daemon(server_url: str, device_type: str = "user"):
    """Run the client as a background daemon."""
    import signal
    import sys
    
    if device_type == "gateway":
        client = AlkalineGateway(server_url)
    else:
        client = AlkalineClient(server_url, device_type)
    
    def signal_handler(sig, frame):
        print("\n[STOPPING] Received shutdown signal...")
        client.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    client.run()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Alkaline Device Client")
    parser.add_argument(
        "--server", 
        default="http://localhost:5000",
        help="Dashboard server URL"
    )
    parser.add_argument(
        "--type",
        choices=["user", "gateway"],
        default="user",
        help="Device type"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Heartbeat interval in seconds"
    )
    
    args = parser.parse_args()
    
    print()
    print("=" * 50)
    print("  ⚡ ALKALINE DEVICE CLIENT")
    print("=" * 50)
    print()
    
    run_daemon(args.server, args.type)
