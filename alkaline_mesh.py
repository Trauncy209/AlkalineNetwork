#!/usr/bin/env python3
"""
Alkaline Network - Mesh Auto-Discovery & Management
====================================================

This handles ALL the automatic mesh networking:
  - Auto-discovery of gateways
  - Connect to best/closest gateway
  - Automatic failover if gateway dies
  - Signal strength monitoring
  - Path optimization

This runs ON the Heltec device alongside alkaline_complete.py

Architecture:
  
  PINGER (Customer Device)              GATEWAY (Hoster Device)
  ┌─────────────────────────┐          ┌─────────────────────────┐
  │  alkaline_mesh.py       │          │  alkaline_mesh.py       │
  │  - Discovers gateways   │◄────────►│  - Broadcasts presence  │
  │  - Picks best one       │  HaLow   │  - Reports capacity     │
  │  - Auto-reconnects      │  Mesh    │  - Manages customers    │
  │                         │          │                         │
  │  alkaline_complete.py   │          │  alkaline_complete.py   │
  │  - Encrypted tunnel     │────────► │  - Encrypted tunnel     │
  └─────────────────────────┘  UDP     └─────────────────────────┘
                                                  │
                                                  ▼
                                        ┌─────────────────────────┐
                                        │  YOUR SERVER            │
                                        │  alkaline_complete.py   │
                                        │  --server mode          │
                                        └─────────────────────────┘

Usage:
  # On Gateway (Hoster) device:
  python alkaline_mesh.py --gateway --max-customers 9
  
  # On Pinger (Customer) device:
  python alkaline_mesh.py --pinger --auto-connect

Requirements:
  pip install pynacl netifaces

Author: AlkalineTech
License: MIT
"""

import os
import sys
import time
import json
import socket
import struct
import asyncio
import logging
import argparse
import subprocess
import threading
import signal
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Set
from pathlib import Path
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("alkaline.mesh")

# =============================================================================
# CONFIGURATION
# =============================================================================

CONFIG_DIR = Path("/etc/alkaline")
STATE_DIR = Path("/var/lib/alkaline")
LOG_DIR = Path("/var/log/alkaline")

# Discovery protocol
DISCOVERY_PORT = 51821
DISCOVERY_MAGIC = b"ALKN"
DISCOVERY_VERSION = 1

# Timing
BEACON_INTERVAL = 5.0        # Gateways beacon every 5 seconds
DISCOVERY_TIMEOUT = 15.0     # Consider gateway dead after 15s
HEALTH_CHECK_INTERVAL = 10.0 # Check connection health every 10s
FAILOVER_THRESHOLD = 3       # Failover after 3 missed health checks

# Mesh network
MESH_NETWORK_ID = "AlkalineNetwork"
DEFAULT_CHANNEL = 1  # HaLow channel

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class GatewayInfo:
    """Information about a discovered gateway."""
    gateway_id: str
    public_key: str
    ip_address: str
    mesh_ip: str
    signal_strength: int  # dBm, higher (less negative) = better
    hop_count: int
    customer_count: int
    max_customers: int
    uptime: int  # seconds
    last_seen: float
    latency_ms: float = 0.0
    
    @property
    def available_slots(self) -> int:
        return self.max_customers - self.customer_count
    
    @property
    def is_available(self) -> bool:
        return self.available_slots > 0
    
    @property
    def score(self) -> float:
        """
        Calculate gateway score for selection.
        Higher = better.
        
        Factors:
          - Signal strength (most important)
          - Hop count (fewer = better)
          - Available capacity
          - Latency
        """
        # Normalize signal: -30 dBm = excellent, -90 dBm = poor
        signal_score = (self.signal_strength + 90) / 60 * 100
        
        # Hop penalty: each hop reduces score
        hop_penalty = self.hop_count * 10
        
        # Capacity bonus: prefer gateways with more space
        capacity_score = (self.available_slots / self.max_customers) * 20
        
        # Latency penalty
        latency_penalty = min(self.latency_ms / 10, 30)
        
        return signal_score - hop_penalty + capacity_score - latency_penalty


@dataclass
class PingerInfo:
    """Information about a connected pinger (customer)."""
    pinger_id: str
    public_key: str
    ip_address: str
    connected_at: float
    bytes_up: int = 0
    bytes_down: int = 0
    last_seen: float = 0.0
    signal_strength: int = -50


