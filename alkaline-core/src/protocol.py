"""
Alkaline Protocol - Ultra-Low Bandwidth Compression

This is the magic that makes internet work over ham radio.
We compress the hell out of everything.
"""

import zlib
import struct
from collections import defaultdict


class AlkalineProtocol:
    """
    Compresses TCP/IP traffic to fit through ~1 KB/s ham radio links.
    
    Techniques used:
    - Dictionary compression for common headers
    - Delta encoding for sequential data
    - Aggressive zlib compression
    - Header stripping and reconstruction
    """
    
    def __init__(self):
        # Common HTTP headers - we send index instead of full string
        self.http_header_dict = {
            0: b"Host",
            1: b"User-Agent",
            2: b"Accept",
            3: b"Accept-Language",
            4: b"Accept-Encoding",
            5: b"Connection",
            6: b"Content-Type",
            7: b"Content-Length",
            8: b"Cookie",
            9: b"Cache-Control",
            10: b"Referer",
            11: b"Origin",
            12: b"Authorization",
        }
        
        # Reverse lookup
        self.http_header_reverse = {v: k for k, v in self.http_header_dict.items()}
        
        # Common domains - we send 2-byte index instead of full domain
        self.domain_dict = {
            0: "google.com",
            1: "youtube.com",
            2: "facebook.com",
            3: "amazon.com",
            4: "twitter.com",
            5: "instagram.com",
            6: "reddit.com",
            7: "discord.com",
            8: "github.com",
            9: "microsoft.com",
            10: "apple.com",
            11: "netflix.com",
            12: "twitch.tv",
            13: "wikipedia.org",
            14: "mc.hypixel.net",
            15: "cloudflare.com",
        }
        self.domain_reverse = {v: k for k, v in self.domain_dict.items()}
        
        # State for delta encoding
        self.last_positions = {}  # entity_id -> (x, y, z)
        
        # Stats
        self.stats = {
            "packets_compressed": 0,
            "bytes_original": 0,
            "bytes_compressed": 0,
        }
    
    def compress(self, data):
        """
        Compress arbitrary data using best available method.
        Returns: compressed bytes
        """
        if len(data) < 10:
            # Too small to compress effectively
            return b'\x00' + data
            
        # Try zlib compression
        compressed = zlib.compress(data, level=9)
        
        if len(compressed) < len(data):
            self.stats["packets_compressed"] += 1
            self.stats["bytes_original"] += len(data)
            self.stats["bytes_compressed"] += len(compressed)
            return b'\x01' + compressed
        else:
            # Compression made it bigger, send raw
            return b'\x00' + data
    
    def decompress(self, data):
        """Decompress data."""
        if len(data) < 1:
            return data
            
        compression_type = data[0]
        payload = data[1:]
        
        if compression_type == 0x00:
            # Raw data
            return payload
        elif compression_type == 0x01:
            # Zlib compressed
            return zlib.decompress(payload)
        else:
            # Unknown, return as-is
            return data
    
    def compress_http_request(self, request):
        """
        Compress an HTTP request by:
        1. Replacing common headers with indices
        2. Replacing known domains with indices
        3. Stripping unnecessary headers
        4. Zlib compressing the result
        """
        lines = request.split(b'\r\n')
        if not lines:
            return self.compress(request)
        
        # Parse request line
        request_line = lines[0]
        
        # Process headers
        compressed_headers = []
        for line in lines[1:]:
            if not line:
                continue
            if b':' not in line:
                continue
                
            header_name, header_value = line.split(b':', 1)
            header_name = header_name.strip()
            header_value = header_value.strip()
            
            # Skip unnecessary headers
            if header_name.lower() in [b'user-agent', b'accept-language', b'accept-encoding']:
                continue
            
            # Replace known header names with index
            if header_name in self.http_header_reverse:
                idx = self.http_header_reverse[header_name]
                compressed_headers.append(struct.pack('!B', idx) + b':' + header_value)
            else:
                compressed_headers.append(line)
        
        # Reconstruct minimal request
        minimal = request_line + b'\r\n' + b'\r\n'.join(compressed_headers) + b'\r\n\r\n'
        
        return self.compress(minimal)
    
    def compress_minecraft_position(self, entity_id, x, y, z, yaw, pitch):
        """
        Compress a Minecraft position update.
        
        Original format: 46 bytes (4 doubles + 2 floats + flags)
        Compressed format: 4 bytes (3 delta bytes + 1 rotation byte)
        
        That's 91% reduction!
        """
        # Get last known position
        last = self.last_positions.get(entity_id, (x, y, z))
        last_x, last_y, last_z = last
        
        # Calculate deltas (1/32 block precision, -4 to +3.96875 block range)
        dx = int((x - last_x) * 32)
        dy = int((y - last_y) * 32)
        dz = int((z - last_z) * 32)
        
        # Clamp to signed byte range
        dx = max(-128, min(127, dx))
        dy = max(-128, min(127, dy))
        dz = max(-128, min(127, dz))
        
        # Pack rotation into single byte (4 bits yaw, 4 bits pitch)
        yaw_4bit = int((yaw % 360) / 360 * 16) & 0x0F
        pitch_4bit = int(((pitch + 90) % 180) / 180 * 16) & 0x0F
        rotation = (yaw_4bit << 4) | pitch_4bit
        
        # Update state
        # Use actual delta values to avoid drift
        new_x = last_x + (dx / 32)
        new_y = last_y + (dy / 32)
        new_z = last_z + (dz / 32)
        self.last_positions[entity_id] = (new_x, new_y, new_z)
        
        # Pack into 4 bytes
        return struct.pack('!bbbB', dx, dy, dz, rotation)
    
    def decompress_minecraft_position(self, entity_id, data):
        """
        Decompress a Minecraft position update back to full coordinates.
        """
        if len(data) != 4:
            raise ValueError("Invalid compressed position data")
            
        dx, dy, dz, rotation = struct.unpack('!bbbB', data)
        
        # Get last known position
        last = self.last_positions.get(entity_id, (0, 0, 0))
        last_x, last_y, last_z = last
        
        # Calculate new position
        x = last_x + (dx / 32)
        y = last_y + (dy / 32)
        z = last_z + (dz / 32)
        
        # Unpack rotation
        yaw_4bit = (rotation >> 4) & 0x0F
        pitch_4bit = rotation & 0x0F
        yaw = (yaw_4bit / 16) * 360
        pitch = (pitch_4bit / 16) * 180 - 90
        
        # Update state
        self.last_positions[entity_id] = (x, y, z)
        
        return (x, y, z, yaw, pitch)
    
    def compress_dns_query(self, query):
        """
        Compress a DNS query.
        Replace known domains with 2-byte index.
        """
        # Parse domain from DNS query (simplified)
        # Skip header (12 bytes)
        if len(query) < 13:
            return self.compress(query)
            
        # Extract domain
        domain_parts = []
        i = 12
        while i < len(query) and query[i] != 0:
            length = query[i]
            domain_parts.append(query[i+1:i+1+length].decode())
            i += length + 1
        domain = ".".join(domain_parts)
        
        # Check if it's a known domain
        if domain in self.domain_reverse:
            idx = self.domain_reverse[domain]
            # Return: [0x02 = compressed domain] [2-byte index] [query type/class]
            return struct.pack('!BH', 0x02, idx) + query[i:]
        
        return self.compress(query)
    
    def get_compression_ratio(self):
        """Get overall compression ratio."""
        if self.stats["bytes_original"] == 0:
            return 0
        return 1 - (self.stats["bytes_compressed"] / self.stats["bytes_original"])
    
    def get_stats(self):
        """Get compression statistics."""
        return {
            **self.stats,
            "compression_ratio": f"{self.get_compression_ratio() * 100:.1f}%"
        }


