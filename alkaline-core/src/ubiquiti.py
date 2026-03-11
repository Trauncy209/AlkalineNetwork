"""
Alkaline Hosting - Ubiquiti airOS Integration

DEPRECATED: This module was for the original WISP model using Ubiquiti 
NanoStation/LiteBeam hardware. The current Alkaline Network uses HaLow 
(802.11ah) with GL.iNet HaLowLink devices instead.

Kept for reference - may be useful if expanding to hybrid deployments.

This module talks to REAL Ubiquiti devices (NanoStation, LiteBeam, etc.)
using the airOS API. No fake simulation - this is production code.

Requires: pip install airos aiohttp

Based on: https://github.com/CoMPaTech/python-airos
"""

import asyncio
import aiohttp
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum

# Try to import the airos library
try:
    from airos.airos8 import AirOS8
    from airos.airos6 import AirOS6
    from airos.helpers import async_get_firmware_data, DetectDeviceData
    from airos.discovery import async_discover_devices
    AIROS_AVAILABLE = True
except ImportError:
    AIROS_AVAILABLE = False
    print("[UBIQUITI] WARNING: airos library not installed. Run: pip install airos")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alkaline.ubiquiti")


class DeviceRole(Enum):
    """Role of the Ubiquiti device in our network."""
    ACCESS_POINT = "ap"      # Hoster's device - accepts connections
    STATION = "station"      # Customer's device - connects to AP
    UNKNOWN = "unknown"


@dataclass
class UbiquitiDevice:
    """Represents a Ubiquiti airOS device."""
    host: str                          # IP address
    mac_address: str = ""
    hostname: str = ""
    model: str = ""                    # e.g., "NanoStation 5AC Loco"
    firmware: str = ""                 # e.g., "v8.7.19"
    role: DeviceRole = DeviceRole.UNKNOWN
    
    # Wireless stats
    frequency: int = 0                 # MHz, e.g., 5180
    channel_width: int = 0             # MHz, e.g., 40
    tx_power: int = 0                  # dBm
    signal: int = 0                    # dBm (for stations)
    noise: int = 0                     # dBm
    ccq: int = 0                       # Client Connection Quality %
    
    # Traffic stats
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_rate: int = 0                   # Mbps
    rx_rate: int = 0                   # Mbps
    
    # Connection info
    connected_stations: List[Dict] = field(default_factory=list)  # For APs
    uptime: int = 0                    # seconds
    last_seen: float = field(default_factory=lambda: datetime.now().timestamp())
    
    # Authentication
    username: str = "ubnt"             # Default airOS username
    password: str = "ubnt"             # Default airOS password
    
    # Internal
    _session: Optional[aiohttp.ClientSession] = field(default=None, repr=False)
    _client: Optional[Any] = field(default=None, repr=False)


