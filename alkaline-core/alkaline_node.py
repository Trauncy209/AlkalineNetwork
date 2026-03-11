#!/usr/bin/env python3
"""
Alkaline Node - The Integration Layer

This is the missing piece that connects:
- encryption.py (NaCl/libsodium crypto)
- protocol.py (compression)
- radio.py (KISS/AX.25 radio protocols)

Into a working mesh network node.

ARCHITECTURE:
=============

    USER DEVICE                    ALKALINE NODE                      GATEWAY NODE
    (phone/laptop)                 (Raspberry Pi)                     (has internet)
    
    ┌─────────────┐               ┌─────────────────┐               ┌─────────────────┐
    │             │               │                 │               │                 │
    │  Browser    │──WiFi/ETH───▶│  1. Receive     │               │                 │
    │  App        │               │  2. Compress    │───RADIO──────▶│  1. Receive     │
    │  etc        │               │  3. Encrypt     │   (encrypted) │  2. Decrypt     │
    │             │               │  4. Transmit    │               │  3. Decompress  │
    │             │               │                 │               │  4. Forward     │──▶ INTERNET
    └─────────────┘               └─────────────────┘               └─────────────────┘
    
    Traffic is encrypted BEFORE it leaves the node.
    Radio link only sees encrypted blobs.
    Gateway decrypts and forwards to internet.


MODES:
======

    CLIENT MODE:  User device → [compress → encrypt → radio] → Gateway
    GATEWAY MODE: Radio → [decrypt → decompress] → Internet
    RELAY MODE:   Radio → [forward encrypted] → Radio (no decryption)


USAGE:
======

    # Start as gateway (has internet, serves clients)
    python alkaline_node.py --mode gateway --radio /dev/ttyUSB0
    
    # Start as client (connects to gateway)
    python alkaline_node.py --mode client --gateway-key <pubkey> --radio /dev/ttyUSB0
    
    # Start as relay (forwards packets, can't read them)
    python alkaline_node.py --mode relay --radio /dev/ttyUSB0

"""

import os
import sys
import time
import json
import struct
import asyncio
import logging
import hashlib
import argparse
import socket
import select
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable
from enum import Enum
from collections import deque

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class NodeConfig:
    """Configuration for an Alkaline node."""
    
    # Identity
    node_id: str = ""
    
    # Mode: 'client', 'gateway', 'relay'
    mode: str = "client"
    
    # Network
    gateway_public_key: Optional[bytes] = None
    gateway_address: str = ""  # For direct IP connection (testing)
    
    # Radio
    radio_device: str = "/dev/ttyUSB0"
    radio_baud: int = 115200
    frequency_mhz: float = 915.0
    
    # Local network (what devices connect to this node)
    local_interface: str = "wlan0"
    local_ip: str = "10.42.0.1"
    local_netmask: str = "255.255.255.0"
    dhcp_range_start: str = "10.42.0.10"
    dhcp_range_end: str = "10.42.0.250"
    
    # Paths
    identity_path: str = "~/.alkaline/identity"
    config_path: str = "~/.alkaline/config.json"
    
    # Performance
    compression_level: int = 6
    max_packet_size: int = 250  # LoRa max is ~250 bytes
    keepalive_interval: int = 30
    
    def save(self, path: str = None):
        """Save config to file."""
        path = path or os.path.expanduser(self.config_path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'node_id': self.node_id,
            'mode': self.mode,
            'gateway_public_key': self.gateway_public_key.hex() if self.gateway_public_key else None,
            'gateway_address': self.gateway_address,
            'radio_device': self.radio_device,
            'radio_baud': self.radio_baud,
            'frequency_mhz': self.frequency_mhz,
            'local_interface': self.local_interface,
            'local_ip': self.local_ip,
        }
        
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    
    @classmethod
    def load(cls, path: str) -> 'NodeConfig':
        """Load config from file."""
        path = os.path.expanduser(path)
        with open(path, 'r') as f:
            data = json.load(f)
        
        config = cls()
        config.node_id = data.get('node_id', '')
        config.mode = data.get('mode', 'client')
        if data.get('gateway_public_key'):
            config.gateway_public_key = bytes.fromhex(data['gateway_public_key'])
        config.gateway_address = data.get('gateway_address', '')
        config.radio_device = data.get('radio_device', '/dev/ttyUSB0')
        config.radio_baud = data.get('radio_baud', 115200)
        config.frequency_mhz = data.get('frequency_mhz', 915.0)
        config.local_interface = data.get('local_interface', 'wlan0')
        config.local_ip = data.get('local_ip', '10.42.0.1')
        
        return config


