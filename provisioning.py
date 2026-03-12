#!/usr/bin/env python3
"""
Alkaline Network - One-Click Provisioning System
=================================================

This connects the website signups to the flash tool for true one-click deployment.

FLOW:
  1. Customer signs up on website + pays via Stripe
  2. Order appears in pending_orders.json
  3. You plug in a blank device
  4. Flash tool sees pending order, shows "PROVISION FOR: John Doe"
  5. Click button → device configured + registered + assigned to gateway
  6. Unplug, ship to customer's address
  7. Customer plugs in → internet works

For gateway hosts:
  1. Host signs up on website
  2. You flash device as gateway
  3. Ship to host
  4. Host plugs in → starts serving customers

Author: AlkalineTech
License: MIT
"""

import os
import sys
import json
import time
import secrets
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
ORDERS_FILE = SCRIPT_DIR / "pending_orders.json"
DEVICES_FILE = SCRIPT_DIR / "provisioned_devices.json"
DB_PATH = SCRIPT_DIR / "alkaline.db"
CLIENTS_JSON = SCRIPT_DIR / "clients.json"

# Tunnel server config (update with your VPS IP)
SERVER_HOST = os.environ.get("ALKALINE_SERVER", "your-vps-ip.com")
SERVER_PORT = int(os.environ.get("ALKALINE_PORT", "51820"))


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class PendingOrder:
    """A pending device order from the website."""
    order_id: str
    order_type: str  # 'pinger' or 'gateway'
    customer_id: str
    customer_name: str
    customer_email: str
    customer_phone: str
    customer_address: str
    plan: str  # 'option_a' or 'option_b'
    stripe_payment_id: str
    deposit_paid: float
    created_at: str
    status: str  # 'pending', 'provisioning', 'shipped', 'active'
    assigned_gateway: str = ""
    device_id: str = ""
    public_key: str = ""
    tunnel_ip: str = ""


@dataclass 
class ProvisionedDevice:
    """A device that has been provisioned and shipped."""
    device_id: str
    device_type: str  # 'pinger' or 'gateway'
    order_id: str
    customer_id: str
    public_key: str
    private_key_hash: str  # We don't store private key, just hash for verification
    tunnel_ip: str
    mac_address: str
    provisioned_at: str
    shipped_at: str
    activated_at: str
    status: str  # 'provisioned', 'shipped', 'active', 'inactive'


# =============================================================================
# ORDER MANAGEMENT
# =============================================================================

class OrderManager:
    """Manages pending orders from website signups."""
    
    def __init__(self, orders_file: Path = ORDERS_FILE):
        self.orders_file = orders_file
        self._ensure_file()
    
    def _ensure_file(self):
        if not self.orders_file.exists():
            self._save([])
    
    def _load(self) -> List[Dict]:
        with open(self.orders_file) as f:
            return json.load(f)
    
    def _save(self, orders: List[Dict]):
        with open(self.orders_file, 'w') as f:
            json.dump(orders, f, indent=2)
    
    def get_pending_orders(self) -> List[PendingOrder]:
        """Get all orders waiting to be provisioned."""
        orders = self._load()
        return [
            PendingOrder(**o) for o in orders 
            if o.get('status') == 'pending'
        ]
    
    def get_all_orders(self) -> List[PendingOrder]:
        """Get all orders."""
        return [PendingOrder(**o) for o in self._load()]
    
    def add_order(self, order: PendingOrder) -> bool:
        """Add a new order."""
        orders = self._load()
        
        # Check for duplicate
        if any(o['order_id'] == order.order_id for o in orders):
            return False
        
        orders.append(asdict(order))
        self._save(orders)
        return True
    
    def update_order(self, order_id: str, updates: Dict) -> bool:
        """Update an order."""
        orders = self._load()
        
        for o in orders:
            if o['order_id'] == order_id:
                o.update(updates)
                self._save(orders)
                return True
        
        return False
    
    def mark_provisioned(self, order_id: str, device_id: str, 
                         public_key: str, tunnel_ip: str) -> bool:
        """Mark order as provisioned with device info."""
        return self.update_order(order_id, {
            'status': 'provisioning',
            'device_id': device_id,
            'public_key': public_key,
            'tunnel_ip': tunnel_ip
        })
    
    def mark_shipped(self, order_id: str) -> bool:
        """Mark order as shipped."""
        return self.update_order(order_id, {
            'status': 'shipped'
        })
    
    def mark_active(self, order_id: str) -> bool:
        """Mark order as active (customer plugged in device)."""
        return self.update_order(order_id, {
            'status': 'active'
        })


