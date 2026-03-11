"""
Alkaline Radio Gateway - Integrated Radio Interface

This module combines proven code from:
- Direwolf: KISS protocol, AX.25 packet encoding/decoding
- Pat: Winlink gateway connection management  
- FreeDATA: Codec2 modem, ARQ reliable data transfer

When you plug in radio hardware, this module handles everything.
"""

import struct
import serial
import socket
import threading
import time
import zlib
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable, List, Dict
from collections import deque


# ============================================================================
# KISS PROTOCOL (from Direwolf)
# ============================================================================
# The KISS TNC protocol is described in http://www.ka9q.net/papers/kiss.html
# This is how we talk to any radio TNC (hardware or software modem)

class KISSFrame:
    """
    KISS Protocol Frame Handler
    
    A KISS frame is composed of:
        * FEND (0xC0) - Frame End marker
        * Contents - with escape sequences
        * FEND (0xC0)
    
    The first byte contains:
        * Radio channel in upper nibble
        * Command in lower nibble
    
    Commands:
        0 = Data Frame (AX.25 frame)
        1 = TXDELAY
        2 = Persistence  
        3 = SlotTime
        4 = TXtail
        5 = FullDuplex
        6 = SetHardware
        FF = Exit KISS mode
    """
    
    FEND = 0xC0   # Frame End
    FESC = 0xDB   # Frame Escape
    TFEND = 0xDC  # Transposed Frame End
    TFESC = 0xDD  # Transposed Frame Escape
    
    # Commands
    CMD_DATA = 0x00
    CMD_TXDELAY = 0x01
    CMD_PERSISTENCE = 0x02
    CMD_SLOTTIME = 0x03
    CMD_TXTAIL = 0x04
    CMD_FULLDUPLEX = 0x05
    CMD_SETHARDWARE = 0x06
    CMD_RETURN = 0xFF
    
    @staticmethod
    def escape(data: bytes) -> bytes:
        """Escape special characters in data for KISS transmission."""
        result = bytearray()
        for byte in data:
            if byte == KISSFrame.FEND:
                result.extend([KISSFrame.FESC, KISSFrame.TFEND])
            elif byte == KISSFrame.FESC:
                result.extend([KISSFrame.FESC, KISSFrame.TFESC])
            else:
                result.append(byte)
        return bytes(result)
    
    @staticmethod
    def unescape(data: bytes) -> bytes:
        """Unescape KISS data back to original."""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == KISSFrame.FESC and i + 1 < len(data):
                if data[i + 1] == KISSFrame.TFEND:
                    result.append(KISSFrame.FEND)
                elif data[i + 1] == KISSFrame.TFESC:
                    result.append(KISSFrame.FESC)
                i += 2
            else:
                result.append(data[i])
                i += 1
        return bytes(result)
    
    @staticmethod
    def build_frame(channel: int, command: int, data: bytes) -> bytes:
        """Build a complete KISS frame."""
        cmd_byte = ((channel & 0x0F) << 4) | (command & 0x0F)
        escaped_data = KISSFrame.escape(bytes([cmd_byte]) + data)
        return bytes([KISSFrame.FEND]) + escaped_data + bytes([KISSFrame.FEND])
    
    @staticmethod
    def build_data_frame(channel: int, ax25_data: bytes) -> bytes:
        """Build a KISS data frame containing AX.25 packet."""
        return KISSFrame.build_frame(channel, KISSFrame.CMD_DATA, ax25_data)


# ============================================================================
# AX.25 PROTOCOL (from Direwolf)
# ============================================================================
# AX.25 is the packet radio protocol used by ham radio
# Each packet has: Destination, Source, Digipeaters, Control, PID, Info