# =============================================================================
# PACKET TYPES
# =============================================================================

class PacketType(Enum):
    """Types of packets in the Alkaline protocol."""
    DATA = 0x01           # Regular data packet
    ACK = 0x02            # Acknowledgment
    KEEPALIVE = 0x03      # Keepalive/heartbeat
    DISCOVER = 0x04       # Gateway discovery
    ANNOUNCE = 0x05       # Gateway announcement
    KEY_EXCHANGE = 0x06   # Key exchange for new session
    DISCONNECT = 0x07     # Clean disconnect


@dataclass
class Packet:
    """An Alkaline network packet."""
    type: PacketType
    source: bytes          # 32-byte public key
    destination: bytes     # 32-byte public key (or broadcast)
    sequence: int          # 16-bit sequence number
    payload: bytes         # Encrypted and compressed data
    timestamp: int = 0     # Unix timestamp
    
    HEADER_SIZE = 32 + 32 + 2 + 4 + 1  # source + dest + seq + timestamp + type = 71 bytes
    BROADCAST = b'\xff' * 32
    
    def to_bytes(self) -> bytes:
        """Serialize packet to bytes."""
        header = struct.pack(
            '>B',           # type (1 byte)
            self.type.value
        )
        header += self.source
        header += self.destination
        header += struct.pack('>H', self.sequence)
        header += struct.pack('>I', self.timestamp or int(time.time()))
        
        return header + self.payload
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'Packet':
        """Deserialize packet from bytes."""
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(f"Packet too short: {len(data)} < {cls.HEADER_SIZE}")
        
        ptype = PacketType(data[0])
        source = data[1:33]
        destination = data[33:65]
        sequence = struct.unpack('>H', data[65:67])[0]
        timestamp = struct.unpack('>I', data[67:71])[0]
        payload = data[71:]
        
        return cls(
            type=ptype,
            source=source,
            destination=destination,
            sequence=sequence,
            payload=payload,
            timestamp=timestamp
        )
    
    def is_broadcast(self) -> bool:
        """Check if this is a broadcast packet."""
        return self.destination == self.BROADCAST


# =============================================================================
# CRYPTO WRAPPER (uses encryption.py)
# =============================================================================

class NodeCrypto:
    """
    Cryptographic operations for a node.
    Wraps encryption.py with node-specific functionality.
    """
    
    def __init__(self, identity_path: str = None):
        """
        Initialize crypto with identity.
        
        Args:
            identity_path: Path to identity file. Creates new if doesn't exist.
        """
        # Import encryption module
        try:
            from encryption import AlkalineEncryption, KeyPair, NACL_AVAILABLE
            if not NACL_AVAILABLE:
                raise ImportError("PyNaCl not installed")
        except ImportError:
            # Fallback: try to find it
            for search_path in ['alkaline-core/src', 'src', '.']:
                sys.path.insert(0, search_path)
                try:
                    from encryption import AlkalineEncryption, KeyPair, NACL_AVAILABLE
                    break
                except ImportError:
                    continue
            else:
                raise ImportError("Cannot find encryption.py - run: pip install pynacl")
        
        self.identity_path = os.path.expanduser(identity_path or "~/.alkaline/identity")
        
        # Load or create identity
        if os.path.exists(self.identity_path):
            with open(self.identity_path, 'rb') as f:
                private_key = f.read(32)
            self._crypto = AlkalineEncryption(private_key)
        else:
            self._crypto = AlkalineEncryption()
            self._save_identity()
        
        # Cache of peer crypto contexts
        self._peer_cache: Dict[bytes, 'AlkalineEncryption'] = {}
    
    def _save_identity(self):
        """Save identity to file."""
        Path(self.identity_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.identity_path, 'wb') as f:
            f.write(self._crypto.private_key)
        os.chmod(self.identity_path, 0o600)
    
    @property
    def public_key(self) -> bytes:
        """Get our public key."""
        return self._crypto.public_key
    
    @property
    def public_key_short(self) -> str:
        """Get short form of public key for display."""
        return self.public_key.hex()[:16] + "..."
    
    def encrypt(self, data: bytes, recipient_public_key: bytes) -> bytes:
        """
        Encrypt data for a specific recipient.
        
        Args:
            data: Plaintext data
            recipient_public_key: 32-byte public key
            
        Returns:
            Encrypted data (nonce + ciphertext)
        """
        return self._crypto.encrypt_bytes(data, recipient_public_key)
    
    def decrypt(self, data: bytes, sender_public_key: bytes = None) -> bytes:
        """
        Decrypt data from a sender.
        
        Args:
            data: Encrypted data (nonce + ciphertext)
            sender_public_key: 32-byte public key (extracted from data if not provided)
            
        Returns:
            Decrypted plaintext
        """
        return self._crypto.decrypt_bytes(data, sender_public_key)