# =============================================================================
# DEVICE PROVISIONING
# =============================================================================

class DeviceProvisioner:
    """Handles device provisioning and registration."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.devices_file = DEVICES_FILE
        self._ensure_devices_file()
    
    def _ensure_devices_file(self):
        if not self.devices_file.exists():
            with open(self.devices_file, 'w') as f:
                json.dump([], f)
    
    def generate_device_id(self, device_type: str) -> str:
        """Generate unique device ID."""
        prefix = "GW" if device_type == "gateway" else "PN"
        random_part = secrets.token_hex(4).upper()
        return f"{prefix}-{random_part}"
    
    def generate_tunnel_ip(self) -> str:
        """Generate next available tunnel IP."""
        # Load existing devices to find next IP
        try:
            with open(self.devices_file) as f:
                devices = json.load(f)
        except:
            devices = []
        
        # Also check database
        used_ips = set()
        
        for d in devices:
            ip = d.get('tunnel_ip', '')
            if ip:
                used_ips.add(ip)
        
        # Check database too
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(str(self.db_path))
                c = conn.cursor()
                c.execute("SELECT tunnel_ip FROM customers WHERE tunnel_ip IS NOT NULL")
                for row in c.fetchall():
                    if row[0]:
                        used_ips.add(row[0])
                conn.close()
            except:
                pass
        
        # Find next available IP in 10.100.0.0/16 range
        for i in range(2, 65534):
            ip = f"10.100.{i // 256}.{i % 256}"
            if ip not in used_ips:
                return ip
        
        raise Exception("No available tunnel IPs!")
    
    def provision_device(self, order: PendingOrder, 
                         public_key: str, private_key_hash: str,
                         mac_address: str = "") -> ProvisionedDevice:
        """
        Provision a device for an order.
        
        Args:
            order: The pending order
            public_key: Device's public key (generated on device)
            private_key_hash: Hash of private key (for verification)
            mac_address: Device MAC address
        
        Returns:
            ProvisionedDevice with all info
        """
        device_id = self.generate_device_id(order.order_type)
        tunnel_ip = self.generate_tunnel_ip()
        
        device = ProvisionedDevice(
            device_id=device_id,
            device_type=order.order_type,
            order_id=order.order_id,
            customer_id=order.customer_id,
            public_key=public_key,
            private_key_hash=private_key_hash,
            tunnel_ip=tunnel_ip,
            mac_address=mac_address,
            provisioned_at=datetime.now().isoformat(),
            shipped_at="",
            activated_at="",
            status="provisioned"
        )
        
        # Save to devices file
        with open(self.devices_file) as f:
            devices = json.load(f)
        devices.append(asdict(device))
        with open(self.devices_file, 'w') as f:
            json.dump(devices, f, indent=2)
        
        # Register in database
        self._register_in_database(order, device)
        
        # Sync to clients.json for tunnel server
        self._sync_to_clients_json(device, order)
        
        return device
    
    def _register_in_database(self, order: PendingOrder, device: ProvisionedDevice):
        """Register the device/customer in the main database."""
        if not self.db_path.exists():
            return
        
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        if device.device_type == 'pinger':
            # Update customer with device info
            c.execute("""
                UPDATE customers SET
                    pinger_id = ?,
                    public_key = ?,
                    tunnel_ip = ?,
                    subscription_status = 'active',
                    last_seen = ?
                WHERE customer_id = ?
            """, (
                device.device_id,
                device.public_key,
                device.tunnel_ip,
                time.time(),
                order.customer_id
            ))
        else:
            # For gateway, update gateway record
            c.execute("""
                UPDATE gateways SET
                    public_key = ?,
                    ip_address = ?,
                    last_seen = ?
                WHERE gateway_id = ?
            """, (
                device.public_key,
                device.tunnel_ip,
                time.time(),
                order.customer_id  # For gateways, customer_id is gateway_id
            ))
        
        conn.commit()
        conn.close()
    
    def _sync_to_clients_json(self, device: ProvisionedDevice, order: PendingOrder):
        """Add device to clients.json so tunnel server allows it."""
        clients = {}
        
        if CLIENTS_JSON.exists():
            with open(CLIENTS_JSON) as f:
                clients = json.load(f)
        
        # Add this device
        clients[device.public_key] = {
            "name": order.customer_name,
            "tunnel_ip": device.tunnel_ip,
            "customer_id": order.customer_id,
            "device_id": device.device_id,
            "device_type": device.device_type,
            "plan": order.plan,
            "bytes_up": 0,
            "bytes_down": 0
        }
        
        with open(CLIENTS_JSON, 'w') as f:
            json.dump(clients, f, indent=2)
        
        print(f"[SYNC] Added {device.device_id} to clients.json")


# =============================================================================
# GATEWAY ASSIGNMENT
# =============================================================================

class GatewayAssigner:
    """Automatically assigns customers to best available gateway."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
    
    def get_available_gateways(self) -> List[Dict]:
        """Get gateways with available capacity."""
        if not self.db_path.exists():
            return []
        
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("""
            SELECT g.*, 
                   (SELECT COUNT(*) FROM customers c 
                    WHERE c.gateway_id = g.gateway_id AND c.status = 'active') as customer_count
            FROM gateways g
            WHERE g.status = 'active'
        """)
        
        gateways = []
        for row in c.fetchall():
            g = dict(row)
            g['available_slots'] = g.get('max_customers', 9) - g.get('customer_count', 0)
            if g['available_slots'] > 0:
                gateways.append(g)
        
        conn.close()
        return gateways
    
    def assign_to_best_gateway(self, customer_id: str) -> Optional[str]:
        """Assign customer to gateway with most available slots."""
        gateways = self.get_available_gateways()
        
        if not gateways:
            print("[WARNING] No gateways with available slots!")
            return None
        
        # Sort by available slots (most first)
        gateways.sort(key=lambda g: g['available_slots'], reverse=True)
        
        best_gateway = gateways[0]
        gateway_id = best_gateway['gateway_id']
        
        # Assign in database
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute(
            "UPDATE customers SET gateway_id = ? WHERE customer_id = ?",
            (gateway_id, customer_id)
        )
        conn.commit()
        conn.close()
        
        print(f"[ASSIGN] Customer {customer_id} → Gateway {gateway_id}")
        return gateway_id