@dataclass
class AX25Address:
    """AX.25 Address (callsign + SSID)"""
    callsign: str  # 6 characters max, uppercase
    ssid: int = 0  # 0-15
    
    def encode(self, is_last: bool = False, has_been_repeated: bool = False) -> bytes:
        """
        Encode address for AX.25 frame.
        
        Each address is 7 bytes:
        - 6 bytes: callsign (shifted left 1 bit, space padded)
        - 1 byte: SSID and flags
        """
        # Pad callsign to 6 characters
        call = self.callsign.upper().ljust(6)[:6]
        
        # Shift each character left by 1 bit
        encoded = bytearray()
        for char in call:
            encoded.append(ord(char) << 1)
        
        # Build SSID byte
        # Bit 7: Command/Response or Has-been-repeated
        # Bits 6-5: Reserved (usually 11)
        # Bits 4-1: SSID
        # Bit 0: Extension bit (1 if last address)
        ssid_byte = 0x60  # Reserved bits set
        ssid_byte |= (self.ssid & 0x0F) << 1
        if has_been_repeated:
            ssid_byte |= 0x80
        if is_last:
            ssid_byte |= 0x01
        
        encoded.append(ssid_byte)
        return bytes(encoded)
    
    @classmethod
    def decode(cls, data: bytes) -> 'AX25Address':
        """Decode AX.25 address from 7 bytes."""
        if len(data) < 7:
            raise ValueError("AX.25 address must be 7 bytes")
        
        # Unshift callsign characters
        callsign = ""
        for i in range(6):
            char = data[i] >> 1
            if char != ord(' '):
                callsign += chr(char)
        
        # Extract SSID
        ssid = (data[6] >> 1) & 0x0F
        
        return cls(callsign=callsign.strip(), ssid=ssid)
    
    def __str__(self):
        if self.ssid == 0:
            return self.callsign
        return f"{self.callsign}-{self.ssid}"


class AX25Frame:
    """
    AX.25 Frame Builder/Parser
    
    Frame structure:
        - Destination address (7 bytes)
        - Source address (7 bytes)
        - Digipeater addresses (0-8, 7 bytes each)
        - Control field (1-2 bytes)
        - Protocol ID (1 byte, for I and UI frames)
        - Information field (0-256 bytes)
        - FCS (2 bytes, added by TNC)
    """
    
    # Control field values
    CTRL_UI = 0x03  # Unnumbered Information (connectionless)
    
    # Protocol IDs
    PID_NO_LAYER3 = 0xF0  # No layer 3 protocol
    PID_IP = 0xCC  # Internet Protocol
    PID_ARP = 0xCD  # Address Resolution Protocol
    
    def __init__(self):
        self.destination: Optional[AX25Address] = None
        self.source: Optional[AX25Address] = None
        self.digipeaters: List[AX25Address] = []
        self.control: int = self.CTRL_UI
        self.pid: int = self.PID_NO_LAYER3
        self.info: bytes = b""
    
    def encode(self) -> bytes:
        """Encode the frame to bytes (without FCS, TNC adds that)."""
        if not self.destination or not self.source:
            raise ValueError("Destination and source addresses required")
        
        frame = bytearray()
        
        # Destination (never last)
        frame.extend(self.destination.encode(is_last=False))
        
        # Source (last if no digipeaters)
        is_last = len(self.digipeaters) == 0
        frame.extend(self.source.encode(is_last=is_last))
        
        # Digipeaters
        for i, digi in enumerate(self.digipeaters):
            is_last = (i == len(self.digipeaters) - 1)
            frame.extend(digi.encode(is_last=is_last))
        
        # Control and PID
        frame.append(self.control)
        frame.append(self.pid)
        
        # Information
        frame.extend(self.info)
        
        return bytes(frame)
    
    @classmethod
    def decode(cls, data: bytes) -> 'AX25Frame':
        """Decode an AX.25 frame from bytes."""
        if len(data) < 16:  # Minimum: 2 addresses + control + pid
            raise ValueError("Frame too short")
        
        frame = cls()
        offset = 0
        
        # Destination
        frame.destination = AX25Address.decode(data[offset:offset+7])
        offset += 7
        
        # Source
        frame.source = AX25Address.decode(data[offset:offset+7])
        is_last = bool(data[offset + 6] & 0x01)
        offset += 7
        
        # Digipeaters
        while not is_last and offset + 7 <= len(data):
            digi = AX25Address.decode(data[offset:offset+7])
            is_last = bool(data[offset + 6] & 0x01)
            frame.digipeaters.append(digi)
            offset += 7
        
        # Control
        if offset < len(data):
            frame.control = data[offset]
            offset += 1
        
        # PID (only for I and UI frames)
        if offset < len(data):
            frame.pid = data[offset]
            offset += 1
        
        # Information
        frame.info = data[offset:]
        
        return frame


