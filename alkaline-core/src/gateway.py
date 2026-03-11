"""
Alkaline Network - Radio Gateway Interface

This module handles communication with ham radio hardware and gateways.
Currently supports:
- Simulation mode (for testing without hardware)
- Future: QDX HF transceiver
- Future: RTL-SDR + transmitter
- Future: LoRa modules
"""

import socket
import threading
import time
import struct
from abc import ABC, abstractmethod


class GatewayInterface(ABC):
    """Abstract base class for gateway interfaces."""
    
    @abstractmethod
    def connect(self):
        """Connect to the gateway."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Disconnect from the gateway."""
        pass
    
    @abstractmethod
    def send(self, data):
        """Send data through the gateway."""
        pass
    
    @abstractmethod
    def receive(self, timeout=5):
        """Receive data from the gateway."""
        pass
    
    @abstractmethod
    def get_status(self):
        """Get gateway status."""
        pass


class SimulatedGateway(GatewayInterface):
    """
    Simulated gateway for testing.
    Routes traffic through regular internet but simulates radio characteristics:
    - Added latency (simulating radio propagation)
    - Bandwidth limiting (simulating radio throughput)
    - Occasional packet loss (simulating radio interference)
    """
    
    def __init__(self, simulated_latency_ms=200, simulated_bandwidth_bps=1200):
        self.latency = simulated_latency_ms / 1000  # Convert to seconds
        self.bandwidth = simulated_bandwidth_bps
        self.connected = False
        self.stats = {
            "packets_sent": 0,
            "packets_received": 0,
            "bytes_sent": 0,
            "bytes_received": 0,
        }
    
    def connect(self):
        """Simulate connection to gateway."""
        print("[GATEWAY] Connecting to simulated gateway...")
        time.sleep(0.5)  # Simulate connection time
        self.connected = True
        print("[GATEWAY] Connected! (Simulation mode)")
        return True
    
    def disconnect(self):
        """Disconnect from simulated gateway."""
        self.connected = False
        print("[GATEWAY] Disconnected from simulated gateway")
    
    def send(self, data):
        """
        Send data through simulated gateway.
        Adds artificial delay to simulate radio.
        """
        if not self.connected:
            raise Exception("Not connected to gateway")
        
        # Simulate bandwidth limiting
        transmit_time = len(data) * 8 / self.bandwidth
        time.sleep(transmit_time)
        
        # Simulate propagation delay
        time.sleep(self.latency / 2)
        
        self.stats["packets_sent"] += 1
        self.stats["bytes_sent"] += len(data)
        
        return True
    
    def receive(self, timeout=5):
        """
        Receive data from simulated gateway.
        In simulation, this would be called after send() in a real request-response flow.
        """
        # Simulate propagation delay for response
        time.sleep(self.latency / 2)
        
        return None  # Actual data comes from the internet proxy
    
    def get_status(self):
        """Get gateway status."""
        return {
            "type": "simulated",
            "connected": self.connected,
            "latency_ms": self.latency * 1000,
            "bandwidth_bps": self.bandwidth,
            "stats": self.stats,
        }


