"""
Alkaline Hosting - Gateway Auto-Detection System
This runs on YOUR gateway (Raspberry Pi) and automatically:
1. Detects new devices connecting to the network
2. Registers them with the dashboard
3. Routes their traffic through the proxy with QoS
4. Reports stats back to dashboard

THIS is what makes devices "automatically appear" in the dashboard.
"""

import subprocess
import threading
import time
import socket
import requests
import re
import os
import json
from datetime import datetime
from typing import Dict, Set, Optional
from dataclasses import dataclass, field

@dataclass
class DetectedDevice:
    """A device detected on the network."""
    mac_address: str
    ip_address: str
    hostname: str = "Unknown"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    bytes_down: int = 0
    bytes_up: int = 0
    registered: bool = False
    device_id: Optional[str] = None

class GatewayAutoDetect:
    """
    Runs on the Hoster's gateway device (Raspberry Pi).
    Automatically detects and registers all devices that connect.
    
    How it works:
    1. ARP scanning - detects devices on local network
    2. DHCP lease monitoring - catches new connections immediately  
    3. iptables traffic counting - tracks bandwidth per device
    4. Registers new devices with central dashboard
    5. Sends heartbeats with usage stats
    """
    
    def __init__(self, 
                 dashboard_url: str = "http://localhost:5000",
                 gateway_id: str = None,
                 interface: str = "wlan0",
                 scan_interval: int = 10):
        
        self.dashboard_url = dashboard_url.rstrip('/')
        self.gateway_id = gateway_id or self._generate_gateway_id()
        self.interface = interface
        self.scan_interval = scan_interval
        
        self.known_devices: Dict[str, DetectedDevice] = {}
        self.running = False
        
        # Get our own MAC for identification
        self.gateway_mac = self._get_own_mac()
        self.gateway_ip = self._get_own_ip()
        
        print(f"[GATEWAY] Initialized")
        print(f"  ID: {self.gateway_id}")
        print(f"  MAC: {self.gateway_mac}")
        print(f"  IP: {self.gateway_ip}")
        print(f"  Interface: {self.interface}")
        print(f"  Dashboard: {self.dashboard_url}")
    
    def _generate_gateway_id(self) -> str:
        """Generate a unique gateway ID based on MAC."""
        mac = self._get_own_mac()
        return f"GW-{mac.replace(':', '')[-6:].upper()}"
    
    def _get_own_mac(self) -> str:
        """Get this device's MAC address."""
        try:
            # Try reading from /sys
            path = f"/sys/class/net/{self.interface}/address"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip().upper()
            
            # Fallback: parse ip link
            output = subprocess.check_output(
                f"ip link show {self.interface}", 
                shell=True
            ).decode()
            match = re.search(r'link/ether ([0-9a-fA-F:]+)', output)
            if match:
                return match.group(1).upper()
        except:
            pass
        return "00:00:00:00:00:00"
    
    def _get_own_ip(self) -> str:
        """Get this device's IP address."""
        try:
            output = subprocess.check_output(
                f"ip addr show {self.interface}", 
                shell=True
            ).decode()
            match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', output)
            if match:
                return match.group(1)
        except:
            pass
        return "0.0.0.0"
    
    def _get_network_range(self) -> str:
        """Get the network range to scan (e.g., 192.168.1.0/24)."""
        ip = self._get_own_ip()
        if ip == "0.0.0.0":
            return "192.168.1.0/24"  # Default fallback
        
        # Assume /24 subnet
        parts = ip.split('.')
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    
    # =========================================
    # DEVICE DETECTION METHODS
    # =========================================
    
    def scan_arp_table(self) -> Dict[str, str]:
        """
        Read ARP table to find devices on network.
        Returns {mac: ip} mapping.
        """
        devices = {}
        try:
            # Read /proc/net/arp
            with open('/proc/net/arp', 'r') as f:
                lines = f.readlines()[1:]  # Skip header
            
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[0]
                    mac = parts[3].upper()
                    
                    # Skip incomplete entries and our own MAC
                    if mac != "00:00:00:00:00:00" and mac != self.gateway_mac:
                        devices[mac] = ip
        except Exception as e:
            print(f"[ARP] Error reading ARP table: {e}")
        
        return devices
    
    def scan_dhcp_leases(self) -> Dict[str, dict]:
        """
        Read DHCP leases to get hostnames.
        Works with dnsmasq (common on Raspberry Pi).
        Returns {mac: {ip, hostname}} mapping.
        """
        devices = {}
        lease_files = [
            '/var/lib/misc/dnsmasq.leases',
            '/var/lib/dhcp/dhcpd.leases',
            '/tmp/dhcp.leases',
        ]
        
        for lease_file in lease_files:
            if os.path.exists(lease_file):
                try:
                    with open(lease_file, 'r') as f:
                        for line in f:
                            parts = line.strip().split()
                            if len(parts) >= 4:
                                # dnsmasq format: timestamp mac ip hostname clientid
                                mac = parts[1].upper()
                                ip = parts[2]
                                hostname = parts[3] if parts[3] != '*' else 'Unknown'
                                devices[mac] = {'ip': ip, 'hostname': hostname}
                except Exception as e:
                    print(f"[DHCP] Error reading {lease_file}: {e}")
        
        return devices
    
    def get_hostname_from_ip(self, ip: str) -> str:
        """Try to resolve hostname from IP."""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
            return hostname.split('.')[0]  # Just the hostname part
        except:
            return "Unknown"
    
    def get_traffic_stats(self, ip: str) -> tuple:
        """
        Get traffic stats for a specific IP using iptables.
        Returns (bytes_in, bytes_out).
        """
        try:
            # Check if we have iptables rules for this IP
            # This requires setting up accounting rules first
            output = subprocess.check_output(
                f"iptables -L -v -n -x 2>/dev/null | grep {ip}",
                shell=True
            ).decode()
            
            bytes_in = 0
            bytes_out = 0
            
            for line in output.split('\n'):
                if ip in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        # Column 2 is bytes
                        try:
                            bytes_count = int(parts[1])
                            if 'destination' in line.lower() or parts[-1] == ip:
                                bytes_in += bytes_count
                            else:
                                bytes_out += bytes_count
                        except:
                            pass
            
            return bytes_in, bytes_out
        except:
            return 0, 0
    
    def setup_iptables_accounting(self, ip: str):
        """Set up iptables rules to count traffic for an IP."""
        try:
            # Add accounting rules (if not already present)
            subprocess.run(
                f"iptables -C FORWARD -s {ip} 2>/dev/null || iptables -A FORWARD -s {ip}",
                shell=True
            )
            subprocess.run(
                f"iptables -C FORWARD -d {ip} 2>/dev/null || iptables -A FORWARD -d {ip}",
                shell=True
            )
        except:
            pass
    
    # =========================================
    # DASHBOARD COMMUNICATION
    # =========================================
    
    def register_gateway(self) -> bool:
        """Register this gateway with the dashboard."""
        try:
            response = requests.post(
                f"{self.dashboard_url}/api/device/register",
                json={
                    "mac_address": self.gateway_mac,
                    "ip_address": self.gateway_ip,
                    "hostname": socket.gethostname(),
                    "device_type": "gateway"
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"[GATEWAY] Registered with dashboard: {data.get('device_id')}")
                return True
        except Exception as e:
            print(f"[GATEWAY] Failed to register: {e}")
        
        return False
    
    def register_device(self, device: DetectedDevice) -> bool:
        """Register a detected device with the dashboard."""
        try:
            response = requests.post(
                f"{self.dashboard_url}/api/device/register",
                json={
                    "mac_address": device.mac_address,
                    "ip_address": device.ip_address,
                    "hostname": device.hostname,
                    "device_type": "user",
                    "gateway_id": self.gateway_id
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                device.device_id = data.get('device_id')
                device.registered = True
                
                if data.get('is_new'):
                    print(f"[NEW DEVICE] {device.hostname} ({device.mac_address}) -> {device.device_id}")
                
                return True
        except Exception as e:
            print(f"[REGISTER] Failed for {device.mac_address}: {e}")
        
        return False
    
    def send_heartbeat(self, device: DetectedDevice) -> bool:
        """Send heartbeat with usage stats for a device."""
        try:
            bytes_in, bytes_out = self.get_traffic_stats(device.ip_address)
            
            # Calculate delta since last report
            delta_down = max(0, bytes_in - device.bytes_down)
            delta_up = max(0, bytes_out - device.bytes_up)
            
            device.bytes_down = bytes_in
            device.bytes_up = bytes_out
            
            response = requests.post(
                f"{self.dashboard_url}/api/device/heartbeat",
                json={
                    "mac_address": device.mac_address,
                    "bytes_down": delta_down,
                    "bytes_up": delta_up,
                    "signal_strength": 0
                },
                timeout=5
            )
            
            return response.status_code == 200
        except:
            return False
    
    # =========================================
    # MAIN DETECTION LOOP
    # =========================================
    
    def scan_and_register(self):
        """Perform one scan cycle - detect devices and register new ones."""
        # Get current ARP table
        arp_devices = self.scan_arp_table()
        
        # Get DHCP info for hostnames
        dhcp_info = self.scan_dhcp_leases()
        
        # Process each detected device
        for mac, ip in arp_devices.items():
            if mac not in self.known_devices:
                # New device detected!
                hostname = "Unknown"
                
                # Try to get hostname from DHCP
                if mac in dhcp_info:
                    hostname = dhcp_info[mac].get('hostname', 'Unknown')
                
                # Fallback: reverse DNS
                if hostname == "Unknown":
                    hostname = self.get_hostname_from_ip(ip)
                
                # Create device record
                device = DetectedDevice(
                    mac_address=mac,
                    ip_address=ip,
                    hostname=hostname
                )
                
                self.known_devices[mac] = device
                
                # Set up traffic accounting
                self.setup_iptables_accounting(ip)
                
                # Register with dashboard
                self.register_device(device)
            
            else:
                # Known device - update last seen and send heartbeat
                device = self.known_devices[mac]
                device.last_seen = time.time()
                device.ip_address = ip  # IP might have changed
                
                # Send heartbeat every scan
                self.send_heartbeat(device)
        
        # Check for devices that went offline (not seen in 5 minutes)
        now = time.time()
        offline_threshold = 300  # 5 minutes
        
        for mac, device in list(self.known_devices.items()):
            if now - device.last_seen > offline_threshold:
                print(f"[OFFLINE] {device.hostname} ({mac})")
                # Don't remove - they might come back
    
    def run(self):
        """Start the auto-detection loop."""
        print(f"\n{'='*60}")
        print("  ALKALINE GATEWAY - Auto-Detection Running")
        print(f"{'='*60}")
        print(f"  Scanning {self.interface} every {self.scan_interval}s")
        print(f"  New devices will appear in dashboard automatically")
        print(f"{'='*60}\n")
        
        self.running = True
        
        # Register ourselves first
        self.register_gateway()
        
        # Main loop
        while self.running:
            try:
                self.scan_and_register()
            except Exception as e:
                print(f"[ERROR] Scan failed: {e}")
            
            time.sleep(self.scan_interval)
    
    def stop(self):
        """Stop the auto-detection loop."""
        self.running = False
        print("[GATEWAY] Stopping...")


class GatewayWithProxy(GatewayAutoDetect):
    """
    Extended gateway that also runs the proxy for traffic routing.
    This combines auto-detection with actual internet sharing.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proxy_port = 8888
        self.dns_port = 53
    
    def setup_nat(self):
        """Set up NAT/masquerading to share internet connection."""
        try:
            # Enable IP forwarding
            subprocess.run("echo 1 > /proc/sys/net/ipv4/ip_forward", shell=True)
            
            # Set up NAT (assuming eth0 is upstream, wlan0 is downstream)
            commands = [
                "iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE",
                "iptables -A FORWARD -i eth0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT",
                "iptables -A FORWARD -i wlan0 -o eth0 -j ACCEPT",
            ]
            
            for cmd in commands:
                subprocess.run(cmd, shell=True)
            
            print("[NAT] Internet sharing enabled")
            return True
        except Exception as e:
            print(f"[NAT] Setup failed: {e}")
            return False
    
    def setup_transparent_proxy(self):
        """Redirect all HTTP/HTTPS traffic through our proxy."""
        try:
            # Redirect port 80 to proxy
            subprocess.run(
                f"iptables -t nat -A PREROUTING -i {self.interface} -p tcp --dport 80 "
                f"-j REDIRECT --to-port {self.proxy_port}",
                shell=True
            )
            
            # For HTTPS, we need to do DNS-based filtering or MITM (not recommended)
            # Instead, we'll rely on the proxy being configured on devices
            
            print(f"[PROXY] Transparent proxy enabled on port {self.proxy_port}")
            return True
        except Exception as e:
            print(f"[PROXY] Setup failed: {e}")
            return False


# =========================================
# CLI
# =========================================

if __name__ == "__main__":
    import argparse
    import signal
    import sys
    
    parser = argparse.ArgumentParser(description="Alkaline Gateway Auto-Detection")
    parser.add_argument("--dashboard", default="http://localhost:5000", 
                        help="Dashboard server URL")
    parser.add_argument("--interface", default="wlan0", 
                        help="Network interface to monitor")
    parser.add_argument("--interval", type=int, default=10, 
                        help="Scan interval in seconds")
    parser.add_argument("--with-proxy", action="store_true",
                        help="Also set up NAT and proxy")
    
    args = parser.parse_args()
    
    if args.with_proxy:
        gateway = GatewayWithProxy(
            dashboard_url=args.dashboard,
            interface=args.interface,
            scan_interval=args.interval
        )
        gateway.setup_nat()
        gateway.setup_transparent_proxy()
    else:
        gateway = GatewayAutoDetect(
            dashboard_url=args.dashboard,
            interface=args.interface,
            scan_interval=args.interval
        )
    
    # Handle Ctrl+C
    def signal_handler(sig, frame):
        gateway.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    gateway.run()
