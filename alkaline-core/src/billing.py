"""
Alkaline Hosting - Billing System
Handles Stripe payments, subscriptions, and hoster payouts.
"""

import stripe
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass
from enum import Enum

# Pricing in cents
PRICING = {
    "basic": 799,   # $7.99
    "plus": 1499,   # $14.99
    "pro": 2499,    # $24.99
}

HOSTER_RATE_CENTS = 200  # $2.00 per customer

@dataclass
class Customer:
    customer_id: str
    email: str
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    tier: str
    hoster_id: str
    active: bool
    created_at: datetime

@dataclass
class Hoster:
    hoster_id: str
    name: str
    email: str
    stripe_connect_id: Optional[str]
    active: bool
    
class BillingManager:
    """
    Manages all billing operations for Alkaline Hosting.
    """
    
    def __init__(self, db_path: str = "alkaline_billing.db", 
                 stripe_secret_key: Optional[str] = None):
        self.db_path = db_path
        
        # Initialize Stripe
        if stripe_secret_key:
            stripe.api_key = stripe_secret_key
        elif os.environ.get('STRIPE_SECRET_KEY'):
            stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
        else:
            print("[WARNING] No Stripe API key - running in test mode")
            self.test_mode = True
        
        self._init_db()
    
    def _init_db(self):
        """Initialize billing database."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS billing_customers (
                customer_id TEXT PRIMARY KEY,
                email TEXT UNIQUE,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                tier TEXT DEFAULT 'basic',
                hoster_id TEXT,
                active INTEGER DEFAULT 1,
                created_at REAL,
                last_payment REAL,
                payment_status TEXT DEFAULT 'pending'
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS billing_hosters (
                hoster_id TEXT PRIMARY KEY,
                name TEXT,
                email TEXT UNIQUE,
                stripe_connect_id TEXT,
                payout_method TEXT DEFAULT 'stripe',
                paypal_email TEXT,
                venmo_handle TEXT,
                active INTEGER DEFAULT 1,
                total_earned REAL DEFAULT 0,
                last_payout REAL
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                customer_id TEXT,
                amount_cents INTEGER,
                stripe_payment_id TEXT,
                status TEXT,
                created_at REAL,
                FOREIGN KEY (customer_id) REFERENCES billing_customers(customer_id)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS payouts (
                payout_id TEXT PRIMARY KEY,
                hoster_id TEXT,
                amount_cents INTEGER,
                customer_count INTEGER,
                stripe_transfer_id TEXT,
                status TEXT,
                created_at REAL,
                FOREIGN KEY (hoster_id) REFERENCES billing_hosters(hoster_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # ============================================
    # CUSTOMER OPERATIONS
    # ============================================
    
    def create_customer(self, customer_id: str, email: str, 
                        tier: str = "basic", hoster_id: str = None) -> dict:
        """Create a new customer with Stripe."""
        try:
            # Create Stripe customer
            stripe_customer = stripe.Customer.create(
                email=email,
                metadata={
                    "alkaline_customer_id": customer_id,
                    "tier": tier
                }
            )
            
            # Save to database
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''
                INSERT INTO billing_customers 
                (customer_id, email, stripe_customer_id, tier, hoster_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (customer_id, email, stripe_customer.id, tier, hoster_id, 
                  datetime.now().timestamp()))
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "customer_id": customer_id,
                "stripe_customer_id": stripe_customer.id
            }
            
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def create_subscription(self, customer_id: str, 
                            payment_method_id: str) -> dict:
        """Create a subscription for a customer."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT stripe_customer_id, tier FROM billing_customers WHERE customer_id = ?',
                  (customer_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            return {"success": False, "error": "Customer not found"}
        
        stripe_customer_id, tier = row
        price_cents = PRICING.get(tier, PRICING["basic"])
        
        try:
            # Attach payment method
            stripe.PaymentMethod.attach(
                payment_method_id,
                customer=stripe_customer_id
            )
            
            # Set as default
            stripe.Customer.modify(
                stripe_customer_id,
                invoice_settings={"default_payment_method": payment_method_id}
            )
            
            # Create subscription
            subscription = stripe.Subscription.create(
                customer=stripe_customer_id,
                items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Alkaline Hosting - {tier.title()} Plan"
                        },
                        "recurring": {"interval": "month"},
                        "unit_amount": price_cents
                    }
                }],
                metadata={"alkaline_customer_id": customer_id}
            )
            
            # Update database
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''
                UPDATE billing_customers 
                SET stripe_subscription_id = ?, payment_status = 'active'
                WHERE customer_id = ?
            ''', (subscription.id, customer_id))
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "subscription_id": subscription.id,
                "status": subscription.status
            }
            
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def cancel_subscription(self, customer_id: str) -> dict:
        """Cancel a customer's subscription."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT stripe_subscription_id FROM billing_customers WHERE customer_id = ?',
                  (customer_id,))
        row = c.fetchone()
        
        if not row or not row[0]:
            conn.close()
            return {"success": False, "error": "No active subscription"}
        
        try:
            stripe.Subscription.delete(row[0])
            
            c.execute('''
                UPDATE billing_customers 
                SET stripe_subscription_id = NULL, payment_status = 'cancelled', active = 0
                WHERE customer_id = ?
            ''', (customer_id,))
            conn.commit()
            conn.close()
            
            return {"success": True, "message": "Subscription cancelled"}
            
        except stripe.error.StripeError as e:
            conn.close()
            return {"success": False, "error": str(e)}
    
    def change_tier(self, customer_id: str, new_tier: str) -> dict:
        """Change a customer's subscription tier."""
        if new_tier not in PRICING:
            return {"success": False, "error": "Invalid tier"}
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT stripe_subscription_id FROM billing_customers WHERE customer_id = ?',
                  (customer_id,))
        row = c.fetchone()
        
        if not row or not row[0]:
            conn.close()
            return {"success": False, "error": "No active subscription"}
        
        try:
            subscription = stripe.Subscription.retrieve(row[0])
            
            stripe.Subscription.modify(
                row[0],
                items=[{
                    "id": subscription['items']['data'][0].id,
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Alkaline Hosting - {new_tier.title()} Plan"
                        },
                        "recurring": {"interval": "month"},
                        "unit_amount": PRICING[new_tier]
                    }
                }],
                proration_behavior='create_prorations'
            )
            
            c.execute('UPDATE billing_customers SET tier = ? WHERE customer_id = ?',
                      (new_tier, customer_id))
            conn.commit()
            conn.close()
            
            return {"success": True, "new_tier": new_tier}
            
        except stripe.error.StripeError as e:
            conn.close()
            return {"success": False, "error": str(e)}
    
    # ============================================
    # HOSTER OPERATIONS
    # ============================================
    
    def register_hoster(self, hoster_id: str, name: str, email: str,
                        payout_method: str = "stripe") -> dict:
        """Register a new hoster."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        try:
            c.execute('''
                INSERT INTO billing_hosters (hoster_id, name, email, payout_method)
                VALUES (?, ?, ?, ?)
            ''', (hoster_id, name, email, payout_method))
            conn.commit()
            conn.close()
            
            return {"success": True, "hoster_id": hoster_id}
            
        except sqlite3.IntegrityError:
            conn.close()
            return {"success": False, "error": "Hoster already exists"}
    
    def setup_stripe_connect(self, hoster_id: str) -> dict:
        """Set up Stripe Connect for hoster payouts."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT email FROM billing_hosters WHERE hoster_id = ?', (hoster_id,))
        row = c.fetchone()
        
        if not row:
            conn.close()
            return {"success": False, "error": "Hoster not found"}
        
        try:
            # Create Stripe Connect account
            account = stripe.Account.create(
                type="express",
                email=row[0],
                capabilities={
                    "transfers": {"requested": True}
                },
                metadata={"alkaline_hoster_id": hoster_id}
            )
            
            # Create account link for onboarding
            account_link = stripe.AccountLink.create(
                account=account.id,
                refresh_url=f"https://alkalinehosting.com/hoster/refresh?id={hoster_id}",
                return_url=f"https://alkalinehosting.com/hoster/complete?id={hoster_id}",
                type="account_onboarding"
            )
            
            c.execute('UPDATE billing_hosters SET stripe_connect_id = ? WHERE hoster_id = ?',
                      (account.id, hoster_id))
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "account_id": account.id,
                "onboarding_url": account_link.url
            }
            
        except stripe.error.StripeError as e:
            conn.close()
            return {"success": False, "error": str(e)}
    
    def calculate_hoster_earnings(self, hoster_id: str) -> dict:
        """Calculate current month earnings for a hoster."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT COUNT(*) FROM billing_customers 
            WHERE hoster_id = ? AND active = 1 AND payment_status = 'active'
        ''', (hoster_id,))
        
        customer_count = c.fetchone()[0]
        earnings_cents = customer_count * HOSTER_RATE_CENTS
        
        conn.close()
        
        return {
            "hoster_id": hoster_id,
            "customer_count": customer_count,
            "earnings_cents": earnings_cents,
            "earnings_dollars": earnings_cents / 100
        }
    
    def process_hoster_payout(self, hoster_id: str) -> dict:
        """Process monthly payout to a hoster."""
        earnings = self.calculate_hoster_earnings(hoster_id)
        
        if earnings["earnings_cents"] == 0:
            return {"success": False, "error": "No earnings to pay out"}
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT stripe_connect_id, payout_method FROM billing_hosters WHERE hoster_id = ?',
                  (hoster_id,))
        row = c.fetchone()
        
        if not row:
            conn.close()
            return {"success": False, "error": "Hoster not found"}
        
        stripe_connect_id, payout_method = row
        
        if payout_method == "stripe" and stripe_connect_id:
            try:
                transfer = stripe.Transfer.create(
                    amount=earnings["earnings_cents"],
                    currency="usd",
                    destination=stripe_connect_id,
                    metadata={
                        "alkaline_hoster_id": hoster_id,
                        "customer_count": earnings["customer_count"],
                        "period": datetime.now().strftime("%Y-%m")
                    }
                )
                
                # Record payout
                payout_id = f"PO-{hoster_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                c.execute('''
                    INSERT INTO payouts (payout_id, hoster_id, amount_cents, customer_count, 
                                         stripe_transfer_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'completed', ?)
                ''', (payout_id, hoster_id, earnings["earnings_cents"], 
                      earnings["customer_count"], transfer.id, datetime.now().timestamp()))
                
                c.execute('''
                    UPDATE billing_hosters 
                    SET total_earned = total_earned + ?, last_payout = ?
                    WHERE hoster_id = ?
                ''', (earnings["earnings_cents"] / 100, datetime.now().timestamp(), hoster_id))
                
                conn.commit()
                conn.close()
                
                return {
                    "success": True,
                    "payout_id": payout_id,
                    "amount": earnings["earnings_dollars"],
                    "transfer_id": transfer.id
                }
                
            except stripe.error.StripeError as e:
                conn.close()
                return {"success": False, "error": str(e)}
        else:
            # Manual payout (PayPal/Venmo)
            payout_id = f"PO-{hoster_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            c.execute('''
                INSERT INTO payouts (payout_id, hoster_id, amount_cents, customer_count, 
                                     status, created_at)
                VALUES (?, ?, ?, ?, 'pending_manual', ?)
            ''', (payout_id, hoster_id, earnings["earnings_cents"], 
                  earnings["customer_count"], datetime.now().timestamp()))
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "payout_id": payout_id,
                "amount": earnings["earnings_dollars"],
                "method": payout_method,
                "status": "pending_manual"
            }
    
    def process_all_payouts(self) -> dict:
        """Process payouts for all active hosters."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT hoster_id FROM billing_hosters WHERE active = 1')
        hosters = [row[0] for row in c.fetchall()]
        conn.close()
        
        results = []
        for hoster_id in hosters:
            result = self.process_hoster_payout(hoster_id)
            results.append({
                "hoster_id": hoster_id,
                **result
            })
        
        return {
            "processed": len(results),
            "results": results
        }
    
    # ============================================
    # WEBHOOK HANDLING
    # ============================================
    
    def handle_webhook(self, payload: bytes, sig_header: str, 
                       webhook_secret: str) -> dict:
        """Handle Stripe webhooks."""
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        except ValueError:
            return {"success": False, "error": "Invalid payload"}
        except stripe.error.SignatureVerificationError:
            return {"success": False, "error": "Invalid signature"}
        
        # Handle event types
        if event['type'] == 'invoice.paid':
            # Subscription payment successful
            invoice = event['data']['object']
            customer_id = invoice['metadata'].get('alkaline_customer_id')
            if customer_id:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute('''
                    UPDATE billing_customers 
                    SET payment_status = 'active', last_payment = ?
                    WHERE customer_id = ?
                ''', (datetime.now().timestamp(), customer_id))
                conn.commit()
                conn.close()
        
        elif event['type'] == 'invoice.payment_failed':
            # Payment failed
            invoice = event['data']['object']
            customer_id = invoice['metadata'].get('alkaline_customer_id')
            if customer_id:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute('''
                    UPDATE billing_customers 
                    SET payment_status = 'past_due'
                    WHERE customer_id = ?
                ''', (customer_id,))
                conn.commit()
                conn.close()
        
        elif event['type'] == 'customer.subscription.deleted':
            # Subscription cancelled
            subscription = event['data']['object']
            customer_id = subscription['metadata'].get('alkaline_customer_id')
            if customer_id:
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute('''
                    UPDATE billing_customers 
                    SET active = 0, payment_status = 'cancelled'
                    WHERE customer_id = ?
                ''', (customer_id,))
                conn.commit()
                conn.close()
        
        return {"success": True, "event_type": event['type']}
    
    # ============================================
    # REPORTING
    # ============================================
    
    def get_revenue_report(self) -> dict:
        """Get revenue summary."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Active customers by tier
        c.execute('''
            SELECT tier, COUNT(*) FROM billing_customers 
            WHERE active = 1 AND payment_status = 'active'
            GROUP BY tier
        ''')
        tier_counts = dict(c.fetchall())
        
        # Calculate MRR
        mrr_cents = sum(
            tier_counts.get(tier, 0) * price 
            for tier, price in PRICING.items()
        )
        
        # Total hoster payouts
        total_customers = sum(tier_counts.values())
        hoster_payouts_cents = total_customers * HOSTER_RATE_CENTS
        
        # Net revenue
        net_cents = mrr_cents - hoster_payouts_cents
        
        conn.close()
        
        return {
            "tier_breakdown": tier_counts,
            "total_customers": total_customers,
            "mrr_cents": mrr_cents,
            "mrr_dollars": mrr_cents / 100,
            "hoster_payouts_cents": hoster_payouts_cents,
            "hoster_payouts_dollars": hoster_payouts_cents / 100,
            "net_revenue_cents": net_cents,
            "net_revenue_dollars": net_cents / 100
        }


# CLI for testing
if __name__ == "__main__":
    print("Alkaline Hosting - Billing System")
    print("=" * 40)
    
    billing = BillingManager("test_billing.db")
    
    # Test hoster registration
    result = billing.register_hoster("H001", "Test Hoster", "hoster@test.com")
    print(f"Register hoster: {result}")
    
    # Test customer creation (will fail without Stripe key)
    print("\nNote: Full testing requires STRIPE_SECRET_KEY environment variable")
    
    # Show revenue report
    report = billing.get_revenue_report()
    print(f"\nRevenue Report:")
    print(f"  Customers: {report['total_customers']}")
    print(f"  MRR: ${report['mrr_dollars']:.2f}")
    print(f"  Hoster Payouts: ${report['hoster_payouts_dollars']:.2f}")
    print(f"  Net Revenue: ${report['net_revenue_dollars']:.2f}")
    
    # Cleanup
    os.remove("test_billing.db")
    print("\n✓ Billing system ready!")