class HamPacketGateway(GatewayInterface):
    """
    Real ham radio packet gateway interface.
    
    Connects to actual ham radio hardware (QDX, etc.) and communicates
    with Winlink/APRS/packet gateways.
    
    NOTE: This requires actual hardware to function.
    """
    
    def __init__(self, serial_port="/dev/ttyUSB0", baudrate=9600, callsign="N0CALL"):
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.callsign = callsign
        self.connected = False
        self.serial = None
        
        # Known gateway frequencies and callsigns
        self.gateways = [
            {"call": "W3ADO", "freq": 145.090, "mode": "packet"},
            {"call": "K4CJX", "freq": 145.030, "mode": "packet"},
            {"call": "N0ARY", "freq": 144.930, "mode": "packet"},
        ]
        
        self.current_gateway = None
        
    def connect(self):
        """
        Connect to ham radio hardware and find a gateway.
        """
        try:
            import serial
        except ImportError:
            print("[ERROR] pyserial not installed. Run: pip install pyserial")
            return False
            
        try:
            print(f"[GATEWAY] Opening serial port {self.serial_port}...")
            self.serial = serial.Serial(
                port=self.serial_port,
                baudrate=self.baudrate,
                timeout=1
            )
            
            print("[GATEWAY] Scanning for packet gateways...")
            
            # Try to connect to each known gateway
            for gateway in self.gateways:
                print(f"[GATEWAY] Trying {gateway['call']} on {gateway['freq']} MHz...")
                
                # Send connect request
                # This is simplified - real AX.25 packet radio is more complex
                connect_cmd = f"CONNECT {gateway['call']}\r"
                self.serial.write(connect_cmd.encode())
                
                # Wait for response
                time.sleep(2)
                response = self.serial.read(256)
                
                if b"CONNECTED" in response:
                    self.current_gateway = gateway
                    self.connected = True
                    print(f"[GATEWAY] Connected to {gateway['call']}!")
                    return True
            
            print("[GATEWAY] No gateways found. Check your antenna and frequency.")
            return False
            
        except Exception as e:
            print(f"[GATEWAY ERROR] {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the gateway."""
        if self.serial:
            try:
                self.serial.write(b"DISCONNECT\r")
                time.sleep(0.5)
                self.serial.close()
            except:
                pass
        self.connected = False
        self.current_gateway = None
    
    def send(self, data):
        """
        Send data through ham radio.
        
        Format: [length:2][checksum:2][data]
        """
        if not self.connected or not self.serial:
            raise Exception("Not connected to gateway")
        
        # Calculate simple checksum
        checksum = sum(data) & 0xFFFF
        
        # Build packet
        packet = struct.pack('!HH', len(data), checksum) + data
        
        # Send
        self.serial.write(packet)
        
        return True
    
    def receive(self, timeout=5):
        """Receive data from ham radio."""
        if not self.connected or not self.serial:
            raise Exception("Not connected to gateway")
        
        self.serial.timeout = timeout
        
        # Read header
        header = self.serial.read(4)
        if len(header) < 4:
            return None
            
        length, checksum = struct.unpack('!HH', header)
        
        # Read data
        data = self.serial.read(length)
        
        # Verify checksum
        if sum(data) & 0xFFFF != checksum:
            print("[GATEWAY WARNING] Checksum mismatch, packet may be corrupt")
        
        return data
    
    def get_status(self):
        """Get gateway status."""
        return {
            "type": "ham_packet",
            "connected": self.connected,
            "serial_port": self.serial_port,
            "callsign": self.callsign,
            "current_gateway": self.current_gateway,
        }


class LoRaGateway(GatewayInterface):
    """
    LoRa mesh network gateway.
    
    For local/regional mesh networking.
    Lower latency than HF, but shorter range.
    """
    
    def __init__(self, spi_bus=0, spi_device=0, frequency=915.0):
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.frequency = frequency  # MHz
        self.connected = False
        self.lora = None
        
    def connect(self):
        """Initialize LoRa radio."""
        try:
            # This would use a library like pySX127x or CircuitPython
            print(f"[LORA] Initializing LoRa on {self.frequency} MHz...")
            # TODO: Actual LoRa initialization
            self.connected = True
            print("[LORA] LoRa radio ready!")
            return True
        except Exception as e:
            print(f"[LORA ERROR] {e}")
            return False
    
    def disconnect(self):
        """Shutdown LoRa radio."""
        self.connected = False
    
    def send(self, data):
        """Send data via LoRa."""
        if not self.connected:
            raise Exception("LoRa not initialized")
        # TODO: Actual LoRa transmission
        return True
    
    def receive(self, timeout=5):
        """Receive data via LoRa."""
        if not self.connected:
            raise Exception("LoRa not initialized")
        # TODO: Actual LoRa reception
        return None
    
    def get_status(self):
        """Get LoRa status."""
        return {
            "type": "lora",
            "connected": self.connected,
            "frequency_mhz": self.frequency,
        }


def create_gateway(mode="simulation", **kwargs):
    """
    Factory function to create appropriate gateway.
    
    Args:
        mode: "simulation", "ham", or "lora"
        **kwargs: Additional arguments for specific gateway types
    
    Returns:
        GatewayInterface instance
    """
    if mode == "simulation":
        return SimulatedGateway(
            simulated_latency_ms=kwargs.get("latency_ms", 200),
            simulated_bandwidth_bps=kwargs.get("bandwidth_bps", 1200)
        )
    elif mode == "ham":
        return HamPacketGateway(
            serial_port=kwargs.get("serial_port", "/dev/ttyUSB0"),
            baudrate=kwargs.get("baudrate", 9600),
            callsign=kwargs.get("callsign", "N0CALL")
        )
    elif mode == "lora":
        return LoRaGateway(
            frequency=kwargs.get("frequency", 915.0)
        )
    else:
        raise ValueError(f"Unknown gateway mode: {mode}")


# Test
if __name__ == "__main__":
    print("Testing Simulated Gateway...")
    
    gw = create_gateway("simulation", latency_ms=300, bandwidth_bps=1200)
    gw.connect()
    
    print(f"Status: {gw.get_status()}")
    
    # Simulate sending some data
    test_data = b"Hello, this is a test packet!"
    print(f"Sending {len(test_data)} bytes...")
    
    start = time.time()
    gw.send(test_data)
    elapsed = time.time() - start
    
    print(f"Send completed in {elapsed*1000:.0f}ms")
    print(f"Effective throughput: {len(test_data)*8/elapsed:.0f} bps")
    
    gw.disconnect()
