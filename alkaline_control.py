#!/usr/bin/env python3
"""
Alkaline Network - Control Panel
=================================

One window to control everything:
  - Flash devices (Gateway / Pinger buttons)
  - View customers and gateways
  - Move customers, set limits, auto-balance
  - See network stats

Run: python alkaline_control.py
"""

import os
import sys
import json
import time
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "network_config.json"
DB_FILE = SCRIPT_DIR / "alkaline.db"

# Heltec defaults
DEVICE_IP = "10.42.0.1"
DEVICE_USER = "root"
DEVICE_PASSWORD = "heltec.org"

# =============================================================================
# DATABASE
# =============================================================================

def init_db():
    """Initialize SQLite database."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS gateways (
        gateway_id TEXT PRIMARY KEY,
        owner_name TEXT,
        owner_email TEXT,
        owner_payment TEXT,
        max_customers INTEGER DEFAULT 9,
        created_at REAL,
        last_seen REAL,
        status TEXT DEFAULT 'active'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT,
        phone TEXT,
        plan TEXT DEFAULT 'option_a',
        gateway_id TEXT,
        created_at REAL,
        last_seen REAL,
        status TEXT DEFAULT 'active'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        device_type TEXT,
        mac_address TEXT,
        provisioned_at REAL
    )''')
    
    conn.commit()
    conn.close()

def get_stats():
    """Get network statistics."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM gateways WHERE status='active'")
    gateways = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM customers WHERE status='active'")
    customers = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM devices")
    devices = c.fetchone()[0]
    
    conn.close()
    
    # Calculate revenue
    revenue = customers * 7.99  # Simplified
    
    return {
        'gateways': gateways,
        'customers': customers,
        'devices': devices,
        'revenue': revenue
    }

def get_all_gateways():
    """Get all gateways."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM gateways ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_all_customers():
    """Get all customers."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM customers ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_gateway_customer_count(gateway_id):
    """Get customer count for a gateway."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM customers WHERE gateway_id=? AND status='active'", (gateway_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def move_customer(customer_id, new_gateway_id):
    """Move a customer to a different gateway."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("UPDATE customers SET gateway_id=? WHERE customer_id=?", (new_gateway_id, customer_id))
    conn.commit()
    conn.close()

def set_gateway_limit(gateway_id, limit):
    """Set gateway customer limit."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("UPDATE gateways SET max_customers=? WHERE gateway_id=?", (limit, gateway_id))
    conn.commit()
    conn.close()

def add_device(device_id, device_type, mac_address):
    """Record a provisioned device."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?, ?, ?, ?)",
              (device_id, device_type, mac_address, time.time()))
    conn.commit()
    conn.close()

# =============================================================================
# NETWORK CONFIG
# =============================================================================

def load_config():
    """Load or create network config."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    
    # Create new config
    import secrets
    config = {
        "mesh_id": "AlkalineNet",
        "mesh_passphrase": secrets.token_hex(16),
        "admin_password": secrets.token_hex(8),
        "customer_wifi_password": secrets.token_hex(6).upper(),
        "server_ip": "",
        "server_pubkey": "",
        "gateway_count": 0,
        "pinger_count": 0,
        "created_at": datetime.now().isoformat()
    }
    
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    
    return config

def save_config(config):
    """Save network config."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

# =============================================================================
# FLASH FUNCTIONS
# =============================================================================

