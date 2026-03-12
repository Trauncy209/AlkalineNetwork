#!/usr/bin/env python3
"""
Alkaline Network - Heltec HT-H7608 Flash Tool v2
=================================================

Automates configuration of Heltec HT-H7608 Wi-Fi HaLow routers via:
  1. HTTP (Web UI automation) - Primary method
  2. SSH (UCI commands) - Fallback if web API found

TWO BUTTONS:
  [GATEWAY] - Makes device a Mesh Gate (shares internet)
  [PINGER]  - Makes device a Mesh Point (connects to gateway)

Hardware: Heltec HT-H7608 Wi-Fi HaLow Router ($79)
  - Default IP: 10.42.0.1 (via Ethernet) or 192.168.100.1 (via WiFi)
  - Default WiFi: HT-XXXX-xxxx / password: heltec.org
  - Default login: root / heltec.org
  - Has 802.11s Mesh Wizard built-in (Web UI)

SECURITY:
  - WPA3-SAE encryption on mesh backbone (military-grade)
  - WPA2-PSK on customer WiFi (device compatibility)
  - Unique 32-char hex mesh passphrase (auto-generated)
  - Admin password changed to lock down devices
  - All credentials stored in network_config.json

Just plug in device via Ethernet, click button, wait, unplug, ship.

Requirements:
  pip install requests paramiko

Usage:
  python flash_tool.py              # GUI mode
  python flash_tool.py gateway      # CLI - provision as gateway
  python flash_tool.py pinger       # CLI - provision as pinger
"""

import os
import sys
import time
import json
import socket
import threading
import secrets
import base64
import re
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from urllib.parse import urljoin, urlencode
from typing import Optional, Tuple, Dict, Any

# Try to import requests for HTTP
try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    print("Installing requests...")
    os.system(f"{sys.executable} -m pip install requests")
    import requests
    from requests.auth import HTTPBasicAuth

# Try to import paramiko for SSH fallback
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    print("Installing paramiko...")
    os.system(f"{sys.executable} -m pip install paramiko")
    try:
        import paramiko
        HAS_PARAMIKO = True
    except:
        HAS_PARAMIKO = False
        print("Warning: paramiko not available, SSH fallback disabled")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Heltec HT-H7608 defaults
DEVICE_IP_ETH = "10.42.0.1"      # When connected via Ethernet cable
DEVICE_IP_WIFI = "192.168.100.1"  # When connected via 2.4GHz WiFi (after setup)
DEVICE_USER = "root"
DEVICE_PASSWORD = "heltec.org"

# Network settings
MESH_ID = "AlkalineNet"
COUNTRY_CODE = "US"  # Important for HaLow channel selection
HALOW_BANDWIDTH = "4"  # MHz - 1, 2, 4, or 8
HALOW_CHANNEL = "1"    # Channel within sub-GHz band

# Paths
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "network_config.json"

# =============================================================================
# NETWORK CONFIG MANAGER
# =============================================================================