class MinecraftProtocolOptimizer:
    """
    Specialized optimizer for Minecraft network traffic.
    Makes Minecraft playable over ham radio by aggressive compression.
    """
    
    def __init__(self):
        self.protocol = AlkalineProtocol()
        self.entity_states = {}  # Track entity states for delta encoding
        self.chunk_cache = {}    # Cache sent chunks to avoid resending
        self.tick_rate = 5       # Reduced from 20 ticks/sec to 5
        self.tick_accumulator = 0
        
    def should_send_tick(self):
        """
        Reduce tick rate from 20/sec to 5/sec.
        Client will interpolate between updates.
        """
        self.tick_accumulator += 1
        if self.tick_accumulator >= 4:  # Every 4th tick
            self.tick_accumulator = 0
            return True
        return False
    
    def compress_move_packet(self, entity_id, x, y, z, yaw, pitch):
        """Compress a movement packet."""
        if not self.should_send_tick():
            return None  # Skip this tick
            
        return self.protocol.compress_minecraft_position(entity_id, x, y, z, yaw, pitch)
    
    def compress_chunk(self, chunk_x, chunk_z, chunk_data):
        """
        Compress chunk data.
        Uses aggressive compression + caching.
        """
        chunk_key = (chunk_x, chunk_z)
        
        # Check if we've sent this chunk before
        if chunk_key in self.chunk_cache:
            cached_hash = self.chunk_cache[chunk_key]
            current_hash = hash(chunk_data)
            if cached_hash == current_hash:
                # Chunk unchanged, send reference
                return struct.pack('!BHH', 0x03, chunk_x & 0xFFFF, chunk_z & 0xFFFF)
        
        # New or changed chunk - compress and send
        compressed = zlib.compress(chunk_data, level=9)
        self.chunk_cache[chunk_key] = hash(chunk_data)
        
        return struct.pack('!BHH', 0x04, chunk_x & 0xFFFF, chunk_z & 0xFFFF) + compressed
    
    def estimate_bandwidth(self, num_players):
        """
        Estimate bandwidth needed for a game session.
        """
        # Per player, per second
        position_updates = self.tick_rate * 4  # 4 bytes per update, 5 updates/sec = 20 bytes/sec
        action_overhead = 50  # Attacks, block breaks, etc
        misc_overhead = 30    # Keepalives, chat, etc
        
        per_player = position_updates + action_overhead + misc_overhead  # ~100 bytes/sec
        total = per_player * num_players
        
        return {
            "per_player_bps": per_player * 8,
            "total_bps": total * 8,
            "total_bytes_per_sec": total,
            "viable_on_1200_baud": total * 8 < 1200,
            "viable_on_9600_baud": total * 8 < 9600,
        }


# Test
if __name__ == "__main__":
    proto = AlkalineProtocol()
    
    # Test position compression
    print("Testing Minecraft position compression...")
    
    # Original: 46 bytes
    original_size = 46
    
    # Compressed: 4 bytes
    compressed = proto.compress_minecraft_position(1, 100.5, 64.0, -200.3, 45.0, -10.0)
    print(f"Original: {original_size} bytes")
    print(f"Compressed: {len(compressed)} bytes")
    print(f"Reduction: {(1 - len(compressed)/original_size) * 100:.1f}%")
    
    # Test decompression
    x, y, z, yaw, pitch = proto.decompress_minecraft_position(1, compressed)
    print(f"Decompressed position: ({x:.2f}, {y:.2f}, {z:.2f}) yaw={yaw:.1f} pitch={pitch:.1f}")
    
    # Test bandwidth estimate
    print("\nBandwidth estimates:")
    mc = MinecraftProtocolOptimizer()
    for players in [2, 5, 8]:
        est = mc.estimate_bandwidth(players)
        print(f"  {players} players: {est['total_bps']} bps - 1200 baud viable: {est['viable_on_1200_baud']}")
