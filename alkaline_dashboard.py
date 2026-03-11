#!/usr/bin/env python3
"""
Alkaline Network - Dashboard
============================

Web-based dashboard for monitoring and managing the Alkaline Network.

Features:
  - Real-time network status
  - Connected customers list
  - Gateway health monitoring  
  - Bandwidth usage stats
  - Customer cap management
  - Billing integration

Runs on:
  - Your server (full dashboard)
  - Gateway devices (local status only)

Usage:
  python alkaline_dashboard.py --port 8080

Then open http://localhost:8080 in your browser.

Author: AlkalineTech
License: MIT
"""

import os
import sys
import json
import time
import asyncio
import logging
import argparse
import threading
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import socket

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("alkaline.dashboard")

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path("/var/lib/alkaline")
DB_PATH = DATA_DIR / "alkaline.db"

# Pricing
MONTHLY_PRICE_OPTION_A = 7.99   # With deposit
MONTHLY_PRICE_OPTION_B = 14.99  # No deposit
GATEWAY_PAYOUT_PER_CUSTOMER = 2.00

# =============================================================================
# DATABASE
# =============================================================================

class Database:
    """SQLite database for persistent storage."""
    
    def __init__(self, path: str = None):
        self.path = path or str(DB_PATH)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        
        # Gateways table
        c.execute('''
            CREATE TABLE IF NOT EXISTS gateways (
                gateway_id TEXT PRIMARY KEY,
                public_key TEXT,
                owner_name TEXT,
                owner_email TEXT,
                owner_payment TEXT,
                ip_address TEXT,
                max_customers INTEGER DEFAULT 9,
                created_at REAL,
                last_seen REAL,
                total_bytes_up INTEGER DEFAULT 0,
                total_bytes_down INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        ''')
        
        # Customers table
        c.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                pinger_id TEXT,
                public_key TEXT,
                name TEXT,
                email TEXT,
                phone TEXT,
                address TEXT,
                plan TEXT DEFAULT 'option_a',
                deposit_paid REAL DEFAULT 0,
                gateway_id TEXT,
                tunnel_ip TEXT,
                created_at REAL,
                last_seen REAL,
                bytes_up INTEGER DEFAULT 0,
                bytes_down INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (gateway_id) REFERENCES gateways(gateway_id)
            )
        ''')
        
        # Billing table
        c.execute('''
            CREATE TABLE IF NOT EXISTS billing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT,
                gateway_id TEXT,
                amount REAL,
                type TEXT,
                description TEXT,
                created_at REAL,
                paid_at REAL,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        # Usage stats table (hourly)
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT,
                entity_id TEXT,
                hour TEXT,
                bytes_up INTEGER DEFAULT 0,
                bytes_down INTEGER DEFAULT 0,
                UNIQUE(entity_type, entity_id, hour)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def execute(self, query: str, params: tuple = ()) -> list:
        """Execute a query and return results."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, params)
        results = c.fetchall()
        conn.commit()
        conn.close()
        return [dict(row) for row in results]
    
    def execute_one(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Execute a query and return one result."""
        results = self.execute(query, params)
        return results[0] if results else None
    
    # Gateway methods
    def add_gateway(self, gateway_id: str, public_key: str, owner_name: str,
                    owner_email: str, owner_payment: str, max_customers: int = 9) -> bool:
        """Add a new gateway."""
        try:
            self.execute('''
                INSERT INTO gateways (gateway_id, public_key, owner_name, owner_email,
                                     owner_payment, max_customers, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (gateway_id, public_key, owner_name, owner_email, owner_payment,
                  max_customers, time.time(), time.time()))
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_gateway(self, gateway_id: str) -> Optional[dict]:
        """Get gateway by ID."""
        return self.execute_one(
            'SELECT * FROM gateways WHERE gateway_id = ?', (gateway_id,)
        )
    
    def get_all_gateways(self) -> List[dict]:
        """Get all gateways."""
        return self.execute('SELECT * FROM gateways ORDER BY created_at DESC')
    
    def update_gateway_seen(self, gateway_id: str, ip_address: str = None):
        """Update gateway last seen time."""
        if ip_address:
            self.execute(
                'UPDATE gateways SET last_seen = ?, ip_address = ? WHERE gateway_id = ?',
                (time.time(), ip_address, gateway_id)
            )
        else:
            self.execute(
                'UPDATE gateways SET last_seen = ? WHERE gateway_id = ?',
                (time.time(), gateway_id)
            )
    
    def get_gateway_customer_count(self, gateway_id: str) -> int:
        """Get number of active customers on a gateway."""
        result = self.execute_one(
            'SELECT COUNT(*) as count FROM customers WHERE gateway_id = ? AND status = ?',
            (gateway_id, 'active')
        )
        return result['count'] if result else 0
    
    # Customer methods
    def add_customer(self, customer_id: str, name: str, email: str, phone: str,
                     address: str, plan: str, pinger_id: str = None,
                     public_key: str = None) -> bool:
        """Add a new customer."""
        try:
            # Assign tunnel IP
            count = self.execute_one('SELECT COUNT(*) as c FROM customers')['c']
            tunnel_ip = f"10.100.0.{count + 2}"  # .1 is server
            
            self.execute('''
                INSERT INTO customers (customer_id, pinger_id, public_key, name, email,
                                       phone, address, plan, tunnel_ip, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (customer_id, pinger_id, public_key, name, email, phone, address,
                  plan, tunnel_ip, time.time(), time.time()))
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_customer(self, customer_id: str) -> Optional[dict]:
        """Get customer by ID."""
        return self.execute_one(
            'SELECT * FROM customers WHERE customer_id = ?', (customer_id,)
        )
    
    def get_all_customers(self) -> List[dict]:
        """Get all customers."""
        return self.execute('SELECT * FROM customers ORDER BY created_at DESC')
    
    def get_active_customers(self) -> List[dict]:
        """Get active customers."""
        return self.execute(
            'SELECT * FROM customers WHERE status = ? ORDER BY created_at DESC',
            ('active',)
        )
    
    def assign_customer_to_gateway(self, customer_id: str, gateway_id: str) -> bool:
        """Assign a customer to a gateway."""
        gateway = self.get_gateway(gateway_id)
        if not gateway:
            return False
        
        current_count = self.get_gateway_customer_count(gateway_id)
        if current_count >= gateway['max_customers']:
            return False  # At capacity
        
        self.execute(
            'UPDATE customers SET gateway_id = ?, last_seen = ? WHERE customer_id = ?',
            (gateway_id, time.time(), customer_id)
        )
        return True
    
    def update_customer_seen(self, customer_id: str):
        """Update customer last seen time."""
        self.execute(
            'UPDATE customers SET last_seen = ? WHERE customer_id = ?',
            (time.time(), customer_id)
        )
    
    def update_customer_usage(self, customer_id: str, bytes_up: int, bytes_down: int):
        """Update customer bandwidth usage."""
        self.execute('''
            UPDATE customers 
            SET bytes_up = bytes_up + ?, bytes_down = bytes_down + ?, last_seen = ?
            WHERE customer_id = ?
        ''', (bytes_up, bytes_down, time.time(), customer_id))
    
    # Billing methods
    def create_invoice(self, customer_id: str, amount: float, 
                       inv_type: str, description: str) -> int:
        """Create a billing invoice."""
        self.execute('''
            INSERT INTO billing (customer_id, amount, type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (customer_id, amount, inv_type, description, time.time()))
        
        result = self.execute_one('SELECT last_insert_rowid() as id')
        return result['id']
    
    def get_pending_invoices(self) -> List[dict]:
        """Get all pending invoices."""
        return self.execute(
            'SELECT * FROM billing WHERE status = ? ORDER BY created_at DESC',
            ('pending',)
        )
    
    def mark_invoice_paid(self, invoice_id: int):
        """Mark an invoice as paid."""
        self.execute(
            'UPDATE billing SET status = ?, paid_at = ? WHERE id = ?',
            ('paid', time.time(), invoice_id)
        )
    
    # Stats methods
    def record_usage(self, entity_type: str, entity_id: str, 
                     bytes_up: int, bytes_down: int):
        """Record hourly usage stats."""
        hour = datetime.now().strftime('%Y-%m-%d-%H')
        
        self.execute('''
            INSERT INTO usage_stats (entity_type, entity_id, hour, bytes_up, bytes_down)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id, hour) DO UPDATE SET
                bytes_up = bytes_up + excluded.bytes_up,
                bytes_down = bytes_down + excluded.bytes_down
        ''', (entity_type, entity_id, hour, bytes_up, bytes_down))
    
    def get_usage_stats(self, entity_type: str, entity_id: str, 
                        hours: int = 24) -> List[dict]:
        """Get usage stats for past N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d-%H')
        return self.execute('''
            SELECT * FROM usage_stats 
            WHERE entity_type = ? AND entity_id = ? AND hour >= ?
            ORDER BY hour ASC
        ''', (entity_type, entity_id, cutoff))
    
    def move_customer(self, customer_id: str, new_gateway_id: str) -> bool:
        """Move a customer to a different gateway."""
        try:
            self.execute(
                'UPDATE customers SET gateway_id = ?, last_seen = ? WHERE customer_id = ?',
                (new_gateway_id, time.time(), customer_id)
            )
            logger.info(f"Moved customer {customer_id} to gateway {new_gateway_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to move customer: {e}")
            return False
    
    def update_gateway_limit(self, gateway_id: str, max_customers: int) -> bool:
        """Update gateway customer limit. Auto-moves overflow customers."""
        try:
            current_count = self.get_gateway_customer_count(gateway_id)
            
            # If new limit is lower than current customers, move overflow
            if max_customers < current_count:
                overflow = current_count - max_customers
                logger.info(f"Gateway {gateway_id} limit {max_customers} < {current_count} customers, moving {overflow}")
                
                # Get customers to move (most recent first)
                customers = self.execute(
                    '''SELECT customer_id FROM customers 
                       WHERE gateway_id = ? AND status = ? 
                       ORDER BY created_at DESC LIMIT ?''',
                    (gateway_id, 'active', overflow)
                )
                
                for c in customers:
                    new_gw = self.get_best_gateway_for_new_customer(exclude=gateway_id)
                    if new_gw:
                        self.move_customer(c['customer_id'], new_gw)
                        logger.info(f"Auto-moved {c['customer_id']} to {new_gw}")
                    else:
                        logger.warning(f"No available gateway for {c['customer_id']}")
            
            self.execute(
                'UPDATE gateways SET max_customers = ? WHERE gateway_id = ?',
                (max_customers, gateway_id)
            )
            logger.info(f"Updated gateway {gateway_id} limit to {max_customers}")
            return True
        except Exception as e:
            logger.error(f"Failed to update limit: {e}")
            return False
    
    def handle_gateway_offline(self, gateway_id: str) -> dict:
        """Handle gateway going offline - reassign all customers."""
        customers = self.execute(
            'SELECT customer_id FROM customers WHERE gateway_id = ? AND status = ?',
            (gateway_id, 'active')
        )
        
        moved = 0
        failed = 0
        
        for c in customers:
            new_gw = self.get_best_gateway_for_new_customer(exclude=gateway_id)
            if new_gw:
                self.move_customer(c['customer_id'], new_gw)
                moved += 1
            else:
                failed += 1
        
        # Mark gateway as offline
        self.execute(
            'UPDATE gateways SET status = ? WHERE gateway_id = ?',
            ('offline', gateway_id)
        )
        
        logger.info(f"Gateway {gateway_id} offline: moved {moved}, failed {failed}")
        return {'moved': moved, 'failed': failed}
    
    def auto_balance_customers(self) -> dict:
        """
        Auto-balance customers across gateways.
        Moves customers from overloaded gateways to underloaded ones.
        """
        gateways = self.get_all_gateways()
        if len(gateways) < 2:
            return {'moved': 0, 'message': 'Need at least 2 gateways to balance'}
        
        # Calculate load for each gateway
        gateway_loads = []
        for g in gateways:
            count = self.get_gateway_customer_count(g['gateway_id'])
            max_c = g['max_customers']
            gateway_loads.append({
                'gateway_id': g['gateway_id'],
                'count': count,
                'max': max_c,
                'available': max_c - count,
                'load_pct': (count / max_c * 100) if max_c > 0 else 100
            })
        
        # Sort by load percentage
        gateway_loads.sort(key=lambda x: x['load_pct'], reverse=True)
        
        moved = 0
        moves = []
        
        # Find overloaded gateways (>80% or at capacity)
        for source in gateway_loads:
            if source['load_pct'] < 80 or source['count'] <= 1:
                continue
            
            # Find underloaded gateways (<50%)
            for target in reversed(gateway_loads):
                if target['gateway_id'] == source['gateway_id']:
                    continue
                if target['load_pct'] >= 60 or target['available'] <= 0:
                    continue
                
                # Get customers on source gateway
                customers = self.execute(
                    'SELECT customer_id FROM customers WHERE gateway_id = ? AND status = ? LIMIT 1',
                    (source['gateway_id'], 'active')
                )
                
                if customers:
                    customer_id = customers[0]['customer_id']
                    if self.move_customer(customer_id, target['gateway_id']):
                        moved += 1
                        moves.append({
                            'customer': customer_id,
                            'from': source['gateway_id'],
                            'to': target['gateway_id']
                        })
                        source['count'] -= 1
                        source['available'] += 1
                        target['count'] += 1
                        target['available'] -= 1
                        
                        # Recalculate loads
                        source['load_pct'] = (source['count'] / source['max'] * 100)
                        target['load_pct'] = (target['count'] / target['max'] * 100)
                        
                        # Only move one at a time per source
                        break
        
        return {
            'moved': moved,
            'moves': moves,
            'message': f'Moved {moved} customer(s) to balance load'
        }
    
    def get_best_gateway_for_new_customer(self, exclude: str = None) -> Optional[str]:
        """
        Get the best gateway for a new customer.
        Returns gateway with most available capacity and best load distribution.
        """
        gateways = self.get_all_gateways()
        
        best_gateway = None
        best_score = -1
        
        for g in gateways:
            # Skip excluded gateway
            if g['gateway_id'] == exclude:
                continue
            
            # Skip offline gateways
            if g.get('status') == 'offline':
                continue
                
            count = self.get_gateway_customer_count(g['gateway_id'])
            max_c = g['max_customers']
            available = max_c - count
            
            if available <= 0:
                continue
            
            # Score based on available slots and load percentage
            # Prefer gateways with more room
            score = available * 10 + (100 - (count / max_c * 100))
            
            if score > best_score:
                best_score = score
                best_gateway = g['gateway_id']
        
        return best_gateway


# =============================================================================
# DASHBOARD HTML
# =============================================================================

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alkaline Network - Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a; 
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #0f0f1a 100%);
            padding: 20px 40px;
            border-bottom: 1px solid #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo { font-size: 24px; font-weight: bold; color: #00ff88; }
        .nav a { color: #888; text-decoration: none; margin-left: 30px; }
        .nav a:hover { color: #00ff88; }
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: #1a1a2e;
            border-radius: 12px;
            padding: 24px;
            border: 1px solid #333;
        }
        .stat-card h3 { color: #888; font-size: 14px; margin-bottom: 8px; }
        .stat-card .value { font-size: 36px; font-weight: bold; color: #00ff88; }
        .stat-card .subtitle { color: #666; font-size: 12px; margin-top: 8px; }
        
        .section { margin-bottom: 30px; }
        .section h2 { 
            font-size: 20px; 
            margin-bottom: 20px; 
            padding-bottom: 10px;
            border-bottom: 1px solid #333;
        }
        
        table { width: 100%; border-collapse: collapse; }
        th, td { 
            padding: 12px 16px; 
            text-align: left; 
            border-bottom: 1px solid #222;
        }
        th { color: #888; font-weight: 500; font-size: 12px; text-transform: uppercase; }
        tr:hover { background: #1a1a2e; }
        
        .status { 
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }
        .status.online { background: #0a3d2a; color: #00ff88; }
        .status.offline { background: #3d0a0a; color: #ff4444; }
        .status.warning { background: #3d3d0a; color: #ffaa00; }
        
        .btn {
            background: #00ff88;
            color: #000;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
        }
        .btn:hover { background: #00cc6a; }
        .btn.secondary { background: #333; color: #fff; }
        .btn.secondary:hover { background: #444; }
        
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.8);
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: #1a1a2e;
            padding: 30px;
            border-radius: 12px;
            max-width: 500px;
            width: 90%;
        }
        .modal h3 { margin-bottom: 20px; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; color: #888; font-size: 14px; }
        .form-group input, .form-group select {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid #333;
            border-radius: 6px;
            background: #0a0a0a;
            color: #fff;
            font-size: 14px;
        }
        .form-group input:focus { outline: none; border-color: #00ff88; }
        
        .refresh-indicator {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #1a1a2e;
            padding: 10px 20px;
            border-radius: 20px;
            font-size: 12px;
            color: #888;
            border: 1px solid #333;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">Alkaline Network</div>
        <div class="nav">
            <a href="#" onclick="showSection('overview')">Overview</a>
            <a href="#" onclick="showSection('gateways')">Gateways</a>
            <a href="#" onclick="showSection('customers')">Customers</a>
            <a href="#" onclick="showSection('billing')">Billing</a>
        </div>
    </div>
    
    <div class="container">
        <!-- Overview Section -->
        <div id="section-overview" class="section">
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>Active Gateways</h3>
                    <div class="value" id="stat-gateways">-</div>
                    <div class="subtitle">Online now</div>
                </div>
                <div class="stat-card">
                    <h3>Active Customers</h3>
                    <div class="value" id="stat-customers">-</div>
                    <div class="subtitle">Connected</div>
                </div>
                <div class="stat-card">
                    <h3>Monthly Revenue</h3>
                    <div class="value" id="stat-revenue">$-</div>
                    <div class="subtitle">This month</div>
                </div>
                <div class="stat-card">
                    <h3>Bandwidth Today</h3>
                    <div class="value" id="stat-bandwidth">-</div>
                    <div class="subtitle">Total transferred</div>
                </div>
            </div>
            
            <h2>Recent Activity</h2>
            <table id="activity-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Type</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- Gateways Section -->
        <div id="section-gateways" class="section" style="display:none">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h2 style="border:none; margin:0; padding:0;">Gateways</h2>
                <div>
                    <button class="btn secondary" onclick="autoBalance()">⚖️ Auto-Balance</button>
                    <button class="btn" onclick="showAddGateway()">+ Add Gateway</button>
                </div>
            </div>
            <table id="gateways-table">
                <thead>
                    <tr>
                        <th>Gateway ID</th>
                        <th>Owner</th>
                        <th>Customers</th>
                        <th>Bandwidth</th>
                        <th>Status</th>
                        <th>Last Seen</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- Customers Section -->
        <div id="section-customers" class="section" style="display:none">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h2 style="border:none; margin:0; padding:0;">Customers</h2>
                <button class="btn" onclick="showAddCustomer()">+ Add Customer</button>
            </div>
            <table id="customers-table">
                <thead>
                    <tr>
                        <th>Customer</th>
                        <th>Plan</th>
                        <th>Gateway</th>
                        <th>Tunnel IP</th>
                        <th>Bandwidth</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- Billing Section -->
        <div id="section-billing" class="section" style="display:none">
            <h2>Pending Payments</h2>
            <table id="billing-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Customer</th>
                        <th>Amount</th>
                        <th>Description</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
            
            <h2 style="margin-top:40px;">Gateway Payouts</h2>
            <table id="payouts-table">
                <thead>
                    <tr>
                        <th>Gateway</th>
                        <th>Owner</th>
                        <th>Customers</th>
                        <th>Amount Owed</th>
                        <th>Payment Method</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
    
    <!-- Add Gateway Modal -->
    <div id="modal-add-gateway" class="modal">
        <div class="modal-content">
            <h3>Add New Gateway</h3>
            <form id="form-add-gateway" onsubmit="submitAddGateway(event)">
                <div class="form-group">
                    <label>Gateway ID</label>
                    <input type="text" name="gateway_id" placeholder="GW-XXXXXX" required>
                </div>
                <div class="form-group">
                    <label>Owner Name</label>
                    <input type="text" name="owner_name" required>
                </div>
                <div class="form-group">
                    <label>Owner Email</label>
                    <input type="email" name="owner_email" required>
                </div>
                <div class="form-group">
                    <label>Payment Method (PayPal/Venmo email or description)</label>
                    <input type="text" name="owner_payment" required>
                </div>
                <div class="form-group">
                    <label>Max Customers</label>
                    <input type="number" name="max_customers" value="9" min="1" max="15">
                </div>
                <div style="display:flex; gap:10px; margin-top:20px;">
                    <button type="submit" class="btn">Add Gateway</button>
                    <button type="button" class="btn secondary" onclick="closeModal('modal-add-gateway')">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <!-- Add Customer Modal -->
    <div id="modal-add-customer" class="modal">
        <div class="modal-content">
            <h3>Add New Customer</h3>
            <form id="form-add-customer" onsubmit="submitAddCustomer(event)">
                <div class="form-group">
                    <label>Name</label>
                    <input type="text" name="name" required>
                </div>
                <div class="form-group">
                    <label>Email</label>
                    <input type="email" name="email" required>
                </div>
                <div class="form-group">
                    <label>Phone</label>
                    <input type="tel" name="phone">
                </div>
                <div class="form-group">
                    <label>Address</label>
                    <input type="text" name="address">
                </div>
                <div class="form-group">
                    <label>Plan</label>
                    <select name="plan">
                        <option value="option_a">Option A - $7.99/mo + $100 deposit</option>
                        <option value="option_b">Option B - $14.99/mo, no deposit</option>
                    </select>
                </div>
                <div style="display:flex; gap:10px; margin-top:20px;">
                    <button type="submit" class="btn">Add Customer</button>
                    <button type="button" class="btn secondary" onclick="closeModal('modal-add-customer')">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <div class="refresh-indicator">
        Auto-refreshing every 10s
    </div>
    
    <script>
        // State
        let currentSection = 'overview';
        
        // Navigation
        function showSection(section) {
            document.querySelectorAll('.section').forEach(s => s.style.display = 'none');
            document.getElementById('section-' + section).style.display = 'block';
            currentSection = section;
            refreshData();
        }
        
        // Modals
        function showAddGateway() {
            document.getElementById('modal-add-gateway').classList.add('active');
        }
        function showAddCustomer() {
            document.getElementById('modal-add-customer').classList.add('active');
        }
        function closeModal(id) {
            document.getElementById(id).classList.remove('active');
        }
        
        // API calls
        async function api(endpoint, method = 'GET', data = null) {
            const options = { method, headers: { 'Content-Type': 'application/json' } };
            if (data) options.body = JSON.stringify(data);
            const resp = await fetch('/api/' + endpoint, options);
            return resp.json();
        }
        
        // Format helpers
        function formatBytes(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
            if (bytes < 1024*1024*1024) return (bytes/1024/1024).toFixed(1) + ' MB';
            return (bytes/1024/1024/1024).toFixed(2) + ' GB';
        }
        
        function formatTime(timestamp) {
            if (!timestamp) return 'Never';
            const d = new Date(timestamp * 1000);
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff/60) + 'm ago';
            if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
            return d.toLocaleDateString();
        }
        
        function getStatus(lastSeen) {
            if (!lastSeen) return '<span class="status offline">Offline</span>';
            const diff = (Date.now()/1000) - lastSeen;
            if (diff < 60) return '<span class="status online">Online</span>';
            if (diff < 300) return '<span class="status warning">Idle</span>';
            return '<span class="status offline">Offline</span>';
        }
        
        // Refresh data
        async function refreshData() {
            try {
                const data = await api('stats');
                
                // Update stats
                document.getElementById('stat-gateways').textContent = data.active_gateways || 0;
                document.getElementById('stat-customers').textContent = data.active_customers || 0;
                document.getElementById('stat-revenue').textContent = '$' + (data.monthly_revenue || 0).toFixed(2);
                document.getElementById('stat-bandwidth').textContent = formatBytes(data.bandwidth_today || 0);
                
                // Update tables based on current section
                if (currentSection === 'gateways') {
                    const gateways = await api('gateways');
                    updateGatewaysTable(gateways);
                } else if (currentSection === 'customers') {
                    const customers = await api('customers');
                    updateCustomersTable(customers);
                } else if (currentSection === 'billing') {
                    const billing = await api('billing');
                    updateBillingTable(billing);
                }
            } catch (e) {
                console.error('Refresh error:', e);
            }
        }
        
        function updateGatewaysTable(gateways) {
            const tbody = document.querySelector('#gateways-table tbody');
            tbody.innerHTML = gateways.map(g => `
                <tr>
                    <td><strong>${g.gateway_id}</strong></td>
                    <td>${g.owner_name}<br><small style="color:#666">${g.owner_email}</small></td>
                    <td>${g.customer_count || 0} / ${g.max_customers}</td>
                    <td>${formatBytes((g.total_bytes_up || 0) + (g.total_bytes_down || 0))}</td>
                    <td>${getStatus(g.last_seen)}</td>
                    <td>${formatTime(g.last_seen)}</td>
                    <td>
                        <button class="btn secondary" onclick="setGatewayLimit('${g.gateway_id}')">Limit</button>
                        <button class="btn secondary" onclick="editGateway('${g.gateway_id}')">Edit</button>
                    </td>
                </tr>
            `).join('');
        }
        
        function updateCustomersTable(customers) {
            const tbody = document.querySelector('#customers-table tbody');
            tbody.innerHTML = customers.map(c => `
                <tr>
                    <td><strong>${c.name}</strong><br><small style="color:#666">${c.email}</small></td>
                    <td>${c.plan === 'option_a' ? '$7.99/mo' : '$14.99/mo'}</td>
                    <td>${c.gateway_id || 'Not assigned'}</td>
                    <td><code>${c.tunnel_ip || '-'}</code></td>
                    <td>${formatBytes((c.bytes_up || 0) + (c.bytes_down || 0))}</td>
                    <td>${getStatus(c.last_seen)}</td>
                    <td>
                        <button class="btn secondary" onclick="moveCustomer('${c.customer_id}', '${c.gateway_id || ''}')">Move</button>
                        <button class="btn secondary" onclick="editCustomer('${c.customer_id}')">Edit</button>
                    </td>
                </tr>
            `).join('');
        }
        
        function updateBillingTable(billing) {
            const tbody = document.querySelector('#billing-table tbody');
            tbody.innerHTML = (billing.invoices || []).map(b => `
                <tr>
                    <td>${formatTime(b.created_at)}</td>
                    <td>${b.customer_id}</td>
                    <td>$${b.amount.toFixed(2)}</td>
                    <td>${b.description}</td>
                    <td><span class="status ${b.status === 'paid' ? 'online' : 'warning'}">${b.status}</span></td>
                    <td>${b.status === 'pending' ? 
                        `<button class="btn" onclick="markPaid(${b.id})">Mark Paid</button>` : ''}</td>
                </tr>
            `).join('');
            
            const payouts = document.querySelector('#payouts-table tbody');
            payouts.innerHTML = (billing.payouts || []).map(p => `
                <tr>
                    <td>${p.gateway_id}</td>
                    <td>${p.owner_name}</td>
                    <td>${p.customer_count}</td>
                    <td>$${(p.customer_count * 2).toFixed(2)}</td>
                    <td>${p.owner_payment}</td>
                    <td><button class="btn secondary">Send Payment</button></td>
                </tr>
            `).join('');
        }
        
        // Form submissions
        async function submitAddGateway(e) {
            e.preventDefault();
            const form = e.target;
            const data = Object.fromEntries(new FormData(form));
            await api('gateways', 'POST', data);
            closeModal('modal-add-gateway');
            form.reset();
            refreshData();
        }
        
        async function submitAddCustomer(e) {
            e.preventDefault();
            const form = e.target;
            const data = Object.fromEntries(new FormData(form));
            data.customer_id = 'CUST-' + Math.random().toString(36).substr(2, 8).toUpperCase();
            await api('customers', 'POST', data);
            closeModal('modal-add-customer');
            form.reset();
            refreshData();
        }
        
        async function markPaid(id) {
            await api('billing/' + id + '/paid', 'POST');
            refreshData();
        }
        
        async function moveCustomer(customerId, currentGateway) {
            // Get available gateways
            const gateways = await api('gateways');
            const available = gateways.filter(g => 
                g.gateway_id !== currentGateway && 
                (g.customer_count || 0) < g.max_customers
            );
            
            if (available.length === 0) {
                alert('No other gateways with available capacity');
                return;
            }
            
            const options = available.map(g => 
                `${g.gateway_id} (${g.customer_count || 0}/${g.max_customers} customers)`
            ).join('\\n');
            
            const choice = prompt(
                `Move customer ${customerId} to which gateway?\\n\\nAvailable:\\n${options}\\n\\nEnter gateway ID:`
            );
            
            if (choice && available.some(g => g.gateway_id === choice)) {
                const result = await api('customers/move', 'POST', {
                    customer_id: customerId,
                    gateway_id: choice
                });
                
                if (result.success) {
                    alert('Customer moved successfully');
                    refreshData();
                } else {
                    alert('Failed to move: ' + (result.error || 'Unknown error'));
                }
            }
        }
        
        async function autoBalance() {
            if (!confirm('Auto-balance will move customers from overloaded gateways to underloaded ones. Continue?')) {
                return;
            }
            
            const result = await api('gateways/balance', 'POST');
            alert(result.message || 'Balance complete');
            refreshData();
        }
        
        async function setGatewayLimit(gatewayId) {
            const newLimit = prompt('Set customer limit (1-20):', '9');
            if (newLimit && !isNaN(newLimit)) {
                const result = await api('gateways/set-limit', 'POST', {
                    gateway_id: gatewayId,
                    max_customers: parseInt(newLimit)
                });
                if (result.success) {
                    refreshData();
                } else {
                    alert('Failed: ' + (result.error || 'Unknown error'));
                }
            }
        }
        
        // Initial load and auto-refresh
        refreshData();
        setInterval(refreshData, 10000);
    </script>
</body>
</html>
'''


# =============================================================================
# HTTP SERVER
# =============================================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for dashboard."""
    
    db = None  # Set by server
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass
    
    def send_json(self, data: dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Dashboard HTML
        if path == '/' or path == '/dashboard':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
            return
        
        # API endpoints
        if path == '/api/stats':
            gateways = self.db.get_all_gateways()
            customers = self.db.get_active_customers()
            
            # Calculate stats
            now = time.time()
            active_gateways = sum(1 for g in gateways if now - (g.get('last_seen') or 0) < 300)
            active_customers = len(customers)
            
            # Revenue calculation
            revenue = sum(
                7.99 if c['plan'] == 'option_a' else 14.99
                for c in customers
            )
            
            # Bandwidth
            bandwidth = sum(
                (c.get('bytes_up') or 0) + (c.get('bytes_down') or 0)
                for c in customers
            )
            
            self.send_json({
                'active_gateways': active_gateways,
                'total_gateways': len(gateways),
                'active_customers': active_customers,
                'monthly_revenue': revenue,
                'bandwidth_today': bandwidth
            })
            return
        
        if path == '/api/gateways':
            gateways = self.db.get_all_gateways()
            for g in gateways:
                g['customer_count'] = self.db.get_gateway_customer_count(g['gateway_id'])
            self.send_json(gateways)
            return
        
        if path == '/api/customers':
            customers = self.db.get_all_customers()
            self.send_json(customers)
            return
        
        if path == '/api/billing':
            invoices = self.db.get_pending_invoices()
            gateways = self.db.get_all_gateways()
            
            payouts = []
            for g in gateways:
                count = self.db.get_gateway_customer_count(g['gateway_id'])
                if count > 0:
                    payouts.append({
                        'gateway_id': g['gateway_id'],
                        'owner_name': g['owner_name'],
                        'owner_payment': g['owner_payment'],
                        'customer_count': count
                    })
            
            self.send_json({'invoices': invoices, 'payouts': payouts})
            return
        
        # 404
        self.send_response(404)
        self.end_headers()
    
    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}
        
        if path == '/api/gateways':
            success = self.db.add_gateway(
                gateway_id=data.get('gateway_id', ''),
                public_key=data.get('public_key', ''),
                owner_name=data.get('owner_name', ''),
                owner_email=data.get('owner_email', ''),
                owner_payment=data.get('owner_payment', ''),
                max_customers=int(data.get('max_customers', 9))
            )
            self.send_json({'success': success})
            return
        
        if path == '/api/customers':
            success = self.db.add_customer(
                customer_id=data.get('customer_id', ''),
                name=data.get('name', ''),
                email=data.get('email', ''),
                phone=data.get('phone', ''),
                address=data.get('address', ''),
                plan=data.get('plan', 'option_a')
            )
            self.send_json({'success': success})
            return
        
        if '/api/billing/' in path and '/paid' in path:
            invoice_id = int(path.split('/')[-2])
            self.db.mark_invoice_paid(invoice_id)
            self.send_json({'success': True})
            return
        
        # Move customer to different gateway
        if path == '/api/customers/move':
            customer_id = data.get('customer_id')
            new_gateway_id = data.get('gateway_id')
            
            if not customer_id or not new_gateway_id:
                self.send_json({'success': False, 'error': 'Missing customer_id or gateway_id'}, 400)
                return
            
            # Check gateway has capacity
            gateway = self.db.get_gateway(new_gateway_id)
            if not gateway:
                self.send_json({'success': False, 'error': 'Gateway not found'}, 404)
                return
            
            current_count = self.db.get_gateway_customer_count(new_gateway_id)
            max_customers = gateway.get('max_customers', 9)
            
            if current_count >= max_customers:
                self.send_json({'success': False, 'error': 'Gateway at capacity'}, 400)
                return
            
            # Move customer
            success = self.db.move_customer(customer_id, new_gateway_id)
            self.send_json({'success': success})
            return
        
        # Auto-balance customers across gateways
        if path == '/api/gateways/balance':
            result = self.db.auto_balance_customers()
            self.send_json(result)
            return
        
        # Update gateway max_customers
        if path == '/api/gateways/set-limit':
            gateway_id = data.get('gateway_id')
            max_customers = int(data.get('max_customers', 9))
            
            if max_customers < 1 or max_customers > 20:
                self.send_json({'success': False, 'error': 'Limit must be 1-20'}, 400)
                return
            
            success = self.db.update_gateway_limit(gateway_id, max_customers)
            self.send_json({'success': success})
            return
        
        self.send_response(404)
        self.end_headers()
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Alkaline Network Dashboard")
    parser.add_argument('--host', default='0.0.0.0', help='Listen host')
    parser.add_argument('--port', type=int, default=8080, help='Listen port')
    parser.add_argument('--db', default=str(DB_PATH), help='Database path')
    
    args = parser.parse_args()
    
    # Initialize database
    db = Database(args.db)
    DashboardHandler.db = db
    
    # Start server
    server = HTTPServer((args.host, args.port), DashboardHandler)
    
    logger.info("=" * 60)
    logger.info("  ALKALINE NETWORK - DASHBOARD")
    logger.info("=" * 60)
    logger.info(f"Dashboard running at http://{args.host}:{args.port}")
    logger.info("=" * 60)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
