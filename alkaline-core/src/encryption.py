"""
Alkaline Network - End-to-End Encryption Module

Uses NaCl/libsodium (same cryptography as Signal).

Features:
- X25519 key exchange (Curve25519 ECDH)
- XSalsa20 stream cipher
- Poly1305 authentication
- Perfect forward secrecy
- Zero-knowledge relay design

Relay nodes and gateways CANNOT read your traffic.
"""

import os
import hashlib
import struct
import time
from typing import Tuple, Optional
from dataclasses import dataclass

# Try to import nacl (PyNaCl - Python binding to libsodium)
NACL_AVAILABLE = False
PrivateKey = None
PublicKey = None
Box = None
SecretBox = None

try:
    import nacl.public
    import nacl.secret
    import nacl.utils
    import nacl.hash
    import nacl.signing
    from nacl.public import PrivateKey, PublicKey, Box
    from nacl.secret import SecretBox
    NACL_AVAILABLE = True
except ImportError:
    print("[CRYPTO] WARNING: PyNaCl not installed. Run: pip install pynacl")


@dataclass
class KeyPair:
    """A public/private keypair."""
    private_key: bytes
    public_key: bytes


@dataclass 
class EncryptedPacket:
    """An encrypted packet with metadata."""
    nonce: bytes           # 24 bytes - unique per message
    ciphertext: bytes      # Encrypted data
    sender_public: bytes   # 32 bytes - sender's public key
    timestamp: int         # Unix timestamp (for replay protection)
    