# =============================================================================
# COMPRESSION WRAPPER (uses protocol.py)
# =============================================================================

class NodeCompression:
    """
    Compression for packets.
    Wraps protocol.py.
    """
    
    def __init__(self, level: int = 6):
        """
        Initialize compression.
        
        Args:
            level: Compression level (1-9, higher = more compression, slower)
        """
        self.level = level
        
        # Try to import protocol.py
        try:
            from protocol import AlkalineProtocol
            self._proto = AlkalineProtocol()
        except ImportError:
            # Fallback to zlib directly
            self._proto = None
        
        import zlib
        self._zlib = zlib
        
        # Stats
        self.total_original = 0
        self.total_compressed = 0
    
    def compress(self, data: bytes) -> bytes:
        """Compress data."""
        if len(data) < 10:
            # Too small to benefit from compression
            return b'\x00' + data  # 0x00 = not compressed
        
        if self._proto:
            compressed = self._proto.compress(data)
        else:
            compressed = self._zlib.compress(data, self.level)
        
        # Only use compressed if it's actually smaller
        if len(compressed) < len(data):
            self.total_original += len(data)
            self.total_compressed += len(compressed)
            return b'\x01' + compressed  # 0x01 = compressed
        else:
            self.total_original += len(data)
            self.total_compressed += len(data)
            return b'\x00' + data  # 0x00 = not compressed
    
    def decompress(self, data: bytes) -> bytes:
        """Decompress data."""
        if len(data) < 1:
            return data
        
        flag = data[0]
        payload = data[1:]
        
        if flag == 0x00:
            # Not compressed
            return payload
        elif flag == 0x01:
            # Compressed
            if self._proto:
                return self._proto.decompress(payload)
            else:
                return self._zlib.decompress(payload)
        else:
            # Unknown flag, return as-is
            return payload
    
    @property
    def ratio(self) -> float:
        """Get compression ratio (0-100%)."""
        if self.total_original == 0:
            return 0.0
        return (1 - self.total_compressed / self.total_original) * 100


# =============================================================================
# RADIO WRAPPER (uses radio.py)
# =============================================================================

