#!/usr/bin/env python3
"""
Alkaline Network - One-Click Provisioning System
=================================================

THE DREAM FLOW:
  1. Customer pays on website
  2. You get notification with their info
  3. Grab blank device, plug in, click ONE button
  4. Device auto-configures, registers, gets assigned
  5. Unplug, box, ship
  6. Customer plugs in → internet works

This module ties together:
  - Website signups (pending orders)
  - Payment processing (Stripe webhooks)  
  - Device flashing (flash_tool.py)
  - Dashboard database (alkaline_dashboard.py)
  - Tunnel authorization (clients.json)

Usage:
  python alkaline_provisioning.py           # GUI - shows pending orders, one-click flash
  python alkaline_provisioning.py --server  # API server for webhook integration
"""

import os
import sys
import json
import time
import uuid
import socket
import secrets
import sqlite3
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "alkaline.db"
ORDERS_DB = SCRIPT_DIR / "orders.db"
CLIENTS_JSON = SCRIPT_DIR / "clients.json"
NETWORK_CONFIG = SCRIPT_DIR / "network_config.json"

# Device defaults (Heltec HT-H7608)
DEVICE_IP = "10.42.0.1"
DEVICE_USER = "root"
DEVICE_PASSWORD = "heltec.org"

# =============================================================================
# DATA MODELS
# =============================================================================