class UbiquitiManager:
    """
    Manages all Ubiquiti devices in the Alkaline network.
    
    This replaces our fake "modem/gateway" code with real Ubiquiti API calls.
    """
    
    def __init__(self, dashboard_callback=None):
        """
        Initialize the Ubiquiti manager.
        
        Args:
            dashboard_callback: Function to call when device status changes.
                               Signature: callback(event_type: str, device: UbiquitiDevice)
        """
        if not AIROS_AVAILABLE:
            raise ImportError("airos library required. Install with: pip install airos")
        
        self.devices: Dict[str, UbiquitiDevice] = {}  # mac -> device
        self.dashboard_callback = dashboard_callback
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._poll_interval = 30  # seconds
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            # Disable SSL verification for self-signed certs on airOS devices
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session
    
    async def close(self):
        """Clean up resources."""
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()
    
    # =========================================
    # DEVICE DISCOVERY
    # =========================================
    
    async def discover_devices(self, timeout: int = 10) -> List[UbiquitiDevice]:
        """
        Discover Ubiquiti devices on the local network.
        
        Uses the same discovery protocol as Ubiquiti's Device Discovery Tool.
        Devices broadcast on UDP port 10001.
        
        Returns:
            List of discovered UbiquitiDevice objects
        """
        logger.info(f"Discovering Ubiquiti devices (timeout: {timeout}s)...")
        
        try:
            discovered = await async_discover_devices(timeout=timeout)
            
            devices = []
            for mac, info in discovered.items():
                device = UbiquitiDevice(
                    host=info.get("ip", ""),
                    mac_address=mac,
                    hostname=info.get("hostname", ""),
                    model=info.get("model", ""),
                    firmware=info.get("firmware", ""),
                )
                devices.append(device)
                logger.info(f"  Found: {device.model} at {device.host} ({mac})")
            
            return devices
            
        except Exception as e:
            logger.error(f"Discovery failed: {e}")
            return []
    
    # =========================================
    # DEVICE CONNECTION
    # =========================================
    
    async def add_device(self, host: str, username: str = "ubnt", 
                         password: str = "ubnt") -> Optional[UbiquitiDevice]:
        """
        Add and connect to a Ubiquiti device.
        
        Args:
            host: IP address of the device
            username: airOS login username (default: ubnt)
            password: airOS login password
            
        Returns:
            UbiquitiDevice if successful, None otherwise
        """
        session = await self._get_session()
        
        try:
            # Detect firmware version
            conn_data = {
                "host": host,
                "username": username,
                "password": password,
                "session": session
            }
            
            fw_info = await async_get_firmware_data(**conn_data)
            fw_major = fw_info.get("fw_major", 8)
            
            # Use appropriate client for firmware version
            if fw_major == 6:
                client = AirOS6(**conn_data)
            else:
                client = AirOS8(**conn_data)
            
            # Login
            login_result = await client.login()
            if not login_result:
                logger.error(f"Failed to login to {host}")
                return None
            
            # Get status
            status = await client.status()
            
            # Determine role from wireless mode
            wireless_mode = status.wireless.mode if hasattr(status, 'wireless') else ""
            if wireless_mode in ["ap", "ap-ptmp"]:
                role = DeviceRole.ACCESS_POINT
            elif wireless_mode in ["sta", "sta-ptmp"]:
                role = DeviceRole.STATION
            else:
                role = DeviceRole.UNKNOWN
            
            # Create device object
            device = UbiquitiDevice(
                host=host,
                mac_address=status.host.mac if hasattr(status.host, 'mac') else "",
                hostname=status.host.hostname if hasattr(status.host, 'hostname') else "",
                model=status.host.devmodel if hasattr(status.host, 'devmodel') else "",
                firmware=status.host.fwversion if hasattr(status.host, 'fwversion') else "",
                role=role,
                username=username,
                password=password,
                _client=client,
                _session=session
            )
            
            # Get wireless stats
            if hasattr(status, 'wireless'):
                device.frequency = getattr(status.wireless, 'frequency', 0)
                device.channel_width = getattr(status.wireless, 'chanbw', 0)
                device.tx_power = getattr(status.wireless, 'txpower', 0)
                device.noise = getattr(status.wireless, 'noisef', 0)
                
                # Get connected stations if AP
                if hasattr(status.wireless, 'sta') and status.wireless.sta:
                    device.connected_stations = [
                        {
                            "mac": sta.mac,
                            "signal": sta.signal,
                            "ccq": getattr(sta, 'ccq', 0),
                            "tx_rate": getattr(sta, 'tx_rate', 0),
                            "rx_rate": getattr(sta, 'rx_rate', 0),
                            "uptime": getattr(sta, 'uptime', 0),
                        }
                        for sta in status.wireless.sta
                    ]
            
            # Get traffic stats
            if hasattr(status, 'interfaces'):
                for iface in status.interfaces:
                    if hasattr(iface, 'status') and iface.status.get('enabled'):
                        device.tx_bytes = iface.status.get('tx_bytes', 0)
                        device.rx_bytes = iface.status.get('rx_bytes', 0)
            
            # Get uptime
            if hasattr(status.host, 'uptime'):
                device.uptime = status.host.uptime
            
            # Store device
            self.devices[device.mac_address] = device
            
            logger.info(f"Added device: {device.hostname} ({device.model}) as {role.value}")
            
            # Notify dashboard
            if self.dashboard_callback:
                await self._notify("device_added", device)
            
            return device
            
        except Exception as e:
            logger.error(f"Failed to add device {host}: {e}")
            return None
    
    async def remove_device(self, mac_address: str):
        """Remove a device from management."""
        if mac_address in self.devices:
            del self.devices[mac_address]
            logger.info(f"Removed device: {mac_address}")
    
    # =========================================
    # DEVICE STATUS
    # =========================================
    
    async def get_device_status(self, mac_address: str) -> Optional[UbiquitiDevice]:
        """
        Get fresh status from a device.
        
        Returns:
            Updated UbiquitiDevice or None if failed
        """
        device = self.devices.get(mac_address)
        if not device or not device._client:
            return None
        
        try:
            status = await device._client.status()
            
            # Update stats
            if hasattr(status, 'wireless'):
                device.noise = getattr(status.wireless, 'noisef', 0)
                
                if hasattr(status.wireless, 'sta') and status.wireless.sta:
                    device.connected_stations = [
                        {
                            "mac": sta.mac,
                            "signal": sta.signal,
                            "ccq": getattr(sta, 'ccq', 0),
                            "tx_rate": getattr(sta, 'tx_rate', 0),
                            "rx_rate": getattr(sta, 'rx_rate', 0),
                            "uptime": getattr(sta, 'uptime', 0),
                        }
                        for sta in status.wireless.sta
                    ]
            
            if hasattr(status, 'interfaces'):
                for iface in status.interfaces:
                    if hasattr(iface, 'status') and iface.status.get('enabled'):
                        device.tx_bytes = iface.status.get('tx_bytes', 0)
                        device.rx_bytes = iface.status.get('rx_bytes', 0)
            
            device.uptime = getattr(status.host, 'uptime', 0)
            device.last_seen = datetime.now().timestamp()
            
            return device
            
        except Exception as e:
            logger.error(f"Failed to get status for {mac_address}: {e}")
            return None
    
    async def get_all_status(self) -> List[UbiquitiDevice]:
        """Get status from all managed devices."""
        results = []
        for mac in self.devices:
            device = await self.get_device_status(mac)
            if device:
                results.append(device)
        return results
    
    # =========================================
    # DEVICE CONTROL
    # =========================================
    
    async def kick_station(self, ap_mac: str, station_mac: str) -> bool:
        """
        Kick (disconnect) a station from an access point.
        
        Args:
            ap_mac: MAC address of the access point
            station_mac: MAC address of the station to kick
            
        Returns:
            True if successful
        """
        device = self.devices.get(ap_mac)
        if not device or not device._client:
            return False
        
        try:
            result = await device._client.stakick(station_mac)
            logger.info(f"Kicked station {station_mac} from {ap_mac}")
            return True
        except Exception as e:
            logger.error(f"Failed to kick station: {e}")
            return False
    
    async def reboot_device(self, mac_address: str) -> bool:
        """Reboot a device."""
        device = self.devices.get(mac_address)
        if not device or not device._client:
            return False
        
        try:
            await device._client.reboot()
            logger.info(f"Rebooted device {mac_address}")
            return True
        except Exception as e:
            logger.error(f"Failed to reboot device: {e}")
            return False
    
    # =========================================
    # BANDWIDTH CONTROL (Traffic Shaping)
    # =========================================
    
    async def set_bandwidth_limit(self, ap_mac: str, station_mac: str,
                                   download_kbps: int, upload_kbps: int) -> bool:
        """
        Set bandwidth limit for a station.
        
        NOTE: This requires the device to have traffic shaping configured.
        For production, use UISP CRM which handles this automatically
        based on service plans.
        
        Args:
            ap_mac: Access point MAC
            station_mac: Station MAC to limit
            download_kbps: Download limit in kbps (e.g., 25000 for 25 Mbps)
            upload_kbps: Upload limit in kbps
            
        Returns:
            True if successful
        """
        # Traffic shaping on airOS devices requires SSH access and
        # modifying /tmp/system.cfg or using the traffic shaping UI.
        # 
        # For a real WISP, you'd use UISP's CRM module which handles
        # this through service plans and the gateway router.
        #
        # This is a placeholder - in production, integrate with UISP API.
        
        logger.warning("Bandwidth limiting requires UISP integration. "
                       "See: https://help.uisp.com/hc/en-us/articles/22590998317719")
        return False
    
    # =========================================
    # MONITORING LOOP
    # =========================================
    
    async def start_monitoring(self, interval: int = 30):
        """
        Start background monitoring of all devices.
        
        Polls devices periodically and calls dashboard_callback with updates.
        """
        self._running = True
        self._poll_interval = interval
        
        logger.info(f"Starting device monitoring (interval: {interval}s)")
        
        while self._running:
            for mac, device in list(self.devices.items()):
                try:
                    updated = await self.get_device_status(mac)
                    if updated:
                        await self._notify("device_status", updated)
                except Exception as e:
                    logger.error(f"Monitoring error for {mac}: {e}")
            
            await asyncio.sleep(self._poll_interval)
    
    def stop_monitoring(self):
        """Stop background monitoring."""
        self._running = False
    
    async def _notify(self, event_type: str, device: UbiquitiDevice):
        """Notify dashboard of device event."""
        if self.dashboard_callback:
            if asyncio.iscoroutinefunction(self.dashboard_callback):
                await self.dashboard_callback(event_type, device)
            else:
                self.dashboard_callback(event_type, device)