class NodeRadio:
    """
    Radio interface for a node.
    Wraps radio.py.
    """
    
    def __init__(self, device: str = "/dev/ttyUSB0", baud: int = 115200):
        """
        Initialize radio.
        
        Args:
            device: Serial device path
            baud: Baud rate
        """
        self.device = device
        self.baud = baud
        self._serial = None
        self._kiss = None
        
        # Try to import radio.py
        try:
            from radio import KISS, AlkalineRadio
            self._kiss = KISS()
        except ImportError:
            pass
        
        # Queue for received packets
        self._rx_queue: deque = deque(maxlen=100)
        
        # Stats
        self.packets_sent = 0
        self.packets_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
    
    def open(self):
        """Open the radio device."""
        if self.device == "simulate":
            # Simulation mode - no actual hardware
            return
        
        try:
            import serial
            self._serial = serial.Serial(
                self.device,
                self.baud,
                timeout=0.1
            )
        except ImportError:
            raise ImportError("pyserial not installed. Run: pip install pyserial")
        except Exception as e:
            raise RuntimeError(f"Cannot open {self.device}: {e}")
    
    def close(self):
        """Close the radio device."""
        if self._serial:
            self._serial.close()
            self._serial = None
    
    def send(self, data: bytes) -> bool:
        """
        Send data over radio.
        
        Args:
            data: Raw bytes to send
            
        Returns:
            True if sent successfully
        """
        # Encode with KISS framing
        if self._kiss:
            frame = self._kiss.encode(data)
        else:
            # Simple framing: length + data
            frame = struct.pack('>H', len(data)) + data
        
        if self.device == "simulate":
            # Simulation - just log
            self.packets_sent += 1
            self.bytes_sent += len(data)
            return True
        
        if self._serial:
            try:
                self._serial.write(frame)
                self.packets_sent += 1
                self.bytes_sent += len(data)
                return True
            except Exception as e:
                logging.error(f"Radio send error: {e}")
                return False
        
        return False
    
    def receive(self) -> Optional[bytes]:
        """
        Receive data from radio (non-blocking).
        
        Returns:
            Received data or None if nothing available
        """
        if self.device == "simulate":
            # Simulation - check queue
            if self._rx_queue:
                return self._rx_queue.popleft()
            return None
        
        if self._serial:
            try:
                if self._serial.in_waiting > 0:
                    # Read available data
                    raw = self._serial.read(self._serial.in_waiting)
                    
                    # Decode KISS frame
                    if self._kiss:
                        data = self._kiss.decode(raw)
                    else:
                        # Simple framing
                        if len(raw) >= 2:
                            length = struct.unpack('>H', raw[:2])[0]
                            data = raw[2:2+length]
                        else:
                            data = raw
                    
                    if data:
                        self.packets_received += 1
                        self.bytes_received += len(data)
                        return data
            except Exception as e:
                logging.error(f"Radio receive error: {e}")
        
        return None
    
    def simulate_receive(self, data: bytes):
        """Inject data for simulation/testing."""
        self._rx_queue.append(data)


# =============================================================================
# THE NODE - BRINGS IT ALL TOGETHER
# =============================================================================