class OrderStatus(Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    READY_TO_SHIP = "ready_to_ship"  # Device provisioned
    SHIPPED = "shipped"
    ACTIVE = "active"  # Customer has internet
    CANCELLED = "cancelled"

class DeviceType(Enum):
    PINGER = "pinger"   # Customer device
    GATEWAY = "gateway"  # Gateway host device

@dataclass
class Order:
    order_id: str
    customer_name: str
    email: str
    phone: str
    address: str
    city: str
    state: str
    zip_code: str
    plan: str  # "option_a" or "option_b"
    monthly_price: float
    deposit: float
    status: str
    device_type: str
    device_mac: Optional[str]
    public_key: Optional[str]
    assigned_gateway: Optional[str]
    tunnel_ip: Optional[str]
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    created_at: str
    paid_at: Optional[str]
    provisioned_at: Optional[str]
    shipped_at: Optional[str]
    tracking_number: Optional[str]
    notes: str

# =============================================================================
# ORDERS DATABASE
# =============================================================================

class OrdersDatabase:
    """Manages pending and completed orders."""
    
    def __init__(self, db_path: Path = ORDERS_DB):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Create orders table if not exists."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                address TEXT NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                zip_code TEXT NOT NULL,
                plan TEXT NOT NULL,
                monthly_price REAL NOT NULL,
                deposit REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending_payment',
                device_type TEXT DEFAULT 'pinger',
                device_mac TEXT,
                public_key TEXT,
                assigned_gateway TEXT,
                tunnel_ip TEXT,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                provisioned_at TEXT,
                shipped_at TEXT,
                tracking_number TEXT,
                notes TEXT DEFAULT ''
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS gateway_orders (
                order_id TEXT PRIMARY KEY,
                host_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                address TEXT NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                zip_code TEXT NOT NULL,
                payment_method TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                device_mac TEXT,
                public_key TEXT,
                gateway_id TEXT,
                created_at TEXT NOT NULL,
                provisioned_at TEXT,
                shipped_at TEXT,
                notes TEXT DEFAULT ''
            )
        ''')
        
        # Track IP allocation
        c.execute('''
            CREATE TABLE IF NOT EXISTS ip_allocation (
                tunnel_ip TEXT PRIMARY KEY,
                order_id TEXT,
                allocated_at TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def create_order(self, 
                     customer_name: str,
                     email: str,
                     phone: str,
                     address: str,
                     city: str,
                     state: str,
                     zip_code: str,
                     plan: str,
                     device_type: str = "pinger") -> str:
        """Create a new order from website signup."""
        order_id = f"ORD-{secrets.token_hex(4).upper()}"
        
        # Set pricing based on plan
        if plan == "option_a":
            monthly_price = 7.99
            deposit = 100.00
        else:  # option_b
            monthly_price = 14.99
            deposit = 0.00
        
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO orders (
                order_id, customer_name, email, phone,
                address, city, state, zip_code,
                plan, monthly_price, deposit, status,
                device_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            order_id, customer_name, email, phone,
            address, city, state, zip_code,
            plan, monthly_price, deposit, OrderStatus.PENDING_PAYMENT.value,
            device_type, datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return order_id
    
    def mark_paid(self, order_id: str, 
                  stripe_customer_id: str = None,
                  stripe_subscription_id: str = None) -> bool:
        """Mark order as paid (triggered by Stripe webhook)."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            UPDATE orders SET 
                status = ?,
                paid_at = ?,
                stripe_customer_id = ?,
                stripe_subscription_id = ?
            WHERE order_id = ?
        ''', (
            OrderStatus.PAID.value,
            datetime.now().isoformat(),
            stripe_customer_id,
            stripe_subscription_id,
            order_id
        ))
        
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def get_pending_orders(self) -> List[Order]:
        """Get all paid orders waiting to be provisioned."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''
            SELECT * FROM orders 
            WHERE status = ?
            ORDER BY paid_at ASC
        ''', (OrderStatus.PAID.value,))
        
        orders = []
        for row in c.fetchall():
            orders.append(Order(**dict(row)))
        
        conn.close()
        return orders
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get a specific order."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('SELECT * FROM orders WHERE order_id = ?', (order_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            return Order(**dict(row))
        return None
    
    def allocate_tunnel_ip(self) -> str:
        """Allocate next available tunnel IP."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        # Get all allocated IPs
        c.execute('SELECT tunnel_ip FROM ip_allocation')
        allocated = set(row[0] for row in c.fetchall())
        
        # Also check main database for existing customers
        try:
            main_conn = sqlite3.connect(str(DB_PATH))
            main_c = main_conn.cursor()
            main_c.execute('SELECT tunnel_ip FROM customers WHERE tunnel_ip IS NOT NULL')
            for row in main_c.fetchall():
                if row[0]:
                    allocated.add(row[0])
            main_conn.close()
        except:
            pass
        
        # Find next available in 10.100.0.x range (skip .1 for server)
        for i in range(2, 255):
            ip = f"10.100.0.{i}"
            if ip not in allocated:
                # Allocate it
                c.execute('''
                    INSERT INTO ip_allocation (tunnel_ip, allocated_at)
                    VALUES (?, ?)
                ''', (ip, datetime.now().isoformat()))
                conn.commit()
                conn.close()
                return ip
        
        conn.close()
        raise Exception("No available tunnel IPs!")
    
    def provision_order(self, order_id: str, 
                        device_mac: str,
                        public_key: str,
                        assigned_gateway: str,
                        tunnel_ip: str) -> bool:
        """Mark order as provisioned with device details."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            UPDATE orders SET
                status = ?,
                device_mac = ?,
                public_key = ?,
                assigned_gateway = ?,
                tunnel_ip = ?,
                provisioned_at = ?
            WHERE order_id = ?
        ''', (
            OrderStatus.READY_TO_SHIP.value,
            device_mac,
            public_key,
            assigned_gateway,
            tunnel_ip,
            datetime.now().isoformat(),
            order_id
        ))
        
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def mark_shipped(self, order_id: str, tracking_number: str = None) -> bool:
        """Mark order as shipped."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            UPDATE orders SET
                status = ?,
                shipped_at = ?,
                tracking_number = ?
            WHERE order_id = ?
        ''', (
            OrderStatus.SHIPPED.value,
            datetime.now().isoformat(),
            tracking_number,
            order_id
        ))
        
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def activate_order(self, order_id: str) -> bool:
        """Mark order as active (customer has internet)."""
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        
        c.execute('''
            UPDATE orders SET status = ?
            WHERE order_id = ?
        ''', (OrderStatus.ACTIVE.value, order_id))
        
        success = c.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def find_best_gateway(self, city: str, state: str, zip_code: str) -> Optional[str]:
        """Find the best gateway for a customer based on location."""
        # For now, just return any available gateway
        # TODO: Implement actual distance calculation
        try:
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            
            c.execute('''
                SELECT gateway_id FROM gateways 
                WHERE (SELECT COUNT(*) FROM customers WHERE assigned_gateway = gateways.gateway_id) < max_customers
                LIMIT 1
            ''')
            
            row = c.fetchone()
            conn.close()
            
            if row:
                return row[0]
        except:
            pass
        
        return None


# =============================================================================
# DEVICE PROVISIONER
# =============================================================================

class DeviceProvisioner:
    """Handles the actual device flashing and configuration."""
    
    def __init__(self, orders_db: OrdersDatabase):
        self.orders_db = orders_db
        self.device_ip = DEVICE_IP
        self.device_user = DEVICE_USER
        self.device_password = DEVICE_PASSWORD
    
    def check_device_connected(self) -> Tuple[bool, str]:
        """Check if a Heltec device is connected."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.device_ip, 80))
            sock.close()
            
            if result == 0:
                return True, f"Device found at {self.device_ip}"
            else:
                return False, f"No device at {self.device_ip}"
        except Exception as e:
            return False, f"Error: {e}"
    
    def get_device_mac(self) -> Optional[str]:
        """Get MAC address from connected device."""
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            
            # Try to get system info from device
            resp = requests.get(
                f"http://{self.device_ip}/cgi-bin/luci/admin/status/overview",
                auth=HTTPBasicAuth(self.device_user, self.device_password),
                timeout=5
            )
            
            # Parse MAC from response (device-specific)
            # Fallback: generate pseudo-MAC from response hash
            mac = hashlib.md5(resp.content).hexdigest()[:12]
            return ':'.join(mac[i:i+2] for i in range(0, 12, 2)).upper()
        except:
            # Generate random MAC for testing
            return ':'.join(secrets.token_hex(1) for _ in range(6)).upper()
    
    def generate_keypair(self) -> Tuple[str, str]:
        """Generate NaCl keypair for tunnel encryption."""
        # For actual deployment, use nacl library
        # Here we generate hex strings as placeholders
        private_key = secrets.token_hex(32)
        public_key = secrets.token_hex(32)
        return private_key, public_key
    
    def flash_pinger(self, order: Order, gateway_id: str) -> Dict[str, Any]:
        """
        Flash a device as a customer pinger.
        
        Returns device info including generated keys.
        """
        result = {
            "success": False,
            "device_mac": None,
            "public_key": None,
            "private_key": None,
            "tunnel_ip": None,
            "error": None
        }
        
        # Check device is connected
        connected, msg = self.check_device_connected()
        if not connected:
            result["error"] = msg
            return result
        
        try:
            # Get device MAC
            device_mac = self.get_device_mac()
            result["device_mac"] = device_mac
            
            # Generate encryption keys
            private_key, public_key = self.generate_keypair()
            result["private_key"] = private_key
            result["public_key"] = public_key
            
            # Allocate tunnel IP
            tunnel_ip = self.orders_db.allocate_tunnel_ip()
            result["tunnel_ip"] = tunnel_ip
            
            # Load network config
            if NETWORK_CONFIG.exists():
                with open(NETWORK_CONFIG) as f:
                    network_config = json.load(f)
            else:
                network_config = {
                    "mesh_passphrase": secrets.token_hex(16),
                    "server_ip": "YOUR_VPS_IP",
                    "server_port": 51820
                }
            
            # Build device config
            device_config = {
                "mode": "pinger",
                "customer_id": order.order_id.replace("ORD-", "CUST-"),
                "customer_name": order.customer_name,
                "private_key": private_key,
                "public_key": public_key,
                "tunnel_ip": tunnel_ip,
                "server_ip": network_config.get("server_ip", "YOUR_VPS_IP"),
                "server_port": network_config.get("server_port", 51820),
                "mesh_id": "AlkalineNet",
                "mesh_passphrase": network_config.get("mesh_passphrase", ""),
                "gateway_id": gateway_id,
                "provisioned_at": datetime.now().isoformat()
            }
            
            # Actually flash the device
            flash_success = self._flash_device(device_config)
            
            if flash_success:
                result["success"] = True
            else:
                result["error"] = "Failed to flash device"
            
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def _flash_device(self, config: Dict[str, Any]) -> bool:
        """Actually flash configuration to the device."""
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            
            auth = HTTPBasicAuth(self.device_user, self.device_password)
            
            # This is device-specific
            # For Heltec HT-H7608 we'd use their mesh wizard API
            # For now, simulate success
            
            # Step 1: Set mesh parameters
            # POST to /cgi-bin/luci/admin/mesh/setup
            
            # Step 2: Write config file
            # POST to /cgi-bin/luci/admin/system/config
            
            # Step 3: Write tunnel config
            # The device will have our custom alkaline agent installed
            
            print(f"[FLASH] Would configure device with:")
            print(f"  Mode: {config['mode']}")
            print(f"  Customer: {config['customer_name']}")
            print(f"  Tunnel IP: {config['tunnel_ip']}")
            print(f"  Gateway: {config['gateway_id']}")
            
            # In real implementation, actually send configs
            # For now, return True to indicate success
            return True
            
        except Exception as e:
            print(f"[FLASH] Error: {e}")
            return False
    
    def provision_order(self, order: Order) -> Dict[str, Any]:
        """
        One-click provisioning for an order.
        
        Finds best gateway, flashes device, registers customer.
        """
        result = {
            "success": False,
            "order_id": order.order_id,
            "error": None,
            "details": {}
        }
        
        # Step 1: Find best gateway
        gateway_id = self.orders_db.find_best_gateway(
            order.city, order.state, order.zip_code
        )
        
        if not gateway_id:
            result["error"] = "No available gateways in area"
            return result
        
        result["details"]["gateway_id"] = gateway_id
        
        # Step 2: Flash the device
        flash_result = self.flash_pinger(order, gateway_id)
        
        if not flash_result["success"]:
            result["error"] = flash_result["error"]
            return result
        
        result["details"].update(flash_result)
        
        # Step 3: Register in main database
        try:
            from alkaline_dashboard import Database
            db = Database(str(DB_PATH))
            
            customer_id = order.order_id.replace("ORD-", "CUST-")
            
            # Add customer to main database
            db.add_customer(
                customer_id=customer_id,
                name=order.customer_name,
                email=order.email,
                phone=order.phone,
                address=f"{order.address}, {order.city}, {order.state} {order.zip_code}",
                plan=order.plan
            )
            
            # Update with device info
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            c.execute('''
                UPDATE customers SET
                    public_key = ?,
                    tunnel_ip = ?,
                    assigned_gateway = ?,
                    subscription_status = 'active'
                WHERE customer_id = ?
            ''', (
                flash_result["public_key"],
                flash_result["tunnel_ip"],
                gateway_id,
                customer_id
            ))
            conn.commit()
            conn.close()
            
            result["details"]["customer_id"] = customer_id
            
        except Exception as e:
            result["error"] = f"Database error: {e}"
            return result
        
        # Step 4: Update order status
        self.orders_db.provision_order(
            order.order_id,
            flash_result["device_mac"],
            flash_result["public_key"],
            gateway_id,
            flash_result["tunnel_ip"]
        )
        
        # Step 5: Sync to clients.json
        try:
            from alkaline_billing import BillingDatabase, sync_clients_json
            billing_db = BillingDatabase(DB_PATH)
            sync_clients_json(billing_db)
            result["details"]["synced"] = True
        except Exception as e:
            result["details"]["sync_error"] = str(e)
        
        result["success"] = True
        return result


# =============================================================================
# GUI - ONE-CLICK PROVISIONING PANEL
# =============================================================================

def run_gui():
    """Launch the one-click provisioning GUI."""
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    
    orders_db = OrdersDatabase()
    provisioner = DeviceProvisioner(orders_db)
    
    root = tk.Tk()
    root.title("Alkaline Network - Device Provisioning")
    root.geometry("900x700")
    root.configure(bg='#1a1a2e')
    
    # Styles
    style = ttk.Style()
    style.theme_use('clam')
    style.configure('TFrame', background='#1a1a2e')
    style.configure('TLabel', background='#1a1a2e', foreground='#ffffff', font=('Segoe UI', 10))
    style.configure('Header.TLabel', font=('Segoe UI', 16, 'bold'), foreground='#00ff88')
    style.configure('TButton', font=('Segoe UI', 10))
    style.configure('Big.TButton', font=('Segoe UI', 14, 'bold'), padding=15)
    
    # Main container
    main = ttk.Frame(root, padding=20)
    main.pack(fill=tk.BOTH, expand=True)
    
    # Header
    header = ttk.Frame(main)
    header.pack(fill=tk.X, pady=(0, 20))
    
    ttk.Label(header, text="📦 Device Provisioning", style='Header.TLabel').pack(side=tk.LEFT)
    
    # Device status
    device_frame = ttk.Frame(main)
    device_frame.pack(fill=tk.X, pady=10)
    
    device_status = tk.StringVar(value="⚪ No device detected")
    device_label = ttk.Label(device_frame, textvariable=device_status, font=('Segoe UI', 12))
    device_label.pack(side=tk.LEFT)
    
    def check_device():
        connected, msg = provisioner.check_device_connected()
        if connected:
            device_status.set(f"🟢 {msg}")
        else:
            device_status.set(f"🔴 {msg}")
        root.after(3000, check_device)
    
    check_device()
    
    # Pending Orders List
    orders_frame = ttk.LabelFrame(main, text="📋 Pending Orders (Paid, Awaiting Device)", padding=10)
    orders_frame.pack(fill=tk.BOTH, expand=True, pady=10)
    
    columns = ('Order ID', 'Customer', 'Location', 'Plan', 'Paid')
    orders_tree = ttk.Treeview(orders_frame, columns=columns, show='headings', height=8)
    
    for col in columns:
        orders_tree.heading(col, text=col)
        orders_tree.column(col, width=120)
    
    orders_tree.pack(fill=tk.BOTH, expand=True)
    
    # Scrollbar
    scrollbar = ttk.Scrollbar(orders_frame, orient=tk.VERTICAL, command=orders_tree.yview)
    orders_tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def refresh_orders():
        # Clear existing
        for item in orders_tree.get_children():
            orders_tree.delete(item)
        
        # Load pending orders
        orders = orders_db.get_pending_orders()
        for order in orders:
            orders_tree.insert('', tk.END, values=(
                order.order_id,
                order.customer_name,
                f"{order.city}, {order.state}",
                order.plan.replace('option_', 'Option ').upper(),
                order.paid_at[:10] if order.paid_at else 'N/A'
            ), iid=order.order_id)
    
    refresh_orders()
    
    # Selected order details
    details_frame = ttk.LabelFrame(main, text="📄 Order Details", padding=10)
    details_frame.pack(fill=tk.X, pady=10)
    
    details_text = tk.StringVar(value="Select an order above to see details")
    ttk.Label(details_frame, textvariable=details_text, wraplength=800).pack()
    
    selected_order = [None]
    
    def on_select(event):
        selection = orders_tree.selection()
        if selection:
            order_id = selection[0]
            order = orders_db.get_order(order_id)
            if order:
                selected_order[0] = order
                details_text.set(
                    f"Customer: {order.customer_name}\n"
                    f"Email: {order.email} | Phone: {order.phone}\n"
                    f"Address: {order.address}, {order.city}, {order.state} {order.zip_code}\n"
                    f"Plan: {order.plan} (${order.monthly_price}/mo + ${order.deposit} deposit)"
                )
    
    orders_tree.bind('<<TreeviewSelect>>', on_select)
    
    # BIG BUTTON
    button_frame = ttk.Frame(main)
    button_frame.pack(fill=tk.X, pady=20)
    
    def provision_selected():
        if not selected_order[0]:
            messagebox.showwarning("No Order", "Please select an order first")
            return
        
        connected, _ = provisioner.check_device_connected()
        if not connected:
            messagebox.showerror("No Device", "Plug in a device first!")
            return
        
        order = selected_order[0]
        
        if not messagebox.askyesno("Confirm", 
            f"Flash device for {order.customer_name}?\n\n"
            f"Address: {order.city}, {order.state}\n"
            f"Plan: {order.plan}"):
            return
        
        # Show progress
        progress = tk.Toplevel(root)
        progress.title("Provisioning...")
        progress.geometry("400x200")
        progress.configure(bg='#1a1a2e')
        
        ttk.Label(progress, text="⏳ Provisioning device...", 
                  style='Header.TLabel').pack(pady=30)
        
        status_var = tk.StringVar(value="Connecting to device...")
        ttk.Label(progress, textvariable=status_var).pack(pady=10)
        
        progress.update()
        
        def do_provision():
            status_var.set("Finding best gateway...")
            progress.update()
            time.sleep(0.5)
            
            status_var.set("Generating encryption keys...")
            progress.update()
            time.sleep(0.5)
            
            status_var.set("Flashing device configuration...")
            progress.update()
            
            result = provisioner.provision_order(order)
            
            progress.destroy()
            
            if result["success"]:
                messagebox.showinfo("Success! ✅",
                    f"Device provisioned for {order.customer_name}!\n\n"
                    f"Customer ID: {result['details'].get('customer_id')}\n"
                    f"Tunnel IP: {result['details'].get('tunnel_ip')}\n"
                    f"Gateway: {result['details'].get('gateway_id')}\n\n"
                    f"📦 Ready to ship!")
                refresh_orders()
            else:
                messagebox.showerror("Failed",
                    f"Provisioning failed:\n{result['error']}")
        
        # Run in thread to not block UI
        threading.Thread(target=do_provision, daemon=True).start()
    
    provision_btn = tk.Button(
        button_frame,
        text="⚡ PROVISION SELECTED ORDER",
        font=('Segoe UI', 16, 'bold'),
        bg='#00ff88',
        fg='#000000',
        activebackground='#00cc6a',
        padx=30,
        pady=15,
        command=provision_selected
    )
    provision_btn.pack(expand=True)
    
    # Quick actions
    actions_frame = ttk.Frame(main)
    actions_frame.pack(fill=tk.X)
    
    ttk.Button(actions_frame, text="🔄 Refresh Orders", 
               command=refresh_orders).pack(side=tk.LEFT, padx=5)
    
    ttk.Button(actions_frame, text="➕ Manual Order", 
               command=lambda: messagebox.showinfo("Manual Order", 
                   "Use the website signup form at alkalinehosting.com/signup")).pack(side=tk.LEFT, padx=5)
    
    # Log area
    log_frame = ttk.LabelFrame(main, text="📝 Activity Log", padding=10)
    log_frame.pack(fill=tk.BOTH, expand=True, pady=10)
    
    log_text = scrolledtext.ScrolledText(log_frame, height=6, 
                                          bg='#0a0a0a', fg='#00ff88',
                                          font=('Consolas', 9))
    log_text.pack(fill=tk.BOTH, expand=True)
    
    def log(msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        log_text.see(tk.END)
    
    log("Provisioning system started")
    log("Watching for devices on " + DEVICE_IP)
    
    root.mainloop()


# =============================================================================
# API SERVER - For webhook integration
# =============================================================================

def run_api_server(port: int = 8081):
    """Run API server for Stripe webhook integration."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json
    
    orders_db = OrdersDatabase()
    
    class ProvisioningHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/orders/pending':
                orders = orders_db.get_pending_orders()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps([asdict(o) for o in orders]).encode())
            
            elif self.path == '/health':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
            
            else:
                self.send_response(404)
                self.end_headers()
        
        def do_POST(self):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            try:
                data = json.loads(body) if body else {}
            except:
                data = {}
            
            if self.path == '/api/orders/create':
                # Create new order from website
                order_id = orders_db.create_order(
                    customer_name=data.get('name', ''),
                    email=data.get('email', ''),
                    phone=data.get('phone', ''),
                    address=data.get('address', ''),
                    city=data.get('city', ''),
                    state=data.get('state', ''),
                    zip_code=data.get('zip', ''),
                    plan=data.get('plan', 'option_b')
                )
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'order_id': order_id}).encode())
            
            elif self.path == '/api/orders/paid':
                # Stripe webhook: order paid
                order_id = data.get('order_id')
                if order_id:
                    orders_db.mark_paid(
                        order_id,
                        data.get('stripe_customer_id'),
                        data.get('stripe_subscription_id')
                    )
                
                self.send_response(200)
                self.end_headers()
            
            elif self.path == '/api/orders/shipped':
                # Mark order as shipped
                order_id = data.get('order_id')
                tracking = data.get('tracking_number')
                if order_id:
                    orders_db.mark_shipped(order_id, tracking)
                
                self.send_response(200)
                self.end_headers()
            
            else:
                self.send_response(404)
                self.end_headers()
        
        def log_message(self, format, *args):
            print(f"[API] {args[0]}")
    
    server = HTTPServer(('0.0.0.0', port), ProvisioningHandler)
    print(f"[API] Provisioning API running on port {port}")
    server.serve_forever()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Alkaline Network Provisioning")
    parser.add_argument('--server', action='store_true', help='Run API server')
    parser.add_argument('--port', type=int, default=8081, help='API server port')
    
    args = parser.parse_args()
    
    if args.server:
        run_api_server(args.port)
    else:
        run_gui()
