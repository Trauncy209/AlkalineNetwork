#!/usr/bin/env python3
"""
Alkaline Network - Billing & Payment System
=============================================

This module handles:
  1. Customer billing ($7.99/mo or $14.99/mo no deposit)
  2. Gateway host payouts ($2/customer/month)
  3. Stripe integration for payments and payouts
  4. Syncing paid/unpaid status to the tunnel server

The tunnel server reads from this system to know who's allowed online.

Stripe Setup Required:
  1. Create Stripe account at stripe.com
  2. Get API keys from dashboard.stripe.com/apikeys
  3. Set environment variables:
     - STRIPE_SECRET_KEY=sk_live_xxx (or sk_test_xxx for testing)
     - STRIPE_WEBHOOK_SECRET=whsec_xxx
  4. For payouts to gateway hosts, enable Stripe Connect

Usage:
  # Run billing cycle (cron this monthly)
  python alkaline_billing.py --run-billing

  # Check payment status
  python alkaline_billing.py --status customer_123

  # Manual payout to gateway host
  python alkaline_billing.py --payout gateway_001

Requirements:
  pip install stripe

Author: AlkalineTech
License: MIT
"""

import os
import sys
import json
import time
import sqlite3
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("alkaline.billing")

# =============================================================================
# CONFIGURATION
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "alkaline.db"
CLIENTS_JSON = SCRIPT_DIR / "clients.json"  # What the tunnel server reads
CONFIG_FILE = SCRIPT_DIR / "billing_config.json"

# Pricing
PLAN_PRICES = {
    "option_a": Decimal("7.99"),   # With $100 deposit
    "option_b": Decimal("14.99"),  # No deposit
}
DEPOSIT_AMOUNT = Decimal("100.00")
GATEWAY_PAYOUT_PER_CUSTOMER = Decimal("2.00")

# Stripe (loaded from env)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Try to import Stripe
try:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    HAS_STRIPE = bool(STRIPE_SECRET_KEY)
except ImportError:
    HAS_STRIPE = False
    logger.warning("Stripe not installed. Run: pip install stripe")


# =============================================================================
# DATABASE
# =============================================================================