# =========================================
# ALKALINE-SPECIFIC WRAPPER
# =========================================

class AlkalineUbiquitiNetwork:
    """
    High-level interface for Alkaline Hosting's Ubiquiti network.
    
    Wraps UbiquitiManager with Alkaline-specific logic:
    - Maps devices to Hosters/Customers
    - Tracks tiers and earnings
    - Integrates with dashboard
    """
    
    # Tier bandwidth limits (kbps)
    TIER_LIMITS = {
        "basic": {"down": 25000, "up": 10000},    # 25/10 Mbps
        "plus": {"down": 50000, "up": 20000},     # 50/20 Mbps
        "pro": {"down": 100000, "up": 40000},     # 100/40 Mbps
    }
    
    def __init__(self, dashboard_url: str = "http://localhost:5000"):
        self.dashboard_url = dashboard_url
        self.manager = UbiquitiManager(dashboard_callback=self._on_device_event)
        
        # Mapping: station_mac -> {hoster_id, customer_id, tier}
        self.station_assignments: Dict[str, Dict] = {}
        
        # Mapping: ap_mac -> hoster_id
        self.ap_assignments: Dict[str, str] = {}
    
    async def _on_device_event(self, event_type: str, device: UbiquitiDevice):
        """Handle device events and sync with dashboard."""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                if event_type == "device_added":
                    # Register with dashboard
                    await session.post(
                        f"{self.dashboard_url}/api/device/register",
                        json={
                            "mac_address": device.mac_address,
                            "ip_address": device.host,
                            "hostname": device.hostname,
                            "device_type": "gateway" if device.role == DeviceRole.ACCESS_POINT else "modem",
                            "model": device.model,
                            "firmware": device.firmware,
                        }
                    )
                
                elif event_type == "device_status":
                    # Send heartbeat
                    await session.post(
                        f"{self.dashboard_url}/api/device/heartbeat",
                        json={
                            "mac_address": device.mac_address,
                            "bytes_down": device.rx_bytes,
                            "bytes_up": device.tx_bytes,
                            "signal_strength": device.signal,
                            "connected_count": len(device.connected_stations),
                        }
                    )
                    
                    # Report connected stations for APs
                    if device.role == DeviceRole.ACCESS_POINT:
                        for sta in device.connected_stations:
                            await session.post(
                                f"{self.dashboard_url}/api/gateway/modem_connected",
                                json={
                                    "gateway_id": device.mac_address,
                                    "modem_mac": sta["mac"],
                                }
                            )
        
        except Exception as e:
            logger.error(f"Dashboard sync error: {e}")
    
    async def add_hoster_gateway(self, host: str, hoster_id: str,
                                  username: str = "ubnt", password: str = "ubnt"):
        """
        Add a Hoster's access point to the network.
        
        Args:
            host: IP address of the AP
            hoster_id: Alkaline Hoster ID
            username: airOS login
            password: airOS password
        """
        device = await self.manager.add_device(host, username, password)
        if device:
            self.ap_assignments[device.mac_address] = hoster_id
            logger.info(f"Assigned AP {device.mac_address} to Hoster {hoster_id}")
            return device
        return None
    
    async def add_customer_station(self, host: str, customer_id: str,
                                    tier: str = "basic", hoster_id: str = None,
                                    username: str = "ubnt", password: str = "ubnt"):
        """
        Add a Customer's station (CPE) to the network.
        
        Args:
            host: IP address of the station
            customer_id: Alkaline Customer ID
            tier: Service tier (basic, plus, pro)
            hoster_id: Which Hoster this customer connects to
            username: airOS login
            password: airOS password
        """
        device = await self.manager.add_device(host, username, password)
        if device:
            self.station_assignments[device.mac_address] = {
                "customer_id": customer_id,
                "hoster_id": hoster_id,
                "tier": tier,
            }
            logger.info(f"Assigned Station {device.mac_address} to Customer {customer_id} ({tier})")
            return device
        return None
    
    def get_hoster_stats(self, hoster_id: str) -> Dict:
        """
        Get stats for a Hoster.
        
        Returns:
            {
                "ap_mac": "...",
                "connected_customers": 5,
                "total_tx_bytes": 123456,
                "total_rx_bytes": 654321,
                "earnings": 10.00  # $2/customer
            }
        """
        # Find AP for this hoster
        ap_mac = None
        for mac, h_id in self.ap_assignments.items():
            if h_id == hoster_id:
                ap_mac = mac
                break
        
        if not ap_mac or ap_mac not in self.manager.devices:
            return {}
        
        device = self.manager.devices[ap_mac]
        customer_count = len(device.connected_stations)
        
        return {
            "ap_mac": ap_mac,
            "ap_hostname": device.hostname,
            "ap_model": device.model,
            "connected_customers": customer_count,
            "total_tx_bytes": device.tx_bytes,
            "total_rx_bytes": device.rx_bytes,
            "uptime": device.uptime,
            "earnings": customer_count * 2.00,  # $2/customer
        }
    
    async def start(self):
        """Start the network monitoring."""
        await self.manager.start_monitoring(interval=30)
    
    async def stop(self):
        """Stop the network and clean up."""
        self.manager.stop_monitoring()
        await self.manager.close()


# =========================================
# CLI FOR TESTING
# =========================================

async def main():
    """Test the Ubiquiti integration."""
    print("=" * 60)
    print("  ALKALINE HOSTING - Ubiquiti Integration Test")
    print("=" * 60)
    
    if not AIROS_AVAILABLE:
        print("\n❌ airos library not installed!")
        print("   Run: pip install airos")
        return
    
    print("\n✅ airos library available")
    
    # Test discovery
    print("\n[1] Discovering devices...")
    manager = UbiquitiManager()
    
    try:
        devices = await manager.discover_devices(timeout=5)
        
        if devices:
            print(f"\n   Found {len(devices)} devices:")
            for d in devices:
                print(f"   - {d.model} at {d.host} ({d.mac_address})")
        else:
            print("   No devices found (make sure you're on the same network)")
        
        print("\n[2] To add a device manually:")
        print("    device = await manager.add_device('192.168.1.20', 'ubnt', 'password')")
        
    finally:
        await manager.close()
    
    print("\n" + "=" * 60)
    print("  Integration ready for use!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
