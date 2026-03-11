"""
Alkaline Network - QoS and Bandwidth Management
Handles bandwidth management for HaLow mesh network.

NOTE: HaLow (802.11ah) maxes out at ~20 Mbps. We don't have speed tiers
because the radio is the bottleneck, not software throttling.
The two pricing options (deposit vs included) are payment plans, not speed tiers.
"""

import time
import threading
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
import sqlite3
import os

class PaymentPlan(Enum):
    """Payment plans matching website pricing."""
    DEPOSIT = "deposit"      # $7.99/mo + $100 refundable deposit
    INCLUDED = "included"    # $14.99/mo, $0 down, keep equipment after 12mo

@dataclass
class PlanDetails:
    """Details for each payment plan."""
    monthly_cost: float
    deposit: float
    equipment_ownership: str  # "return" or "keep_after_12mo"
    
PLAN_DETAILS: Dict[PaymentPlan, PlanDetails] = {
    PaymentPlan.DEPOSIT: PlanDetails(
        monthly_cost=7.99,
        deposit=100.00,
        equipment_ownership="return"
    ),
    PaymentPlan.INCLUDED: PlanDetails(
        monthly_cost=14.99,
        deposit=0.00,
        equipment_ownership="keep_after_12mo"
    ),
}

# HaLow bandwidth - hardware limited, not software throttled
# All users share the same ~20 Mbps max from each gateway
HALOW_MAX_BANDWIDTH_MBPS = 20

@dataclass
class CustomerUsage:
    """Track customer bandwidth usage."""
    customer_id: str
    plan: PaymentPlan
    bytes_downloaded: int = 0
    bytes_uploaded: int = 0
    months_active: int = 0
    deposit_paid: float = 0
    last_reset: float = 0  # timestamp
    current_download_rate: float = 0  # bytes per second
    current_upload_rate: float = 0

class TokenBucket:
    """
    Token bucket algorithm for rate limiting.
    Allows bursting while maintaining average rate.
    """
    def __init__(self, rate_bps: int, burst_seconds: float = 1.0):
        self.rate = rate_bps / 8  # Convert to bytes per second
        self.burst_size = self.rate * burst_seconds  # Allow 1 second burst
        self.tokens = self.burst_size
        self.last_update = time.time()
        self.lock = threading.Lock()
    
    def consume(self, bytes_count: int) -> tuple[bool, float]:
        """
        Try to consume tokens for sending/receiving bytes.
        Returns (allowed, wait_time).
        """
        with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            
            # Add tokens based on time elapsed
            self.tokens = min(
                self.burst_size,
                self.tokens + (elapsed * self.rate)
            )
            
            if self.tokens >= bytes_count:
                self.tokens -= bytes_count
                return True, 0
            else:
                # Calculate wait time
                needed = bytes_count - self.tokens
                wait_time = needed / self.rate
                return False, wait_time
    
    def update_rate(self, new_rate_bps: int):
        """Update the rate limit."""
        with self.lock:
            self.rate = new_rate_bps / 8
            self.burst_size = self.rate * 1.0