class AlkalineNode:
    """
    An Alkaline network node.
    
    This is the main integration class that brings together:
    - Crypto (encryption.py)
    - Compression (protocol.py)
    - Radio (radio.py)
    
    Into a working mesh network node.
    """
    
    def __init__(self, config: NodeConfig = None):
        """
        Initialize node.
        
        Args:
            config: Node configuration
        """
        self.config = config or NodeConfig()
        self.running = False
        
        # Initialize components
        self.crypto = NodeCrypto(self.config.identity_path)
        self.compression = NodeCompression(self.config.compression_level)
        self.radio = NodeRadio(self.config.radio_device, self.config.radio_baud)
        
        # Set node ID from public key if not set
        if not self.config.node_id:
            self.config.node_id = self.crypto.public_key.hex()[:8]
        
        # Sequence number for packets
        self._sequence = 0
        
        # Known peers (public_key -> last_seen timestamp)
        self.peers: Dict[bytes, int] = {}
        
        # Gateway info (for client mode)
        self.gateway_key: Optional[bytes] = self.config.gateway_public_key
        
        # Pending data to send (for client mode)
        self._send_queue: deque = deque(maxlen=1000)
        
        # Callbacks
        self._on_receive: Optional[Callable[[bytes, bytes], None]] = None
        
        # Logger
        self.log = logging.getLogger(f"alkaline.node.{self.config.node_id}")
    
    def _next_sequence(self) -> int:
        """Get next sequence number."""
        self._sequence = (self._sequence + 1) % 65536
        return self._sequence
    
    # -------------------------------------------------------------------------
    # CORE OPERATIONS: The data flow
    # -------------------------------------------------------------------------
    
    def send_to_gateway(self, data: bytes, destination: str = "") -> bool:
        """
        Send data to the gateway (client mode).
        
        This is the main entry point for user traffic.
        
        Flow:
            User data → Compress → Encrypt → Radio → Gateway
        
        Args:
            data: Raw data to send
            destination: Final destination (e.g., "google.com:443")
            
        Returns:
            True if queued successfully
        """
        if not self.gateway_key:
            self.log.error("No gateway key configured")
            return False
        
        # 1. Prepend destination if provided
        if destination:
            dest_bytes = destination.encode('utf-8')
            data = struct.pack('>H', len(dest_bytes)) + dest_bytes + data
        
        # 2. Compress
        compressed = self.compression.compress(data)
        self.log.debug(f"Compressed {len(data)} → {len(compressed)} bytes")
        
        # 3. Encrypt for gateway
        encrypted = self.crypto.encrypt(compressed, self.gateway_key)
        self.log.debug(f"Encrypted to {len(encrypted)} bytes")
        
        # 4. Build packet
        packet = Packet(
            type=PacketType.DATA,
            source=self.crypto.public_key,
            destination=self.gateway_key,
            sequence=self._next_sequence(),
            payload=encrypted
        )
        
        # 5. Send over radio
        packet_bytes = packet.to_bytes()
        
        if len(packet_bytes) > self.config.max_packet_size:
            # TODO: Fragment large packets
            self.log.warning(f"Packet too large: {len(packet_bytes)} > {self.config.max_packet_size}")
            return False
        
        success = self.radio.send(packet_bytes)
        if success:
            self.log.info(f"Sent {len(data)} bytes to gateway (encrypted: {len(encrypted)})")
        
        return success
    
    def receive_from_radio(self) -> Optional[Tuple[bytes, bytes]]:
        """
        Receive and process a packet from radio (gateway mode).
        
        Flow:
            Radio → Decrypt → Decompress → User data
        
        Returns:
            Tuple of (sender_public_key, decrypted_data) or None
        """
        # 1. Get raw packet from radio
        raw = self.radio.receive()
        if not raw:
            return None
        
        try:
            # 2. Parse packet
            packet = Packet.from_bytes(raw)
            
            # 3. Check if it's for us
            if packet.destination != self.crypto.public_key and not packet.is_broadcast():
                # Not for us - could relay if in relay mode
                if self.config.mode == 'relay':
                    self.radio.send(raw)  # Forward as-is
                return None
            
            # 4. Update peer tracking
            self.peers[packet.source] = int(time.time())
            
            # 5. Handle by packet type
            if packet.type == PacketType.DATA:
                return self._handle_data_packet(packet)
            elif packet.type == PacketType.KEEPALIVE:
                self.log.debug(f"Keepalive from {packet.source.hex()[:8]}")
                return None
            elif packet.type == PacketType.DISCOVER:
                self._handle_discover(packet)
                return None
            else:
                self.log.warning(f"Unknown packet type: {packet.type}")
                return None
                
        except Exception as e:
            self.log.error(f"Error processing packet: {e}")
            return None
    
    def _handle_data_packet(self, packet: Packet) -> Optional[Tuple[bytes, bytes]]:
        """Handle a DATA packet."""
        try:
            # 1. Decrypt
            decrypted = self.crypto.decrypt(packet.payload, packet.source)
            
            # 2. Decompress
            decompressed = self.compression.decompress(decrypted)
            
            # 3. Extract destination if present
            # (First 2 bytes = destination length)
            if len(decompressed) >= 2:
                dest_len = struct.unpack('>H', decompressed[:2])[0]
                if dest_len > 0 and dest_len < len(decompressed) - 2:
                    # destination = decompressed[2:2+dest_len].decode('utf-8')
                    data = decompressed[2+dest_len:]
                else:
                    data = decompressed
            else:
                data = decompressed
            
            self.log.info(f"Received {len(data)} bytes from {packet.source.hex()[:8]}")
            
            return (packet.source, data)
            
        except Exception as e:
            self.log.error(f"Error decrypting packet: {e}")
            return None
    
    def _handle_discover(self, packet: Packet):
        """Handle a DISCOVER packet (announce ourselves as gateway)."""
        if self.config.mode != 'gateway':
            return
        
        # Send announcement
        announce = Packet(
            type=PacketType.ANNOUNCE,
            source=self.crypto.public_key,
            destination=Packet.BROADCAST,
            sequence=self._next_sequence(),
            payload=b''  # Could include gateway metadata
        )
        
        self.radio.send(announce.to_bytes())
        self.log.info(f"Announced as gateway to {packet.source.hex()[:8]}")
    
    # -------------------------------------------------------------------------
    # MAIN LOOP
    # -------------------------------------------------------------------------
    
    async def run(self):
        """Main run loop."""
        self.running = True
        self.log.info(f"Starting node {self.config.node_id} in {self.config.mode} mode")
        self.log.info(f"Public key: {self.crypto.public_key_short}")
        
        # Open radio
        try:
            self.radio.open()
        except Exception as e:
            self.log.error(f"Failed to open radio: {e}")
            if self.config.radio_device != "simulate":
                return
        
        last_keepalive = 0
        
        try:
            while self.running:
                # Process incoming packets
                result = self.receive_from_radio()
                if result:
                    sender, data = result
                    if self._on_receive:
                        self._on_receive(sender, data)
                    
                    # Gateway mode: forward to internet
                    if self.config.mode == 'gateway':
                        await self._forward_to_internet(sender, data)
                
                # Send keepalives
                now = time.time()
                if now - last_keepalive > self.config.keepalive_interval:
                    self._send_keepalive()
                    last_keepalive = now
                
                # Don't spin
                await asyncio.sleep(0.01)
                
        finally:
            self.radio.close()
            self.log.info("Node stopped")
    
    def _send_keepalive(self):
        """Send a keepalive packet."""
        if self.config.mode == 'client' and self.gateway_key:
            dest = self.gateway_key
        else:
            dest = Packet.BROADCAST
        
        packet = Packet(
            type=PacketType.KEEPALIVE,
            source=self.crypto.public_key,
            destination=dest,
            sequence=self._next_sequence(),
            payload=b''
        )
        
        self.radio.send(packet.to_bytes())
    
    async def _forward_to_internet(self, sender: bytes, data: bytes):
        """
        Forward decrypted data to internet (gateway mode).
        
        This is where the gateway takes decrypted user traffic and sends it
        to the actual internet, then returns the response.
        
        Data format from client:
            [dest_len:2][destination:dest_len][payload]
        Where destination is like "google.com:443" or "93.184.216.34:80"
        """
        try:
            # Parse destination from data
            if len(data) < 2:
                self.log.warning("Data too short to contain destination")
                return
            
            dest_len = struct.unpack('>H', data[:2])[0]
            if dest_len == 0 or dest_len > 255 or len(data) < 2 + dest_len:
                self.log.warning(f"Invalid destination length: {dest_len}")
                return
            
            destination = data[2:2+dest_len].decode('utf-8')
            payload = data[2+dest_len:]
            
            # Parse host:port
            if ':' in destination:
                host, port_str = destination.rsplit(':', 1)
                port = int(port_str)
            else:
                host = destination
                port = 80
            
            self.log.info(f"Forwarding {len(payload)} bytes to {host}:{port} for {sender.hex()[:8]}")
            
            # Create connection to destination
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                self.log.error(f"Connection timeout to {host}:{port}")
                return
            except Exception as e:
                self.log.error(f"Failed to connect to {host}:{port}: {e}")
                return
            
            try:
                # Send payload
                writer.write(payload)
                await writer.drain()
                
                # Read response (up to 64KB for now)
                response = await asyncio.wait_for(
                    reader.read(65536),
                    timeout=30.0
                )
                
                if response:
                    # Send response back to client (encrypted)
                    await self._send_response_to_client(sender, response)
                    self.log.info(f"Sent {len(response)} byte response back to {sender.hex()[:8]}")
                    
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass
                    
        except Exception as e:
            self.log.error(f"Internet forwarding error: {e}")
    
    async def _send_response_to_client(self, client_key: bytes, data: bytes):
        """Send response data back to a client."""
        # Compress
        compressed = self.compression.compress(data)
        
        # Encrypt for client
        encrypted = self.crypto.encrypt(compressed, client_key)
        
        # Build packet
        packet = Packet(
            type=PacketType.DATA,
            source=self.crypto.public_key,
            destination=client_key,
            sequence=self._next_sequence(),
            payload=encrypted
        )
        
        # Send over radio
        packet_bytes = packet.to_bytes()
        
        # Fragment if needed
        if len(packet_bytes) > self.config.max_packet_size:
            # TODO: Implement fragmentation for large responses
            self.log.warning(f"Response too large ({len(packet_bytes)} bytes), needs fragmentation")
            return
        
        self.radio.send(packet_bytes)
    
    def stop(self):
        """Stop the node."""
        self.running = False
    
    # -------------------------------------------------------------------------
    # STATUS
    # -------------------------------------------------------------------------
    
    def status(self) -> dict:
        """Get node status."""
        return {
            'node_id': self.config.node_id,
            'mode': self.config.mode,
            'public_key': self.crypto.public_key.hex(),
            'peers': len(self.peers),
            'radio': {
                'device': self.config.radio_device,
                'packets_sent': self.radio.packets_sent,
                'packets_received': self.radio.packets_received,
                'bytes_sent': self.radio.bytes_sent,
                'bytes_received': self.radio.bytes_received,
            },
            'compression_ratio': f"{self.compression.ratio:.1f}%",
        }


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Alkaline Network Node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start as gateway (has internet access)
  python alkaline_node.py --mode gateway
  
  # Start as client (connects to gateway)
  python alkaline_node.py --mode client --gateway-key <hex>
  
  # Start as relay (forwards packets)
  python alkaline_node.py --mode relay
  
  # Simulation mode (no hardware)
  python alkaline_node.py --mode gateway --radio simulate
        """
    )
    
    parser.add_argument('--mode', choices=['client', 'gateway', 'relay'],
                        default='client', help='Node mode')
    parser.add_argument('--gateway-key', type=str,
                        help='Gateway public key (hex) for client mode')
    parser.add_argument('--radio', type=str, default='/dev/ttyUSB0',
                        help='Radio device (or "simulate")')
    parser.add_argument('--config', type=str,
                        help='Path to config file')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--status', action='store_true',
                        help='Show node status and exit')
    parser.add_argument('--generate-keys', action='store_true',
                        help='Generate new identity keys')
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Load or create config
    if args.config and os.path.exists(args.config):
        config = NodeConfig.load(args.config)
    else:
        config = NodeConfig()
    
    # Override with CLI args
    config.mode = args.mode
    config.radio_device = args.radio
    
    if args.gateway_key:
        config.gateway_public_key = bytes.fromhex(args.gateway_key)
    
    # Handle --generate-keys
    if args.generate_keys:
        print("Generating new identity keys...")
        identity_path = os.path.expanduser(config.identity_path)
        if os.path.exists(identity_path):
            print(f"WARNING: Removing existing identity at {identity_path}")
            os.remove(identity_path)
        crypto = NodeCrypto(identity_path)
        print(f"New public key: {crypto.public_key.hex()}")
        return
    
    # Create node
    node = AlkalineNode(config)
    
    # Handle --status
    if args.status:
        status = node.status()
        print("=" * 60)
        print("  ALKALINE NODE STATUS")
        print("=" * 60)
        print(f"  Node ID:      {status['node_id']}")
        print(f"  Mode:         {status['mode']}")
        print(f"  Public Key:   {status['public_key'][:32]}...")
        print(f"  Peers:        {status['peers']}")
        print(f"  Radio:        {status['radio']['device']}")
        print(f"  Packets Sent: {status['radio']['packets_sent']}")
        print(f"  Packets Recv: {status['radio']['packets_received']}")
        print(f"  Compression:  {status['compression_ratio']}")
        print("=" * 60)
        return
    
    print("=" * 60)
    print(f"  ALKALINE NODE - {config.mode.upper()} MODE")
    print("=" * 60)
    print(f"  Node ID:    {node.config.node_id}")
    print(f"  Public Key: {node.crypto.public_key.hex()}")
    print(f"  Radio:      {config.radio_device}")
    print("=" * 60)
    
    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        print("\nStopping...")
        node.stop()


# =============================================================================
# DEMO / TEST
# =============================================================================

def demo():
    """Run a demo showing the data flow."""
    print("=" * 70)
    print("  ALKALINE NODE - DATA FLOW DEMO")
    print("=" * 70)
    
    # Create a gateway and a client
    print("\n[1] Creating gateway node...")
    gateway_config = NodeConfig(mode='gateway', radio_device='simulate')
    gateway = AlkalineNode(gateway_config)
    print(f"    Gateway public key: {gateway.crypto.public_key.hex()[:32]}...")
    
    print("\n[2] Creating client node...")
    client_config = NodeConfig(
        mode='client',
        radio_device='simulate',
        gateway_public_key=gateway.crypto.public_key
    )
    client = AlkalineNode(client_config)
    print(f"    Client public key: {client.crypto.public_key.hex()[:32]}...")
    
    # Simulate sending data
    print("\n[3] Client sends data to gateway...")
    test_data = b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n"
    print(f"    Original data ({len(test_data)} bytes):")
    print(f"    {test_data[:50]}...")
    
    # Manual step-through of the flow
    print("\n[4] DATA FLOW:")
    
    # Step 1: Compress
    compressed = client.compression.compress(test_data)
    print(f"    [COMPRESS] {len(test_data)} → {len(compressed)} bytes ({client.compression.ratio:.1f}% saved)")
    
    # Step 2: Encrypt
    encrypted = client.crypto.encrypt(compressed, gateway.crypto.public_key)
    print(f"    [ENCRYPT]  {len(compressed)} → {len(encrypted)} bytes")
    print(f"               Encrypted payload: {encrypted[:32].hex()}...")
    
    # Step 3: Build packet
    packet = Packet(
        type=PacketType.DATA,
        source=client.crypto.public_key,
        destination=gateway.crypto.public_key,
        sequence=1,
        payload=encrypted
    )
    packet_bytes = packet.to_bytes()
    print(f"    [PACKET]   Header + payload = {len(packet_bytes)} bytes total")
    
    # Step 4: "Send over radio" (simulate)
    print(f"    [RADIO]    Transmitting {len(packet_bytes)} bytes...")
    gateway.radio.simulate_receive(packet_bytes)
    
    # Step 5: Gateway receives
    print("\n[5] Gateway receives and decrypts...")
    result = gateway.receive_from_radio()
    
    if result:
        sender, decrypted = result
        print(f"    [RADIO]    Received from {sender.hex()[:16]}...")
        print(f"    [DECRYPT]  {len(encrypted)} → {len(compressed)} bytes")
        print(f"    [DECOMP]   {len(compressed)} → {len(decrypted)} bytes")
        print(f"    [RESULT]   {decrypted[:50]}...")
        
        if decrypted == test_data:
            print("\n    ✅ SUCCESS: Data matches original!")
        else:
            print("\n    ❌ ERROR: Data mismatch!")
    else:
        print("    ❌ ERROR: No data received!")
    
    print("\n" + "=" * 70)
    print("  WHAT THIS MEANS")
    print("=" * 70)
    print("""
    The data flow is:
    
    CLIENT                          RADIO                           GATEWAY
    ──────                          ─────                           ───────
    User data                                                       
       ↓                                                           
    Compress (91% smaller)                                         
       ↓                                                           
    Encrypt (NaCl/libsodium)                                       
       ↓                                                           
    Build packet                                                   
       ↓                                                           
    Send ─────────────────────→ [encrypted blob] ─────────────────→ Receive
                                                                      ↓
                                                              Parse packet
                                                                      ↓
                                                              Decrypt
                                                                      ↓
                                                              Decompress
                                                                      ↓
                                                              Original data!
    
    The radio link ONLY sees encrypted bytes.
    A relay node in the middle would just forward the blob - it can't read it.
    """)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--demo':
        demo()
    else:
        main()