class BillingDatabase:
    """Handles all billing database operations."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_billing_tables()
    
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_billing_tables(self):
        """Add billing-specific tables if they don't exist."""
        conn = self._get_conn()
        c = conn.cursor()
        
        # Payment methods table (Stripe customer IDs)
        c.execute('''
            CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT UNIQUE,
                stripe_customer_id TEXT,
                stripe_payment_method_id TEXT,
                card_last4 TEXT,
                card_brand TEXT,
                created_at REAL,
                updated_at REAL
            )
        ''')
        
        # Gateway payout accounts (Stripe Connect)
        c.execute('''
            CREATE TABLE IF NOT EXISTS gateway_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gateway_id TEXT UNIQUE,
                stripe_account_id TEXT,
                payout_method TEXT,
                payout_email TEXT,
                total_earned REAL DEFAULT 0,
                total_paid REAL DEFAULT 0,
                last_payout_at REAL,
                created_at REAL
            )
        ''')
        
        # Transaction history
        c.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                entity_type TEXT,
                entity_id TEXT,
                amount REAL,
                stripe_charge_id TEXT,
                stripe_transfer_id TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending',
                created_at REAL,
                completed_at REAL
            )
        ''')
        
        # Add stripe columns to customers if not exist
        try:
            c.execute("ALTER TABLE customers ADD COLUMN stripe_customer_id TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            c.execute("ALTER TABLE customers ADD COLUMN subscription_status TEXT DEFAULT 'inactive'")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE customers ADD COLUMN last_payment_at REAL")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE customers ADD COLUMN next_billing_at REAL")
        except sqlite3.OperationalError:
            pass
        
        # Add stripe columns to gateways if not exist
        try:
            c.execute("ALTER TABLE gateways ADD COLUMN stripe_account_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE gateways ADD COLUMN pending_payout REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        conn.commit()
        conn.close()
    
    def get_active_customers(self) -> List[Dict]:
        """Get all customers with active subscriptions."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM customers 
            WHERE status = 'active' 
            AND subscription_status = 'active'
        """)
        results = [dict(row) for row in c.fetchall()]
        conn.close()
        return results
    
    def get_all_customers(self) -> List[Dict]:
        """Get all customers."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM customers")
        results = [dict(row) for row in c.fetchall()]
        conn.close()
        return results
    
    def get_customer(self, customer_id: str) -> Optional[Dict]:
        """Get a single customer."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def update_customer_subscription(self, customer_id: str, status: str, 
                                     next_billing: float = None):
        """Update customer subscription status."""
        conn = self._get_conn()
        c = conn.cursor()
        
        if next_billing:
            c.execute("""
                UPDATE customers 
                SET subscription_status = ?, next_billing_at = ?, last_payment_at = ?
                WHERE customer_id = ?
            """, (status, next_billing, time.time(), customer_id))
        else:
            c.execute("""
                UPDATE customers 
                SET subscription_status = ?
                WHERE customer_id = ?
            """, (status, customer_id))
        
        conn.commit()
        conn.close()
    
    def get_gateway(self, gateway_id: str) -> Optional[Dict]:
        """Get a single gateway."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM gateways WHERE gateway_id = ?", (gateway_id,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_all_gateways(self) -> List[Dict]:
        """Get all gateways."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM gateways WHERE status = 'active'")
        results = [dict(row) for row in c.fetchall()]
        conn.close()
        return results
    
    def get_gateway_customers(self, gateway_id: str) -> List[Dict]:
        """Get all active customers for a gateway."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM customers 
            WHERE gateway_id = ? 
            AND status = 'active' 
            AND subscription_status = 'active'
        """, (gateway_id,))
        results = [dict(row) for row in c.fetchall()]
        conn.close()
        return results
    
    def add_pending_payout(self, gateway_id: str, amount: float):
        """Add to gateway's pending payout."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE gateways 
            SET pending_payout = pending_payout + ?
            WHERE gateway_id = ?
        """, (amount, gateway_id))
        conn.commit()
        conn.close()
    
    def clear_pending_payout(self, gateway_id: str) -> float:
        """Clear and return pending payout amount."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT pending_payout FROM gateways WHERE gateway_id = ?", (gateway_id,))
        row = c.fetchone()
        amount = row['pending_payout'] if row else 0
        
        c.execute("UPDATE gateways SET pending_payout = 0 WHERE gateway_id = ?", (gateway_id,))
        conn.commit()
        conn.close()
        return amount
    
    def add_transaction(self, type: str, entity_type: str, entity_id: str,
                       amount: float, description: str, status: str = "pending",
                       stripe_charge_id: str = None, stripe_transfer_id: str = None):
        """Record a transaction."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO transactions 
            (type, entity_type, entity_id, amount, stripe_charge_id, 
             stripe_transfer_id, description, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (type, entity_type, entity_id, amount, stripe_charge_id,
              stripe_transfer_id, description, status, time.time()))
        conn.commit()
        conn.close()
    
    def set_customer_stripe_id(self, customer_id: str, stripe_customer_id: str):
        """Set Stripe customer ID."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE customers SET stripe_customer_id = ? WHERE customer_id = ?
        """, (stripe_customer_id, customer_id))
        conn.commit()
        conn.close()
    
    def set_gateway_stripe_account(self, gateway_id: str, stripe_account_id: str):
        """Set Stripe Connect account ID for gateway."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE gateways SET stripe_account_id = ? WHERE gateway_id = ?
        """, (stripe_account_id, gateway_id))
        conn.commit()
        conn.close()


# =============================================================================
# STRIPE INTEGRATION
# =============================================================================

class StripePayments:
    """Handles all Stripe operations."""
    
    def __init__(self, db: BillingDatabase):
        self.db = db
        
        if not HAS_STRIPE:
            logger.warning("Stripe not configured - payments disabled")
    
    def create_customer(self, customer_id: str, email: str, name: str) -> Optional[str]:
        """Create a Stripe customer and return their ID."""
        if not HAS_STRIPE:
            return None
        
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"alkaline_customer_id": customer_id}
            )
            
            self.db.set_customer_stripe_id(customer_id, customer.id)
            logger.info(f"Created Stripe customer {customer.id} for {customer_id}")
            return customer.id
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating customer: {e}")
            return None
    
    def create_checkout_session(self, customer_id: str, plan: str, 
                                success_url: str, cancel_url: str) -> Optional[str]:
        """Create a Stripe Checkout session for subscription signup."""
        if not HAS_STRIPE:
            return None
        
        customer = self.db.get_customer(customer_id)
        if not customer:
            logger.error(f"Customer {customer_id} not found")
            return None
        
        price = PLAN_PRICES.get(plan, PLAN_PRICES["option_a"])
        
        try:
            # Create or get Stripe customer
            stripe_customer_id = customer.get("stripe_customer_id")
            if not stripe_customer_id:
                stripe_customer_id = self.create_customer(
                    customer_id, 
                    customer.get("email", ""),
                    customer.get("name", "")
                )
            
            # Build line items
            line_items = [
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(price * 100),  # Cents
                        "recurring": {"interval": "month"},
                        "product_data": {
                            "name": f"Alkaline Internet - {'Standard' if plan == 'option_a' else 'No Deposit'} Plan",
                            "description": "Rural mesh internet service"
                        }
                    },
                    "quantity": 1
                }
            ]
            
            # Add deposit if option_a and not yet paid
            if plan == "option_a" and not customer.get("deposit_paid"):
                line_items.append({
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(DEPOSIT_AMOUNT * 100),
                        "product_data": {
                            "name": "Equipment Deposit",
                            "description": "Refundable deposit for mesh equipment"
                        }
                    },
                    "quantity": 1
                })
            
            session = stripe.checkout.Session.create(
                customer=stripe_customer_id,
                payment_method_types=["card"],
                line_items=line_items,
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "alkaline_customer_id": customer_id,
                    "plan": plan
                }
            )
            
            logger.info(f"Created checkout session {session.id} for {customer_id}")
            return session.url
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout: {e}")
            return None
    
    def charge_customer(self, customer_id: str, amount: Decimal, 
                       description: str) -> Optional[str]:
        """Charge a customer's saved payment method."""
        if not HAS_STRIPE:
            return None
        
        customer = self.db.get_customer(customer_id)
        if not customer or not customer.get("stripe_customer_id"):
            logger.error(f"No Stripe customer for {customer_id}")
            return None
        
        try:
            # Get default payment method
            stripe_customer = stripe.Customer.retrieve(customer["stripe_customer_id"])
            payment_method = stripe_customer.invoice_settings.default_payment_method
            
            if not payment_method:
                logger.error(f"No payment method for {customer_id}")
                return None
            
            # Create payment intent
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),
                currency="usd",
                customer=customer["stripe_customer_id"],
                payment_method=payment_method,
                off_session=True,
                confirm=True,
                description=description,
                metadata={"alkaline_customer_id": customer_id}
            )
            
            if intent.status == "succeeded":
                self.db.add_transaction(
                    type="charge",
                    entity_type="customer",
                    entity_id=customer_id,
                    amount=float(amount),
                    description=description,
                    status="completed",
                    stripe_charge_id=intent.id
                )
                logger.info(f"Charged {customer_id} ${amount}")
                return intent.id
            else:
                logger.warning(f"Payment intent status: {intent.status}")
                return None
                
        except stripe.error.CardError as e:
            logger.error(f"Card declined for {customer_id}: {e}")
            self.db.update_customer_subscription(customer_id, "payment_failed")
            return None
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error charging {customer_id}: {e}")
            return None
    
    def create_gateway_connect_account(self, gateway_id: str, email: str) -> Optional[str]:
        """Create a Stripe Connect Express account for a gateway host."""
        if not HAS_STRIPE:
            return None
        
        try:
            account = stripe.Account.create(
                type="express",
                country="US",
                email=email,
                capabilities={
                    "transfers": {"requested": True}
                },
                metadata={"alkaline_gateway_id": gateway_id}
            )
            
            self.db.set_gateway_stripe_account(gateway_id, account.id)
            logger.info(f"Created Connect account {account.id} for gateway {gateway_id}")
            return account.id
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating Connect account: {e}")
            return None
    
    def get_connect_onboarding_link(self, gateway_id: str, 
                                    return_url: str, refresh_url: str) -> Optional[str]:
        """Get the Stripe Connect onboarding link for a gateway host."""
        if not HAS_STRIPE:
            return None
        
        gateway = self.db.get_gateway(gateway_id)
        if not gateway:
            return None
        
        account_id = gateway.get("stripe_account_id")
        if not account_id:
            account_id = self.create_gateway_connect_account(
                gateway_id, gateway.get("owner_email", "")
            )
        
        if not account_id:
            return None
        
        try:
            link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding"
            )
            return link.url
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating onboarding link: {e}")
            return None
    
    def payout_gateway(self, gateway_id: str, amount: Decimal = None) -> Optional[str]:
        """Send payout to a gateway host via Stripe Connect."""
        if not HAS_STRIPE:
            return None
        
        gateway = self.db.get_gateway(gateway_id)
        if not gateway:
            logger.error(f"Gateway {gateway_id} not found")
            return None
        
        account_id = gateway.get("stripe_account_id")
        if not account_id:
            logger.error(f"No Stripe account for gateway {gateway_id}")
            return None
        
        # Use pending payout if no amount specified
        if amount is None:
            amount = Decimal(str(self.db.clear_pending_payout(gateway_id)))
        
        if amount <= 0:
            logger.info(f"No payout due for gateway {gateway_id}")
            return None
        
        try:
            transfer = stripe.Transfer.create(
                amount=int(amount * 100),
                currency="usd",
                destination=account_id,
                description=f"Alkaline Network gateway payout - {gateway_id}",
                metadata={"alkaline_gateway_id": gateway_id}
            )
            
            self.db.add_transaction(
                type="payout",
                entity_type="gateway",
                entity_id=gateway_id,
                amount=float(amount),
                description="Monthly gateway host payout",
                status="completed",
                stripe_transfer_id=transfer.id
            )
            
            logger.info(f"Sent ${amount} payout to gateway {gateway_id}")
            return transfer.id
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error paying gateway {gateway_id}: {e}")
            return None
    
    def handle_webhook(self, payload: bytes, sig_header: str) -> bool:
        """Handle incoming Stripe webhook."""
        if not HAS_STRIPE or not STRIPE_WEBHOOK_SECRET:
            return False
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            logger.error("Invalid webhook payload")
            return False
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid webhook signature")
            return False
        
        # Handle the event
        if event.type == "checkout.session.completed":
            session = event.data.object
            customer_id = session.metadata.get("alkaline_customer_id")
            if customer_id:
                self._handle_checkout_complete(customer_id, session)
        
        elif event.type == "invoice.paid":
            invoice = event.data.object
            customer_id = self._get_customer_from_stripe_id(
                invoice.customer
            )
            if customer_id:
                self._handle_invoice_paid(customer_id, invoice)
        
        elif event.type == "invoice.payment_failed":
            invoice = event.data.object
            customer_id = self._get_customer_from_stripe_id(
                invoice.customer
            )
            if customer_id:
                self._handle_payment_failed(customer_id)
        
        elif event.type == "customer.subscription.deleted":
            subscription = event.data.object
            customer_id = self._get_customer_from_stripe_id(
                subscription.customer
            )
            if customer_id:
                self._handle_subscription_cancelled(customer_id)
        
        return True
    
    def _get_customer_from_stripe_id(self, stripe_customer_id: str) -> Optional[str]:
        """Look up our customer ID from Stripe customer ID."""
        conn = self.db._get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT customer_id FROM customers WHERE stripe_customer_id = ?",
            (stripe_customer_id,)
        )
        row = c.fetchone()
        conn.close()
        return row["customer_id"] if row else None
    
    def _handle_checkout_complete(self, customer_id: str, session):
        """Handle successful checkout."""
        logger.info(f"Checkout complete for {customer_id}")
        
        # Mark subscription as active
        next_billing = time.time() + (30 * 24 * 60 * 60)  # 30 days
        self.db.update_customer_subscription(customer_id, "active", next_billing)
        
        # Sync to tunnel server
        sync_clients_json(self.db)
    
    def _handle_invoice_paid(self, customer_id: str, invoice):
        """Handle successful recurring payment."""
        logger.info(f"Invoice paid for {customer_id}")
        
        # Extend subscription
        next_billing = time.time() + (30 * 24 * 60 * 60)
        self.db.update_customer_subscription(customer_id, "active", next_billing)
        
        # Calculate gateway payout
        customer = self.db.get_customer(customer_id)
        if customer and customer.get("gateway_id"):
            self.db.add_pending_payout(
                customer["gateway_id"], 
                float(GATEWAY_PAYOUT_PER_CUSTOMER)
            )
        
        # Sync to tunnel server
        sync_clients_json(self.db)
    
    def _handle_payment_failed(self, customer_id: str):
        """Handle failed payment."""
        logger.warning(f"Payment failed for {customer_id}")
        self.db.update_customer_subscription(customer_id, "payment_failed")
        
        # Sync to tunnel server (will remove access)
        sync_clients_json(self.db)
    
    def _handle_subscription_cancelled(self, customer_id: str):
        """Handle subscription cancellation."""
        logger.info(f"Subscription cancelled for {customer_id}")
        self.db.update_customer_subscription(customer_id, "cancelled")
        
        # Sync to tunnel server
        sync_clients_json(self.db)


# =============================================================================
# SYNC TO TUNNEL SERVER
# =============================================================================

def sync_clients_json(db: BillingDatabase):
    """
    Sync active customers to clients.json for the tunnel server.
    
    This is the critical link - only customers with active subscriptions
    get added to clients.json, which the tunnel server reads to allow connections.
    """
    active_customers = db.get_active_customers()
    
    clients = {}
    for customer in active_customers:
        public_key = customer.get("public_key")
        if not public_key:
            continue
        
        clients[public_key] = {
            "name": customer.get("name", customer["customer_id"]),
            "tunnel_ip": customer.get("tunnel_ip", ""),
            "customer_id": customer["customer_id"],
            "gateway_id": customer.get("gateway_id", ""),
            "plan": customer.get("plan", "option_a"),
            "bytes_up": customer.get("bytes_up", 0),
            "bytes_down": customer.get("bytes_down", 0),
        }
    
    # Write to clients.json
    with open(CLIENTS_JSON, 'w') as f:
        json.dump(clients, f, indent=2)
    
    logger.info(f"Synced {len(clients)} active customers to clients.json")
    return len(clients)


def sync_from_tunnel_server(db: BillingDatabase):
    """
    Sync usage stats from tunnel server back to database.
    
    Call this periodically to update bytes_up/bytes_down for billing.
    """
    if not CLIENTS_JSON.exists():
        return
    
    with open(CLIENTS_JSON) as f:
        clients = json.load(f)
    
    conn = db._get_conn()
    c = conn.cursor()
    
    for public_key, info in clients.items():
        if "customer_id" in info:
            c.execute("""
                UPDATE customers 
                SET bytes_up = ?, bytes_down = ?, last_seen = ?
                WHERE customer_id = ?
            """, (
                info.get("bytes_up", 0),
                info.get("bytes_down", 0),
                time.time(),
                info["customer_id"]
            ))
    
    conn.commit()
    conn.close()
    logger.info(f"Synced usage stats for {len(clients)} customers")


# =============================================================================
# BILLING CYCLE
# =============================================================================

def run_monthly_billing(db: BillingDatabase, payments: StripePayments):
    """
    Run the monthly billing cycle.
    
    This should be run via cron on the 1st of each month:
      0 0 1 * * python /opt/alkaline/alkaline_billing.py --run-billing
    """
    logger.info("Starting monthly billing cycle...")
    
    # 1. Charge all active customers
    customers = db.get_all_customers()
    charged = 0
    failed = 0
    
    for customer in customers:
        if customer.get("subscription_status") != "active":
            continue
        
        # Check if billing is due
        next_billing = customer.get("next_billing_at", 0)
        if next_billing > time.time():
            continue  # Not due yet
        
        plan = customer.get("plan", "option_a")
        amount = PLAN_PRICES.get(plan, PLAN_PRICES["option_a"])
        
        result = payments.charge_customer(
            customer["customer_id"],
            amount,
            f"Alkaline Internet - {datetime.now().strftime('%B %Y')}"
        )
        
        if result:
            charged += 1
            # Add gateway payout
            if customer.get("gateway_id"):
                db.add_pending_payout(
                    customer["gateway_id"],
                    float(GATEWAY_PAYOUT_PER_CUSTOMER)
                )
        else:
            failed += 1
    
    logger.info(f"Billing complete: {charged} charged, {failed} failed")
    
    # 2. Pay out gateway hosts
    gateways = db.get_all_gateways()
    paid_out = 0
    
    for gateway in gateways:
        pending = gateway.get("pending_payout", 0)
        if pending > 0:
            result = payments.payout_gateway(gateway["gateway_id"])
            if result:
                paid_out += 1
    
    logger.info(f"Gateway payouts: {paid_out} gateways paid")
    
    # 3. Sync to tunnel server
    active_count = sync_clients_json(db)
    logger.info(f"Active customers synced: {active_count}")
    
    return {
        "customers_charged": charged,
        "customers_failed": failed,
        "gateways_paid": paid_out,
        "active_customers": active_count
    }


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Alkaline Network Billing System"
    )
    
    parser.add_argument("--run-billing", action="store_true",
                       help="Run monthly billing cycle")
    parser.add_argument("--sync", action="store_true",
                       help="Sync active customers to clients.json")
    parser.add_argument("--status", type=str, metavar="CUSTOMER_ID",
                       help="Check customer payment status")
    parser.add_argument("--payout", type=str, metavar="GATEWAY_ID",
                       help="Trigger payout for a gateway")
    parser.add_argument("--summary", action="store_true",
                       help="Show billing summary")
    
    args = parser.parse_args()
    
    db = BillingDatabase()
    payments = StripePayments(db)
    
    if args.run_billing:
        results = run_monthly_billing(db, payments)
        print(f"\nBilling Cycle Complete:")
        print(f"  Customers charged: {results['customers_charged']}")
        print(f"  Failed charges: {results['customers_failed']}")
        print(f"  Gateways paid: {results['gateways_paid']}")
        print(f"  Active customers: {results['active_customers']}")
        return
    
    if args.sync:
        count = sync_clients_json(db)
        print(f"Synced {count} active customers to clients.json")
        return
    
    if args.status:
        customer = db.get_customer(args.status)
        if customer:
            print(f"\nCustomer: {args.status}")
            print(f"  Name: {customer.get('name', '-')}")
            print(f"  Plan: {customer.get('plan', '-')}")
            print(f"  Status: {customer.get('status', '-')}")
            print(f"  Subscription: {customer.get('subscription_status', '-')}")
            print(f"  Gateway: {customer.get('gateway_id', '-')}")
            if customer.get('next_billing_at'):
                next_bill = datetime.fromtimestamp(customer['next_billing_at'])
                print(f"  Next billing: {next_bill.strftime('%Y-%m-%d')}")
        else:
            print(f"Customer {args.status} not found")
        return
    
    if args.payout:
        result = payments.payout_gateway(args.payout)
        if result:
            print(f"Payout sent: {result}")
        else:
            print("Payout failed or no balance")
        return
    
    if args.summary:
        customers = db.get_all_customers()
        gateways = db.get_all_gateways()
        
        active = sum(1 for c in customers if c.get('subscription_status') == 'active')
        total_mrr = sum(
            float(PLAN_PRICES.get(c.get('plan', 'option_a'), PLAN_PRICES['option_a']))
            for c in customers if c.get('subscription_status') == 'active'
        )
        pending_payouts = sum(g.get('pending_payout', 0) for g in gateways)
        
        print(f"\n{'='*50}")
        print("ALKALINE NETWORK - BILLING SUMMARY")
        print(f"{'='*50}")
        print(f"Total Customers: {len(customers)}")
        print(f"Active Subscriptions: {active}")
        print(f"Monthly Recurring Revenue: ${total_mrr:.2f}")
        print(f"Gateway Count: {len(gateways)}")
        print(f"Pending Gateway Payouts: ${pending_payouts:.2f}")
        print(f"Stripe Configured: {'Yes' if HAS_STRIPE else 'No'}")
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
