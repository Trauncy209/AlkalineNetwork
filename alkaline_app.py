#!/usr/bin/env python3
"""
Alkaline Network - Main Application
====================================

ONE APP TO RULE THEM ALL.

90s style GUI - no terminal needed.
Everything in one window with tabs.

Double-click to run. That's it.
"""

import os
import sys
import json
import time
import sqlite3
import threading
import webbrowser
import subprocess
from pathlib import Path
from datetime import datetime

# =============================================================================
# GUI SETUP
# =============================================================================

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
except ImportError:
    print("ERROR: tkinter not available")
    sys.exit(1)

# Try to import paramiko for SSH
try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "network_config.json"
DB_FILE = SCRIPT_DIR / "alkaline.db"

DEFAULT_CONFIG = {
    "mesh_id": "AlkalineMesh",
    "mesh_passphrase": "CHANGE_THIS_SECURE_PASSPHRASE_32_CHARS",
    "customer_wifi_password": "alkaline123",
    "admin_password": "admin",
    "gateway_count": 0,
    "pinger_count": 0,
    "devices": []
}

# =============================================================================
# MAIN APPLICATION
# =============================================================================

class AlkalineApp:
    """Main application window with tabs for everything."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Alkaline Network")
        self.root.geometry("900x700")
        self.root.configure(bg='#1a1a2e')
        
        # Try to set icon (won't crash if missing)
        try:
            self.root.iconbitmap('icon.ico')
        except:
            pass
        
        # Load config
        self.load_config()
        
        # Create database if needed
        self.init_database()
        
        # Build UI
        self.create_header()
        self.create_tabs()
        self.create_status_bar()
        
        # Auto-refresh
        self.refresh_data()
        self.auto_refresh()
    
    def load_config(self):
        """Load or create config file."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                self.config = json.load(f)
        else:
            self.config = DEFAULT_CONFIG.copy()
            self.save_config()
    
    def save_config(self):
        """Save config to file."""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def init_database(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(str(DB_FILE))
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS gateways (
            gateway_id TEXT PRIMARY KEY,
            public_key TEXT,
            owner_name TEXT,
            owner_email TEXT,
            owner_payment TEXT,
            location TEXT,
            status TEXT DEFAULT 'active',
            last_seen REAL,
            created_at REAL
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            pinger_id TEXT,
            gateway_id TEXT,
            public_key TEXT,
            name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            plan TEXT DEFAULT 'basic',
            status TEXT DEFAULT 'pending',
            subscription_status TEXT DEFAULT 'pending',
            last_seen REAL,
            created_at REAL
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS billing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT,
            amount REAL,
            type TEXT,
            status TEXT DEFAULT 'pending',
            stripe_id TEXT,
            description TEXT,
            created_at REAL
        )''')
        
        conn.commit()
        conn.close()
    
    # =========================================================================
    # HEADER
    # =========================================================================
    
    def create_header(self):
        """Create header with logo and stats."""
        header = tk.Frame(self.root, bg='#0f0f1a', height=80)
        header.pack(fill='x')
        header.pack_propagate(False)
        
        # Logo
        logo_frame = tk.Frame(header, bg='#0f0f1a')
        logo_frame.pack(side='left', padx=20, pady=15)
        
        tk.Label(
            logo_frame,
            text="⚡",
            font=('Arial', 32),
            fg='#00ff88',
            bg='#0f0f1a'
        ).pack(side='left')
        
        tk.Label(
            logo_frame,
            text="ALKALINE NETWORK",
            font=('Helvetica', 20, 'bold'),
            fg='white',
            bg='#0f0f1a'
        ).pack(side='left', padx=10)
        
        # Stats
        stats_frame = tk.Frame(header, bg='#0f0f1a')
        stats_frame.pack(side='right', padx=20)
        
        self.stats_label = tk.Label(
            stats_frame,
            text="Loading...",
            font=('Consolas', 11),
            fg='#888',
            bg='#0f0f1a'
        )
        self.stats_label.pack()
    
    # =========================================================================
    # TABS
    # =========================================================================
    
    def create_tabs(self):
        """Create tabbed interface."""
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background='#1a1a2e', borderwidth=0)
        style.configure('TNotebook.Tab', 
            background='#2a2a4e', 
            foreground='white',
            padding=[20, 10],
            font=('Helvetica', 11, 'bold')
        )
        style.map('TNotebook.Tab',
            background=[('selected', '#3b82f6')],
            foreground=[('selected', 'white')]
        )
        
        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Create tabs
        self.create_flash_tab()
        self.create_dashboard_tab()
        self.create_billing_tab()
        self.create_settings_tab()
    
    # =========================================================================
    # FLASH TAB
    # =========================================================================
    
    def create_flash_tab(self):
        """Flash devices tab."""
        tab = tk.Frame(self.notebook, bg='#1a1a2e')
        self.notebook.add(tab, text='📡 Flash Devices')
        
        # Instructions
        tk.Label(
            tab,
            text="FLASH A NEW DEVICE",
            font=('Helvetica', 16, 'bold'),
            fg='white',
            bg='#1a1a2e'
        ).pack(pady=15)
        
        tk.Label(
            tab,
            text="1. Plug Heltec into your computer via Ethernet\n"
                 "2. Wait for IP 192.168.4.1\n"
                 "3. Click GATEWAY or PINGER below",
            font=('Helvetica', 11),
            fg='#aaa',
            bg='#1a1a2e',
            justify='center'
        ).pack(pady=5)
        
        # Password field
        pw_frame = tk.Frame(tab, bg='#1a1a2e')
        pw_frame.pack(pady=15)
        
        tk.Label(
            pw_frame,
            text="Device Password:",
            font=('Helvetica', 11),
            fg='white',
            bg='#1a1a2e'
        ).pack(side='left', padx=5)
        
        self.device_password = tk.Entry(pw_frame, width=20, show='*')
        self.device_password.insert(0, 'heltec')  # Default Heltec password
        self.device_password.pack(side='left', padx=5)
        
        # Buttons
        btn_frame = tk.Frame(tab, bg='#1a1a2e')
        btn_frame.pack(pady=20)
        
        tk.Button(
            btn_frame,
            text="🌐 GATEWAY\n(Has Internet)",
            font=('Helvetica', 14, 'bold'),
            fg='white',
            bg='#0066cc',
            activebackground='#0088ff',
            width=20,
            height=3,
            command=lambda: self.flash_device('gateway')
        ).pack(side='left', padx=20)
        
        tk.Button(
            btn_frame,
            text="📡 PINGER\n(Customer Device)",
            font=('Helvetica', 14, 'bold'),
            fg='white',
            bg='#00aa44',
            activebackground='#00cc66',
            width=20,
            height=3,
            command=lambda: self.flash_device('pinger')
        ).pack(side='left', padx=20)
        
        # Log
        tk.Label(
            tab,
            text="Log:",
            font=('Helvetica', 10, 'bold'),
            fg='#888',
            bg='#1a1a2e',
            anchor='w'
        ).pack(fill='x', padx=20, pady=(20, 5))
        
        self.flash_log = scrolledtext.ScrolledText(
            tab,
            height=15,
            bg='#0a0a15',
            fg='#00ff88',
            font=('Consolas', 10),
            insertbackground='#00ff88'
        )
        self.flash_log.pack(fill='both', expand=True, padx=20, pady=(0, 20))
    
    def flash_device(self, mode: str):
        """Flash device as gateway or pinger."""
        if not PARAMIKO_AVAILABLE:
            messagebox.showerror("Missing Dependency", 
                "paramiko not installed.\n\nRun: pip install paramiko")
            return
        
        password = self.device_password.get()
        
        def do_flash():
            self.log_flash(f"\n{'='*50}")
            self.log_flash(f"FLASHING AS {mode.upper()}")
            self.log_flash(f"{'='*50}\n")
            
            try:
                # Connect
                self.log_flash("Connecting to 192.168.4.1...")
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect('192.168.4.1', username='root', password=password, timeout=10)
                self.log_flash("✓ Connected!\n")
                
                # Generate device ID
                device_num = self.config['gateway_count'] + self.config['pinger_count'] + 1
                prefix = 'GW' if mode == 'gateway' else 'PN'
                device_id = f"{prefix}-{device_num:03d}"
                
                self.log_flash(f"Device ID: {device_id}")
                
                # Create directories
                self.log_flash("Creating directories...")
                client.exec_command('mkdir -p /opt/alkaline /etc/alkaline')
                time.sleep(0.5)
                
                # Upload files via SFTP
                self.log_flash("Uploading files...")
                sftp = client.open_sftp()
                
                files = [
                    ('alkaline_device.py', '/opt/alkaline/alkaline_device.py'),
                    ('adaptive_bandwidth.py', '/opt/alkaline/adaptive_bandwidth.py'),
                    ('scripts/alkaline_boot.sh', '/opt/alkaline/alkaline_boot.sh'),
                ]
                
                for local, remote in files:
                    local_path = SCRIPT_DIR / local
                    if local_path.exists():
                        sftp.put(str(local_path), remote)
                        self.log_flash(f"  ✓ {local}")
                    else:
                        self.log_flash(f"  ✗ {local} not found!")
                
                # Create config
                config_data = {
                    "mode": mode,
                    "device_id": device_id,
                    "mesh_id": self.config['mesh_id'],
                    "max_customers": 12,
                    "version": "2.0"
                }
                
                import base64
                config_b64 = base64.b64encode(json.dumps(config_data, indent=2).encode()).decode()
                client.exec_command(f'echo "{config_b64}" | base64 -d > /etc/alkaline/config.json')
                self.log_flash("  ✓ config.json")
                
                # Make executable
                client.exec_command('chmod +x /opt/alkaline/*.sh /opt/alkaline/*.py')
                
                # Enable on boot
                self.log_flash("\nEnabling auto-start...")
                boot_cmd = f'/opt/alkaline/alkaline_boot.sh'
                client.exec_command(f'echo "{boot_cmd}" >> /etc/rc.local')
                
                sftp.close()
                
                # Update local config
                if mode == 'gateway':
                    self.config['gateway_count'] += 1
                else:
                    self.config['pinger_count'] += 1
                
                self.config['devices'].append({
                    'id': device_id,
                    'type': mode,
                    'provisioned': datetime.now().isoformat()
                })
                self.save_config()
                
                # Add to database
                self.add_device_to_db(device_id, mode)
                
                # Reboot
                self.log_flash("\nRebooting device...")
                client.exec_command('reboot')
                client.close()
                
                self.log_flash(f"\n{'='*50}")
                self.log_flash(f"✓ SUCCESS! {device_id} is ready!")
                self.log_flash(f"{'='*50}")
                self.log_flash(f"\nWiFi Name: Alkaline-{device_id}")
                self.log_flash(f"WiFi Password: {self.config['customer_wifi_password']}")
                self.log_flash(f"\nUnplug and deploy!")
                
                # Refresh stats
                self.root.after(100, self.refresh_data)
                
                messagebox.showinfo("Success!", 
                    f"Device {device_id} flashed successfully!\n\n"
                    f"WiFi: Alkaline-{device_id}\n"
                    f"Password: {self.config['customer_wifi_password']}\n\n"
                    f"Unplug and deploy!")
                
            except Exception as e:
                self.log_flash(f"\n✗ ERROR: {e}")
                messagebox.showerror("Flash Failed", str(e))
        
        threading.Thread(target=do_flash, daemon=True).start()
    
    def log_flash(self, msg: str):
        """Log to flash tab."""
        self.flash_log.configure(state='normal')
        self.flash_log.insert('end', msg + '\n')
        self.flash_log.see('end')
        self.flash_log.configure(state='disabled')
    
    def add_device_to_db(self, device_id: str, mode: str):
        """Add device to database."""
        conn = sqlite3.connect(str(DB_FILE))
        c = conn.cursor()
        
        if mode == 'gateway':
            c.execute('''INSERT OR REPLACE INTO gateways 
                (gateway_id, status, created_at) VALUES (?, ?, ?)''',
                (device_id, 'active', time.time()))
        else:
            c.execute('''INSERT OR REPLACE INTO customers 
                (customer_id, pinger_id, status, created_at) VALUES (?, ?, ?, ?)''',
                (f'manual_{device_id}', device_id, 'provisioned', time.time()))
        
        conn.commit()
        conn.close()
    
    # =========================================================================
    # DASHBOARD TAB
    # =========================================================================
    
    def create_dashboard_tab(self):
        """Dashboard tab showing gateways and customers."""
        tab = tk.Frame(self.notebook, bg='#1a1a2e')
        self.notebook.add(tab, text='📊 Dashboard')
        
        # Split into two panels
        left = tk.Frame(tab, bg='#1a1a2e')
        left.pack(side='left', fill='both', expand=True, padx=10, pady=10)
        
        right = tk.Frame(tab, bg='#1a1a2e')
        right.pack(side='right', fill='both', expand=True, padx=10, pady=10)
        
        # Gateways panel
        tk.Label(
            left,
            text="🌐 GATEWAYS",
            font=('Helvetica', 14, 'bold'),
            fg='#3b82f6',
            bg='#1a1a2e'
        ).pack(anchor='w', pady=(0, 10))
        
        self.gateway_list = tk.Listbox(
            left,
            bg='#0a0a15',
            fg='white',
            font=('Consolas', 11),
            selectbackground='#3b82f6',
            height=20
        )
        self.gateway_list.pack(fill='both', expand=True)
        
        # Customers panel
        tk.Label(
            right,
            text="👥 CUSTOMERS",
            font=('Helvetica', 14, 'bold'),
            fg='#00ff88',
            bg='#1a1a2e'
        ).pack(anchor='w', pady=(0, 10))
        
        self.customer_list = tk.Listbox(
            right,
            bg='#0a0a15',
            fg='white',
            font=('Consolas', 11),
            selectbackground='#00ff88',
            height=20
        )
        self.customer_list.pack(fill='both', expand=True)
        
        # Refresh button
        tk.Button(
            tab,
            text="🔄 Refresh",
            font=('Helvetica', 10),
            command=self.refresh_data
        ).pack(pady=10)
    
    # =========================================================================
    # BILLING TAB
    # =========================================================================
    
    def create_billing_tab(self):
        """Billing tab."""
        tab = tk.Frame(self.notebook, bg='#1a1a2e')
        self.notebook.add(tab, text='💰 Billing')
        
        # Summary
        summary_frame = tk.Frame(tab, bg='#2a2a4e', padx=20, pady=20)
        summary_frame.pack(fill='x', padx=20, pady=20)
        
        tk.Label(
            summary_frame,
            text="BILLING SUMMARY",
            font=('Helvetica', 14, 'bold'),
            fg='white',
            bg='#2a2a4e'
        ).pack(anchor='w')
        
        self.billing_summary = tk.Label(
            summary_frame,
            text="Loading...",
            font=('Consolas', 12),
            fg='#00ff88',
            bg='#2a2a4e',
            justify='left'
        )
        self.billing_summary.pack(anchor='w', pady=10)
        
        # Actions
        btn_frame = tk.Frame(tab, bg='#1a1a2e')
        btn_frame.pack(pady=20)
        
        tk.Button(
            btn_frame,
            text="💳 Run Monthly Billing",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#9333ea',
            width=25,
            height=2,
            command=self.run_billing
        ).pack(pady=10)
        
        tk.Button(
            btn_frame,
            text="📤 Pay Gateway Hosts",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#0891b2',
            width=25,
            height=2,
            command=self.pay_hosts
        ).pack(pady=10)
        
        # Pending invoices
        tk.Label(
            tab,
            text="PENDING INVOICES",
            font=('Helvetica', 12, 'bold'),
            fg='#888',
            bg='#1a1a2e'
        ).pack(anchor='w', padx=20, pady=(20, 5))
        
        self.invoice_list = tk.Listbox(
            tab,
            bg='#0a0a15',
            fg='white',
            font=('Consolas', 10),
            height=10
        )
        self.invoice_list.pack(fill='x', padx=20, pady=(0, 20))
    
    def run_billing(self):
        """Run monthly billing."""
        if messagebox.askyesno("Run Billing", 
            "This will charge all active customers via Stripe.\n\n"
            "Continue?"):
            try:
                result = subprocess.run(
                    [sys.executable, 'alkaline_billing.py', '--run-billing'],
                    capture_output=True, text=True, cwd=str(SCRIPT_DIR)
                )
                messagebox.showinfo("Billing Complete", result.stdout or "Billing completed!")
                self.refresh_data()
            except Exception as e:
                messagebox.showerror("Error", str(e))
    
    def pay_hosts(self):
        """Pay gateway hosts."""
        messagebox.showinfo("Coming Soon", 
            "Gateway host payouts will be available\n"
            "once you have active customers.")
    
    # =========================================================================
    # SETTINGS TAB
    # =========================================================================
    
    def create_settings_tab(self):
        """Settings tab."""
        tab = tk.Frame(self.notebook, bg='#1a1a2e')
        self.notebook.add(tab, text='⚙️ Settings')
        
        # Scrollable frame
        canvas = tk.Canvas(tab, bg='#1a1a2e', highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient='vertical', command=canvas.yview)
        scrollable = tk.Frame(canvas, bg='#1a1a2e')
        
        scrollable.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scrollable, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True, padx=20, pady=20)
        scrollbar.pack(side='right', fill='y')
        
        # Network settings
        tk.Label(
            scrollable,
            text="NETWORK SETTINGS",
            font=('Helvetica', 14, 'bold'),
            fg='#3b82f6',
            bg='#1a1a2e'
        ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 15))
        
        settings = [
            ("Mesh ID:", "mesh_id"),
            ("Mesh Passphrase:", "mesh_passphrase"),
            ("Customer WiFi Password:", "customer_wifi_password"),
            ("Admin Password:", "admin_password"),
        ]
        
        self.setting_vars = {}
        
        for i, (label, key) in enumerate(settings):
            tk.Label(
                scrollable,
                text=label,
                font=('Helvetica', 11),
                fg='white',
                bg='#1a1a2e'
            ).grid(row=i+1, column=0, sticky='w', pady=5)
            
            var = tk.StringVar(value=self.config.get(key, ''))
            self.setting_vars[key] = var
            
            entry = tk.Entry(scrollable, textvariable=var, width=40, font=('Consolas', 11))
            entry.grid(row=i+1, column=1, sticky='w', padx=10, pady=5)
        
        # Save button
        tk.Button(
            scrollable,
            text="💾 Save Settings",
            font=('Helvetica', 12, 'bold'),
            fg='white',
            bg='#22c55e',
            command=self.save_settings
        ).grid(row=len(settings)+1, column=0, columnspan=2, pady=20)
        
        # Stats
        tk.Label(
            scrollable,
            text="DEVICE STATS",
            font=('Helvetica', 14, 'bold'),
            fg='#3b82f6',
            bg='#1a1a2e'
        ).grid(row=len(settings)+2, column=0, columnspan=2, sticky='w', pady=(30, 15))
        
        self.device_stats = tk.Label(
            scrollable,
            text=f"Gateways: {self.config.get('gateway_count', 0)}\n"
                 f"Pingers: {self.config.get('pinger_count', 0)}",
            font=('Consolas', 12),
            fg='#888',
            bg='#1a1a2e',
            justify='left'
        )
        self.device_stats.grid(row=len(settings)+3, column=0, columnspan=2, sticky='w')
    
    def save_settings(self):
        """Save settings to config."""
        for key, var in self.setting_vars.items():
            self.config[key] = var.get()
        self.save_config()
        messagebox.showinfo("Saved", "Settings saved!")
    
    # =========================================================================
    # STATUS BAR
    # =========================================================================
    
    def create_status_bar(self):
        """Create status bar at bottom."""
        status = tk.Frame(self.root, bg='#0f0f1a', height=30)
        status.pack(fill='x', side='bottom')
        status.pack_propagate(False)
        
        self.status_label = tk.Label(
            status,
            text="Ready",
            font=('Helvetica', 9),
            fg='#666',
            bg='#0f0f1a'
        )
        self.status_label.pack(side='left', padx=10)
        
        # Version
        tk.Label(
            status,
            text="Alkaline Network v2.0",
            font=('Helvetica', 9),
            fg='#444',
            bg='#0f0f1a'
        ).pack(side='right', padx=10)
    
    # =========================================================================
    # DATA REFRESH
    # =========================================================================
    
    def refresh_data(self):
        """Refresh all data from database."""
        try:
            conn = sqlite3.connect(str(DB_FILE))
            c = conn.cursor()
            
            # Get gateways
            c.execute('SELECT gateway_id, status FROM gateways')
            gateways = c.fetchall()
            
            # Get customers
            c.execute('SELECT customer_id, pinger_id, status FROM customers')
            customers = c.fetchall()
            
            # Get billing
            c.execute('SELECT * FROM billing WHERE status = "pending"')
            invoices = c.fetchall()
            
            conn.close()
            
            # Update gateway list
            self.gateway_list.delete(0, 'end')
            for gw in gateways:
                status_icon = "🟢" if gw[1] == 'active' else "🔴"
                self.gateway_list.insert('end', f"  {status_icon} {gw[0]}")
            
            if not gateways:
                self.gateway_list.insert('end', "  No gateways yet")
            
            # Update customer list
            self.customer_list.delete(0, 'end')
            for cust in customers:
                status_icon = "🟢" if cust[2] == 'active' else "⚪"
                self.customer_list.insert('end', f"  {status_icon} {cust[0]} ({cust[1]})")
            
            if not customers:
                self.customer_list.insert('end', "  No customers yet")
            
            # Update stats
            active_customers = len([c for c in customers if c[2] == 'active'])
            revenue = active_customers * 7.99
            
            self.stats_label.configure(
                text=f"Gateways: {len(gateways)}  |  Customers: {len(customers)}  |  "
                     f"Active: {active_customers}  |  Revenue: ${revenue:.2f}/mo"
            )
            
            # Update billing summary
            self.billing_summary.configure(
                text=f"Active Customers: {active_customers}\n"
                     f"Monthly Revenue: ${revenue:.2f}\n"
                     f"Gateway Payouts: ${active_customers * 2:.2f}\n"
                     f"Net Revenue: ${revenue - (active_customers * 2):.2f}"
            )
            
            # Update invoice list
            self.invoice_list.delete(0, 'end')
            for inv in invoices:
                self.invoice_list.insert('end', f"  ${inv[2]:.2f} - {inv[6]}")
            
            if not invoices:
                self.invoice_list.insert('end', "  No pending invoices")
            
            # Update device stats in settings
            self.device_stats.configure(
                text=f"Gateways: {self.config.get('gateway_count', 0)}\n"
                     f"Pingers: {self.config.get('pinger_count', 0)}\n"
                     f"Total Devices: {len(self.config.get('devices', []))}"
            )
            
            self.status_label.configure(text=f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
            
        except Exception as e:
            self.status_label.configure(text=f"Error: {e}")
    
    def auto_refresh(self):
        """Auto-refresh every 30 seconds."""
        self.refresh_data()
        self.root.after(30000, self.auto_refresh)
    
    # =========================================================================
    # RUN
    # =========================================================================
    
    def run(self):
        """Run the application."""
        self.root.mainloop()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    # Install dependencies if needed
    try:
        import paramiko
    except ImportError:
        print("Installing paramiko...")
        os.system(f'{sys.executable} -m pip install paramiko')
    
    app = AlkalineApp()
    app.run()