# =============================================================================
# STRIPE WEBHOOK HANDLER (for website integration)
# =============================================================================

def create_order_from_signup(signup_data: Dict, stripe_payment_id: str) -> PendingOrder:
    """
    Create a pending order from website signup data.
    
    Called by the website server when a customer completes payment.
    """
    order_id = f"ORD-{secrets.token_hex(6).upper()}"
    customer_id = f"CUST-{secrets.token_hex(4).upper()}"
    
    order = PendingOrder(
        order_id=order_id,
        order_type='pinger',  # Default to pinger for customers
        customer_id=customer_id,
        customer_name=signup_data.get('name', ''),
        customer_email=signup_data.get('email', ''),
        customer_phone=signup_data.get('phone', ''),
        customer_address=signup_data.get('address', ''),
        plan=signup_data.get('plan', 'option_a'),
        stripe_payment_id=stripe_payment_id,
        deposit_paid=100.0 if signup_data.get('plan') == 'option_a' else 0,
        created_at=datetime.now().isoformat(),
        status='pending'
    )
    
    # Save to orders file
    manager = OrderManager()
    manager.add_order(order)
    
    # Also add to database as inactive customer
    _add_customer_to_database(order)
    
    print(f"[ORDER] Created {order.order_id} for {order.customer_name}")
    return order


def create_gateway_order(host_data: Dict) -> PendingOrder:
    """Create order for a new gateway host."""
    order_id = f"ORD-{secrets.token_hex(6).upper()}"
    gateway_id = f"GW-{secrets.token_hex(4).upper()}"
    
    order = PendingOrder(
        order_id=order_id,
        order_type='gateway',
        customer_id=gateway_id,  # For gateways, this is the gateway_id
        customer_name=host_data.get('name', ''),
        customer_email=host_data.get('email', ''),
        customer_phone=host_data.get('phone', ''),
        customer_address=host_data.get('address', ''),
        plan='gateway_host',
        stripe_payment_id='',  # Gateways don't pay
        deposit_paid=0,
        created_at=datetime.now().isoformat(),
        status='pending'
    )
    
    manager = OrderManager()
    manager.add_order(order)
    
    # Add to database as gateway
    _add_gateway_to_database(order)
    
    print(f"[ORDER] Created gateway order {order.order_id} for {order.customer_name}")
    return order