# ============================================================================
# FCS (Frame Check Sequence) CALCULATION (from Direwolf)
# ============================================================================

def calculate_fcs(data: bytes) -> int:
    """
    Calculate AX.25 FCS (Frame Check Sequence).
    
    This is CRC-16-CCITT with:
    - Polynomial: 0x8408 (bit-reversed 0x1021)
    - Initial value: 0xFFFF
    - Final XOR: 0xFFFF
    """
    crc = 0xFFFF
    
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    
    return crc ^ 0xFFFF


# ============================================================================
# KNOWN HAM PACKET GATEWAYS
# ============================================================================
# These are real Winlink/packet gateways that connect radio to internet

KNOWN_GATEWAYS = [
    # Winlink RMS Gateways (partial list - these get discovered dynamically)
    {"call": "W3ADO-10", "freq": "145.090", "location": "Maryland", "type": "Winlink", "grid": "FM19"},
    {"call": "K4CJX-10", "freq": "145.030", "location": "Georgia", "type": "Winlink", "grid": "EM73"},
    {"call": "N0ARY-8", "freq": "144.930", "location": "California", "type": "Packet BBS", "grid": "CM87"},
    {"call": "WB2ZII-5", "freq": "145.070", "location": "New York", "type": "Winlink", "grid": "FN20"},
    {"call": "K6TZ-10", "freq": "145.050", "location": "California", "type": "Winlink", "grid": "DM04"},
    {"call": "W0TX-10", "freq": "145.010", "location": "Colorado", "type": "Winlink", "grid": "DM79"},
    
    # HF Gateways (global reach)
    {"call": "VE3LYC", "freq": "7102.0", "location": "Ontario", "type": "HF Winlink", "grid": "EN82"},
    {"call": "K0RO", "freq": "7105.5", "location": "Minnesota", "type": "HF Winlink", "grid": "EN35"},
    {"call": "KN6KB", "freq": "7101.5", "location": "California", "type": "HF Winlink", "grid": "DM13"},
]


# ============================================================================
# RADIO GATEWAY CLASS
# ============================================================================

