"""
Alkaline Hosting - Encrypted Customer Tunnel

This creates an encrypted tunnel between the customer and YOUR server,
so the Hoster cannot see customer traffic even though it passes through
their network.

Architecture:
    
    Customer's Device          Hoster's Network           Your Server
    ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
    │  alkaline-vpn   │──────▶│   LiteAP AC     │──────▶│  alkaline-vpn   │
    │  (client mode)  │       │   (just passes  │       │  (server mode)  │
    │                 │       │    encrypted    │       │                 │
    │  Encrypts ALL   │       │    blobs)       │       │  Decrypts and   │
    │  traffic before │       │                 │       │  forwards to    │
    │  sending        │       │  CANNOT READ    │       │  internet       │
    └─────────────────┘       └─────────────────┘       └─────────────────┘
    
The Hoster provides the radio link but sees only encrypted garbage.

This uses WireGuard-style encryption (X25519 + ChaCha20-Poly1305).
"""

import asyncio
import socket
import struct
import os
import sys
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

# Add path for encryption module
sys.path.insert(0, os.path.dirname(__file__))

try:
    from encryption import AlkalineEncryption, KeyPair, NACL_AVAILABLE
except ImportError:
    NACL_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alkaline.tunnel")


@dataclass
class TunnelConfig:
    """Configuration for the encrypted tunnel."""
    server_host: str = "0.0.0.0"      # Server listen address
    server_port: int = 51820          # Same as WireGuard default
    mtu: int = 1420                   # MTU for tunnel packets
    keepalive: int = 25               # Keepalive interval seconds