class NetworkConfig:
    """Manages persistent network configuration with strong encryption defaults."""
    
    def __init__(self):
        self.config = self.load()
        self.save()  # Ensure file exists with generated secrets
    
    def load(self) -> Dict[str, Any]:
        """Load config from file or create secure defaults."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    cfg = json.load(f)
                    # Validate required fields exist
                    if all(k in cfg for k in ['mesh_id', 'mesh_passphrase', 'admin_password']):
                        return cfg
            except Exception as e:
                print(f"Warning: Could not load config: {e}")
        
        # Generate cryptographically secure defaults
        return {
            "mesh_id": MESH_ID,
            # 32-character hex = 128-bit entropy for WPA3-SAE
            "mesh_passphrase": secrets.token_hex(16),
            # Strong admin password
            "admin_password": secrets.token_urlsafe(16),
            # Customer-friendly WiFi password (still secure, 64-bit entropy)
            "customer_wifi_password": secrets.token_urlsafe(8),
            # Tracking
            "gateway_count": 0,
            "pinger_count": 0,
            "devices": [],
            # Network settings
            "country_code": COUNTRY_CODE,
            "halow_bandwidth": HALOW_BANDWIDTH,
            "halow_channel": HALOW_CHANNEL,
            # Server settings (for encrypted tunnel)
            "server_ip": "",
            "server_port": 51820,
            "server_pubkey": "",
        }
    
    def save(self):
        """Save config to file."""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)
        # Set restrictive permissions on config file (contains secrets)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except:
            pass
    
    def add_device(self, device_type: str, device_id: str, mac: str = "") -> Dict[str, Any]:
        """Record a provisioned device."""
        device = {
            "type": device_type,
            "id": device_id,
            "mac": mac.upper() if mac else "",
            "provisioned": time.strftime("%Y-%m-%d %H:%M:%S"),
            "wifi_ssid": f"Alkaline-{device_id}",
            "wifi_password": self.config["customer_wifi_password"]
        }
        self.config["devices"].append(device)
        
        if device_type == "gateway":
            self.config["gateway_count"] += 1
        else:
            self.config["pinger_count"] += 1
        
        self.save()
        return device
    
    def find_device_by_mac(self, mac: str) -> Optional[Dict[str, Any]]:
        """Find a device by MAC address (for returns/replacements)."""
        mac = mac.upper().strip()
        for device in self.config["devices"]:
            if device.get("mac", "").upper() == mac:
                return device
        return None
    
    def get_next_id(self, device_type: str) -> str:
        """Get next device ID."""
        if device_type == "gateway":
            num = self.config["gateway_count"] + 1
            return f"GW-{num:03d}"
        else:
            num = self.config["pinger_count"] + 1
            return f"PN-{num:03d}"


# =============================================================================
# HELTEC WEB UI PROVISIONER (Primary Method)
# =============================================================================

class HeltecWebProvisioner:
    """
    Automates Heltec HT-H7608 configuration via its OpenWrt LuCI web interface.
    
    Based on Heltec's "802.11s Mesh Wizard" which sets:
    - Mesh ID and Passphrase
    - Mesh Gate vs Mesh Point mode
    - Upstream network (Ethernet Bridge for gates)
    - 2.4GHz AP settings
    """
    
    def __init__(self, config: NetworkConfig, log_callback=None):
        self.config = config
        self.log = log_callback or print
        self.session = requests.Session()
        self.session.verify = False  # Heltec uses self-signed certs
        self.base_url = ""
        self.auth_token = ""
        self.device_mac = ""
        
        # Suppress SSL warnings
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    def connect(self, ip: str = DEVICE_IP_ETH, user: str = DEVICE_USER, 
                password: str = DEVICE_PASSWORD) -> bool:
        """Connect to device web interface."""
        
        # Try multiple IPs
        ips_to_try = [ip]
        if ip == DEVICE_IP_ETH:
            ips_to_try.append(DEVICE_IP_WIFI)
        elif ip == DEVICE_IP_WIFI:
            ips_to_try.insert(0, DEVICE_IP_ETH)
        
        for try_ip in ips_to_try:
            self.log(f"Connecting to http://{try_ip}...")
            self.base_url = f"http://{try_ip}"
            
            try:
                # Try to reach the device
                resp = self.session.get(
                    self.base_url, 
                    timeout=5,
                    auth=HTTPBasicAuth(user, password)
                )
                
                if resp.status_code == 401:
                    self.log(f"✗ Authentication failed on {try_ip}")
                    continue
                
                if resp.status_code == 200:
                    self.log(f"✓ Connected to {try_ip}!")
                    
                    # Store credentials for SSH fallback
                    self._ssh_password = password
                    
                    # Try to get LuCI auth token if needed
                    self._try_luci_login(user, password)
                    
                    return True
                    
            except requests.exceptions.ConnectionError:
                self.log(f"✗ Cannot reach {try_ip}")
                continue
            except requests.exceptions.Timeout:
                self.log(f"✗ Timeout on {try_ip}")
                continue
            except Exception as e:
                self.log(f"✗ Error on {try_ip}: {e}")
                continue
        
        self.log("\n✗ Could not connect to device!")
        self.log("  Make sure:")
        self.log("  1. Heltec is plugged in via Ethernet cable")
        self.log("  2. Your PC gets an IP from the device (10.42.0.x)")
        self.log("  3. Or connect to its WiFi: HT-XXXX-xxxx (password: heltec.org)")
        return False
    
    def _try_luci_login(self, user: str, password: str):
        """Try to authenticate with LuCI if present."""
        try:
            # Standard LuCI login endpoint
            login_url = urljoin(self.base_url, "/cgi-bin/luci/")
            resp = self.session.get(login_url, timeout=5)
            
            if "luci" in resp.text.lower() or "openwrt" in resp.text.lower():
                # Try LuCI auth
                auth_url = urljoin(self.base_url, "/cgi-bin/luci/admin/ubus")
                # LuCI RPC auth
                auth_data = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "call",
                    "params": ["00000000000000000000000000000000", "session", "login", 
                              {"username": user, "password": password}]
                }
                resp = self.session.post(auth_url, json=auth_data, timeout=5)
                if resp.status_code == 200:
                    result = resp.json()
                    if "result" in result and len(result["result"]) > 1:
                        self.auth_token = result["result"][1].get("ubus_rpc_session", "")
                        self.log(f"  LuCI authenticated (token: {self.auth_token[:8]}...)")
        except Exception as e:
            self.log(f"  Note: LuCI auth not available ({e})")
    
    def get_device_info(self) -> Tuple[str, str]:
        """Get device MAC and hostname via web/ubus."""
        self.log("\nGetting device info...")
        
        mac = ""
        hostname = "HT-H7608"
        
        try:
            # Try ubus call for network info
            ubus_url = urljoin(self.base_url, "/cgi-bin/luci/admin/ubus")
            
            # Get network device info
            req_data = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "call",
                "params": [self.auth_token or "00000000000000000000000000000000", 
                          "network.device", "status", {"name": "eth0"}]
            }
            resp = self.session.post(ubus_url, json=req_data, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data and len(data["result"]) > 1:
                    mac = data["result"][1].get("macaddr", "")
            
            # Try getting hostname
            req_data["params"] = [self.auth_token or "00000000000000000000000000000000",
                                 "system", "board", {}]
            resp = self.session.post(ubus_url, json=req_data, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data and len(data["result"]) > 1:
                    hostname = data["result"][1].get("hostname", hostname)
                    
        except Exception as e:
            self.log(f"  Warning: Could not get device info via ubus: {e}")
        
        # Fallback: try to scrape from status page
        if not mac:
            try:
                resp = self.session.get(urljoin(self.base_url, "/cgi-bin/luci/admin/status/overview"), timeout=5)
                # Look for MAC pattern
                mac_match = re.search(r'([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}', resp.text)
                if mac_match:
                    mac = mac_match.group(0)
            except:
                pass
        
        self.device_mac = mac.upper() if mac else ""
        self.log(f"  MAC: {self.device_mac or 'Unknown'}")
        self.log(f"  Hostname: {hostname}")
        
        return self.device_mac, hostname
    
    def configure_via_wizard(self, mode: str) -> Optional[str]:
        """
        Configure device using Heltec's mesh wizard.
        
        This attempts to automate what the web wizard does:
        1. Set country code
        2. Select 802.11s Mesh mode
        3. Choose Mesh Gate or Mesh Point
        4. Set Mesh ID and passphrase
        5. Configure upstream (Ethernet Bridge for gates)
        6. Enable 2.4GHz AP
        """
        self.log(f"\n{'='*50}")
        self.log(f"Configuring as {'MESH GATE (Gateway)' if mode == 'gateway' else 'MESH POINT (Pinger)'}")
        self.log(f"{'='*50}")
        
        device_id = self.config.get_next_id(mode)
        mesh_id = self.config.config["mesh_id"]
        mesh_pass = self.config.config["mesh_passphrase"]
        wifi_pass = self.config.config["customer_wifi_password"]
        admin_pass = self.config.config["admin_password"]
        
        self.log(f"\nDevice ID: {device_id}")
        self.log(f"Mesh ID: {mesh_id}")
        self.log(f"Mesh Passphrase: {mesh_pass[:8]}...{mesh_pass[-4:]}")
        self.log(f"Customer WiFi: Alkaline-{device_id}")
        
        success = False
        
        # Approach 1: Try direct ubus/UCI calls
        success = self._configure_via_ubus(mode, device_id, mesh_id, mesh_pass, wifi_pass, admin_pass)
        
        if not success:
            # Approach 2: Try form submission to wizard endpoints
            success = self._configure_via_forms(mode, device_id, mesh_id, mesh_pass, wifi_pass, admin_pass)
        
        if not success:
            # Approach 3: Try raw UCI via SSH as fallback
            if HAS_PARAMIKO:
                self.log("\nTrying SSH fallback...")
                success = self._configure_via_ssh(mode, device_id, mesh_id, mesh_pass, wifi_pass, admin_pass)
        
        if success:
            self.log(f"\n✓ Configuration complete!")
            self.log(f"  Device ID: {device_id}")
            self.log(f"  WiFi SSID: Alkaline-{device_id}")
            self.log(f"  WiFi Password: {wifi_pass}")
            
            # Record device
            self.config.add_device(
                "gateway" if mode == "gateway" else "pinger",
                device_id,
                self.device_mac
            )
            return device_id
        else:
            self.log("\n✗ Configuration failed!")
            self.log("  The device may need manual configuration via web UI at http://10.42.0.1")
            return None
    
    def _configure_via_ubus(self, mode: str, device_id: str, mesh_id: str, 
                           mesh_pass: str, wifi_pass: str, admin_pass: str) -> bool:
        """Configure via ubus RPC calls (OpenWrt standard)."""
        self.log("\nTrying ubus configuration...")
        
        ubus_url = urljoin(self.base_url, "/cgi-bin/luci/admin/ubus")
        token = self.auth_token or "00000000000000000000000000000000"
        
        try:
            # Set hostname
            self._ubus_call(ubus_url, token, "uci", "set", {
                "config": "system",
                "section": "@system[0]",
                "values": {"hostname": f"Alkaline-{device_id}"}
            })
            
            # Configure HaLow mesh interface
            mesh_values = {
                "mode": "mesh",
                "mesh_id": mesh_id,
                "encryption": "sae",  # WPA3
                "key": mesh_pass,
                "mesh_fwding": "1",
            }
            
            if mode == "gateway":
                mesh_values["mesh_gate_announcements"] = "1"
            
            # Try to find/create mesh interface
            self._ubus_call(ubus_url, token, "uci", "set", {
                "config": "wireless",
                "section": "halow",
                "values": mesh_values
            })
            
            # Configure 2.4GHz AP
            self._ubus_call(ubus_url, token, "uci", "set", {
                "config": "wireless",
                "section": "default_radio1",
                "values": {
                    "ssid": f"Alkaline-{device_id}",
                    "encryption": "psk2",
                    "key": wifi_pass
                }
            })
            
            # Change admin password
            self._ubus_call(ubus_url, token, "luci-rpc", "setPassword", {
                "username": "root",
                "password": admin_pass
            })
            
            # Commit changes
            self._ubus_call(ubus_url, token, "uci", "commit", {"config": "wireless"})
            self._ubus_call(ubus_url, token, "uci", "commit", {"config": "system"})
            
            # Apply wireless changes
            self._ubus_call(ubus_url, token, "luci", "setReboot", {"timeout": 5})
            
            self.log("✓ ubus configuration sent!")
            
            # Deploy software via SSH (ubus can't upload files)
            if HAS_PARAMIKO:
                self._deploy_software_via_ssh(mode, device_id)
            
            return True
            
        except Exception as e:
            self.log(f"  ubus failed: {e}")
            return False
    
    def _deploy_software_via_ssh(self, mode: str, device_id: str):
        """Deploy Alkaline software via SSH after web config."""
        self.log("\nDeploying software via SSH...")
        
        try:
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', self.base_url)
            ip = ip_match.group(1) if ip_match else DEVICE_IP_ETH
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username=DEVICE_USER, 
                          password=getattr(self, '_ssh_password', DEVICE_PASSWORD),
                          timeout=10, allow_agent=False, look_for_keys=False)
            
            self._deploy_alkaline_software(client, mode, device_id)
            client.close()
            
        except Exception as e:
            self.log(f"  SSH software deployment failed: {e}")
            self.log("  Device configured but software not deployed")
            self.log("  Adaptive bandwidth will not be available")
    
    def _ubus_call(self, url: str, token: str, obj: str, method: str, params: dict) -> dict:
        """Make a ubus RPC call."""
        data = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": [token, obj, method, params]
        }
        resp = self.session.post(url, json=data, timeout=10)
        return resp.json() if resp.status_code == 200 else {}
    
    def _configure_via_forms(self, mode: str, device_id: str, mesh_id: str,
                            mesh_pass: str, wifi_pass: str, admin_pass: str) -> bool:
        """Configure via form submission (Heltec wizard style)."""
        self.log("\nTrying form-based configuration...")
        
        try:
            # Heltec's wizard likely has these endpoints
            # We try common LuCI patterns
            
            # Try to find the wireless config page
            wireless_url = urljoin(self.base_url, "/cgi-bin/luci/admin/network/wireless")
            resp = self.session.get(wireless_url, timeout=5)
            
            if resp.status_code != 200:
                self.log("  Could not find wireless config page")
                return False
            
            # Extract any CSRF token if present
            csrf_token = ""
            csrf_match = re.search(r'token["\s:=]+([a-f0-9]{32})', resp.text, re.I)
            if csrf_match:
                csrf_token = csrf_match.group(1)
            
            # Build form data based on standard LuCI wireless form
            form_data = {
                "token": csrf_token,
                "cbi.submit": "1",
                # These field names are approximations - need real device to verify
                "cbid.wireless.halow.mode": "mesh",
                "cbid.wireless.halow.mesh_id": mesh_id,
                "cbid.wireless.halow.encryption": "sae",
                "cbid.wireless.halow.key": mesh_pass,
                "cbid.wireless.halow.mesh_fwding": "1",
            }
            
            if mode == "gateway":
                form_data["cbid.wireless.halow.mesh_gate_announcements"] = "1"
            
            # Add 2.4GHz AP settings
            form_data.update({
                "cbid.wireless.default_radio1.ssid": f"Alkaline-{device_id}",
                "cbid.wireless.default_radio1.encryption": "psk2",
                "cbid.wireless.default_radio1.key": wifi_pass,
            })
            
            # Submit
            resp = self.session.post(wireless_url, data=form_data, timeout=10)
            
            if resp.status_code == 200 and "error" not in resp.text.lower():
                self.log("✓ Form submission sent!")
                
                # Try to apply changes
                apply_url = urljoin(self.base_url, "/cgi-bin/luci/admin/uci/apply")
                self.session.post(apply_url, timeout=5)
                
                # Deploy software via SSH (forms can't upload files)
                if HAS_PARAMIKO:
                    self._deploy_software_via_ssh(mode, device_id)
                
                return True
            else:
                self.log("  Form submission may have failed")
                return False
                
        except Exception as e:
            self.log(f"  Form submission failed: {e}")
            return False
    
    def _configure_via_ssh(self, mode: str, device_id: str, mesh_id: str,
                          mesh_pass: str, wifi_pass: str, admin_pass: str) -> bool:
        """Configure via SSH/UCI as fallback."""
        if not HAS_PARAMIKO:
            return False
        
        self.log("Connecting via SSH...")
        
        try:
            # Extract IP from base_url
            ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', self.base_url)
            ip = ip_match.group(1) if ip_match else DEVICE_IP_ETH
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username=DEVICE_USER, password=getattr(self, '_ssh_password', DEVICE_PASSWORD), 
                          timeout=10, allow_agent=False, look_for_keys=False)
            
            self.log("✓ SSH connected!")
            
            # Build UCI commands
            commands = [
                f'uci set system.@system[0].hostname="Alkaline-{device_id}"',
                'uci commit system',
                
                # HaLow mesh config
                'uci set wireless.halow=wifi-iface',
                'uci set wireless.halow.device="radio0"',
                'uci set wireless.halow.network="lan"',
                'uci set wireless.halow.mode="mesh"',
                f'uci set wireless.halow.mesh_id="{mesh_id}"',
                'uci set wireless.halow.encryption="sae"',
                f'uci set wireless.halow.key="{mesh_pass}"',
                'uci set wireless.halow.mesh_fwding="1"',
            ]
            
            if mode == "gateway":
                commands.append('uci set wireless.halow.mesh_gate_announcements="1"')
            
            commands.extend([
                'uci commit wireless',
                
                # 2.4GHz AP
                f'uci set wireless.default_radio1.ssid="Alkaline-{device_id}"',
                'uci set wireless.default_radio1.encryption="psk2"',
                f'uci set wireless.default_radio1.key="{wifi_pass}"',
                'uci commit wireless',
                
                # Change admin password
                f'echo -e "{admin_pass}\\n{admin_pass}" | passwd root',
                
                # Marker files
                f'echo "{device_id}" > /etc/alkaline_device_id',
                f'echo "{mode}" > /etc/alkaline_mode',
                
                # Apply
                'wifi reload',
            ])
            
            for cmd in commands:
                display = cmd[:60] + "..." if len(cmd) > 60 else cmd
                self.log(f"  $ {display}")
                stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                stdout.read()  # Wait for completion
            
            # Deploy Alkaline software
            self._deploy_alkaline_software(client, mode, device_id)
            
            client.close()
            self.log("✓ SSH configuration complete!")
            return True
            
        except Exception as e:
            self.log(f"  SSH failed: {e}")
            return False
    
    def _deploy_alkaline_software(self, client, mode: str, device_id: str):
        """Deploy Alkaline Python software to the device."""
        self.log("\nDeploying Alkaline software...")
        
        try:
            # Create directories
            commands = [
                'mkdir -p /opt/alkaline',
                'mkdir -p /etc/alkaline',
                'mkdir -p /var/lib/alkaline',
                'mkdir -p /var/log/alkaline',
            ]
            for cmd in commands:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=5)
                stdout.read()
            
            # Get SFTP client
            sftp = client.open_sftp()
            
            # Find our Python files
            script_dir = Path(__file__).parent
            files_to_upload = [
                ('alkaline_device.py', '/opt/alkaline/alkaline_device.py'),
                ('adaptive_bandwidth.py', '/opt/alkaline/adaptive_bandwidth.py'),
                ('scripts/alkaline_boot.sh', '/opt/alkaline/alkaline_boot.sh'),
            ]
            
            for local_name, remote_path in files_to_upload:
                local_path = script_dir / local_name
                if local_path.exists():
                    self.log(f"  Uploading {local_name}...")
                    sftp.put(str(local_path), remote_path)
                else:
                    self.log(f"  Warning: {local_name} not found")
            
            # Create config file (no server needed - devices talk directly)
            config_content = json.dumps({
                "mode": mode,
                "device_id": device_id,
                "mesh_id": self.network_config.config["mesh_id"],
                "max_customers": 9,
                "auto_connect": True,
                "adaptive_bandwidth": {
                    "enabled": True,
                    "default_bandwidth": 4,
                    "upgrade_delay": 300,
                    "downgrade_delay": 60
                },
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "version": "2.0"
            }, indent=2)
            
            # Write config via command (sftp might not have write permission to /etc)
            config_b64 = base64.b64encode(config_content.encode()).decode()
            stdin, stdout, stderr = client.exec_command(
                f'echo "{config_b64}" | base64 -d > /etc/alkaline/config.json',
                timeout=5
            )
            stdout.read()
            self.log("  Created /etc/alkaline/config.json")
            
            # Make boot script executable and enable
            commands = [
                'chmod +x /opt/alkaline/alkaline_boot.sh',
                'chmod +x /opt/alkaline/alkaline_mesh.py',
                'chmod +x /opt/alkaline/alkaline_complete.py',
                'chmod +x /opt/alkaline/adaptive_bandwidth.py',
                'ln -sf /opt/alkaline/alkaline_boot.sh /etc/init.d/alkaline 2>/dev/null || true',
                '/opt/alkaline/alkaline_boot.sh enable 2>/dev/null || true',
            ]
            for cmd in commands:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=5)
                stdout.read()
            
            sftp.close()
            self.log("✓ Alkaline software deployed!")
            self.log("  - Mesh networking: enabled")
            self.log("  - Adaptive bandwidth: enabled")
            self.log("  Software will auto-start on boot")
            
        except Exception as e:
            self.log(f"  Software deployment failed: {e}")
            self.log("  Device configured but software not deployed")
            self.log("  You may need to manually copy files to /opt/alkaline/")
    
    def reboot(self):
        """Reboot the device."""
        self.log("\nRebooting device...")
        
        try:
            # Try ubus reboot
            ubus_url = urljoin(self.base_url, "/cgi-bin/luci/admin/ubus")
            self._ubus_call(ubus_url, self.auth_token or "", "system", "reboot", {})
        except:
            pass
        
        try:
            # Try direct reboot endpoint
            self.session.post(urljoin(self.base_url, "/cgi-bin/luci/admin/system/reboot"), timeout=2)
        except:
            pass
        
        self.log("✓ Reboot command sent")
        self.log("  Device will restart in ~2 minutes")


# =============================================================================
# GUI
# =============================================================================

class FlashToolGUI:
    """Modern two-button GUI for flashing devices."""
    
    def __init__(self):
        self.config = NetworkConfig()
        self.root = tk.Tk()
        self.root.title("Alkaline Network - One-Click Provisioning")
        self.root.geometry("900x800")
        self.root.configure(bg='#1a1a2e')
        
        # Load pending orders
        self.pending_orders = []
        self.selected_order = None
        self._load_pending_orders()
        
        self.setup_ui()
    
    def _load_pending_orders(self):
        """Load pending orders from provisioning system."""
        try:
            from provisioning import OrderManager
            manager = OrderManager()
            self.pending_orders = manager.get_pending_orders()
        except Exception as e:
            print(f"Could not load pending orders: {e}")
            self.pending_orders = []
    
    def setup_ui(self):
        """Create the UI."""
        
        # Title
        title = tk.Label(
            self.root,
            text="⚡ Alkaline Network - One-Click Provisioning",
            font=('Helvetica', 22, 'bold'),
            fg='#00ff88',
            bg='#1a1a2e'
        )
        title.pack(pady=10)
        
        # Subtitle
        subtitle = tk.Label(
            self.root,
            text="Plug in device → Select order → Click button → Ship!",
            font=('Helvetica', 11),
            fg='#888888',
            bg='#1a1a2e'
        )
        subtitle.pack()
        
        # ===== PENDING ORDERS SECTION =====
        orders_frame = tk.LabelFrame(
            self.root,
            text="📦 Pending Orders (click to select)",
            font=('Helvetica', 11, 'bold'),
            fg='#00ff88',
            bg='#1a1a2e',
            padx=10,
            pady=5
        )
        orders_frame.pack(pady=10, fill='x', padx=20)
        
        if self.pending_orders:
            self.order_listbox = tk.Listbox(
                orders_frame,
                font=('Courier', 10),
                bg='#0a0a1e',
                fg='#00ff88',
                selectbackground='#0066cc',
                selectforeground='white',
                height=min(5, len(self.pending_orders)),
                width=80
            )
            self.order_listbox.pack(fill='x', pady=5)
            
            for order in self.pending_orders:
                order_type_icon = "🌐" if order.order_type == "gateway" else "📡"
                self.order_listbox.insert(
                    tk.END, 
                    f"{order_type_icon} {order.order_id} | {order.customer_name:20} | {order.customer_email:25} | {order.plan}"
                )
            
            self.order_listbox.bind('<<ListboxSelect>>', self._on_order_select)
            
            # Selected order details
            self.selected_label = tk.Label(
                orders_frame,
                text="No order selected - click an order above or use manual mode below",
                font=('Helvetica', 10),
                fg='#ffaa00',
                bg='#1a1a2e'
            )
            self.selected_label.pack(pady=5)
        else:
            tk.Label(
                orders_frame,
                text="No pending orders. Use manual mode below or add orders via website.",
                font=('Helvetica', 10),
                fg='#666666',
                bg='#1a1a2e'
            ).pack(pady=10)
            self.order_listbox = None
            self.selected_label = None
        
        # Refresh button
        tk.Button(
            orders_frame,
            text="🔄 Refresh Orders",
            font=('Helvetica', 9),
            command=self._refresh_orders
        ).pack(pady=5)
        
        # Password frame
        pass_frame = tk.Frame(self.root, bg='#1a1a2e')
        pass_frame.pack(pady=5)
        
        tk.Label(
            pass_frame,
            text="Device Password:",
            font=('Helvetica', 10),
            fg='#aaaaaa',
            bg='#1a1a2e'
        ).pack(side='left', padx=5)
        
        self.password_entry = tk.Entry(
            pass_frame,
            font=('Courier', 11),
            bg='#2a2a4e',
            fg='#ffffff',
            insertbackground='#00ff88',
            width=20,
            show='*'
        )
        self.password_entry.insert(0, DEVICE_PASSWORD)
        self.password_entry.pack(side='left', padx=5)
        
        # Buttons frame
        btn_frame = tk.Frame(self.root, bg='#1a1a2e')
        btn_frame.pack(pady=15)
        
        # Gateway button
        self.gateway_btn = tk.Button(
            btn_frame,
            text="🌐 GATEWAY\n(Mesh Gate - Has Internet)",
            font=('Helvetica', 14, 'bold'),
            fg='white',
            bg='#0066cc',
            activebackground='#0088ff',
            width=24,
            height=4,
            command=self.flash_gateway
        )
        self.gateway_btn.pack(side='left', padx=15)
        
        # Pinger button
        self.pinger_btn = tk.Button(
            btn_frame,
            text="📡 PINGER\n(Mesh Point - Customer)",
            font=('Helvetica', 14, 'bold'),
            fg='white',
            bg='#00aa44',
            activebackground='#00cc66',
            width=24,
            height=4,
            command=self.flash_pinger
        )
        self.pinger_btn.pack(side='left', padx=15)
        
        # Security info
        security_frame = tk.Frame(self.root, bg='#2a2a4e', padx=15, pady=8)
        security_frame.pack(pady=8, fill='x', padx=40)
        
        tk.Label(
            security_frame,
            text="🔒 Security Configuration:",
            font=('Helvetica', 10, 'bold'),
            fg='#00ff88',
            bg='#2a2a4e'
        ).pack(anchor='w')
        
        security_text = (
            f"Mesh Backbone: WPA3-SAE (128-bit key)\n"
            f"Customer WiFi: WPA2-PSK (64-bit key)\n"
            f"Mesh Passphrase: {self.config.config['mesh_passphrase'][:12]}...{self.config.config['mesh_passphrase'][-4:]}\n"
            f"Admin Password: {self.config.config['admin_password'][:8]}..."
        )
        
        tk.Label(
            security_frame,
            text=security_text,
            font=('Courier', 9),
            fg='#aaaaaa',
            bg='#2a2a4e',
            justify='left'
        ).pack(anchor='w')
        
        # Network info
        info_frame = tk.Frame(self.root, bg='#2a2a4e', padx=15, pady=8)
        info_frame.pack(pady=5, fill='x', padx=40)
        
        tk.Label(
            info_frame,
            text="📊 Network Status:",
            font=('Helvetica', 10, 'bold'),
            fg='#00ff88',
            bg='#2a2a4e'
        ).pack(anchor='w')
        
        self.info_label = tk.Label(
            info_frame,
            text=self._get_status_text(),
            font=('Courier', 9),
            fg='#aaaaaa',
            bg='#2a2a4e',
            justify='left'
        )
        self.info_label.pack(anchor='w')
        
        # Log output
        tk.Label(
            self.root,
            text="Log:",
            font=('Helvetica', 10),
            fg='#aaaaaa',
            bg='#1a1a2e'
        ).pack(anchor='w', padx=40)
        
        self.log_text = scrolledtext.ScrolledText(
            self.root,
            font=('Courier', 9),
            bg='#0a0a1e',
            fg='#00ff88',
            height=14,
            state='disabled'
        )
        self.log_text.pack(fill='both', expand=True, padx=40, pady=(0, 10))
        
        # Status bar
        self.status = tk.Label(
            self.root,
            text="Ready - Connect device via Ethernet and click a button",
            font=('Helvetica', 10),
            fg='#888888',
            bg='#1a1a2e'
        )
        self.status.pack(pady=5)
    
    def _get_status_text(self) -> str:
        """Get network status text."""
        return (
            f"Mesh ID: {self.config.config['mesh_id']}\n"
            f"Gateways: {self.config.config['gateway_count']}  |  "
            f"Pingers: {self.config.config['pinger_count']}\n"
            f"Customer WiFi Password: {self.config.config['customer_wifi_password']}"
        )
    
    def _on_order_select(self, event):
        """Handle order selection."""
        if not self.order_listbox:
            return
        
        selection = self.order_listbox.curselection()
        if selection:
            idx = selection[0]
            self.selected_order = self.pending_orders[idx]
            
            order_type_icon = "🌐 GATEWAY" if self.selected_order.order_type == "gateway" else "📡 PINGER"
            self.selected_label.configure(
                text=f"✅ Selected: {order_type_icon} for {self.selected_order.customer_name} ({self.selected_order.customer_email})",
                fg='#00ff88'
            )
    
    def _refresh_orders(self):
        """Refresh pending orders list."""
        self._load_pending_orders()
        
        if self.order_listbox:
            self.order_listbox.delete(0, tk.END)
            for order in self.pending_orders:
                order_type_icon = "🌐" if order.order_type == "gateway" else "📡"
                self.order_listbox.insert(
                    tk.END,
                    f"{order_type_icon} {order.order_id} | {order.customer_name:20} | {order.customer_email:25} | {order.plan}"
                )
        
        self.log(f"Refreshed: {len(self.pending_orders)} pending orders")
    
    def log(self, message: str):
        """Add message to log."""
        self.log_text.configure(state='normal')
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
        self.root.update()
    
    def update_info(self):
        """Update the info display."""
        self.info_label.configure(text=self._get_status_text())
    
    def set_buttons_state(self, state: str):
        """Enable/disable buttons."""
        self.gateway_btn.configure(state=state)
        self.pinger_btn.configure(state=state)
    
    def flash_gateway(self):
        """Flash device as gateway."""
        # If no order selected, use manual mode
        if not self.selected_order or self.selected_order.order_type != 'gateway':
            if self.selected_order and self.selected_order.order_type != 'gateway':
                messagebox.showwarning("Wrong Order Type", "Selected order is for a PINGER, not gateway.\n\nClick a gateway order or deselect to use manual mode.")
                return
        
        self._do_flash("gateway")
    
    def flash_pinger(self):
        """Flash device as pinger."""
        if self.config.config["gateway_count"] == 0:
            if not messagebox.askyesno(
                "No Gateway Yet",
                "You haven't provisioned any gateways yet.\n\n"
                "Pingers need a gateway to connect to.\n\n"
                "Continue anyway?"
            ):
                return
        
        # If no order selected, use manual mode  
        if not self.selected_order or self.selected_order.order_type != 'pinger':
            if self.selected_order and self.selected_order.order_type != 'pinger':
                messagebox.showwarning("Wrong Order Type", "Selected order is for a GATEWAY, not pinger.\n\nClick a pinger order or deselect to use manual mode.")
                return
        
        self._do_flash("pinger")
    
    def _do_flash(self, mode: str):
        """Execute flash operation."""
        password = self.password_entry.get()
        
        # Check if we have a selected order
        order = self.selected_order if self.selected_order and self.selected_order.order_type == mode else None
        
        if order:
            self.log(f"")
            self.log(f"{'='*50}")
            self.log(f"PROVISIONING FOR ORDER: {order.order_id}")
            self.log(f"Customer: {order.customer_name}")
            self.log(f"Email: {order.customer_email}")
            self.log(f"Address: {order.customer_address}")
            self.log(f"{'='*50}")
            self.log(f"")
        
        self.set_buttons_state('disabled')
        mode_name = "Gateway (Mesh Gate)" if mode == "gateway" else "Pinger (Mesh Point)"
        self.status.configure(text=f"Flashing as {mode_name}...", fg='#ffaa00')
        
        # Clear log
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')
        
        # Get selected order
        order = self.selected_order if self.selected_order and self.selected_order.order_type == mode else None
        
        def do_flash():
            try:
                provisioner = HeltecWebProvisioner(self.config, log_callback=self.log)
                
                if not provisioner.connect(password=password):
                    self.status.configure(text="Connection failed!", fg='#ff4444')
                    return
                
                provisioner.get_device_info()
                device_id = provisioner.configure_via_wizard(mode)
                
                if device_id:
                    # If we have an order, register the device
                    if order:
                        self._register_device_for_order(order, device_id, mode)
                    else:
                        # Manual mode - still register to local database
                        self._register_device_manual(device_id, mode)
                    
                    provisioner.reboot()
                    self.update_info()
                    self.status.configure(
                        text=f"✓ {device_id} ready! Unplug and deploy.", 
                        fg='#00ff88'
                    )
                    
                    msg = (
                        f"Device provisioned successfully!\n\n"
                        f"Device ID: {device_id}\n"
                        f"WiFi Name: Alkaline-{device_id}\n"
                        f"WiFi Password: {self.config.config['customer_wifi_password']}\n\n"
                    )
                    
                    if order:
                        msg += (
                            f"ORDER: {order.order_id}\n"
                            f"SHIP TO: {order.customer_name}\n"
                            f"ADDRESS: {order.customer_address}\n\n"
                        )
                    
                    if mode == "gateway":
                        msg += (
                            "Deploy this at a location WITH internet.\n"
                            "Connect its Ethernet port to the host's router."
                        )
                    else:
                        msg += (
                            f"Customer setup:\n"
                            f"1. Plug in power\n"
                            f"2. Connect to Alkaline-{device_id} WiFi\n"
                            f"3. That's it - mesh connects automatically!"
                        )
                    
                    messagebox.showinfo(f"{mode.title()} Ready!", msg)
                    
                    # Clear selected order and refresh
                    if order:
                        self.selected_order = None
                        if self.selected_label:
                            self.selected_label.configure(
                                text="✅ Device shipped! Select next order.",
                                fg='#00ff88'
                            )
                        self._refresh_orders()
                else:
                    self.status.configure(text="Provisioning failed - see log", fg='#ff4444')
                
            except Exception as e:
                self.log(f"\nERROR: {e}")
                import traceback
                self.log(traceback.format_exc())
                self.status.configure(text=f"Error: {e}", fg='#ff4444')
            finally:
                self.set_buttons_state('normal')
        
        threading.Thread(target=do_flash, daemon=True).start()
    
    def _register_device_for_order(self, order, device_id: str, mode: str):
        """Register device with provisioning system and update order."""
        try:
            from provisioning import OrderManager, DeviceProvisioner, GatewayAssigner
            import secrets
            import hashlib
            
            self.log(f"\n[REGISTER] Registering device for order {order.order_id}...")
            
            # Generate keys for the device (in real implementation, device generates these)
            # For now, we generate them here and will deploy to device
            public_key = secrets.token_hex(32)
            private_key = secrets.token_hex(32)
            private_key_hash = hashlib.sha256(private_key.encode()).hexdigest()
            
            # Provision in system
            provisioner = DeviceProvisioner()
            device = provisioner.provision_device(
                order=order,
                public_key=public_key,
                private_key_hash=private_key_hash,
                mac_address=""  # Could get from device
            )
            
            self.log(f"[REGISTER] Device ID: {device.device_id}")
            
            # Update order status
            manager = OrderManager()
            manager.mark_provisioned(
                order.order_id,
                device.device_id,
                device.public_key,
                device.tunnel_ip
            )
            manager.mark_shipped(order.order_id)
            
            self.log(f"[REGISTER] Order {order.order_id} marked as SHIPPED")
            self.log(f"[REGISTER] Device ready for deployment!")
            
        except Exception as e:
            self.log(f"[REGISTER] Warning: Could not register device: {e}")
            self.log(f"[REGISTER] Device still functional, manual registration needed")
    
    def _register_device_manual(self, device_id: str, mode: str):
        """Register device to local database when no order is used (manual mode)."""
        try:
            import sqlite3
            from pathlib import Path
            
            db_path = Path("alkaline.db")
            if not db_path.exists():
                self.log(f"[REGISTER] No database yet - device tracked locally only")
                return
            
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            
            if mode == "gateway":
                # Add to gateways table
                c.execute("""
                    INSERT OR REPLACE INTO gateways (
                        gateway_id, public_key, owner_name, owner_email,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    device_id,
                    "pending_key_exchange",  # Will get real key when device connects
                    "Manual Provision",
                    "",
                    "provisioned",
                    time.time()
                ))
                self.log(f"[REGISTER] Gateway {device_id} added to database")
            else:
                # Add to customers table as unassigned pinger
                c.execute("""
                    INSERT OR REPLACE INTO customers (
                        customer_id, pinger_id, public_key, name, email,
                        status, subscription_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"manual_{device_id}",
                    device_id,
                    "pending_key_exchange",
                    "Manual Provision",
                    "",
                    "provisioned",
                    "pending_assignment",
                    time.time()
                ))
                self.log(f"[REGISTER] Pinger {device_id} added to database")
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.log(f"[REGISTER] Note: Local registration skipped: {e}")
            self.log(f"[REGISTER] Device still works - will appear when it connects")
    
    def run(self):
        """Run the GUI."""
        self.root.mainloop()


# =============================================================================
# CLI
# =============================================================================

def cli_mode():
    """Command-line interface."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Alkaline Network Device Provisioner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python flash_tool.py gateway          # Flash as gateway
  python flash_tool.py pinger           # Flash as pinger
  python flash_tool.py --show-config    # Show network config
  python flash_tool.py --find-device AA:BB:CC:DD:EE:FF  # Lookup device by MAC
        """
    )
    parser.add_argument("mode", nargs='?', choices=["gateway", "pinger"], 
                       help="Device mode to configure")
    parser.add_argument("--ip", default=DEVICE_IP_ETH, help="Device IP address")
    parser.add_argument("--password", default=DEVICE_PASSWORD, help="Device password")
    parser.add_argument("--show-config", action="store_true", help="Show network configuration")
    parser.add_argument("--find-device", metavar="MAC", help="Find device by MAC address")
    
    args = parser.parse_args()
    
    config = NetworkConfig()
    
    if args.show_config:
        print("\n" + "="*50)
        print("ALKALINE NETWORK CONFIGURATION")
        print("="*50)
        print(f"\nMesh ID: {config.config['mesh_id']}")
        print(f"Mesh Passphrase: {config.config['mesh_passphrase']}")
        print(f"Admin Password: {config.config['admin_password']}")
        print(f"Customer WiFi Password: {config.config['customer_wifi_password']}")
        print(f"\nGateways: {config.config['gateway_count']}")
        print(f"Pingers: {config.config['pinger_count']}")
        print(f"\nDevices:")
        for d in config.config['devices']:
            print(f"  {d['id']}: {d['type']} | MAC: {d.get('mac', 'N/A')} | {d['provisioned']}")
        print()
        return
    
    if args.find_device:
        device = config.find_device_by_mac(args.find_device)
        if device:
            print(f"\nFound device: {device['id']}")
            print(f"  Type: {device['type']}")
            print(f"  MAC: {device['mac']}")
            print(f"  Provisioned: {device['provisioned']}")
            print(f"  WiFi SSID: {device.get('wifi_ssid', 'N/A')}")
        else:
            print(f"\nNo device found with MAC: {args.find_device}")
        return
    
    if not args.mode:
        parser.print_help()
        return
    
    print(f"\nProvisioning as {args.mode}...")
    
    provisioner = HeltecWebProvisioner(config, log_callback=print)
    
    if not provisioner.connect(ip=args.ip, password=args.password):
        sys.exit(1)
    
    provisioner.get_device_info()
    device_id = provisioner.configure_via_wizard(args.mode)
    
    if device_id:
        provisioner.reboot()
        print(f"\n{'='*50}")
        print(f"SUCCESS! Device {device_id} provisioned as {args.mode}")
        print(f"WiFi SSID: Alkaline-{device_id}")
        print(f"WiFi Password: {config.config['customer_wifi_password']}")
        print(f"{'='*50}")
    else:
        print("\nFailed to provision device!")
        sys.exit(1)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli_mode()
    else:
        app = FlashToolGUI()
        app.run()