def _add_customer_to_database(order: PendingOrder):
    """Add customer to database with inactive status."""
    if not DB_PATH.exists():
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    try:
        c.execute("""
            INSERT INTO customers (
                customer_id, name, email, phone, address, plan,
                deposit_paid, status, subscription_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.customer_id,
            order.customer_name,
            order.customer_email,
            order.customer_phone,
            order.customer_address,
            order.plan,
            order.deposit_paid,
            'pending',
            'pending_device',
            time.time()
        ))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # Already exists
    finally:
        conn.close()


def _add_gateway_to_database(order: PendingOrder):
    """Add gateway to database."""
    if not DB_PATH.exists():
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    try:
        c.execute("""
            INSERT INTO gateways (
                gateway_id, owner_name, owner_email, owner_payment,
                max_customers, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            order.customer_id,
            order.customer_name,
            order.customer_email,
            '',  # Payment method set up later
            9,
            'pending',
            time.time()
        ))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()


# =============================================================================
# SHIPPING LABEL GENERATOR (bonus feature)
# =============================================================================

def generate_shipping_info(order: PendingOrder) -> Dict:
    """Generate shipping info for an order."""
    return {
        'to_name': order.customer_name,
        'to_address': order.customer_address,
        'to_phone': order.customer_phone,
        'to_email': order.customer_email,
        'from_name': 'Alkaline Network',
        'from_address': '1005 Martin St SE, Atlanta GA 30315',  # Update with your address
        'order_id': order.order_id,
        'device_type': 'Mesh Pinger' if order.order_type == 'pinger' else 'Mesh Gateway',
        'notes': f"Plan: {order.plan}"
    }


# =============================================================================
# CLI
# =============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Alkaline Provisioning System")
    parser.add_argument('--list-pending', action='store_true', help='List pending orders')
    parser.add_argument('--list-all', action='store_true', help='List all orders')
    parser.add_argument('--add-test-order', action='store_true', help='Add a test order')
    parser.add_argument('--add-gateway-order', action='store_true', help='Add a test gateway order')
    
    args = parser.parse_args()
    
    manager = OrderManager()
    
    if args.list_pending:
        orders = manager.get_pending_orders()
        print(f"\n{'='*60}")
        print(f"PENDING ORDERS ({len(orders)})")
        print(f"{'='*60}")
        for o in orders:
            print(f"\n  Order: {o.order_id}")
            print(f"  Type:  {o.order_type.upper()}")
            print(f"  Name:  {o.customer_name}")
            print(f"  Email: {o.customer_email}")
            print(f"  Plan:  {o.plan}")
            print(f"  Date:  {o.created_at[:10]}")
        print()
        return
    
    if args.list_all:
        orders = manager.get_all_orders()
        print(f"\n{'='*60}")
        print(f"ALL ORDERS ({len(orders)})")
        print(f"{'='*60}")
        for o in orders:
            status_icon = {
                'pending': '⏳',
                'provisioning': '🔧',
                'shipped': '📦',
                'active': '✅'
            }.get(o.status, '❓')
            print(f"  {status_icon} {o.order_id} | {o.order_type:7} | {o.customer_name:20} | {o.status}")
        print()
        return
    
    if args.add_test_order:
        order = create_order_from_signup({
            'name': 'Test Customer',
            'email': 'test@example.com',
            'phone': '555-1234',
            'address': '123 Test Street, Test City, TS 12345',
            'plan': 'option_a'
        }, 'pi_test_123')
        print(f"Created test order: {order.order_id}")
        return
    
    if args.add_gateway_order:
        order = create_gateway_order({
            'name': 'Test Gateway Host',
            'email': 'host@example.com', 
            'phone': '555-5678',
            'address': '456 Host Lane, Gateway City, GW 67890'
        })
        print(f"Created gateway order: {order.order_id}")
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