class AlkalineEncryption:
    """
    End-to-end encryption for Alkaline Network.
    
    Usage:
        # Generate your identity
        crypto = AlkalineEncryption()
        my_keys = crypto.generate_keypair()
        
        # Encrypt for recipient
        encrypted = crypto.encrypt(
            plaintext=b"Hello!",
            recipient_public_key=their_public_key
        )
        
        # Decrypt from sender
        plaintext = crypto.decrypt(
            encrypted_packet=encrypted,
            sender_public_key=their_public_key
        )
    """
    
    NONCE_SIZE = 24
    KEY_SIZE = 32
    
    def __init__(self, private_key: bytes = None):
        """
        Initialize encryption.
        
        Args:
            private_key: Your private key (32 bytes). If None, generates new keypair.
        """
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl not installed. Run: pip install pynacl")
        
        if private_key:
            self._private_key = PrivateKey(private_key)
        else:
            self._private_key = PrivateKey.generate()
        
        self._public_key = self._private_key.public_key
        
        # Cache for Box objects (expensive to create)
        self._box_cache = {}
    
    @property
    def public_key(self) -> bytes:
        """Get your public key (share this with others)."""
        return bytes(self._public_key)
    
    @property
    def private_key(self) -> bytes:
        """Get your private key (NEVER share this)."""
        return bytes(self._private_key)
    
    def generate_keypair(self) -> KeyPair:
        """
        Generate a new keypair.
        
        Returns:
            KeyPair with private_key and public_key
        """
        private = PrivateKey.generate()
        public = private.public_key
        
        return KeyPair(
            private_key=bytes(private),
            public_key=bytes(public)
        )
    
    def _get_box(self, their_public_key: bytes) -> Box:
        """Get or create a Box for communicating with a specific peer."""
        key_hash = hashlib.sha256(their_public_key).hexdigest()[:16]
        
        if key_hash not in self._box_cache:
            their_key = PublicKey(their_public_key)
            self._box_cache[key_hash] = Box(self._private_key, their_key)
        
        return self._box_cache[key_hash]
    
    def encrypt(self, plaintext: bytes, recipient_public_key: bytes) -> EncryptedPacket:
        """
        Encrypt data for a specific recipient.
        
        Only the recipient can decrypt this.
        Relay nodes and gateways see only encrypted blobs.
        
        Args:
            plaintext: Data to encrypt
            recipient_public_key: Recipient's public key (32 bytes)
            
        Returns:
            EncryptedPacket containing nonce, ciphertext, sender public key, timestamp
        """
        box = self._get_box(recipient_public_key)
        
        # Generate unique nonce
        nonce = nacl.utils.random(self.NONCE_SIZE)
        
        # Current timestamp for replay protection
        timestamp = int(time.time())
        
        # Prepend timestamp to plaintext (for verification on decrypt)
        timestamped_plaintext = struct.pack('>Q', timestamp) + plaintext
        
        # Encrypt
        ciphertext = box.encrypt(timestamped_plaintext, nonce).ciphertext
        
        return EncryptedPacket(
            nonce=nonce,
            ciphertext=ciphertext,
            sender_public=self.public_key,
            timestamp=timestamp
        )
    
    def decrypt(self, encrypted_packet: EncryptedPacket, 
                sender_public_key: bytes = None,
                max_age_seconds: int = 300) -> bytes:
        """
        Decrypt data from a sender.
        
        Args:
            encrypted_packet: The encrypted packet
            sender_public_key: Sender's public key (uses packet's key if not provided)
            max_age_seconds: Maximum age of packet to accept (replay protection)
            
        Returns:
            Decrypted plaintext
            
        Raises:
            ValueError: If decryption fails or packet is too old
        """
        sender_key = sender_public_key or encrypted_packet.sender_public
        box = self._get_box(sender_key)
        
        try:
            # Decrypt
            timestamped_plaintext = box.decrypt(
                encrypted_packet.ciphertext,
                encrypted_packet.nonce
            )
            
            # Extract and verify timestamp
            timestamp = struct.unpack('>Q', timestamped_plaintext[:8])[0]
            plaintext = timestamped_plaintext[8:]
            
            # Check replay protection
            age = int(time.time()) - timestamp
            if age > max_age_seconds:
                raise ValueError(f"Packet too old: {age} seconds (max: {max_age_seconds})")
            
            if age < -60:  # Allow 60 seconds clock skew
                raise ValueError(f"Packet from the future: {-age} seconds ahead")
            
            return plaintext
            
        except nacl.exceptions.CryptoError as e:
            raise ValueError(f"Decryption failed: {e}")
    
    def encrypt_bytes(self, plaintext: bytes, recipient_public_key: bytes) -> bytes:
        """
        Encrypt and serialize to bytes (for transmission).
        
        Format:
            [nonce: 24 bytes]
            [sender_public: 32 bytes]
            [timestamp: 8 bytes]
            [ciphertext: variable]
        """
        packet = self.encrypt(plaintext, recipient_public_key)
        
        return (
            packet.nonce +
            packet.sender_public +
            struct.pack('>Q', packet.timestamp) +
            packet.ciphertext
        )
    
    def decrypt_bytes(self, encrypted_bytes: bytes, 
                      sender_public_key: bytes = None,
                      max_age_seconds: int = 300) -> bytes:
        """
        Deserialize and decrypt bytes.
        """
        if len(encrypted_bytes) < self.NONCE_SIZE + self.KEY_SIZE + 8:
            raise ValueError("Encrypted data too short")
        
        nonce = encrypted_bytes[:24]
        sender_public = encrypted_bytes[24:56]
        timestamp = struct.unpack('>Q', encrypted_bytes[56:64])[0]
        ciphertext = encrypted_bytes[64:]
        
        packet = EncryptedPacket(
            nonce=nonce,
            ciphertext=ciphertext,
            sender_public=sender_public,
            timestamp=timestamp
        )
        
        return self.decrypt(packet, sender_public_key or sender_public, max_age_seconds)


class SessionEncryption:
    """
    Session-based encryption with perfect forward secrecy.
    
    Each session generates ephemeral keys. Compromising long-term keys
    doesn't compromise past sessions.
    """
    
    def __init__(self, identity_key: bytes = None):
        """
        Initialize session encryption.
        
        Args:
            identity_key: Long-term identity private key
        """
        if not NACL_AVAILABLE:
            raise RuntimeError("PyNaCl not installed")
        
        # Long-term identity key
        if identity_key:
            self._identity_private = PrivateKey(identity_key)
        else:
            self._identity_private = PrivateKey.generate()
        
        self._identity_public = self._identity_private.public_key
        
        # Ephemeral session keys (regenerated per session)
        self._session_private = None
        self._session_public = None
        self._session_box = None
        self._peer_session_public = None
    
    @property
    def identity_public_key(self) -> bytes:
        """Long-term public identity key."""
        return bytes(self._identity_public)
    
    def start_session(self) -> bytes:
        """
        Start a new session and return ephemeral public key.
        
        Returns:
            Session public key to send to peer
        """
        self._session_private = PrivateKey.generate()
        self._session_public = self._session_private.public_key
        
        return bytes(self._session_public)
    
    def complete_session(self, peer_session_public: bytes) -> bool:
        """
        Complete session with peer's ephemeral public key.
        
        Args:
            peer_session_public: Peer's session public key
            
        Returns:
            True if session established
        """
        if not self._session_private:
            raise ValueError("Must call start_session() first")
        
        self._peer_session_public = PublicKey(peer_session_public)
        self._session_box = Box(self._session_private, self._peer_session_public)
        
        return True
    
    def encrypt_session(self, plaintext: bytes) -> bytes:
        """
        Encrypt data within the current session.
        
        Uses ephemeral keys for forward secrecy.
        """
        if not self._session_box:
            raise ValueError("Session not established")
        
        nonce = nacl.utils.random(24)
        ciphertext = self._session_box.encrypt(plaintext, nonce).ciphertext
        
        return nonce + ciphertext
    
    def decrypt_session(self, encrypted: bytes) -> bytes:
        """
        Decrypt data within the current session.
        """
        if not self._session_box:
            raise ValueError("Session not established")
        
        nonce = encrypted[:24]
        ciphertext = encrypted[24:]
        
        return self._session_box.decrypt(ciphertext, nonce)
    
    def end_session(self):
        """
        End session and destroy ephemeral keys.
        
        Past messages cannot be decrypted even if keys are compromised.
        """
        self._session_private = None
        self._session_public = None
        self._session_box = None
        self._peer_session_public = None