class QoSManager:
    """
    Main QoS manager for Alkaline Network.
    
    NOTE: With HaLow, we don't do per-customer rate limiting because
    the radio hardware (20 Mbps max) is the bottleneck. Instead, we
    track usage for billing and manage fair sharing among customers
    on each gateway.
    """
    
    def __init__(self, db_path: str = "alkaline_network.db"):
        self.db_path = db_path
        self.customers: Dict[str, CustomerUsage] = {}
        self.lock = threading.Lock()
        
        # Gateway operator revenue share
        self.gateway_share_per_customer = 2.00  # $2/customer/month
        
        # Stats
        self.total_bytes_served = 0
        
        self._init_db()
        self._load_customers()
    
    def _init_db(self):
        """Initialize the customer database."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                mac_address TEXT UNIQUE,
                plan TEXT DEFAULT 'included',
                gateway_id TEXT,
                active INTEGER DEFAULT 1,
                created_at REAL,
                months_active INTEGER DEFAULT 0,
                deposit_paid REAL DEFAULT 0,
                total_download INTEGER DEFAULT 0,
                total_upload INTEGER DEFAULT 0
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS gateways (
                gateway_id TEXT PRIMARY KEY,
                operator_name TEXT,
                gateway_mac TEXT UNIQUE,
                location TEXT,
                active INTEGER DEFAULT 1,
                customer_count INTEGER DEFAULT 0,
                total_earnings REAL DEFAULT 0
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT,
                timestamp REAL,
                bytes_download INTEGER,
                bytes_upload INTEGER,
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def _load_customers(self):
        """Load customers from database."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT customer_id, plan, months_active, deposit_paid FROM customers WHERE active = 1')
        for row in c.fetchall():
            customer_id, plan_str, months, deposit = row
            plan = PaymentPlan(plan_str) if plan_str in ['deposit', 'included'] else PaymentPlan.INCLUDED
            with self.lock:
                self.customers[customer_id] = CustomerUsage(
                    customer_id=customer_id,
                    plan=plan,
                    months_active=months or 0,
                    deposit_paid=deposit or 0,
                    last_reset=time.time()
                )
        
        conn.close()
    
    def register_customer(self, customer_id: str, mac_address: str, 
                          plan: PaymentPlan = PaymentPlan.INCLUDED,
                          gateway_id: Optional[str] = None) -> bool:
        """Register a new customer."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        plan_details = PLAN_DETAILS[plan]
        
        try:
            c.execute('''
                INSERT INTO customers (customer_id, mac_address, plan, gateway_id, created_at, deposit_paid)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (customer_id, mac_address, plan.value, gateway_id, time.time(), plan_details.deposit))
            
            # Update gateway customer count
            if gateway_id:
                c.execute('''
                    UPDATE gateways SET customer_count = customer_count + 1
                    WHERE gateway_id = ?
                ''', (gateway_id,))
            
            conn.commit()
            
            with self.lock:
                self.customers[customer_id] = CustomerUsage(
                    customer_id=customer_id,
                    plan=plan,
                    deposit_paid=plan_details.deposit,
                    last_reset=time.time()
                )
            return True
            
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def register_gateway(self, gateway_id: str, operator_name: str, 
                         gateway_mac: str, location: str = "") -> bool:
        """Register a new gateway operator."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        try:
            c.execute('''
                INSERT INTO gateways (gateway_id, operator_name, gateway_mac, location)
                VALUES (?, ?, ?, ?)
            ''', (gateway_id, operator_name, gateway_mac, location))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def get_customer_by_mac(self, mac_address: str) -> Optional[str]:
        """Look up customer ID by MAC address."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT customer_id FROM customers WHERE mac_address = ? AND active = 1', 
                  (mac_address,))
        row = c.fetchone()
        conn.close()
        
        return row[0] if row else None
    
    def record_transfer(self, customer_id: str, bytes_count: int, 
                        direction: str = "download"):
        """Record a data transfer for billing/stats."""
        if customer_id not in self.customers:
            return
        
        with self.lock:
            customer = self.customers[customer_id]
            if direction == "download":
                customer.bytes_downloaded += bytes_count
            else:
                customer.bytes_uploaded += bytes_count
            
            self.total_bytes_served += bytes_count
    
    def update_customer_plan(self, customer_id: str, new_plan: PaymentPlan):
        """Update a customer's payment plan."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('UPDATE customers SET plan = ? WHERE customer_id = ?',
                  (new_plan.value, customer_id))
        conn.commit()
        conn.close()
        
        with self.lock:
            if customer_id in self.customers:
                self.customers[customer_id].plan = new_plan
    
    def get_gateway_customer_count(self, gateway_id: str) -> int:
        """Get number of active customers for a gateway."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(*) FROM customers WHERE gateway_id = ? AND active = 1',
                  (gateway_id,))
        count = c.fetchone()[0]
        conn.close()
        
        return count
    
    def calculate_gateway_earnings(self, gateway_id: str) -> float:
        """Calculate monthly earnings for a gateway operator ($2/customer)."""
        customer_count = self.get_gateway_customer_count(gateway_id)
        return customer_count * self.gateway_share_per_customer
    
    def get_all_gateway_earnings(self) -> Dict[str, dict]:
        """Get earnings summary for all gateway operators."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT g.gateway_id, g.operator_name, COUNT(c.customer_id) as customer_count
            FROM gateways g
            LEFT JOIN customers c ON g.gateway_id = c.gateway_id AND c.active = 1
            WHERE g.active = 1
            GROUP BY g.gateway_id
        ''')
        
        results = {}
        for row in c.fetchall():
            gateway_id, name, count = row
            results[gateway_id] = {
                "operator_name": name,
                "customer_count": count,
                "monthly_earnings": count * self.gateway_share_per_customer
            }
        
        conn.close()
        return results
    
    def get_stats(self) -> dict:
        """Get overall network statistics."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(*) FROM customers WHERE active = 1')
        total_customers = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM gateways WHERE active = 1')
        total_gateways = c.fetchone()[0]
        
        c.execute('SELECT plan, COUNT(*) FROM customers WHERE active = 1 GROUP BY plan')
        plan_breakdown = dict(c.fetchall())
        
        conn.close()
        
        return {
            "total_customers": total_customers,
            "total_gateways": total_gateways,
            "plan_breakdown": plan_breakdown,
            "total_bytes_served": self.total_bytes_served,
            "halow_max_bandwidth_mbps": HALOW_MAX_BANDWIDTH_MBPS
        }
    
    def flush_usage_logs(self):
        """Flush current usage to database for billing."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        now = time.time()
        
        with self.lock:
            for customer_id, usage in self.customers.items():
                if usage.bytes_downloaded > 0 or usage.bytes_uploaded > 0:
                    c.execute('''
                        INSERT INTO usage_log (customer_id, timestamp, bytes_download, bytes_upload)
                        VALUES (?, ?, ?, ?)
                    ''', (customer_id, now, usage.bytes_downloaded, usage.bytes_uploaded))
                    
                    # Update totals
                    c.execute('''
                        UPDATE customers 
                        SET total_download = total_download + ?,
                            total_upload = total_upload + ?
                        WHERE customer_id = ?
                    ''', (usage.bytes_downloaded, usage.bytes_uploaded, customer_id))
                    
                    # Reset counters
                    usage.bytes_downloaded = 0
                    usage.bytes_uploaded = 0
        
        conn.commit()
        conn.close()
    
    def process_monthly_billing(self):
        """Process monthly billing - increment months_active, check equipment ownership."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Increment months active for all customers
        c.execute('UPDATE customers SET months_active = months_active + 1 WHERE active = 1')
        
        # Check for customers on 'included' plan who hit 12 months (they now own equipment)
        c.execute('''
            SELECT customer_id FROM customers 
            WHERE plan = 'included' AND months_active >= 12 AND active = 1
        ''')
        owned_equipment = c.fetchall()
        
        conn.commit()
        conn.close()
        
        return {
            "equipment_now_owned": [row[0] for row in owned_equipment]
        }
    
    def calculate_refund(self, customer_id: str) -> float:
        """Calculate refund amount when customer on deposit plan cancels."""
        if customer_id not in self.customers:
            return 0.0
        
        customer = self.customers[customer_id]
        if customer.plan == PaymentPlan.DEPOSIT:
            return customer.deposit_paid  # Full $100 refund on equipment return
        return 0.0


# CLI for testing
if __name__ == "__main__":
    print("Alkaline Network - QoS Manager")
    print("=" * 40)
    
    # Clean up any existing test db
    if os.path.exists("test_qos.db"):
        os.remove("test_qos.db")
    
    qos = QoSManager("test_qos.db")
    
    # Register test gateway
    qos.register_gateway("GW001", "Example Gateway", "AA:BB:CC:DD:EE:FF", "Rural Michigan")
    print("✓ Registered test gateway")
    
    # Register test customers with different plans
    qos.register_customer("CUST001", "11:22:33:44:55:01", PaymentPlan.DEPOSIT, "GW001")
    qos.register_customer("CUST002", "11:22:33:44:55:02", PaymentPlan.INCLUDED, "GW001")
    qos.register_customer("CUST003", "11:22:33:44:55:03", PaymentPlan.INCLUDED, "GW001")
    print("✓ Registered 3 test customers")
    
    # Show stats
    print("\n" + "=" * 40)
    stats = qos.get_stats()
    print(f"Total Customers: {stats['total_customers']}")
    print(f"Total Gateways: {stats['total_gateways']}")
    print(f"Plan Breakdown: {stats['plan_breakdown']}")
    print(f"HaLow Max Bandwidth: {stats['halow_max_bandwidth_mbps']} Mbps")
    
    # Show gateway earnings
    print("\n" + "=" * 40)
    earnings = qos.get_all_gateway_earnings()
    for gw_id, info in earnings.items():
        print(f"Gateway {info['operator_name']}: {info['customer_count']} customers = ${info['monthly_earnings']:.2f}/mo")
    
    # Test refund calculation
    print("\n" + "=" * 40)
    refund = qos.calculate_refund("CUST001")
    print(f"CUST001 (deposit plan) refund: ${refund:.2f}")
    refund = qos.calculate_refund("CUST002")
    print(f"CUST002 (included plan) refund: ${refund:.2f}")
    
    # Cleanup test db
    os.remove("test_qos.db")
    print("\n✓ QoS system working!")