def check_device_connection():
    """Check if Heltec device is connected."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex((DEVICE_IP, 80))
        sock.close()
        return result == 0
    except:
        return False

def flash_device(device_type, log_callback):
    """Flash a device as gateway or pinger."""
    config = load_config()
    
    log_callback(f"Starting {device_type.upper()} provisioning...")
    
    # Check connection
    log_callback("Checking device connection...")
    if not check_device_connection():
        log_callback("ERROR: Cannot connect to device at 10.42.0.1")
        log_callback("Make sure device is connected via Ethernet")
        return False
    
    log_callback("Device found!")
    
    # Try SSH
    try:
        import paramiko
        log_callback("Connecting via SSH...")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(DEVICE_IP, username=DEVICE_USER, password=DEVICE_PASSWORD, timeout=10)
        
        # Get MAC address
        stdin, stdout, stderr = ssh.exec_command("cat /sys/class/net/eth0/address")
        mac = stdout.read().decode().strip().upper()
        log_callback(f"MAC: {mac}")
        
        # Generate device ID
        if device_type == "gateway":
            config["gateway_count"] += 1
            device_id = f"GW-{config['gateway_count']:03d}"
        else:
            config["pinger_count"] += 1
            device_id = f"PN-{config['pinger_count']:03d}"
        
        log_callback(f"Device ID: {device_id}")
        
        # Configure mesh
        log_callback("Configuring mesh settings...")
        commands = [
            f"uci set wireless.halow.mesh_id='{config['mesh_id']}'",
            f"uci set wireless.halow.encryption='sae'",
            f"uci set wireless.halow.key='{config['mesh_passphrase']}'",
            "uci commit wireless",
        ]
        
        if device_type == "gateway":
            commands.insert(0, "uci set wireless.halow.mode='mesh'")
            commands.insert(1, "uci set wireless.halow.mesh_gate_announcements='1'")
        else:
            commands.insert(0, "uci set wireless.halow.mode='mesh'")
        
        for cmd in commands:
            ssh.exec_command(cmd)
            time.sleep(0.2)
        
        # Save device ID
        ssh.exec_command(f"echo '{device_id}' > /etc/alkaline_device_id")
        ssh.exec_command(f"echo '{device_type}' > /etc/alkaline_mode")
        
        # Change admin password
        log_callback("Securing device...")
        ssh.exec_command(f"echo 'root:{config['admin_password']}' | chpasswd")
        
        # Reboot
        log_callback("Rebooting device...")
        ssh.exec_command("reboot")
        
        ssh.close()
        
        # Save to database
        add_device(device_id, device_type, mac)
        save_config(config)
        
        log_callback("")
        log_callback("=" * 40)
        log_callback(f"SUCCESS! Device provisioned as {device_type.upper()}")
        log_callback(f"Device ID: {device_id}")
        log_callback("=" * 40)
        log_callback("")
        log_callback("Unplug device and deploy!")
        
        return True
        
    except ImportError:
        log_callback("ERROR: paramiko not installed")
        log_callback("Run: pip install paramiko")
        return False
    except Exception as e:
        log_callback(f"ERROR: {e}")
        return False

# =============================================================================
# GUI
# =============================================================================

class AlkalineControlPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Alkaline Network - Control Panel")
        self.root.geometry("900x700")
        self.root.configure(bg='#0a0a0a')
        
        # Initialize
        init_db()
        load_config()
        
        self.setup_ui()
        self.refresh_data()
    
    def setup_ui(self):
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#0a0a0a')
        style.configure('TLabel', background='#0a0a0a', foreground='#e0e0e0')
        style.configure('TButton', padding=10)
        style.configure('Header.TLabel', font=('Arial', 24, 'bold'), foreground='#3b82f6')
        style.configure('Stat.TLabel', font=('Arial', 32, 'bold'), foreground='#3b82f6')
        style.configure('StatLabel.TLabel', font=('Arial', 11), foreground='#666666')
        
        # Main container
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill='both', expand=True)
        
        # Header
        header = ttk.Frame(main)
        header.pack(fill='x', pady=(0, 20))
        
        ttk.Label(header, text="Alkaline Network", style='Header.TLabel').pack(side='left')
        
        # Stats row
        stats_frame = ttk.Frame(main)
        stats_frame.pack(fill='x', pady=(0, 20))
        
        self.stat_gateways = self.create_stat_card(stats_frame, "0", "Gateways")
        self.stat_customers = self.create_stat_card(stats_frame, "0", "Customers")
        self.stat_devices = self.create_stat_card(stats_frame, "0", "Devices")
        self.stat_revenue = self.create_stat_card(stats_frame, "$0", "Monthly")
        
        # Notebook for tabs
        notebook = ttk.Notebook(main)
        notebook.pack(fill='both', expand=True)
        
        # Tab 1: Flash Tool
        flash_tab = ttk.Frame(notebook, padding=20)
        notebook.add(flash_tab, text="  Flash Device  ")
        self.setup_flash_tab(flash_tab)
        
        # Tab 2: Gateways
        gateways_tab = ttk.Frame(notebook, padding=20)
        notebook.add(gateways_tab, text="  Gateways  ")
        self.setup_gateways_tab(gateways_tab)
        
        # Tab 3: Customers
        customers_tab = ttk.Frame(notebook, padding=20)
        notebook.add(customers_tab, text="  Customers  ")
        self.setup_customers_tab(customers_tab)
    
    def create_stat_card(self, parent, value, label):
        frame = ttk.Frame(parent)
        frame.pack(side='left', expand=True, fill='x', padx=5)
        
        card = tk.Frame(frame, bg='#151515', highlightbackground='#222', highlightthickness=1)
        card.pack(fill='x', ipady=15)
        
        val_label = tk.Label(card, text=value, font=('Arial', 28, 'bold'), fg='#3b82f6', bg='#151515')
        val_label.pack()
        
        tk.Label(card, text=label, font=('Arial', 10), fg='#666', bg='#151515').pack()
        
        return val_label
    
    def setup_flash_tab(self, parent):
        # Instructions
        tk.Label(parent, text="Connect Heltec device via Ethernet, then click:", 
                 font=('Arial', 12), fg='#888', bg='#0a0a0a').pack(pady=(0, 20))
        
        # Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(pady=20)
        
        gateway_btn = tk.Button(btn_frame, text="🌐  GATEWAY", font=('Arial', 16, 'bold'),
                                bg='#22c55e', fg='white', width=15, height=2,
                                command=lambda: self.flash("gateway"))
        gateway_btn.pack(side='left', padx=10)
        
        pinger_btn = tk.Button(btn_frame, text="📡  PINGER", font=('Arial', 16, 'bold'),
                               bg='#3b82f6', fg='white', width=15, height=2,
                               command=lambda: self.flash("pinger"))
        pinger_btn.pack(side='left', padx=10)
        
        # Log
        tk.Label(parent, text="Log:", font=('Arial', 10), fg='#666', bg='#0a0a0a').pack(anchor='w', pady=(20, 5))
        
        self.log_text = scrolledtext.ScrolledText(parent, height=15, bg='#151515', fg='#e0e0e0',
                                                   font=('Consolas', 10), insertbackground='white')
        self.log_text.pack(fill='both', expand=True)
    
    def setup_gateways_tab(self, parent):
        # Toolbar
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill='x', pady=(0, 10))
        
        tk.Button(toolbar, text="➕ Add Gateway", command=self.add_gateway,
                  bg='#3b82f6', fg='white').pack(side='left')
        tk.Button(toolbar, text="⚖️ Auto-Balance", command=self.auto_balance,
                  bg='#666', fg='white').pack(side='left', padx=5)
        tk.Button(toolbar, text="↻ Refresh", command=self.refresh_data,
                  bg='#333', fg='white').pack(side='right')
        
        # Table
        columns = ('ID', 'Owner', 'Customers', 'Limit', 'Status')
        self.gateways_tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        
        for col in columns:
            self.gateways_tree.heading(col, text=col)
            self.gateways_tree.column(col, width=100)
        
        self.gateways_tree.pack(fill='both', expand=True)
        
        # Context menu
        self.gateways_tree.bind('<Button-3>', self.gateway_context_menu)
    
    def setup_customers_tab(self, parent):
        # Toolbar
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill='x', pady=(0, 10))
        
        tk.Button(toolbar, text="➕ Add Customer", command=self.add_customer,
                  bg='#3b82f6', fg='white').pack(side='left')
        tk.Button(toolbar, text="↻ Refresh", command=self.refresh_data,
                  bg='#333', fg='white').pack(side='right')
        
        # Table
        columns = ('ID', 'Name', 'Email', 'Plan', 'Gateway', 'Status')
        self.customers_tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        
        for col in columns:
            self.customers_tree.heading(col, text=col)
            self.customers_tree.column(col, width=100)
        
        self.customers_tree.pack(fill='both', expand=True)
        
        # Context menu
        self.customers_tree.bind('<Button-3>', self.customer_context_menu)
    
    def log(self, message):
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.root.update()
    
    def flash(self, device_type):
        self.log_text.delete('1.0', 'end')
        
        def do_flash():
            flash_device(device_type, self.log)
            self.refresh_data()
        
        threading.Thread(target=do_flash, daemon=True).start()
    
    def refresh_data(self):
        # Update stats
        stats = get_stats()
        self.stat_gateways.config(text=str(stats['gateways']))
        self.stat_customers.config(text=str(stats['customers']))
        self.stat_devices.config(text=str(stats['devices']))
        self.stat_revenue.config(text=f"${stats['revenue']:.0f}")
        
        # Update gateways table
        for item in self.gateways_tree.get_children():
            self.gateways_tree.delete(item)
        
        for g in get_all_gateways():
            count = get_gateway_customer_count(g['gateway_id'])
            self.gateways_tree.insert('', 'end', values=(
                g['gateway_id'],
                g.get('owner_name', '-'),
                f"{count}/{g.get('max_customers', 9)}",
                g.get('max_customers', 9),
                g.get('status', 'active')
            ))
        
        # Update customers table
        for item in self.customers_tree.get_children():
            self.customers_tree.delete(item)
        
        for c in get_all_customers():
            self.customers_tree.insert('', 'end', values=(
                c['customer_id'],
                c.get('name', '-'),
                c.get('email', '-'),
                c.get('plan', 'option_a'),
                c.get('gateway_id', '-'),
                c.get('status', 'active')
            ))
    
    def add_gateway(self):
        messagebox.showinfo("Add Gateway", "Flash a device as GATEWAY using the Flash Device tab")
    
    def add_customer(self):
        messagebox.showinfo("Add Customer", "Customers are added when they sign up on the website")
    
    def auto_balance(self):
        messagebox.showinfo("Auto Balance", "Auto-balance will distribute customers evenly across gateways.\n\n(Requires active devices)")
    
    def gateway_context_menu(self, event):
        item = self.gateways_tree.identify_row(event.y)
        if item:
            self.gateways_tree.selection_set(item)
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Set Limit", command=lambda: self.set_limit(item))
            menu.post(event.x_root, event.y_root)
    
    def customer_context_menu(self, event):
        item = self.customers_tree.identify_row(event.y)
        if item:
            self.customers_tree.selection_set(item)
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Move to Another Gateway", command=lambda: self.move_customer_dialog(item))
            menu.post(event.x_root, event.y_root)
    
    def set_limit(self, item):
        values = self.gateways_tree.item(item)['values']
        gateway_id = values[0]
        current = values[3]
        
        from tkinter import simpledialog
        new_limit = simpledialog.askinteger("Set Limit", f"Set customer limit for {gateway_id}:",
                                            initialvalue=current, minvalue=1, maxvalue=20)
        if new_limit:
            set_gateway_limit(gateway_id, new_limit)
            self.refresh_data()
    
    def move_customer_dialog(self, item):
        values = self.customers_tree.item(item)['values']
        customer_id = values[0]
        
        gateways = get_all_gateways()
        if not gateways:
            messagebox.showerror("Error", "No gateways available")
            return
        
        from tkinter import simpledialog
        gateway_ids = [g['gateway_id'] for g in gateways]
        new_gw = simpledialog.askstring("Move Customer", 
                                        f"Move {customer_id} to which gateway?\n\nAvailable: {', '.join(gateway_ids)}")
        if new_gw and new_gw in gateway_ids:
            move_customer(customer_id, new_gw)
            self.refresh_data()
            messagebox.showinfo("Success", f"Moved {customer_id} to {new_gw}")
    
    def run(self):
        self.root.mainloop()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = AlkalineControlPanel()
    app.run()