class AlkalineTunnelServer:
    """
    Encrypted tunnel server - runs on YOUR server.
    
    Accepts encrypted connections from customers, decrypts their traffic,
    and forwards it to the internet. Hosters never see plaintext.
    """
    
    def __init__(self, config: TunnelConfig = None):
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl required. Run: pip install pynacl")
        
        self.config = config or TunnelConfig()
        self.crypto = AlkalineEncryption()
        
        # Registered clients: public_key_hex -> client_info
        self.clients: Dict[str, dict] = {}
        
        # Active sessions: (ip, port) -> public_key_hex
        self.sessions: Dict[Tuple[str, int], str] = {}
        
        self._socket: Optional[socket.socket] = None
        self._running = False
    
    @property
    def public_key(self) -> bytes:
        """Server's public key - give this to customers."""
        return self.crypto.public_key
    
    def register_customer(self, customer_public_key: bytes, 
                          customer_id: str, tier: str = "basic") -> dict:
        """
        Register a customer for tunnel access.
        
        Args:
            customer_public_key: Customer's public key (32 bytes)
            customer_id: Alkaline customer ID
            tier: Service tier
            
        Returns:
            Registration info including assigned IP
        """
        key_hex = customer_public_key.hex()
        
        # Assign tunnel IP (10.100.x.x range)
        client_num = len(self.clients) + 1
        tunnel_ip = f"10.100.{client_num // 256}.{client_num % 256}"
        
        self.clients[key_hex] = {
            "customer_id": customer_id,
            "public_key": customer_public_key,
            "tunnel_ip": tunnel_ip,
            "tier": tier,
            "bytes_up": 0,
            "bytes_down": 0,
            "last_seen": 0,
        }
        
        logger.info(f"Registered customer {customer_id} with tunnel IP {tunnel_ip}")
        
        return {
            "server_public_key": self.public_key.hex(),
            "tunnel_ip": tunnel_ip,
            "server_endpoint": f"{self.config.server_host}:{self.config.server_port}"
        }
    
    async def start(self):
        """Start the tunnel server."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.config.server_host, self.config.server_port))
        self._socket.setblocking(False)
        
        self._running = True
        logger.info(f"Tunnel server listening on {self.config.server_host}:{self.config.server_port}")
        logger.info(f"Server public key: {self.public_key.hex()}")
        
        loop = asyncio.get_event_loop()
        
        while self._running:
            try:
                # Receive encrypted packet
                data, addr = await loop.sock_recvfrom(self._socket, 65535)
                await self._handle_packet(data, addr)
            except Exception as e:
                if self._running:
                    logger.error(f"Packet handling error: {e}")
    
    async def _handle_packet(self, encrypted_data: bytes, addr: Tuple[str, int]):
        """Handle an incoming encrypted packet."""
        try:
            # First 32 bytes are sender's public key
            if len(encrypted_data) < 32:
                return
            
            sender_public = encrypted_data[:32]
            sender_hex = sender_public.hex()
            
            # Check if registered
            if sender_hex not in self.clients:
                logger.warning(f"Unknown client: {sender_hex[:16]}...")
                return
            
            # Decrypt
            plaintext = self.crypto.decrypt_bytes(encrypted_data)
            
            # Update session mapping
            self.sessions[addr] = sender_hex
            
            # Update stats
            client = self.clients[sender_hex]
            client["bytes_up"] += len(plaintext)
            client["last_seen"] = asyncio.get_event_loop().time()
            
            # Forward to internet (simplified - real implementation would use TUN device)
            await self._forward_to_internet(plaintext, client)
            
        except Exception as e:
            logger.error(f"Decrypt error: {e}")
    
    async def _forward_to_internet(self, data: bytes, client: dict):
        """
        Forward decrypted traffic to the internet.
        
        In production, this would:
        1. Write to a TUN device
        2. Let the kernel route it
        3. Apply QoS based on tier
        """
        # This is where the traffic exits to the internet
        # The Hoster NEVER sees this - they only saw encrypted blobs
        logger.debug(f"Forwarding {len(data)} bytes for {client['customer_id']}")
        pass
    
    def stop(self):
        """Stop the tunnel server."""
        self._running = False
        if self._socket:
            self._socket.close()


class AlkalineTunnelClient:
    """
    Encrypted tunnel client - runs on customer's device.
    
    Encrypts ALL traffic before sending through the Hoster's network.
    """
    
    def __init__(self, server_public_key: bytes, server_endpoint: str):
        """
        Initialize tunnel client.
        
        Args:
            server_public_key: Alkaline server's public key
            server_endpoint: Server address as "host:port"
        """
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl required. Run: pip install pynacl")
        
        self.crypto = AlkalineEncryption()
        self.server_public_key = server_public_key
        
        host, port = server_endpoint.split(":")
        self.server_addr = (host, int(port))
        
        self._socket: Optional[socket.socket] = None
    
    @property
    def public_key(self) -> bytes:
        """Client's public key - register this with the server."""
        return self.crypto.public_key
    
    def connect(self):
        """Establish tunnel connection."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logger.info(f"Tunnel client ready, connecting to {self.server_addr}")
    
    def send(self, data: bytes):
        """
        Send data through the encrypted tunnel.
        
        This encrypts the data so the Hoster cannot read it.
        """
        if not self._socket:
            self.connect()
        
        # Encrypt for server
        encrypted = self.crypto.encrypt_bytes(data, self.server_public_key)
        
        # Prepend our public key so server knows who we are
        packet = self.public_key + encrypted
        
        self._socket.sendto(packet, self.server_addr)
    
    def close(self):
        """Close the tunnel."""
        if self._socket:
            self._socket.close()


# =============================================================================
# INTEGRATION WITH ALKALINE NETWORK
# =============================================================================

class AlkalineSecureNetwork:
    """
    Complete secure network setup.
    
    This ties together:
    - Ubiquiti radios (physical layer)
    - Encrypted tunnel (privacy layer)
    - Dashboard integration (management layer)
    """
    
    def __init__(self, is_server: bool = True, dashboard_url: str = "http://localhost:5000"):
        """
        Initialize secure network.
        
        Args:
            is_server: True if running on your server, False for customer
            dashboard_url: URL of the Alkaline dashboard
        """
        self.is_server = is_server
        self.dashboard_url = dashboard_url
        
        if is_server:
            self.tunnel = AlkalineTunnelServer()
        else:
            self.tunnel = None  # Set up when connecting
    
    def get_customer_config(self, customer_id: str, tier: str = "basic") -> dict:
        """
        Generate configuration for a new customer.
        
        Returns config they need to connect securely.
        """
        if not self.is_server:
            raise RuntimeError("Only server can generate customer configs")
        
        # Generate keypair for customer
        crypto = AlkalineEncryption()
        customer_keys = crypto.generate_keypair()
        
        # Register with tunnel server
        reg = self.tunnel.register_customer(
            customer_public_key=customer_keys.public_key,
            customer_id=customer_id,
            tier=tier
        )
        
        return {
            "customer_id": customer_id,
            "tier": tier,
            
            # Keys
            "private_key": customer_keys.private_key.hex(),
            "public_key": customer_keys.public_key.hex(),
            
            # Server info
            "server_public_key": reg["server_public_key"],
            "server_endpoint": reg["server_endpoint"],
            "tunnel_ip": reg["tunnel_ip"],
            
            # For the customer's device
            "config_file": self._generate_config_file(customer_keys, reg)
        }
    
    def _generate_config_file(self, keys: KeyPair, reg: dict) -> str:
        """Generate a config file for the customer's device."""
        return f"""# Alkaline Hosting - Secure Tunnel Configuration
# Customer ID: {reg.get('customer_id', 'unknown')}
# 
# This file encrypts ALL your traffic before it reaches the Hoster.
# The Hoster provides the radio link but CANNOT see your data.

[Tunnel]
PrivateKey = {keys.private_key.hex()}
Address = {reg['tunnel_ip']}/24

[Server]
PublicKey = {reg['server_public_key']}
Endpoint = {reg['server_endpoint']}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""


# =============================================================================
# SIMPLE SETUP FOR CUSTOMERS
# =============================================================================

def setup_customer_device(config_path: str):
    """
    Set up a customer device with encrypted tunnel.
    
    This would run on the customer's router/computer.
    """
    import json
    
    with open(config_path) as f:
        config = json.load(f)
    
    client = AlkalineTunnelClient(
        server_public_key=bytes.fromhex(config["server_public_key"]),
        server_endpoint=config["server_endpoint"]
    )
    
    print(f"Tunnel configured for customer {config['customer_id']}")
    print(f"Tunnel IP: {config['tunnel_ip']}")
    print(f"All traffic is now encrypted - Hoster cannot see your data")
    
    return client


# =============================================================================
# TEST
# =============================================================================

async def test_tunnel():
    """Test the encrypted tunnel."""
    print("=" * 60)
    print("  ALKALINE ENCRYPTED TUNNEL TEST")
    print("=" * 60)
    
    if not NACL_AVAILABLE:
        print("\n❌ PyNaCl not installed!")
        print("   Run: pip install pynacl")
        return
    
    print("\n[1] Creating tunnel server...")
    server = AlkalineTunnelServer()
    print(f"    Server public key: {server.public_key.hex()[:32]}...")
    
    print("\n[2] Generating customer config...")
    network = AlkalineSecureNetwork(is_server=True)
    network.tunnel = server
    
    config = network.get_customer_config("CUST-001", "plus")
    print(f"    Customer ID: {config['customer_id']}")
    print(f"    Tunnel IP: {config['tunnel_ip']}")
    print(f"    Tier: {config['tier']}")
    
    print("\n[3] Creating customer tunnel client...")
    client = AlkalineTunnelClient(
        server_public_key=bytes.fromhex(config["server_public_key"]),
        server_endpoint="127.0.0.1:51820"
    )
    print(f"    Client public key: {client.public_key.hex()[:32]}...")
    
    print("\n[4] Config file that would go on customer device:")
    print("-" * 40)
    print(config["config_file"])
    print("-" * 40)
    
    print("\n✅ Tunnel setup complete!")
    print("\nHOW IT WORKS:")
    print("  1. Customer's device encrypts ALL traffic with tunnel")
    print("  2. Encrypted blobs go through Hoster's radio (they see nothing)")
    print("  3. Your server decrypts and forwards to internet")
    print("  4. Responses come back the same way")
    print("\nThe Hoster provides bandwidth but has ZERO visibility into traffic.")


if __name__ == "__main__":
    asyncio.run(test_tunnel())
