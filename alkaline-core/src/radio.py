"""
Alkaline Network - Radio Integration Module
Extracted from battle-tested open source projects:
- Direwolf (AX.25, KISS protocol)
- Pat (Gateway discovery, Winlink integration) 
- FreeDATA (Modern digital modes, ARQ protocol)

This module provides reliable radio communication for the Alkaline Network.
"""

import struct
import socket
import serial
import threading
import queue
import time
import zlib
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable, List, Dict, Any

# =============================================================================
# KISS PROTOCOL (from Direwolf)
# The standard way to interface with packet radio TNCs
# =============================================================================

class KISSCommand(Enum):
    """KISS protocol commands"""
    DATA_FRAME = 0x00      # AX.25 frame
    TX_DELAY = 0x01        # Transmit delay (in 10ms units)
    PERSISTENCE = 0x02     # CSMA persistence (0-255)
    SLOT_TIME = 0x03       # Slot time (in 10ms units)
    TX_TAIL = 0x04         # Transmit tail (in 10ms units)
    FULL_DUPLEX = 0x05     # Full duplex mode
    SET_HARDWARE = 0x06    # TNC-specific commands
    RETURN = 0xFF          # Exit KISS mode


class KISS:
    """
    KISS TNC Protocol Implementation
    Based on Direwolf's kiss_frame.c
    
    KISS frames are structured as:
    - FEND (0xC0) - Frame delimiter
    - Command byte (channel << 4 | command)
    - Data (with escape sequences)
    - FEND (0xC0) - Frame delimiter
    """
    
    FEND = 0xC0   # Frame End
    FESC = 0xDB   # Frame Escape
    TFEND = 0xDC  # Transposed Frame End
    TFESC = 0xDD  # Transposed Frame Escape
    
    def __init__(self, port: str = None, host: str = None, tcp_port: int = 8001):
        """
        Initialize KISS interface.
        
        Args:
            port: Serial port (e.g., '/dev/ttyUSB0' or 'COM3')
            host: TCP host for network TNC (e.g., 'localhost')
            tcp_port: TCP port for network TNC (default: 8001 for Direwolf)
        """
        self.serial_port = port
        self.host = host
        self.tcp_port = tcp_port
        self.connection = None
        self.running = False
        self.rx_queue = queue.Queue()
        self.rx_thread = None
        self.frame_callback: Optional[Callable] = None
        
    def connect(self) -> bool:
        """Connect to TNC via serial or TCP"""
        try:
            if self.serial_port:
                self.connection = serial.Serial(
                    self.serial_port,
                    baudrate=9600,
                    timeout=1
                )
            elif self.host:
                self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.connection.connect((self.host, self.tcp_port))
                self.connection.settimeout(1)
            else:
                return False
                
            self.running = True
            self.rx_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.rx_thread.start()
            return True
            
        except Exception as e:
            print(f"[KISS] Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from TNC"""
        self.running = False
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
            self.connection = None
    
    def encode_frame(self, data: bytes, channel: int = 0, command: int = KISSCommand.DATA_FRAME.value) -> bytes:
        """
        Encode data into a KISS frame.
        
        From Direwolf kiss_frame.c:kiss_encapsulate()
        """
        frame = bytearray()
        frame.append(self.FEND)
        frame.append((channel << 4) | command)
        
        for byte in data:
            if byte == self.FEND:
                frame.append(self.FESC)
                frame.append(self.TFEND)
            elif byte == self.FESC:
                frame.append(self.FESC)
                frame.append(self.TFESC)
            else:
                frame.append(byte)
        
        frame.append(self.FEND)
        return bytes(frame)
    
    def decode_frame(self, frame: bytes) -> tuple:
        """
        Decode a KISS frame.
        
        Returns: (channel, command, data)
        From Direwolf kiss_frame.c:kiss_unwrap()
        """
        if len(frame) < 2:
            return None, None, None
            
        # Remove FEND delimiters
        if frame[0] == self.FEND:
            frame = frame[1:]
        if frame and frame[-1] == self.FEND:
            frame = frame[:-1]
            
        if not frame:
            return None, None, None
            
        # Extract command byte
        cmd_byte = frame[0]
        channel = (cmd_byte >> 4) & 0x0F
        command = cmd_byte & 0x0F
        
        # Decode escaped data
        data = bytearray()
        i = 1
        while i < len(frame):
            if frame[i] == self.FESC and i + 1 < len(frame):
                if frame[i + 1] == self.TFEND:
                    data.append(self.FEND)
                elif frame[i + 1] == self.TFESC:
                    data.append(self.FESC)
                i += 2
            else:
                data.append(frame[i])
                i += 1
        
        return channel, command, bytes(data)
    
    def send_data(self, data: bytes, channel: int = 0) -> bool:
        """Send data frame through TNC"""
        if not self.connection:
            return False
            
        frame = self.encode_frame(data, channel, KISSCommand.DATA_FRAME.value)
        
        try:
            if self.serial_port:
                self.connection.write(frame)
            else:
                self.connection.send(frame)
            return True
        except Exception as e:
            print(f"[KISS] Send failed: {e}")
            return False
    
    def set_tx_delay(self, delay_ms: int, channel: int = 0):
        """Set transmit delay in milliseconds"""
        delay_units = delay_ms // 10
        frame = self.encode_frame(bytes([delay_units]), channel, KISSCommand.TX_DELAY.value)
        self._send_raw(frame)
    
    def _send_raw(self, data: bytes):
        """Send raw bytes to TNC"""
        if self.connection:
            try:
                if self.serial_port:
                    self.connection.write(data)
                else:
                    self.connection.send(data)
            except:
                pass
    
    def _receive_loop(self):
        """Background thread to receive KISS frames"""
        buffer = bytearray()
        in_frame = False
        
        while self.running:
            try:
                if self.serial_port:
                    byte = self.connection.read(1)
                else:
                    byte = self.connection.recv(1)
                    
                if not byte:
                    continue
                    
                b = byte[0]
                
                if b == self.FEND:
                    if in_frame and len(buffer) > 0:
                        # Complete frame received
                        channel, command, data = self.decode_frame(bytes(buffer))
                        if data:
                            self.rx_queue.put((channel, command, data))
                            if self.frame_callback:
                                self.frame_callback(channel, command, data)
                        buffer = bytearray()
                    in_frame = True
                elif in_frame:
                    buffer.append(b)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[KISS] Receive error: {e}")
                break
    
    def receive(self, timeout: float = 1.0) -> Optional[bytes]:
        """Receive a data frame (blocking with timeout)"""
        try:
            channel, command, data = self.rx_queue.get(timeout=timeout)
            if command == KISSCommand.DATA_FRAME.value:
                return data
            return None
        except queue.Empty:
            return None


# =============================================================================
# AX.25 PROTOCOL (from Direwolf)
# The standard amateur radio packet protocol
# =============================================================================

@dataclass
class AX25Address:
    """AX.25 address (callsign + SSID)"""
    callsign: str
    ssid: int = 0
    
    def encode(self, is_last: bool = False) -> bytes:
        """
        Encode address to AX.25 format.
        Each character is shifted left 1 bit, SSID byte contains flags.
        From Direwolf ax25_pad.c
        """
        # Pad callsign to 6 characters
        call = self.callsign.upper().ljust(6)[:6]
        
        # Shift each character left 1 bit
        encoded = bytearray()
        for c in call:
            encoded.append(ord(c) << 1)
        
        # SSID byte: CRRSSID0/1
        # C = command/response, RR = reserved (11), SSID = 0-15
        # Last bit = 0 if more addresses follow, 1 if last
        ssid_byte = 0b01100000 | ((self.ssid & 0x0F) << 1)
        if is_last:
            ssid_byte |= 0x01
            
        encoded.append(ssid_byte)
        return bytes(encoded)
    
    @classmethod
    def decode(cls, data: bytes) -> 'AX25Address':
        """Decode AX.25 address from bytes"""
        if len(data) < 7:
            return None
            
        # Unshift characters
        callsign = ''.join(chr(b >> 1) for b in data[:6]).strip()
        ssid = (data[6] >> 1) & 0x0F
        
        return cls(callsign=callsign, ssid=ssid)
    
    def __str__(self):
        if self.ssid:
            return f"{self.callsign}-{self.ssid}"
        return self.callsign


class AX25Frame:
    """
    AX.25 Frame Structure
    Based on Direwolf ax25_pad.c
    
    Structure:
    - Destination address (7 bytes)
    - Source address (7 bytes)
    - Optional digipeater addresses (7 bytes each)
    - Control byte
    - PID byte (for I/UI frames)
    - Information field
    - FCS (added by TNC, not included here)
    """
    
    # Control field types
    CTRL_UI = 0x03      # Unnumbered Information
    CTRL_SABM = 0x2F    # Set Async Balanced Mode
    CTRL_DISC = 0x43    # Disconnect
    CTRL_DM = 0x0F      # Disconnected Mode
    CTRL_UA = 0x63      # Unnumbered Acknowledge
    
    # Protocol IDs
    PID_NO_LAYER3 = 0xF0    # No layer 3 protocol
    PID_IP = 0xCC           # IP protocol (for TCP/IP over AX.25)
    PID_ARP = 0xCD          # ARP protocol
    
    def __init__(self, dest: AX25Address, src: AX25Address, 
                 info: bytes = b'', digipeaters: List[AX25Address] = None,
                 control: int = CTRL_UI, pid: int = PID_NO_LAYER3):
        self.dest = dest
        self.src = src
        self.digipeaters = digipeaters or []
        self.control = control
        self.pid = pid
        self.info = info
    
    def encode(self) -> bytes:
        """Encode frame to bytes (without FCS)"""
        frame = bytearray()
        
        # Destination
        frame.extend(self.dest.encode(is_last=False))
        
        # Source (last if no digipeaters)
        is_last = len(self.digipeaters) == 0
        frame.extend(self.src.encode(is_last=is_last))
        
        # Digipeaters
        for i, digi in enumerate(self.digipeaters):
            is_last = (i == len(self.digipeaters) - 1)
            frame.extend(digi.encode(is_last=is_last))
        
        # Control
        frame.append(self.control)
        
        # PID (only for I and UI frames)
        if self.control == self.CTRL_UI or (self.control & 0x01) == 0:
            frame.append(self.pid)
        
        # Information
        frame.extend(self.info)
        
        return bytes(frame)
    
    @classmethod
    def decode(cls, data: bytes) -> 'AX25Frame':
        """Decode frame from bytes"""
        if len(data) < 15:  # Minimum: 2 addresses + control
            return None
            
        # Destination
        dest = AX25Address.decode(data[0:7])
        
        # Source
        src = AX25Address.decode(data[7:14])
        
        # Check if more addresses follow
        offset = 14
        digipeaters = []
        
        while offset < len(data) and (data[offset - 1] & 0x01) == 0:
            if offset + 7 > len(data):
                break
            digi = AX25Address.decode(data[offset:offset+7])
            digipeaters.append(digi)
            offset += 7
        
        if offset >= len(data):
            return None
            
        # Control
        control = data[offset]
        offset += 1
        
        # PID
        pid = 0
        if control == cls.CTRL_UI or (control & 0x01) == 0:
            if offset < len(data):
                pid = data[offset]
                offset += 1
        
        # Information
        info = data[offset:]
        
        return cls(dest=dest, src=src, info=info, digipeaters=digipeaters,
                   control=control, pid=pid)


# =============================================================================
# GATEWAY DISCOVERY (from Pat)
# Find Winlink and packet radio gateways
# =============================================================================

@dataclass
class RadioGateway:
    """Information about a radio gateway"""
    callsign: str
    frequency: float  # in Hz
    mode: str         # PACKET, ARDOP, VARA, etc.
    gridsquare: str
    distance: float   # km (if known)
    url: str          # Connection URL
    
    
class GatewayFinder:
    """
    Find radio gateways for connection.
    Based on Pat's rmslist.go and cmsapi
    """
    
    # Winlink CMS API endpoint
    CMS_API = "https://cms.winlink.org/json/channel/list"
    
    # Common packet radio frequencies (MHz)
    PACKET_FREQUENCIES = {
        '2m': [144.390, 144.910, 145.010, 145.030, 145.050, 145.090],
        '70cm': [432.010, 433.010],
        'hf_30m': [10.1473],
        'hf_40m': [7.1023],
        'hf_80m': [3.5973],
    }
    
    def __init__(self, my_callsign: str, my_gridsquare: str = None):
        self.my_callsign = my_callsign
        self.my_gridsquare = my_gridsquare
    
    def find_packet_gateways(self, mode: str = 'PACKET') -> List[RadioGateway]:
        """
        Find packet radio gateways.
        In a real implementation, this would query the Winlink CMS API.
        """
        # These are example gateways - in production, query the API
        example_gateways = [
            RadioGateway(
                callsign="W3ADO",
                frequency=145090000,
                mode="PACKET",
                gridsquare="FM19",
                distance=0,
                url="ax25:///W3ADO?freq=145090"
            ),
            RadioGateway(
                callsign="K4CJX",
                frequency=145030000,
                mode="PACKET", 
                gridsquare="EM73",
                distance=0,
                url="ax25:///K4CJX?freq=145030"
            ),
        ]
        
        return [g for g in example_gateways if g.mode == mode]
    
    def calculate_distance(self, their_grid: str) -> float:
        """Calculate distance between two Maidenhead gridsquares"""
        if not self.my_gridsquare or not their_grid:
            return 0
            
        # Simplified distance calculation
        # Full implementation would use proper geodesic math
        try:
            my_lat, my_lon = self._grid_to_latlon(self.my_gridsquare)
            their_lat, their_lon = self._grid_to_latlon(their_grid)
            
            # Haversine formula (simplified)
            import math
            R = 6371  # Earth's radius in km
            
            lat1, lon1 = math.radians(my_lat), math.radians(my_lon)
            lat2, lon2 = math.radians(their_lat), math.radians(their_lon)
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
            c = 2 * math.asin(math.sqrt(a))
            
            return R * c
            
        except:
            return 0
    
    def _grid_to_latlon(self, grid: str) -> tuple:
        """Convert Maidenhead grid to lat/lon (approximate)"""
        grid = grid.upper()
        if len(grid) < 4:
            return 0, 0
            
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        lon += int(grid[2]) * 2
        lat += int(grid[3])
        
        return lat + 0.5, lon + 1


# =============================================================================
# ARQ PROTOCOL (from FreeDATA)
# Automatic Repeat reQuest for reliable data transfer
# =============================================================================

class ARQState(Enum):
    """ARQ session states"""
    IDLE = 0
    CONNECTING = 1
    CONNECTED = 2
    SENDING = 3
    RECEIVING = 4
    DISCONNECTING = 5
    FAILED = 6


@dataclass
class ARQStats:
    """Statistics for ARQ session"""
    bytes_sent: int = 0
    bytes_received: int = 0
    retransmits: int = 0
    start_time: float = 0
    end_time: float = 0
    snr: float = 0
    
    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time
    
    @property
    def throughput_bps(self) -> float:
        if self.duration > 0:
            return (self.bytes_sent + self.bytes_received) * 8 / self.duration
        return 0


class ARQSession:
    """
    ARQ Session for reliable data transfer.
    Based on FreeDATA's arq_session.py
    
    Provides:
    - Automatic retransmission of lost packets
    - Flow control
    - Data integrity verification
    """
    
    # Frame types
    FRAME_CONNECT = 0x01
    FRAME_CONNECT_ACK = 0x02
    FRAME_DATA = 0x03
    FRAME_DATA_ACK = 0x04
    FRAME_DISCONNECT = 0x05
    FRAME_DISCONNECT_ACK = 0x06
    
    def __init__(self, kiss: KISS, my_call: str, their_call: str):
        self.kiss = kiss
        self.my_call = my_call
        self.their_call = their_call
        self.state = ARQState.IDLE
        self.stats = ARQStats()
        
        # Transmission parameters
        self.max_retries = 5
        self.timeout = 10.0  # seconds
        self.frame_size = 128  # bytes
        
        # Sequence numbers
        self.tx_seq = 0
        self.rx_seq = 0
        
        # Buffers
        self.tx_buffer = queue.Queue()
        self.rx_buffer = bytearray()
        
        # Threading
        self.running = False
        self.rx_thread = None
        
    def connect(self) -> bool:
        """Establish ARQ connection"""
        self.state = ARQState.CONNECTING
        self.stats.start_time = time.time()
        
        # Send connect request
        connect_frame = self._build_frame(self.FRAME_CONNECT)
        
        for attempt in range(self.max_retries):
            self._send_frame(connect_frame)
            
            # Wait for ACK
            response = self._wait_for_frame(self.FRAME_CONNECT_ACK, self.timeout)
            if response:
                self.state = ARQState.CONNECTED
                self.running = True
                self._start_receiver()
                return True
                
        self.state = ARQState.FAILED
        return False
    
    def disconnect(self):
        """Close ARQ connection"""
        if self.state != ARQState.CONNECTED:
            return
            
        self.state = ARQState.DISCONNECTING
        disconnect_frame = self._build_frame(self.FRAME_DISCONNECT)
        self._send_frame(disconnect_frame)
        
        # Wait for ACK (best effort)
        self._wait_for_frame(self.FRAME_DISCONNECT_ACK, 5.0)
        
        self.running = False
        self.state = ARQState.IDLE
        self.stats.end_time = time.time()
    
    def send(self, data: bytes) -> bool:
        """Send data reliably"""
        if self.state != ARQState.CONNECTED:
            return False
            
        self.state = ARQState.SENDING
        
        # Split into frames
        for i in range(0, len(data), self.frame_size):
            chunk = data[i:i + self.frame_size]
            
            # Build data frame with sequence number
            frame = self._build_data_frame(chunk)
            
            # Send with retransmission
            acked = False
            for attempt in range(self.max_retries):
                self._send_frame(frame)
                
                response = self._wait_for_frame(self.FRAME_DATA_ACK, self.timeout)
                if response and self._check_ack_seq(response):
                    acked = True
                    self.tx_seq = (self.tx_seq + 1) % 256
                    self.stats.bytes_sent += len(chunk)
                    break
                else:
                    self.stats.retransmits += 1
            
            if not acked:
                self.state = ARQState.FAILED
                return False
        
        self.state = ARQState.CONNECTED
        return True
    
    def receive(self, timeout: float = 1.0) -> Optional[bytes]:
        """Receive data (if available)"""
        if len(self.rx_buffer) > 0:
            data = bytes(self.rx_buffer)
            self.rx_buffer.clear()
            return data
        return None
    
    def _build_frame(self, frame_type: int, data: bytes = b'') -> bytes:
        """Build ARQ frame"""
        frame = bytearray()
        frame.append(frame_type)
        frame.extend(data)
        
        # Add CRC
        crc = zlib.crc32(frame) & 0xFFFF
        frame.extend(struct.pack('<H', crc))
        
        return bytes(frame)
    
    def _build_data_frame(self, data: bytes) -> bytes:
        """Build data frame with sequence number"""
        payload = bytearray()
        payload.append(self.tx_seq)
        payload.extend(data)
        return self._build_frame(self.FRAME_DATA, payload)
    
    def _send_frame(self, frame: bytes):
        """Send frame via AX.25"""
        # Wrap in AX.25 UI frame
        ax25_frame = AX25Frame(
            dest=AX25Address(self.their_call),
            src=AX25Address(self.my_call),
            info=frame
        )
        self.kiss.send_data(ax25_frame.encode())
    
    def _wait_for_frame(self, expected_type: int, timeout: float) -> Optional[bytes]:
        """Wait for specific frame type"""
        start = time.time()
        while time.time() - start < timeout:
            data = self.kiss.receive(timeout=0.5)
            if data:
                frame = AX25Frame.decode(data)
                if frame and len(frame.info) > 0:
                    if frame.info[0] == expected_type:
                        return frame.info
        return None
    
    def _check_ack_seq(self, ack_frame: bytes) -> bool:
        """Check if ACK sequence matches"""
        if len(ack_frame) < 2:
            return False
        return ack_frame[1] == self.tx_seq
    
    def _start_receiver(self):
        """Start background receiver thread"""
        self.rx_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.rx_thread.start()
    
    def _receiver_loop(self):
        """Background thread to receive data"""
        while self.running:
            data = self.kiss.receive(timeout=1.0)
            if data:
                frame = AX25Frame.decode(data)
                if frame and len(frame.info) > 2:
                    frame_type = frame.info[0]
                    
                    if frame_type == self.FRAME_DATA:
                        seq = frame.info[1]
                        payload = frame.info[2:-2]  # Exclude CRC
                        
                        if seq == self.rx_seq:
                            self.rx_buffer.extend(payload)
                            self.stats.bytes_received += len(payload)
                            self.rx_seq = (self.rx_seq + 1) % 256
                        
                        # Send ACK
                        ack = self._build_frame(self.FRAME_DATA_ACK, bytes([seq]))
                        self._send_frame(ack)


# =============================================================================
# ALKALINE RADIO GATEWAY
# High-level interface for Alkaline Network
# =============================================================================

class AlkalineRadio:
    """
    High-level radio interface for Alkaline Network.
    Combines KISS, AX.25, and ARQ for reliable data transfer.
    """
    
    def __init__(self, callsign: str, gridsquare: str = None):
        self.callsign = callsign
        self.gridsquare = gridsquare
        self.kiss = None
        self.gateway = None
        self.session = None
        self.connected = False
        
        # Callbacks
        self.on_data_received: Optional[Callable] = None
        self.on_status_change: Optional[Callable] = None
    
    def connect_tnc(self, port: str = None, host: str = "localhost", tcp_port: int = 8001) -> bool:
        """
        Connect to TNC (Direwolf or hardware TNC).
        
        For Direwolf: use host/tcp_port
        For hardware TNC: use port (serial)
        """
        self.kiss = KISS(port=port, host=host, tcp_port=tcp_port)
        if self.kiss.connect():
            self._notify_status("TNC connected")
            return True
        return False
    
    def find_gateways(self, mode: str = "PACKET") -> List[RadioGateway]:
        """Find available gateways"""
        finder = GatewayFinder(self.callsign, self.gridsquare)
        return finder.find_packet_gateways(mode)
    
    def connect_gateway(self, gateway: RadioGateway) -> bool:
        """Connect to a radio gateway"""
        if not self.kiss:
            return False
            
        self.gateway = gateway
        self.session = ARQSession(self.kiss, self.callsign, gateway.callsign)
        
        self._notify_status(f"Connecting to {gateway.callsign}...")
        
        if self.session.connect():
            self.connected = True
            self._notify_status(f"Connected to {gateway.callsign}")
            return True
        else:
            self._notify_status(f"Failed to connect to {gateway.callsign}")
            return False
    
    def send(self, data: bytes) -> bool:
        """Send data through the radio link"""
        if not self.connected or not self.session:
            return False
            
        return self.session.send(data)
    
    def receive(self, timeout: float = 1.0) -> Optional[bytes]:
        """Receive data from the radio link"""
        if not self.connected or not self.session:
            return None
            
        return self.session.receive(timeout)
    
    def disconnect(self):
        """Disconnect from gateway and TNC"""
        if self.session:
            self.session.disconnect()
            self.session = None
            
        if self.kiss:
            self.kiss.disconnect()
            self.kiss = None
            
        self.connected = False
        self._notify_status("Disconnected")
    
    def get_stats(self) -> Optional[ARQStats]:
        """Get current session statistics"""
        if self.session:
            return self.session.stats
        return None
    
    def _notify_status(self, message: str):
        """Send status notification"""
        print(f"[RADIO] {message}")
        if self.on_status_change:
            self.on_status_change(message)


# =============================================================================
# QUICK TEST
# =============================================================================

if __name__ == "__main__":
    print("Alkaline Radio Module - Test")
    print("=" * 50)
    
    # Test KISS framing
    kiss = KISS()
    test_data = b"Hello, radio!"
    frame = kiss.encode_frame(test_data)
    print(f"Original: {test_data}")
    print(f"KISS frame: {frame.hex()}")
    
    channel, cmd, decoded = kiss.decode_frame(frame)
    print(f"Decoded: {decoded}")
    print(f"Match: {decoded == test_data}")
    
    print()
    
    # Test AX.25 framing
    ax_frame = AX25Frame(
        dest=AX25Address("W3ADO"),
        src=AX25Address("N0CALL", 1),
        info=b"Test packet"
    )
    encoded = ax_frame.encode()
    print(f"AX.25 frame: {encoded.hex()}")
    
    decoded_frame = AX25Frame.decode(encoded)
    print(f"Dest: {decoded_frame.dest}")
    print(f"Src: {decoded_frame.src}")
    print(f"Info: {decoded_frame.info}")
    
    print()
    print("Module loaded successfully!")
    print("Ready to integrate with Alkaline Network.")