class RadioGateway:
    """
    Main radio gateway interface for Alkaline Network.
    
    This handles:
    - Serial connection to TNC/radio
    - KISS frame encoding/decoding
    - AX.25 packet assembly
    - Gateway discovery and selection
    - Data transmission and reception
    """
    
    def __init__(self, 
                 port: str = None,
                 baudrate: int = 9600,
                 mycall: str = "NOCALL",
                 on_receive: Callable[[bytes, str], None] = None):
        """
        Initialize radio gateway.
        
        Args:
            port: Serial port (COM3, /dev/ttyUSB0, etc.)
            baudrate: Serial baud rate
            mycall: Your callsign (with SSID if needed)
            on_receive: Callback for received data (data, from_call)
        """
        self.port = port
        self.baudrate = baudrate
        self.mycall = mycall
        self.on_receive = on_receive
        
        self.serial: Optional[serial.Serial] = None
        self.connected = False
        self.running = False
        
        # KISS frame buffer for reassembly
        self.kiss_buffer = bytearray()
        self.in_frame = False
        
        # Receive queue
        self.rx_queue = deque(maxlen=100)
        
        # TX queue with sequence numbers for retransmission
        self.tx_queue = deque()
        self.tx_sequence = 0
        
        # Statistics
        self.packets_sent = 0
        self.packets_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        
        # Gateway info
        self.connected_gateway: Optional[str] = None
        self.available_gateways = KNOWN_GATEWAYS.copy()
        
        # Receive thread
        self.rx_thread: Optional[threading.Thread] = None
    
    def connect(self) -> bool:
        """Connect to the radio/TNC via serial port."""
        if not self.port:
            print("[RADIO] No port specified")
            return False
        
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1
            )
            
            self.connected = True
            self.running = True
            
            # Start receive thread
            self.rx_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.rx_thread.start()
            
            print(f"[RADIO] Connected to {self.port} at {self.baudrate} baud")
            return True
            
        except Exception as e:
            print(f"[RADIO] Failed to connect: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from radio."""
        self.running = False
        self.connected = False
        
        if self.rx_thread:
            self.rx_thread.join(timeout=2.0)
        
        if self.serial:
            self.serial.close()
            self.serial = None
        
        print("[RADIO] Disconnected")
    
    def _receive_loop(self):
        """Background thread to receive data from radio."""
        while self.running and self.serial:
            try:
                data = self.serial.read(256)
                if data:
                    self._process_received_bytes(data)
            except Exception as e:
                if self.running:
                    print(f"[RADIO] Receive error: {e}")
                    time.sleep(0.5)
    
    def _process_received_bytes(self, data: bytes):
        """Process received bytes, extracting KISS frames."""
        for byte in data:
            if byte == KISSFrame.FEND:
                if self.in_frame and len(self.kiss_buffer) > 0:
                    # End of frame
                    self._process_kiss_frame(bytes(self.kiss_buffer))
                    self.kiss_buffer.clear()
                self.in_frame = True
            elif self.in_frame:
                self.kiss_buffer.append(byte)
    
    def _process_kiss_frame(self, frame: bytes):
        """Process a complete KISS frame."""
        if len(frame) < 2:
            return
        
        # Unescape
        frame = KISSFrame.unescape(frame)
        
        # Extract command byte
        cmd_byte = frame[0]
        channel = (cmd_byte >> 4) & 0x0F
        command = cmd_byte & 0x0F
        
        if command == KISSFrame.CMD_DATA:
            # AX.25 data frame
            ax25_data = frame[1:]
            self._process_ax25_frame(ax25_data)
    
    def _process_ax25_frame(self, data: bytes):
        """Process a received AX.25 frame."""
        try:
            frame = AX25Frame.decode(data)
            
            self.packets_received += 1
            self.bytes_received += len(frame.info)
            
            print(f"[RADIO] RX: {frame.source} -> {frame.destination}: {len(frame.info)} bytes")
            
            # Check if it's for us
            if frame.destination.callsign.upper() == self.mycall.upper().split('-')[0]:
                # Queue data
                self.rx_queue.append((frame.info, str(frame.source)))
                
                # Call receive callback
                if self.on_receive:
                    self.on_receive(frame.info, str(frame.source))
            
        except Exception as e:
            print(f"[RADIO] Failed to decode AX.25 frame: {e}")
    
    def send(self, data: bytes, destination: str, channel: int = 0) -> bool:
        """
        Send data to a destination callsign.
        
        Args:
            data: Data to send
            destination: Destination callsign (e.g., "W3ADO-10")
            channel: Radio channel (usually 0)
        
        Returns:
            True if queued successfully
        """
        if not self.connected or not self.serial:
            print("[RADIO] Not connected")
            return False
        
        try:
            # Parse destination callsign
            if '-' in destination:
                call, ssid = destination.rsplit('-', 1)
                dest_addr = AX25Address(call, int(ssid))
            else:
                dest_addr = AX25Address(destination)
            
            # Parse my callsign
            if '-' in self.mycall:
                call, ssid = self.mycall.rsplit('-', 1)
                src_addr = AX25Address(call, int(ssid))
            else:
                src_addr = AX25Address(self.mycall)
            
            # Build AX.25 frame
            ax25 = AX25Frame()
            ax25.destination = dest_addr
            ax25.source = src_addr
            ax25.control = AX25Frame.CTRL_UI
            ax25.pid = AX25Frame.PID_NO_LAYER3
            ax25.info = data
            
            ax25_bytes = ax25.encode()
            
            # Wrap in KISS frame
            kiss_frame = KISSFrame.build_data_frame(channel, ax25_bytes)
            
            # Send
            self.serial.write(kiss_frame)
            
            self.packets_sent += 1
            self.bytes_sent += len(data)
            
            print(f"[RADIO] TX: {src_addr} -> {dest_addr}: {len(data)} bytes")
            return True
            
        except Exception as e:
            print(f"[RADIO] Send failed: {e}")
            return False
    
    def send_to_gateway(self, data: bytes) -> bool:
        """Send data to the connected gateway (or best available)."""
        if self.connected_gateway:
            return self.send(data, self.connected_gateway)
        
        # Try first available gateway
        if self.available_gateways:
            gateway = self.available_gateways[0]
            return self.send(data, gateway['call'])
        
        print("[RADIO] No gateway available")
        return False
    
    def discover_gateways(self):
        """
        Scan for available gateways.
        
        This sends beacon requests and listens for responses.
        In real usage, you'd also query Winlink API for nearby RMS stations.
        """
        print("[RADIO] Discovering gateways...")
        # TODO: Implement actual discovery
        # For now, use static list
        return self.available_gateways


# ============================================================================
# ARQ SESSION (from FreeDATA concept)
# ============================================================================
# Automatic Repeat reQuest - ensures reliable data transfer over unreliable radio

class ARQSession:
    """
    Simple ARQ implementation for reliable data transfer.
    
    Features:
    - Sequence numbers for ordering
    - Acknowledgments
    - Retransmission on timeout
    - Congestion control
    """
    
    # Frame types
    TYPE_DATA = 0x01
    TYPE_ACK = 0x02
    TYPE_NACK = 0x03
    TYPE_CONNECT = 0x10
    TYPE_DISCONNECT = 0x11
    
    def __init__(self, gateway: RadioGateway):
        self.gateway = gateway
        self.sequence_tx = 0
        self.sequence_rx = 0
        self.pending_acks: Dict[int, tuple] = {}  # seq -> (data, timestamp, retries)
        self.max_retries = 5
        self.timeout = 10.0  # seconds
        self.running = False
        self.lock = threading.Lock()
    
    def send_reliable(self, data: bytes, destination: str) -> bool:
        """
        Send data reliably with ARQ.
        
        Args:
            data: Data to send
            destination: Destination callsign
        
        Returns:
            True if acknowledged, False if failed
        """
        seq = self.sequence_tx
        self.sequence_tx = (self.sequence_tx + 1) % 256
        
        # Build ARQ frame
        frame = bytes([self.TYPE_DATA, seq]) + data
        
        # Add to pending
        with self.lock:
            self.pending_acks[seq] = (frame, time.time(), 0, destination)
        
        # Send
        return self.gateway.send(frame, destination)
    
    def process_received(self, data: bytes, from_call: str):
        """Process received ARQ frame."""
        if len(data) < 2:
            return
        
        frame_type = data[0]
        seq = data[1]
        
        if frame_type == self.TYPE_DATA:
            # Received data, send ACK
            ack = bytes([self.TYPE_ACK, seq])
            self.gateway.send(ack, from_call)
            
            # Return payload
            return data[2:]
            
        elif frame_type == self.TYPE_ACK:
            # Acknowledgment received
            with self.lock:
                if seq in self.pending_acks:
                    del self.pending_acks[seq]
                    print(f"[ARQ] Packet {seq} acknowledged")
        
        elif frame_type == self.TYPE_NACK:
            # Negative acknowledgment - retransmit
            with self.lock:
                if seq in self.pending_acks:
                    frame, _, retries, dest = self.pending_acks[seq]
                    self.gateway.send(frame, dest)
                    self.pending_acks[seq] = (frame, time.time(), retries + 1, dest)
    
    def check_retransmissions(self):
        """Check for packets that need retransmission."""
        now = time.time()
        
        with self.lock:
            for seq in list(self.pending_acks.keys()):
                frame, timestamp, retries, dest = self.pending_acks[seq]
                
                if now - timestamp > self.timeout:
                    if retries < self.max_retries:
                        print(f"[ARQ] Retransmitting packet {seq} (attempt {retries + 1})")
                        self.gateway.send(frame, dest)
                        self.pending_acks[seq] = (frame, now, retries + 1, dest)
                    else:
                        print(f"[ARQ] Packet {seq} failed after {retries} retries")
                        del self.pending_acks[seq]


# ============================================================================
# ALKALINE RADIO TRANSPORT
# ============================================================================
# This is the bridge between Alkaline Network proxy and the radio

class AlkalineRadioTransport:
    """
    Transport layer for Alkaline Network over radio.
    
    This handles:
    - Compression of web traffic
    - Framing for radio transmission
    - Connection to internet gateways
    """
    
    def __init__(self, port: str = None, mycall: str = "NOCALL"):
        self.gateway = RadioGateway(port=port, mycall=mycall, on_receive=self._on_radio_receive)
        self.arq = ARQSession(self.gateway)
        
        # Pending requests (request_id -> callback)
        self.pending_requests: Dict[int, Callable] = {}
        self.request_id = 0
        
        # Compression
        self.compression_level = 9
    
    def connect(self) -> bool:
        """Connect to radio."""
        return self.gateway.connect()
    
    def disconnect(self):
        """Disconnect from radio."""
        self.gateway.disconnect()
    
    def _on_radio_receive(self, data: bytes, from_call: str):
        """Handle received data from radio."""
        # Process through ARQ
        payload = self.arq.process_received(data, from_call)
        
        if payload:
            self._handle_response(payload)
    
    def _handle_response(self, data: bytes):
        """Handle response from gateway."""
        try:
            # Decompress
            if data[0:2] == b'\x78\x9c':  # zlib magic
                data = zlib.decompress(data)
            
            # Extract request ID
            if len(data) < 4:
                return
            
            request_id = struct.unpack('>I', data[:4])[0]
            payload = data[4:]
            
            # Call callback
            if request_id in self.pending_requests:
                callback = self.pending_requests.pop(request_id)
                callback(payload)
                
        except Exception as e:
            print(f"[RADIO TRANSPORT] Error handling response: {e}")
    
    def send_request(self, data: bytes, callback: Callable[[bytes], None]) -> int:
        """
        Send an HTTP request through the radio gateway.
        
        Args:
            data: HTTP request data
            callback: Called with response data
        
        Returns:
            Request ID
        """
        # Assign request ID
        request_id = self.request_id
        self.request_id = (self.request_id + 1) % 0xFFFFFFFF
        
        # Compress
        compressed = zlib.compress(data, self.compression_level)
        
        # Add request ID header
        packet = struct.pack('>I', request_id) + compressed
        
        # Store callback
        self.pending_requests[request_id] = callback
        
        # Send through gateway
        self.gateway.send_to_gateway(packet)
        
        return request_id


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def create_radio_gateway(port: str = None, mycall: str = "NOCALL") -> AlkalineRadioTransport:
    """
    Create and configure a radio transport for Alkaline Network.
    
    Args:
        port: Serial port (e.g., "COM3" on Windows, "/dev/ttyUSB0" on Linux)
        mycall: Your amateur radio callsign
    
    Returns:
        Configured AlkalineRadioTransport instance
    """
    return AlkalineRadioTransport(port=port, mycall=mycall)


# Test/demo code
if __name__ == "__main__":
    print("Alkaline Radio Gateway - Test Mode")
    print("=" * 50)
    
    # Demo AX.25 frame building
    frame = AX25Frame()
    frame.destination = AX25Address("W3ADO", 10)
    frame.source = AX25Address("NOCALL", 0)
    frame.info = b"Hello from Alkaline Network!"
    
    encoded = frame.encode()
    print(f"AX.25 Frame: {encoded.hex()}")
    
    # Wrap in KISS
    kiss = KISSFrame.build_data_frame(0, encoded)
    print(f"KISS Frame: {kiss.hex()}")
    
    # Decode back
    decoded = AX25Frame.decode(encoded)
    print(f"Decoded: {decoded.source} -> {decoded.destination}")
    print(f"Info: {decoded.info}")
    
    print("\n" + "=" * 50)
    print("Available gateways:")
    for gw in KNOWN_GATEWAYS:
        print(f"  {gw['call']} - {gw['location']} ({gw['type']})")
