"""
Alkaline Network - Core Application
Free internet through ham radio gateways.
"""

import socket
import threading
import time
import zlib
import struct
import sys
import os
from datetime import datetime
from collections import defaultdict

# ANSI colors for console
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

class AlkalineNetwork:
    def __init__(self, mode="simulation"):
        self.mode = mode  # "simulation" or "radio"
        self.running = False
        self.connected_devices = {}
        self.traffic_stats = defaultdict(lambda: {"in": 0, "out": 0, "packets": 0})
        self.gateway = None
        self.dns_port = 53
        self.proxy_port = 8888
        
        # Compression stats - must be set before anything else
        self.total_original = 0
        self.total_compressed = 0
        
        # Detect hotspot IP (this prints output so do it after setting up vars)
        self.hotspot_ip = self.detect_hotspot_ip()
        self.hotspot_subnet = None
        
        if self.hotspot_ip == "0.0.0.0":
            self.proxy_mode = "transparent"
        else:
            self.proxy_mode = "hotspot"
        
        # Gateway list (real ham packet gateways)
        self.known_gateways = [
            {"call": "W3ADO", "freq": "145.090", "location": "Maryland", "type": "Winlink"},
            {"call": "K4CJX", "freq": "145.030", "location": "Georgia", "type": "Packet"},
            {"call": "N0ARY", "freq": "144.930", "location": "California", "type": "BBS"},
        ]
    
    def detect_hotspot_ip(self):
        """
        Auto-detect the IP address of the hotspot/network adapter.
        Falls back to localhost for local testing.
        """
        import subprocess
        
        try:
            # Get all IP addresses
            result = subprocess.run(['ipconfig'], capture_output=True, text=True, shell=True)
            output = result.stdout
            
            # Parse ipconfig output
            adapters = {}
            current_adapter = None
            
            for line in output.split('\n'):
                line = line.strip()
                
                # New adapter section
                if 'adapter' in line.lower() and ':' in line:
                    current_adapter = line.split(':')[0].strip()
                    adapters[current_adapter] = {}
                
                # IPv4 address
                elif current_adapter and 'ipv4' in line.lower():
                    parts = line.split(':')
                    if len(parts) >= 2:
                        ip = parts[1].strip()
                        adapters[current_adapter]['ip'] = ip
            
            # Priority order for finding hotspot adapter
            hotspot_keywords = [
                'local area connection*',  # Windows Mobile Hotspot
                'wi-fi direct',
                'mobile hotspot',
                'microsoft hosted',
            ]
            
            # First try to find a hotspot-specific adapter
            for keyword in hotspot_keywords:
                for adapter_name, info in adapters.items():
                    if keyword in adapter_name.lower() and 'ip' in info:
                        ip = info['ip']
                        if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
                            print(f"[AUTO-DETECT] Found hotspot adapter: {adapter_name}")
                            print(f"[AUTO-DETECT] Using IP: {ip}")
                            return ip
            
            # If no hotspot found, try common hotspot IP ranges
            for adapter_name, info in adapters.items():
                if 'ip' in info:
                    ip = info['ip']
                    if ip.startswith('192.168.137.'):
                        print(f"[AUTO-DETECT] Found likely hotspot: {adapter_name}")
                        print(f"[AUTO-DETECT] Using IP: {ip}")
                        return ip
            
            # Fallback: use localhost for local testing
            print("[AUTO-DETECT] No hotspot found - running in LOCAL TEST MODE")
            print("[AUTO-DETECT] Configure your browser to use proxy 127.0.0.1:8888")
            return "127.0.0.1"
            
        except Exception as e:
            print(f"[AUTO-DETECT] Error: {e}")
            print("[AUTO-DETECT] Running in LOCAL TEST MODE on 127.0.0.1")
            return "127.0.0.1"
        
    def print_banner(self):
        """Print the startup banner."""
        os.system('cls' if os.name == 'nt' else 'clear')
        banner = f"""
{Colors.CYAN}{Colors.BOLD}
╔═══════════════════════════════════════════════════════════════════════════╗
║                                                                           ║
║     █████╗ ██╗     ██╗  ██╗ █████╗ ██╗     ██╗███╗   ██╗███████╗         ║
║    ██╔══██╗██║     ██║ ██╔╝██╔══██╗██║     ██║████╗  ██║██╔════╝         ║
║    ███████║██║     █████╔╝ ███████║██║     ██║██╔██╗ ██║█████╗           ║
║    ██╔══██║██║     ██╔═██╗ ██╔══██║██║     ██║██║╚██╗██║██╔══╝           ║
║    ██║  ██║███████╗██║  ██╗██║  ██║███████╗██║██║ ╚████║███████╗         ║
║    ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝         ║
║                                                                           ║
║                    N E T W O R K   v0.1-alpha                            ║
║                                                                           ║
║           Free Internet • No ISP • No Surveillance • No Bullshit          ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
{Colors.RESET}"""
        print(banner)
        
    def print_status(self):
        """Print current status bar."""
        mode_color = Colors.YELLOW if self.mode == "simulation" else Colors.GREEN
        mode_text = "SIMULATED" if self.mode == "simulation" else "LIVE RADIO"
        
        device_count = len(self.connected_devices)
        compression_ratio = 0
        if self.total_original > 0:
            compression_ratio = (1 - (self.total_compressed / self.total_original)) * 100
            
        print(f"""
{Colors.DIM}═══════════════════════════════════════════════════════════════════════════{Colors.RESET}
 Status: {Colors.GREEN}●{Colors.RESET} ONLINE    Mode: {mode_color}{mode_text}{Colors.RESET}    Devices: {Colors.CYAN}{device_count}{Colors.RESET}    Compression: {Colors.GREEN}{compression_ratio:.1f}%{Colors.RESET}
{Colors.DIM}═══════════════════════════════════════════════════════════════════════════{Colors.RESET}
""")

    def log(self, direction, protocol, destination, size_original, size_compressed=None, extra=""):
        """Log a packet to console."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        if direction == "out":
            arrow = f"{Colors.CYAN}→{Colors.RESET}"
        else:
            arrow = f"{Colors.GREEN}←{Colors.RESET}"
            
        if size_compressed and size_compressed < size_original:
            size_str = f"{size_original} → {Colors.GREEN}{size_compressed}{Colors.RESET} bytes"
            self.total_original += size_original
            self.total_compressed += size_compressed
        else:
            size_str = f"{size_original} bytes"
            self.total_original += size_original
            self.total_compressed += size_original
            
        protocol_colors = {
            "DNS": Colors.YELLOW,
            "HTTPS": Colors.GREEN,
            "HTTP": Colors.BLUE,
            "TCP": Colors.CYAN,
            "UDP": Colors.DIM,
            "MC": Colors.RED,  # Minecraft
        }
        
        pcolor = protocol_colors.get(protocol, Colors.RESET)
        
        extra_str = f" {Colors.DIM}{extra}{Colors.RESET}" if extra else ""
        
        print(f"[{timestamp}] {arrow} {pcolor}{protocol:5}{Colors.RESET}: {destination:40} ({size_str}){extra_str}")

    def compress_packet(self, data):
        """Compress a packet using zlib."""
        compressed = zlib.compress(data, level=9)
        return compressed
    
    def decompress_packet(self, data):
        """Decompress a packet."""
        try:
            return zlib.decompress(data)
        except:
            return data

    def delta_encode_position(self, x, y, z, last_x, last_y, last_z):
        """
        Delta encode a position for Minecraft.
        Instead of 24 bytes (3 doubles), send 3 bytes (signed deltas).
        """
        dx = int((x - last_x) * 32) & 0xFF
        dy = int((y - last_y) * 32) & 0xFF
        dz = int((z - last_z) * 32) & 0xFF
        return bytes([dx, dy, dz])

    def handle_dns_request(self, data, addr, sock):
        """Handle a DNS request from a connected device."""
        # Parse DNS query (simplified)
        try:
            # Skip header (12 bytes), get query name
            query_start = 12
            query_parts = []
            i = query_start
            while data[i] != 0:
                length = data[i]
                query_parts.append(data[i+1:i+1+length].decode())
                i += length + 1
            domain = ".".join(query_parts)
            
            self.log("out", "DNS", domain, len(data))
            
            # In simulation mode, forward to real DNS
            if self.mode == "simulation":
                response = self.forward_dns_real(data)
            else:
                response = self.forward_dns_radio(data)
                
            if response:
                self.log("in", "DNS", domain, len(response))
                sock.sendto(response, addr)
                
        except Exception as e:
            print(f"{Colors.RED}[DNS ERROR] {e}{Colors.RESET}")

    def forward_dns_real(self, data):
        """Forward DNS to real server (simulation mode)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            sock.sendto(data, ("8.8.8.8", 53))
            response, _ = sock.recvfrom(4096)
            sock.close()
            return response
        except:
            return None

    def forward_dns_radio(self, data):
        """Forward DNS through ham radio gateway (real mode)."""
        # This will be implemented when we add radio support
        compressed = self.compress_packet(data)
        # TODO: Send over radio
        # TODO: Receive response
        # return self.decompress_packet(response)
        return None

    def start_dns_server(self):
        """Start the DNS server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind((self.hotspot_ip, self.dns_port))
        except PermissionError:
            print(f"{Colors.YELLOW}[WARNING] Cannot bind to port 53 (need admin). Using port 5353.{Colors.RESET}")
            self.dns_port = 5353
            sock.bind((self.hotspot_ip, self.dns_port))
        except OSError as e:
            print(f"{Colors.YELLOW}[WARNING] Cannot bind DNS: {e}. DNS forwarding disabled.{Colors.RESET}")
            return
            
        print(f"{Colors.GREEN}[DNS]{Colors.RESET} Server listening on {self.hotspot_ip}:{self.dns_port}")
        
        while self.running:
            try:
                sock.settimeout(1)
                data, addr = sock.recvfrom(4096)
                
                # Track connected device
                device_ip = addr[0]
                if device_ip not in self.connected_devices:
                    self.connected_devices[device_ip] = {
                        "first_seen": datetime.now(),
                        "packets": 0
                    }
                    print(f"\n{Colors.GREEN}[DEVICE CONNECTED]{Colors.RESET} {device_ip}\n")
                
                self.connected_devices[device_ip]["packets"] += 1
                
                # Handle in thread
                threading.Thread(target=self.handle_dns_request, args=(data, addr, sock)).start()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"{Colors.RED}[DNS ERROR] {e}{Colors.RESET}")

    def start_proxy_server(self):
        """Start the HTTP/HTTPS proxy server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind((self.hotspot_ip, self.proxy_port))
            sock.listen(100)
        except OSError as e:
            print(f"{Colors.YELLOW}[WARNING] Cannot bind proxy: {e}. Proxy disabled.{Colors.RESET}")
            return
            
        print(f"{Colors.GREEN}[PROXY]{Colors.RESET} Server listening on {self.hotspot_ip}:{self.proxy_port}")
        
        while self.running:
            try:
                sock.settimeout(1)
                client_sock, addr = sock.accept()
                threading.Thread(target=self.handle_proxy_connection, args=(client_sock, addr)).start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"{Colors.RED}[PROXY ERROR] {e}{Colors.RESET}")

    def handle_proxy_connection(self, client_sock, addr):
        """Handle a proxy connection."""
        try:
            client_sock.settimeout(30)
            request = client_sock.recv(8192)
            
            if not request:
                client_sock.close()
                return
                
            # Parse HTTP request
            try:
                first_line = request.split(b'\r\n')[0].decode()
                method, url, _ = first_line.split(' ')
            except:
                client_sock.close()
                return
            
            # Handle CONNECT (HTTPS)
            if method == 'CONNECT':
                host, port = url.split(':')
                port = int(port)
                
                self.log("out", "HTTPS", host, len(request))
                
                if self.mode == "simulation":
                    self.handle_connect_simulation(client_sock, host, port)
                else:
                    self.handle_connect_radio(client_sock, host, port, request)
                    
            # Handle regular HTTP
            else:
                if url.startswith('http://'):
                    url = url[7:]
                host = url.split('/')[0]
                
                self.log("out", "HTTP", host, len(request))
                
                if self.mode == "simulation":
                    self.handle_http_simulation(client_sock, host, request)
                else:
                    self.handle_http_radio(client_sock, host, request)
                    
        except Exception as e:
            pass
        finally:
            try:
                client_sock.close()
            except:
                pass

    def handle_connect_simulation(self, client_sock, host, port):
        """Handle HTTPS CONNECT in simulation mode."""
        server_sock = None
        try:
            # Resolve hostname to IP
            try:
                ip = socket.gethostbyname(host)
            except socket.gaierror as e:
                self.log("out", "HTTPS", f"{host} (DNS FAILED)", 0)
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                client_sock.close()
                return
            
            # Connect to real server
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.settimeout(10)
            server_sock.connect((ip, port))
            
            # Send 200 Connection Established
            client_sock.send(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            
            # Set non-blocking for tunneling
            client_sock.setblocking(False)
            server_sock.setblocking(False)
            
            # Tunnel data using select
            self.tunnel_sockets_select(client_sock, server_sock, host)
            
        except socket.timeout:
            self.log("out", "HTTPS", f"{host} (TIMEOUT)", 0)
            try:
                client_sock.send(b'HTTP/1.1 504 Gateway Timeout\r\n\r\n')
            except:
                pass
        except ConnectionRefusedError:
            self.log("out", "HTTPS", f"{host} (REFUSED)", 0)
            try:
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            except:
                pass
        except Exception as e:
            try:
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            except:
                pass
        finally:
            try:
                client_sock.close()
            except:
                pass
            try:
                if server_sock:
                    server_sock.close()
            except:
                pass

    def tunnel_sockets_select(self, client_sock, server_sock, host):
        """Tunnel data between client and server using select for better performance."""
        import select
        
        sockets = [client_sock, server_sock]
        timeout = 60  # Connection timeout
        
        while self.running:
            try:
                readable, _, exceptional = select.select(sockets, [], sockets, 1)
                
                if exceptional:
                    break
                    
                for sock in readable:
                    try:
                        data = sock.recv(8192)
                        if not data:
                            return  # Connection closed
                        
                        if sock is client_sock:
                            # Client -> Server
                            server_sock.send(data)
                            self.log("out", "HTTPS", host, len(data))
                        else:
                            # Server -> Client
                            client_sock.send(data)
                            self.log("in", "HTTPS", host, len(data))
                            
                    except (BlockingIOError, ssl.SSLWantReadError if 'ssl' in dir() else BlockingIOError):
                        continue
                    except:
                        return
                        
            except select.error:
                break
            except:
                break

    def handle_http_simulation(self, client_sock, host, request):
        """Handle HTTP request in simulation mode."""
        server_sock = None
        try:
            # Parse port from host if present
            if ':' in host:
                hostname, port = host.split(':')
                port = int(port)
            else:
                hostname = host
                port = 80
            
            # Resolve hostname to IP
            try:
                ip = socket.gethostbyname(hostname)
            except socket.gaierror:
                self.log("out", "HTTP", f"{hostname} (DNS FAILED)", 0)
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                client_sock.close()
                return
                
            # Connect to real server
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.settimeout(10)
            server_sock.connect((ip, port))
            server_sock.send(request)
            
            # Forward response
            while True:
                try:
                    data = server_sock.recv(8192)
                    if not data:
                        break
                    self.log("in", "HTTP", hostname, len(data))
                    client_sock.send(data)
                except socket.timeout:
                    break
                
        except Exception as e:
            try:
                client_sock.send(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
            except:
                pass
        finally:
            try:
                client_sock.close()
            except:
                pass
            try:
                if server_sock:
                    server_sock.close()
            except:
                pass

    def tunnel_sockets(self, client_sock, server_sock, host):
        """Tunnel data between client and server."""
        
        def forward(src, dst, direction):
            try:
                while self.running:
                    data = src.recv(8192)
                    if not data:
                        break
                    
                    original_size = len(data)
                    
                    # Compress for logging (we'd actually compress for radio)
                    compressed = self.compress_packet(data)
                    compressed_size = len(compressed)
                    
                    protocol = "HTTPS"
                    self.log(direction, protocol, host, original_size, compressed_size)
                    
                    dst.send(data)
            except:
                pass
            finally:
                try:
                    src.close()
                except:
                    pass
                try:
                    dst.close()
                except:
                    pass
        
        t1 = threading.Thread(target=forward, args=(client_sock, server_sock, "out"))
        t2 = threading.Thread(target=forward, args=(server_sock, client_sock, "in"))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()

    def start(self):
        """Start the Alkaline Network."""
        self.running = True
        
        self.print_banner()
        
        print(f"{Colors.YELLOW}[INIT]{Colors.RESET} Starting Alkaline Network...")
        print(f"{Colors.YELLOW}[INIT]{Colors.RESET} Mode: {self.mode.upper()}")
        
        if self.hotspot_ip == "127.0.0.1":
            print(f"{Colors.CYAN}[INIT]{Colors.RESET} Running in LOCAL TEST MODE")
            print(f"{Colors.CYAN}[INIT]{Colors.RESET} To test, set your browser proxy to: 127.0.0.1:8888")
            print(f"{Colors.CYAN}[INIT]{Colors.RESET} Or use: curl -x http://127.0.0.1:8888 http://example.com")
        elif self.mode == "simulation":
            print(f"{Colors.YELLOW}[INIT]{Colors.RESET} Using simulated gateway (traffic goes through your real internet)")
            print(f"{Colors.YELLOW}[INIT]{Colors.RESET} To test: Connect your phone to this PC's hotspot")
        else:
            print(f"{Colors.GREEN}[INIT]{Colors.RESET} Searching for ham radio gateways...")
            
        print()
        
        # Start DNS server in thread
        dns_thread = threading.Thread(target=self.start_dns_server, daemon=True)
        dns_thread.start()
        
        # Start proxy server in thread
        proxy_thread = threading.Thread(target=self.start_proxy_server, daemon=True)
        proxy_thread.start()
        
        time.sleep(1)
        self.print_status()
        
        print(f"{Colors.GREEN}[READY]{Colors.RESET} Alkaline Network is running!")
        print(f"{Colors.DIM}Press Ctrl+C to stop{Colors.RESET}\n")
        
        # Keep running
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}[SHUTDOWN]{Colors.RESET} Stopping Alkaline Network...")
            self.running = False
            
    def stop(self):
        """Stop the network."""
        self.running = False


def main():
    print("Starting Alkaline Network...")
    
    # Parse arguments
    mode = "simulation"
    if len(sys.argv) > 1:
        if sys.argv[1] == "--radio":
            mode = "radio"
        elif sys.argv[1] == "--help":
            print("""
Alkaline Network - Free Internet Through Ham Radio

Usage:
    python alkaline.py              Run in simulation mode (uses your internet)
    python alkaline.py --radio      Run in radio mode (uses ham gateway)
    python alkaline.py --help       Show this help

Simulation mode lets you test without radio hardware.
Radio mode requires a QDX or similar HF transceiver.
            """)
            sys.exit(0)
    
    network = AlkalineNetwork(mode=mode)
    network.start()


if __name__ == "__main__":
    main()