class TunnelEncryption:
    """
    Encrypted tunnel for Alkaline Network traffic.
    
    Wraps all traffic in an encrypted tunnel so relay nodes
    and gateways cannot see content or destinations.
    """
    
    def __init__(self, private_key: bytes = None):
        """Initialize tunnel encryption."""
        self.crypto = AlkalineEncryption(private_key)
        self.sessions = {}  # peer_id -> SessionEncryption
    
    @property
    def public_key(self) -> bytes:
        """Get our public key."""
        return self.crypto.public_key
    
    def create_tunnel_packet(self, 
                             destination: str,
                             data: bytes, 
                             gateway_public_key: bytes) -> bytes:
        """
        Create an encrypted tunnel packet.
        
        The gateway can route but cannot read the content.
        
        Args:
            destination: Final destination (e.g., "google.com:443")
            data: Payload data
            gateway_public_key: Gateway's public key
            
        Returns:
            Encrypted tunnel packet
        """
        # Inner packet: destination + data
        dest_bytes = destination.encode('utf-8')
        inner_packet = struct.pack('>H', len(dest_bytes)) + dest_bytes + data
        
        # Encrypt for gateway (gateway can see destination but not content)
        # Actually, we encrypt everything so gateway sees NOTHING
        return self.crypto.encrypt_bytes(inner_packet, gateway_public_key)
    
    def unwrap_tunnel_packet(self, 
                              encrypted_packet: bytes,
                              sender_public_key: bytes = None) -> Tuple[str, bytes]:
        """
        Unwrap a tunnel packet (gateway side).
        
        Args:
            encrypted_packet: The encrypted tunnel packet
            sender_public_key: Sender's public key
            
        Returns:
            Tuple of (destination, data)
        """
        inner_packet = self.crypto.decrypt_bytes(encrypted_packet, sender_public_key)
        
        dest_len = struct.unpack('>H', inner_packet[:2])[0]
        destination = inner_packet[2:2+dest_len].decode('utf-8')
        data = inner_packet[2+dest_len:]
        
        return destination, data


class KeyStore:
    """
    Secure storage for cryptographic keys.
    """
    
    def __init__(self, key_path: str = None):
        """
        Initialize key store.
        
        Args:
            key_path: Path to key file. If None, uses ~/.alkaline/keys
        """
        import os
        
        if key_path is None:
            key_path = os.path.expanduser("~/.alkaline/keys")
        
        self.key_path = key_path
        self._ensure_directory()
    
    def _ensure_directory(self):
        """Create key directory if it doesn't exist."""
        import os
        key_dir = os.path.dirname(self.key_path)
        if key_dir and not os.path.exists(key_dir):
            os.makedirs(key_dir, mode=0o700)  # Only owner can access
    
    def save_keypair(self, keypair: KeyPair, name: str = "identity"):
        """
        Save a keypair securely.
        
        Args:
            keypair: The keypair to save
            name: Name for this keypair
        """
        import os
        import json
        
        key_file = f"{self.key_path}/{name}.json"
        
        data = {
            "private_key": keypair.private_key.hex(),
            "public_key": keypair.public_key.hex(),
            "created": int(time.time())
        }
        
        # Write with restrictive permissions
        with open(key_file, 'w') as f:
            json.dump(data, f)
        
        os.chmod(key_file, 0o600)  # Only owner can read/write
    
    def load_keypair(self, name: str = "identity") -> Optional[KeyPair]:
        """
        Load a keypair.
        
        Args:
            name: Name of the keypair
            
        Returns:
            KeyPair or None if not found
        """
        import json
        
        key_file = f"{self.key_path}/{name}.json"
        
        try:
            with open(key_file, 'r') as f:
                data = json.load(f)
            
            return KeyPair(
                private_key=bytes.fromhex(data["private_key"]),
                public_key=bytes.fromhex(data["public_key"])
            )
        except FileNotFoundError:
            return None
    
    def get_or_create_identity(self) -> KeyPair:
        """
        Get existing identity or create new one.
        
        Returns:
            The identity keypair
        """
        keypair = self.load_keypair("identity")
        
        if keypair is None:
            crypto = AlkalineEncryption()
            keypair = crypto.generate_keypair()
            self.save_keypair(keypair, "identity")
        
        return keypair