@dataclass 
class MeshConfig:
    """Configuration for mesh networking."""
    mode: str = "pinger"  # "gateway" or "pinger"
    device_id: str = ""
    mesh_id: str = MESH_NETWORK_ID
    channel: int = DEFAULT_CHANNEL
    
    # Gateway mode settings
    max_customers: int = 9
    server_ip: str = ""
    server_port: int = 51820
    server_pubkey: str = ""
    
    # Pinger mode settings
    auto_connect: bool = True
    preferred_gateway: str = ""  # Optional preferred gateway ID
    
    # Paths
    config_file: str = str(CONFIG_DIR / "mesh.json")
    state_file: str = str(STATE_DIR / "mesh_state.json")
    
    def save(self):
        """Save config to file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(asdict(self), f, indent=2)
    
    @classmethod
    def load(cls, path: str = None) -> 'MeshConfig':
        """Load config from file."""
        path = path or str(CONFIG_DIR / "mesh.json")
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            return cls(**data)
        return cls()


# =============================================================================
# DISCOVERY PROTOCOL
# =============================================================================

class DiscoveryProtocol:
    """
    UDP-based discovery protocol for mesh networking.
    
    Message format:
      - Magic (4 bytes): "ALKN"
      - Version (1 byte)
      - Type (1 byte): 0=beacon, 1=query, 2=response
      - Payload (JSON)
    """
    
    TYPE_BEACON = 0
    TYPE_QUERY = 1
    TYPE_RESPONSE = 2
    TYPE_HEALTH = 3
    TYPE_HEALTH_ACK = 4
    
    @staticmethod
    def encode(msg_type: int, payload: dict) -> bytes:
        """Encode a discovery message."""
        header = DISCOVERY_MAGIC + bytes([DISCOVERY_VERSION, msg_type])
        data = json.dumps(payload).encode()
        return header + data
    
    @staticmethod
    def decode(data: bytes) -> Tuple[int, dict]:
        """Decode a discovery message. Returns (type, payload)."""
        if len(data) < 6:
            raise ValueError("Message too short")
        
        if data[:4] != DISCOVERY_MAGIC:
            raise ValueError("Invalid magic")
        
        version = data[4]
        if version != DISCOVERY_VERSION:
            raise ValueError(f"Unknown version: {version}")
        
        msg_type = data[5]
        payload = json.loads(data[6:].decode())
        
        return msg_type, payload


# =============================================================================
# SIGNAL STRENGTH READER
# =============================================================================

class SignalMonitor:
    """
    Reads signal strength from the HaLow radio.
    
    On OpenWrt/Heltec, this reads from:
      - /sys/kernel/debug/ieee80211/phy0/netdev:*/stations/*/signal
      - Or via 'iw dev <interface> station dump'
    """
    
    def __init__(self, interface: str = "wlan0"):
        self.interface = interface
        self._cache: Dict[str, int] = {}
        self._last_update = 0
    
    def get_signal(self, mac_address: str = None) -> int:
        """Get signal strength in dBm. Returns -100 if unknown."""
        self._update_cache()
        
        if mac_address:
            return self._cache.get(mac_address.lower(), -100)
        
        # Return best signal if no specific MAC
        if self._cache:
            return max(self._cache.values())
        return -100
    
    def _update_cache(self):
        """Update signal strength cache from system."""
        now = time.time()
        if now - self._last_update < 1.0:
            return  # Rate limit
        
        self._last_update = now
        
        try:
            # Try iw command (works on most Linux/OpenWrt)
            result = subprocess.run(
                ["iw", "dev", self.interface, "station", "dump"],
                capture_output=True, text=True, timeout=5
            )
            
            if result.returncode == 0:
                self._parse_iw_output(result.stdout)
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        try:
            # Fallback: read from sysfs
            stations_path = Path(f"/sys/kernel/debug/ieee80211/phy0/netdev:{self.interface}/stations")
            if stations_path.exists():
                for station in stations_path.iterdir():
                    signal_file = station / "signal"
                    if signal_file.exists():
                        signal = int(signal_file.read_text().strip())
                        self._cache[station.name.lower()] = signal
        except:
            pass
    
    def _parse_iw_output(self, output: str):
        """Parse 'iw station dump' output."""
        current_mac = None
        
        for line in output.split('\n'):
            line = line.strip()
            
            if line.startswith("Station"):
                parts = line.split()
                if len(parts) >= 2:
                    current_mac = parts[1].lower()
            
            elif "signal:" in line.lower() and current_mac:
                # Line like: "signal:  -45 dBm"
                try:
                    signal = int(line.split(':')[1].strip().split()[0])
                    self._cache[current_mac] = signal
                except:
                    pass


# =============================================================================
# GATEWAY MODE
# =============================================================================

class GatewayManager:
    """
    Manages gateway (hoster) functionality.
    
    Responsibilities:
      - Broadcast presence beacons
      - Accept pinger connections
      - Track connected customers
      - Enforce customer limits
      - Report statistics
    """
    
    def __init__(self, config: MeshConfig):
        self.config = config
        self.customers: Dict[str, PingerInfo] = {}
        self.signal_monitor = SignalMonitor()
        
        self._socket: socket.socket = None
        self._running = False
        self._start_time = time.time()
        
        # Load saved state
        self._load_state()
    
    @property
    def gateway_id(self) -> str:
        """Unique gateway identifier."""
        if not self.config.device_id:
            # Generate from MAC address
            self.config.device_id = self._get_device_id()
        return self.config.device_id
    
    def _get_device_id(self) -> str:
        """Generate device ID from MAC address."""
        try:
            # Read MAC from interface
            with open(f"/sys/class/net/eth0/address") as f:
                mac = f.read().strip().replace(":", "")
            return f"GW-{mac[-6:].upper()}"
        except:
            import uuid
            return f"GW-{uuid.uuid4().hex[:6].upper()}"
    
    def _get_mesh_ip(self) -> str:
        """Get our IP on the mesh network."""
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "br-lan"],
                capture_output=True, text=True
            )
            for line in result.stdout.split('\n'):
                if 'inet ' in line:
                    return line.split()[1].split('/')[0]
        except:
            pass
        return "192.168.100.1"
    
    def _load_state(self):
        """Load saved state from disk."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file = Path(self.config.state_file)
        
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                # Restore customers (they might reconnect)
                for cust_id, cust_data in state.get("customers", {}).items():
                    self.customers[cust_id] = PingerInfo(**cust_data)
                logger.info(f"Loaded {len(self.customers)} customers from state")
            except Exception as e:
                logger.warning(f"Could not load state: {e}")
    
    def _save_state(self):
        """Save state to disk."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "gateway_id": self.gateway_id,
            "customers": {
                cid: asdict(cust) for cid, cust in self.customers.items()
            }
        }
        with open(self.config.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def get_beacon_payload(self) -> dict:
        """Generate beacon payload."""
        return {
            "gateway_id": self.gateway_id,
            "public_key": "",  # Filled by tunnel
            "mesh_ip": self._get_mesh_ip(),
            "customer_count": len(self.customers),
            "max_customers": self.config.max_customers,
            "uptime": int(time.time() - self._start_time),
            "version": "1.0",
        }
    
    async def start(self):
        """Start gateway services."""
        logger.info("=" * 60)
        logger.info("  ALKALINE MESH - GATEWAY MODE")
        logger.info("=" * 60)
        logger.info(f"Gateway ID: {self.gateway_id}")
        logger.info(f"Max customers: {self.config.max_customers}")
        logger.info("=" * 60)
        
        # Create UDP socket for discovery
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._socket.bind(("0.0.0.0", DISCOVERY_PORT))
        self._socket.setblocking(False)
        
        self._running = True
        
        # Start tasks
        asyncio.create_task(self._beacon_loop())
        asyncio.create_task(self._listen_loop())
        asyncio.create_task(self._cleanup_loop())
        
        # Keep running
        while self._running:
            await asyncio.sleep(1)
    
    async def _beacon_loop(self):
        """Broadcast presence beacons."""
        while self._running:
            try:
                payload = self.get_beacon_payload()
                message = DiscoveryProtocol.encode(
                    DiscoveryProtocol.TYPE_BEACON, payload
                )
                
                # Broadcast on mesh network
                self._socket.sendto(message, ("255.255.255.255", DISCOVERY_PORT))
                
            except Exception as e:
                logger.error(f"Beacon error: {e}")
            
            await asyncio.sleep(BEACON_INTERVAL)
    
    async def _listen_loop(self):
        """Listen for discovery messages."""
        loop = asyncio.get_event_loop()
        
        while self._running:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
                await self._handle_message(data, addr)
            except Exception as e:
                if self._running:
                    await asyncio.sleep(0.1)
    
    async def _handle_message(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming discovery message."""
        try:
            msg_type, payload = DiscoveryProtocol.decode(data)
            
            if msg_type == DiscoveryProtocol.TYPE_QUERY:
                # Pinger is looking for gateways
                await self._handle_query(payload, addr)
            
            elif msg_type == DiscoveryProtocol.TYPE_HEALTH:
                # Health check from pinger
                await self._handle_health_check(payload, addr)
                
        except Exception as e:
            logger.debug(f"Message handling error: {e}")
    
    async def _handle_query(self, payload: dict, addr: Tuple[str, int]):
        """Respond to gateway query from pinger."""
        pinger_id = payload.get("pinger_id", "")
        
        # Check if we have capacity
        if len(self.customers) >= self.config.max_customers:
            logger.info(f"Query from {pinger_id} - at capacity, not responding")
            return
        
        # Send response
        response = self.get_beacon_payload()
        response["in_response_to"] = pinger_id
        
        message = DiscoveryProtocol.encode(
            DiscoveryProtocol.TYPE_RESPONSE, response
        )
        
        self._socket.sendto(message, addr)
        logger.debug(f"Responded to query from {pinger_id} at {addr}")
    
    async def _handle_health_check(self, payload: dict, addr: Tuple[str, int]):
        """Respond to health check."""
        pinger_id = payload.get("pinger_id", "")
        
        # Update customer last_seen
        if pinger_id in self.customers:
            self.customers[pinger_id].last_seen = time.time()
        
        # Send ACK
        response = {
            "gateway_id": self.gateway_id,
            "pinger_id": pinger_id,
            "timestamp": time.time()
        }
        
        message = DiscoveryProtocol.encode(
            DiscoveryProtocol.TYPE_HEALTH_ACK, response
        )
        
        self._socket.sendto(message, addr)
    
    async def _cleanup_loop(self):
        """Clean up stale customers."""
        while self._running:
            now = time.time()
            stale = []
            
            for cust_id, cust in self.customers.items():
                if now - cust.last_seen > DISCOVERY_TIMEOUT * 4:
                    stale.append(cust_id)
            
            for cust_id in stale:
                logger.info(f"Removing stale customer: {cust_id}")
                del self.customers[cust_id]
            
            if stale:
                self._save_state()
            
            await asyncio.sleep(60)  # Check every minute
    
    def add_customer(self, pinger_id: str, public_key: str, ip_address: str) -> bool:
        """Add a new customer. Returns False if at capacity."""
        if len(self.customers) >= self.config.max_customers:
            return False
        
        self.customers[pinger_id] = PingerInfo(
            pinger_id=pinger_id,
            public_key=public_key,
            ip_address=ip_address,
            connected_at=time.time(),
            last_seen=time.time()
        )
        
        self._save_state()
        logger.info(f"Added customer {pinger_id} ({len(self.customers)}/{self.config.max_customers})")
        return True
    
    def remove_customer(self, pinger_id: str):
        """Remove a customer."""
        if pinger_id in self.customers:
            del self.customers[pinger_id]
            self._save_state()
            logger.info(f"Removed customer {pinger_id}")
    
    def stop(self):
        """Stop the gateway."""
        self._running = False
        self._save_state()
        if self._socket:
            self._socket.close()
        logger.info("Gateway stopped")


# =============================================================================
# PINGER MODE
# =============================================================================

class PingerManager:
    """
    Manages pinger (customer) functionality.
    
    Responsibilities:
      - Discover available gateways
      - Select best gateway
      - Connect and maintain connection
      - Auto-failover if gateway dies
      - Report statistics
    """
    
    def __init__(self, config: MeshConfig):
        self.config = config
        self.gateways: Dict[str, GatewayInfo] = {}
        self.current_gateway: Optional[str] = None
        self.signal_monitor = SignalMonitor()
        
        self._socket: socket.socket = None
        self._running = False
        self._health_failures = 0
        self._last_gateway_switch = 0
        
        # Callbacks
        self.on_gateway_connected: Optional[callable] = None
        self.on_gateway_disconnected: Optional[callable] = None
    
    @property
    def pinger_id(self) -> str:
        """Unique pinger identifier."""
        if not self.config.device_id:
            self.config.device_id = self._get_device_id()
        return self.config.device_id
    
    def _get_device_id(self) -> str:
        """Generate device ID from MAC address."""
        try:
            with open(f"/sys/class/net/eth0/address") as f:
                mac = f.read().strip().replace(":", "")
            return f"PN-{mac[-6:].upper()}"
        except:
            import uuid
            return f"PN-{uuid.uuid4().hex[:6].upper()}"
    
    async def start(self):
        """Start pinger services."""
        logger.info("=" * 60)
        logger.info("  ALKALINE MESH - PINGER MODE")
        logger.info("=" * 60)
        logger.info(f"Pinger ID: {self.pinger_id}")
        logger.info(f"Auto-connect: {self.config.auto_connect}")
        logger.info("=" * 60)
        
        # Create UDP socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._socket.bind(("0.0.0.0", DISCOVERY_PORT))
        self._socket.setblocking(False)
        
        self._running = True
        
        # Start tasks
        asyncio.create_task(self._listen_loop())
        asyncio.create_task(self._discovery_loop())
        asyncio.create_task(self._health_loop())
        asyncio.create_task(self._auto_connect_loop())
        
        # Keep running
        while self._running:
            await asyncio.sleep(1)
    
    async def _listen_loop(self):
        """Listen for gateway beacons and responses."""
        loop = asyncio.get_event_loop()
        
        while self._running:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
                await self._handle_message(data, addr)
            except Exception as e:
                if self._running:
                    await asyncio.sleep(0.1)
    
    async def _handle_message(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming message."""
        try:
            msg_type, payload = DiscoveryProtocol.decode(data)
            
            if msg_type == DiscoveryProtocol.TYPE_BEACON:
                await self._handle_beacon(payload, addr)
            
            elif msg_type == DiscoveryProtocol.TYPE_RESPONSE:
                await self._handle_response(payload, addr)
            
            elif msg_type == DiscoveryProtocol.TYPE_HEALTH_ACK:
                await self._handle_health_ack(payload)
                
        except Exception as e:
            logger.debug(f"Message error: {e}")
    
    async def _handle_beacon(self, payload: dict, addr: Tuple[str, int]):
        """Process gateway beacon."""
        gateway_id = payload.get("gateway_id", "")
        if not gateway_id:
            return
        
        # Get signal strength
        signal = self.signal_monitor.get_signal()
        
        # Update or create gateway info
        self.gateways[gateway_id] = GatewayInfo(
            gateway_id=gateway_id,
            public_key=payload.get("public_key", ""),
            ip_address=addr[0],
            mesh_ip=payload.get("mesh_ip", addr[0]),
            signal_strength=signal,
            hop_count=1,  # TODO: detect actual hop count
            customer_count=payload.get("customer_count", 0),
            max_customers=payload.get("max_customers", 9),
            uptime=payload.get("uptime", 0),
            last_seen=time.time()
        )
        
        logger.debug(f"Beacon from {gateway_id}: signal={signal}dBm, " +
                    f"customers={payload.get('customer_count', 0)}/{payload.get('max_customers', 9)}")
    
    async def _handle_response(self, payload: dict, addr: Tuple[str, int]):
        """Handle response to our query."""
        # Same as beacon but confirms gateway is responding to us
        await self._handle_beacon(payload, addr)
    
    async def _handle_health_ack(self, payload: dict):
        """Handle health check acknowledgment."""
        gateway_id = payload.get("gateway_id", "")
        
        if gateway_id == self.current_gateway:
            self._health_failures = 0
            
            # Calculate latency
            sent_time = payload.get("sent_time", 0)
            if sent_time:
                latency = (time.time() - sent_time) * 1000
                if gateway_id in self.gateways:
                    self.gateways[gateway_id].latency_ms = latency
    
    async def _discovery_loop(self):
        """Periodically discover gateways."""
        while self._running:
            try:
                # Send query broadcast
                query = {
                    "pinger_id": self.pinger_id,
                    "timestamp": time.time()
                }
                message = DiscoveryProtocol.encode(
                    DiscoveryProtocol.TYPE_QUERY, query
                )
                
                self._socket.sendto(message, ("255.255.255.255", DISCOVERY_PORT))
                
                # Clean up stale gateways
                now = time.time()
                stale = [
                    gid for gid, gw in self.gateways.items()
                    if now - gw.last_seen > DISCOVERY_TIMEOUT
                ]
                for gid in stale:
                    logger.info(f"Gateway {gid} went offline")
                    del self.gateways[gid]
                    
                    # If this was our gateway, trigger reconnect
                    if gid == self.current_gateway:
                        self.current_gateway = None
                        if self.on_gateway_disconnected:
                            self.on_gateway_disconnected(gid)
                
            except Exception as e:
                logger.error(f"Discovery error: {e}")
            
            await asyncio.sleep(BEACON_INTERVAL)
    
    async def _health_loop(self):
        """Send health checks to current gateway."""
        while self._running:
            if self.current_gateway and self.current_gateway in self.gateways:
                try:
                    gw = self.gateways[self.current_gateway]
                    
                    health = {
                        "pinger_id": self.pinger_id,
                        "gateway_id": self.current_gateway,
                        "sent_time": time.time()
                    }
                    message = DiscoveryProtocol.encode(
                        DiscoveryProtocol.TYPE_HEALTH, health
                    )
                    
                    self._socket.sendto(message, (gw.ip_address, DISCOVERY_PORT))
                    
                    # Check for failures
                    self._health_failures += 1
                    
                    if self._health_failures >= FAILOVER_THRESHOLD:
                        logger.warning(f"Gateway {self.current_gateway} not responding, failing over")
                        self.current_gateway = None
                        if self.on_gateway_disconnected:
                            self.on_gateway_disconnected(self.current_gateway)
                    
                except Exception as e:
                    logger.error(f"Health check error: {e}")
            
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
    
    async def _auto_connect_loop(self):
        """Automatically connect to best gateway."""
        while self._running:
            if self.config.auto_connect and not self.current_gateway:
                best = self.get_best_gateway()
                if best:
                    await self.connect_to_gateway(best.gateway_id)
            
            await asyncio.sleep(5)
    
    def get_available_gateways(self) -> List[GatewayInfo]:
        """Get list of available gateways, sorted by score."""
        now = time.time()
        available = [
            gw for gw in self.gateways.values()
            if gw.is_available and (now - gw.last_seen) < DISCOVERY_TIMEOUT
        ]
        return sorted(available, key=lambda g: g.score, reverse=True)
    
    def get_best_gateway(self) -> Optional[GatewayInfo]:
        """Get the best available gateway."""
        available = self.get_available_gateways()
        
        # Check for preferred gateway
        if self.config.preferred_gateway:
            for gw in available:
                if gw.gateway_id == self.config.preferred_gateway:
                    return gw
        
        return available[0] if available else None
    
    async def connect_to_gateway(self, gateway_id: str) -> bool:
        """Connect to a specific gateway."""
        if gateway_id not in self.gateways:
            logger.error(f"Unknown gateway: {gateway_id}")
            return False
        
        gw = self.gateways[gateway_id]
        
        if not gw.is_available:
            logger.error(f"Gateway {gateway_id} is at capacity")
            return False
        
        logger.info(f"Connecting to gateway {gateway_id} (signal: {gw.signal_strength}dBm)")
        
        # Set as current gateway
        self.current_gateway = gateway_id
        self._health_failures = 0
        self._last_gateway_switch = time.time()
        
        # Callback
        if self.on_gateway_connected:
            self.on_gateway_connected(gw)
        
        return True
    
    def disconnect(self):
        """Disconnect from current gateway."""
        if self.current_gateway:
            logger.info(f"Disconnecting from {self.current_gateway}")
            old_gw = self.current_gateway
            self.current_gateway = None
            
            if self.on_gateway_disconnected:
                self.on_gateway_disconnected(old_gw)
    
    def stop(self):
        """Stop the pinger."""
        self._running = False
        if self._socket:
            self._socket.close()
        logger.info("Pinger stopped")


# =============================================================================
# INTEGRATION: Mesh + Tunnel
# =============================================================================

class AlkalineMeshNode:
    """
    Complete mesh node - integrates mesh discovery with encrypted tunnel.
    
    This is what actually runs on the Heltec device.
    """
    
    def __init__(self, config: MeshConfig):
        self.config = config
        
        if config.mode == "gateway":
            self.mesh = GatewayManager(config)
        else:
            self.mesh = PingerManager(config)
            self.mesh.on_gateway_connected = self._on_gateway_connected
            self.mesh.on_gateway_disconnected = self._on_gateway_disconnected
        
        self.tunnel_process = None
    
    def _on_gateway_connected(self, gateway: GatewayInfo):
        """Called when pinger connects to a gateway."""
        logger.info(f"Connected to gateway {gateway.gateway_id}")
        
        # Start tunnel to server through this gateway
        self._start_tunnel()
    
    def _on_gateway_disconnected(self, gateway_id: str):
        """Called when pinger disconnects from gateway."""
        logger.info(f"Disconnected from gateway {gateway_id}")
        
        # Stop tunnel
        self._stop_tunnel()
    
    def _start_tunnel(self):
        """Start the encrypted tunnel."""
        if not self.config.server_ip or not self.config.server_pubkey:
            logger.warning("Server not configured, tunnel not started")
            return
        
        # Start alkaline_complete.py in client/gateway mode
        script_dir = Path(__file__).parent
        tunnel_script = script_dir / "alkaline_complete.py"
        
        if not tunnel_script.exists():
            logger.error(f"Tunnel script not found: {tunnel_script}")
            return
        
        mode = "--gateway" if self.config.mode == "gateway" else "--client"
        
        # Generate tunnel IP from device ID (last 2 bytes of ID as IP octets)
        # This ensures each device gets a unique tunnel IP
        device_hash = hash(self.config.device_id) & 0xFFFF
        tunnel_ip = f"10.100.{(device_hash >> 8) & 0xFF}.{device_hash & 0xFF}"
        if tunnel_ip.endswith(".0") or tunnel_ip.endswith(".1"):
            tunnel_ip = f"10.100.{(device_hash >> 8) & 0xFF}.{(device_hash & 0xFF) + 2}"
        
        cmd = [
            sys.executable, str(tunnel_script),
            mode,
            "--server-ip", self.config.server_ip,
            "--server-pubkey", self.config.server_pubkey,
            "--tunnel-ip", tunnel_ip
        ]
        
        logger.info(f"Starting tunnel: {' '.join(cmd)}")
        
        self.tunnel_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    
    def _stop_tunnel(self):
        """Stop the encrypted tunnel."""
        if self.tunnel_process:
            self.tunnel_process.terminate()
            try:
                self.tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel_process.kill()
            self.tunnel_process = None
    
    async def start(self):
        """Start the mesh node."""
        # Start mesh manager
        await self.mesh.start()
    
    def stop(self):
        """Stop the mesh node."""
        self._stop_tunnel()
        self.mesh.stop()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Alkaline Network - Mesh Auto-Discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gateway", action="store_true", 
                     help="Run as gateway (Mesh Gate)")
    mode.add_argument("--pinger", action="store_true",
                     help="Run as pinger (Mesh Point)")
    
    # Gateway options
    parser.add_argument("--max-customers", type=int, default=9,
                       help="Max customers for gateway mode")
    
    # Pinger options
    parser.add_argument("--auto-connect", action="store_true", default=True,
                       help="Auto-connect to best gateway")
    parser.add_argument("--preferred-gateway", type=str,
                       help="Preferred gateway ID")
    
    # Server options (for tunnel)
    parser.add_argument("--server-ip", type=str,
                       help="Alkaline server IP")
    parser.add_argument("--server-port", type=int, default=51820,
                       help="Alkaline server port")
    parser.add_argument("--server-pubkey", type=str,
                       help="Alkaline server public key")
    
    # Config
    parser.add_argument("--config", type=str,
                       help="Config file path")
    
    args = parser.parse_args()
    
    # Build config
    config = MeshConfig()
    
    if args.config and os.path.exists(args.config):
        config = MeshConfig.load(args.config)
    
    config.mode = "gateway" if args.gateway else "pinger"
    config.max_customers = args.max_customers
    config.auto_connect = args.auto_connect
    
    if args.preferred_gateway:
        config.preferred_gateway = args.preferred_gateway
    if args.server_ip:
        config.server_ip = args.server_ip
    if args.server_port:
        config.server_port = args.server_port
    if args.server_pubkey:
        config.server_pubkey = args.server_pubkey
    
    # Create and run node
    node = AlkalineMeshNode(config)
    
    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        node.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run
    try:
        asyncio.run(node.start())
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