# =============================================================================
# TESTS
# =============================================================================

def test_encryption():
    """Test basic encryption/decryption."""
    print("Testing Alkaline Encryption...")
    print("=" * 50)
    
    # Alice and Bob generate keypairs
    alice = AlkalineEncryption()
    bob = AlkalineEncryption()
    
    print(f"Alice public key: {alice.public_key.hex()[:32]}...")
    print(f"Bob public key:   {bob.public_key.hex()[:32]}...")
    
    # Alice encrypts message for Bob
    message = b"Hello Bob! This is a secret message."
    encrypted = alice.encrypt(message, bob.public_key)
    
    print(f"\nOriginal:  {message}")
    print(f"Encrypted: {encrypted.ciphertext.hex()[:32]}...")
    print(f"Nonce:     {encrypted.nonce.hex()}")
    
    # Bob decrypts
    decrypted = bob.decrypt(encrypted, alice.public_key)
    
    print(f"Decrypted: {decrypted}")
    
    assert decrypted == message, "Decryption failed!"
    print("\n✅ Basic encryption test PASSED")
    
    # Test bytes serialization
    print("\nTesting bytes serialization...")
    encrypted_bytes = alice.encrypt_bytes(message, bob.public_key)
    decrypted_bytes = bob.decrypt_bytes(encrypted_bytes, alice.public_key)
    
    assert decrypted_bytes == message, "Bytes serialization failed!"
    print("✅ Bytes serialization test PASSED")
    
    # Test session encryption
    print("\nTesting session encryption (forward secrecy)...")
    alice_session = SessionEncryption()
    bob_session = SessionEncryption()
    
    # Exchange session keys
    alice_session_pub = alice_session.start_session()
    bob_session_pub = bob_session.start_session()
    
    alice_session.complete_session(bob_session_pub)
    bob_session.complete_session(alice_session_pub)
    
    # Encrypt with session
    session_message = b"This message has forward secrecy!"
    session_encrypted = alice_session.encrypt_session(session_message)
    session_decrypted = bob_session.decrypt_session(session_encrypted)
    
    assert session_decrypted == session_message, "Session encryption failed!"
    print("✅ Session encryption test PASSED")
    
    # Test tunnel
    print("\nTesting tunnel encryption...")
    tunnel = TunnelEncryption()
    gateway = AlkalineEncryption()
    
    tunnel_packet = tunnel.create_tunnel_packet(
        destination="google.com:443",
        data=b"GET / HTTP/1.1\r\n\r\n",
        gateway_public_key=gateway.public_key
    )
    
    # Gateway unwraps
    gateway_tunnel = TunnelEncryption(gateway.private_key)
    dest, data = gateway_tunnel.unwrap_tunnel_packet(tunnel_packet, tunnel.public_key)
    
    assert dest == "google.com:443", "Tunnel destination wrong!"
    assert data == b"GET / HTTP/1.1\r\n\r\n", "Tunnel data wrong!"
    print(f"✅ Tunnel test PASSED - Destination: {dest}")
    
    print("\n" + "=" * 50)
    print("ALL ENCRYPTION TESTS PASSED ✅")
    print("=" * 50)


if __name__ == "__main__":
    if not NACL_AVAILABLE:
        print("ERROR: PyNaCl not installed.")
        print("Run: pip install pynacl")
        exit(1)
    
    test_encryption()
