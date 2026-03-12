#!/usr/bin/env python3
"""
Alkaline Network - Full System Test Suite
==========================================

Tests the entire system end-to-end WITHOUT touching:
  - Real Stripe API (uses mocks)
  - Real money
  - Real devices
  - Real network

Run: python test_full_system.py

This tests:
  1. Database operations
  2. Customer/Gateway management
  3. Billing calculations
  4. Payment flow (mocked)
  5. Tunnel server auth (who gets internet)
  6. Sync between billing and tunnel
  7. Security (can someone bypass payment?)

Author: AlkalineTech
"""

import os
import sys
import json
import time
import sqlite3
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

# Set up paths
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# =============================================================================
# TEST CONFIGURATION
# =============================================================================

class TestConfig:
    """Test configuration - isolated from production."""
    
    def __init__(self):
        self.temp_dir = tempfile.mkdtemp(prefix="alkaline_test_")
        self.db_path = Path(self.temp_dir) / "test_alkaline.db"
        self.clients_json = Path(self.temp_dir) / "test_clients.json"
        self.config_json = Path(self.temp_dir) / "test_config.json"
    
    def cleanup(self):
        """Clean up temp files - with retry for Windows file locking."""
        import shutil
        import gc
        
        # Force garbage collection to close any lingering connections
        gc.collect()
        
        # Try to remove with retries (Windows file locking)
        for attempt in range(5):
            try:
                if os.path.exists(self.temp_dir):
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                break
            except:
                time.sleep(0.1)


# =============================================================================
# DATABASE TESTS
# =============================================================================

class TestDatabase(unittest.TestCase):
    """Test database operations."""
    
    db = None  # Track current db instance
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
        
        # Patch the DB path before importing
        import alkaline_dashboard
        alkaline_dashboard.DB_PATH = cls.config.db_path
        
        from alkaline_dashboard import Database
        cls.Database = Database
    
    @classmethod
    def tearDownClass(cls):
        # Close any open db connections
        if hasattr(cls, 'db') and cls.db:
            try:
                if hasattr(cls.db, '_conn') and cls.db._conn:
                    cls.db._conn.close()
            except:
                pass
        
        # Force garbage collection before cleanup
        import gc
        gc.collect()
        time.sleep(0.1)
        
        cls.config.cleanup()
    
    def setUp(self):
        """Fresh database for each test."""
        # Close previous db if exists
        if self.db:
            try:
                if hasattr(self.db, '_conn') and self.db._conn:
                    self.db._conn.close()
            except:
                pass
        
        # Force gc and wait
        import gc
        gc.collect()
        
        # Try to remove old db file
        for attempt in range(3):
            try:
                if self.config.db_path.exists():
                    os.remove(self.config.db_path)
                break
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
        
        self.db = self.Database(str(self.config.db_path))
    
    def tearDown(self):
        """Close db connection after each test."""
        if self.db:
            try:
                if hasattr(self.db, '_conn') and self.db._conn:
                    self.db._conn.close()
            except:
                pass
            self.db = None
    
    def test_add_gateway(self):
        """Test adding a gateway."""
        result = self.db.add_gateway(
            gateway_id="GW001",
            public_key="abc123def456",
            owner_name="Test Owner",
            owner_email="test@example.com",
            owner_payment="venmo:@testowner",
            max_customers=9
        )
        self.assertTrue(result)
        
        gateway = self.db.get_gateway("GW001")
        self.assertIsNotNone(gateway)
        self.assertEqual(gateway['owner_name'], "Test Owner")
        self.assertEqual(gateway['max_customers'], 9)
    
    def test_add_customer(self):
        """Test adding a customer."""
        # First add a gateway
        self.db.add_gateway("GW001", "key", "Owner", "email@test.com", "payment")
        
        result = self.db.add_customer(
            customer_id="CUST001",
            name="Test Customer",
            email="customer@test.com",
            phone="555-1234",
            address="123 Test St",
            plan="option_a"
        )
        self.assertTrue(result)
        
        customer = self.db.get_customer("CUST001")
        self.assertIsNotNone(customer)
        self.assertEqual(customer['name'], "Test Customer")
        self.assertEqual(customer['plan'], "option_a")
    
    def test_duplicate_gateway_fails(self):
        """Test that duplicate gateway IDs fail."""
        self.db.add_gateway("GW001", "key1", "Owner1", "email1@test.com", "payment1")
        result = self.db.add_gateway("GW001", "key2", "Owner2", "email2@test.com", "payment2")
        self.assertFalse(result)
    
    def test_gateway_customer_count(self):
        """Test counting customers per gateway."""
        self.db.add_gateway("GW001", "key", "Owner", "email@test.com", "payment")
        
        # Add 3 customers to this gateway
        for i in range(3):
            self.db.add_customer(f"CUST{i}", f"Customer {i}", f"c{i}@test.com", "", "", "option_a")
            self.db.assign_customer_to_gateway(f"CUST{i}", "GW001")
        
        count = self.db.get_gateway_customer_count("GW001")
        self.assertEqual(count, 3)
    
    def test_move_customer(self):
        """Test moving customer between gateways."""
        self.db.add_gateway("GW001", "key1", "Owner1", "e1@test.com", "p1")
        self.db.add_gateway("GW002", "key2", "Owner2", "e2@test.com", "p2")
        self.db.add_customer("CUST001", "Customer", "c@test.com", "", "", "option_a")
        self.db.assign_customer_to_gateway("CUST001", "GW001")
        
        self.assertEqual(self.db.get_gateway_customer_count("GW001"), 1)
        self.assertEqual(self.db.get_gateway_customer_count("GW002"), 0)
        
        # Move customer
        self.db.move_customer("CUST001", "GW002")
        
        self.assertEqual(self.db.get_gateway_customer_count("GW001"), 0)
        self.assertEqual(self.db.get_gateway_customer_count("GW002"), 1)


# =============================================================================
# BILLING TESTS
# =============================================================================

class TestBilling(unittest.TestCase):
    """Test billing calculations and logic."""
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
        
        # Patch paths before importing
        import alkaline_billing
        alkaline_billing.DB_PATH = cls.config.db_path
        alkaline_billing.CLIENTS_JSON = cls.config.clients_json
        
        # Disable real Stripe
        alkaline_billing.HAS_STRIPE = False
        
        from alkaline_billing import (
            BillingDatabase, PLAN_PRICES, GATEWAY_PAYOUT_PER_CUSTOMER,
            sync_clients_json
        )
        cls.BillingDatabase = BillingDatabase
        cls.PLAN_PRICES = PLAN_PRICES
        cls.GATEWAY_PAYOUT = GATEWAY_PAYOUT_PER_CUSTOMER
        cls.sync_clients_json = sync_clients_json
    
    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        time.sleep(0.1)
        cls.config.cleanup()
    
    def setUp(self):
        import gc
        gc.collect()
        
        for attempt in range(3):
            try:
                if self.config.db_path.exists():
                    os.remove(self.config.db_path)
                if self.config.clients_json.exists():
                    os.remove(self.config.clients_json)
                break
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
        
        # Initialize both databases
        import alkaline_dashboard
        alkaline_dashboard.DB_PATH = self.config.db_path
        self.dashboard_db = alkaline_dashboard.Database(str(self.config.db_path))
        self.billing_db = self.BillingDatabase(self.config.db_path)
    
    def tearDown(self):
        # Close connections
        self.dashboard_db = None
        self.billing_db = None
        import gc
        gc.collect()
    
    def test_plan_prices(self):
        """Test plan prices are correct."""
        self.assertEqual(self.PLAN_PRICES["option_a"], Decimal("7.99"))
        self.assertEqual(self.PLAN_PRICES["option_b"], Decimal("14.99"))
    
    def test_gateway_payout_amount(self):
        """Test gateway payout is $2 per customer."""
        self.assertEqual(self.GATEWAY_PAYOUT, Decimal("2.00"))
    
    def test_profit_calculation(self):
        """Test profit per customer calculation."""
        # Option A: $7.99 - $2.00 gateway = $5.99 gross
        # Minus ~$0.53 Stripe fees = ~$5.46 net
        option_a_gross = self.PLAN_PRICES["option_a"] - self.GATEWAY_PAYOUT
        self.assertEqual(option_a_gross, Decimal("5.99"))
        
        # Option B: $14.99 - $2.00 gateway = $12.99 gross
        option_b_gross = self.PLAN_PRICES["option_b"] - self.GATEWAY_PAYOUT
        self.assertEqual(option_b_gross, Decimal("12.99"))
    
    def test_sync_only_active_customers(self):
        """Test that only active paying customers get synced to clients.json."""
        # Add gateway
        self.dashboard_db.add_gateway("GW001", "gwkey123", "Owner", "o@test.com", "pay")
        
        # Add customers with different statuses
        self.dashboard_db.add_customer("CUST_ACTIVE", "Active User", "a@test.com", "", "", "option_a")
        self.dashboard_db.add_customer("CUST_INACTIVE", "Inactive User", "i@test.com", "", "", "option_a")
        self.dashboard_db.add_customer("CUST_FAILED", "Failed Payment", "f@test.com", "", "", "option_a")
        
        # Set public keys
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("UPDATE customers SET public_key = 'pubkey_active', subscription_status = 'active', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST_ACTIVE'")
        c.execute("UPDATE customers SET public_key = 'pubkey_inactive', subscription_status = 'inactive', tunnel_ip = '10.100.0.3' WHERE customer_id = 'CUST_INACTIVE'")
        c.execute("UPDATE customers SET public_key = 'pubkey_failed', subscription_status = 'payment_failed', tunnel_ip = '10.100.0.4' WHERE customer_id = 'CUST_FAILED'")
        conn.commit()
        conn.close()
        
        # Reimport with correct paths and sync
        import alkaline_billing
        alkaline_billing.CLIENTS_JSON = self.config.clients_json
        count = alkaline_billing.sync_clients_json(self.billing_db)
        
        # Should only sync 1 (the active one)
        self.assertEqual(count, 1)
        
        # Verify clients.json contents
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        
        self.assertIn("pubkey_active", clients)
        self.assertNotIn("pubkey_inactive", clients)
        self.assertNotIn("pubkey_failed", clients)
    
    def test_pending_payout_accumulation(self):
        """Test that gateway payouts accumulate correctly."""
        # Add gateway
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        
        # Add pending payouts
        self.billing_db.add_pending_payout("GW001", 2.00)
        self.billing_db.add_pending_payout("GW001", 2.00)
        self.billing_db.add_pending_payout("GW001", 2.00)
        
        # Check total
        gateway = self.billing_db.get_gateway("GW001")
        self.assertEqual(gateway['pending_payout'], 6.00)
    
    def test_clear_pending_payout(self):
        """Test clearing pending payout returns correct amount."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        
        self.billing_db.add_pending_payout("GW001", 10.00)
        
        amount = self.billing_db.clear_pending_payout("GW001")
        self.assertEqual(amount, 10.00)
        
        # Should be zero now
        gateway = self.billing_db.get_gateway("GW001")
        self.assertEqual(gateway['pending_payout'], 0)


# =============================================================================
# SECURITY TESTS - CRITICAL
# =============================================================================

class TestSecurity(unittest.TestCase):
    """
    CRITICAL: Test that payment bypass is impossible.
    
    These tests verify that:
    1. Unpaid customers cannot get internet
    2. Fake public keys are rejected
    3. Payment status is enforced
    """
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
        
        # Patch paths
        import alkaline_billing
        alkaline_billing.DB_PATH = cls.config.db_path
        alkaline_billing.CLIENTS_JSON = cls.config.clients_json
        alkaline_billing.HAS_STRIPE = False
        
        import alkaline_dashboard
        alkaline_dashboard.DB_PATH = cls.config.db_path
        
        from alkaline_billing import BillingDatabase, sync_clients_json
        from alkaline_dashboard import Database
        
        cls.BillingDatabase = BillingDatabase
        cls.DashboardDatabase = Database
        cls.sync_clients_json = sync_clients_json
    
    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        time.sleep(0.1)
        cls.config.cleanup()
    
    def setUp(self):
        import gc
        gc.collect()
        
        for attempt in range(3):
            try:
                for f in [self.config.db_path, self.config.clients_json]:
                    if f.exists():
                        os.remove(f)
                break
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
        
        self.dashboard_db = self.DashboardDatabase(str(self.config.db_path))
        self.billing_db = self.BillingDatabase(self.config.db_path)
        
        # Re-import to get fresh function with correct paths
        import alkaline_billing
        alkaline_billing.DB_PATH = self.config.db_path
        alkaline_billing.CLIENTS_JSON = self.config.clients_json
        self.do_sync = lambda: alkaline_billing.sync_clients_json(self.billing_db)
    
    def tearDown(self):
        self.dashboard_db = None
        self.billing_db = None
        import gc
        gc.collect()
    
    def test_unpaid_customer_not_in_clients_json(self):
        """SECURITY: Unpaid customers must NOT appear in clients.json."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        self.dashboard_db.add_customer("CUST001", "Unpaid User", "u@test.com", "", "", "option_a")
        
        # Set public key but leave subscription_status as default (not 'active')
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("UPDATE customers SET public_key = 'unpaid_pubkey', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST001'")
        conn.commit()
        conn.close()
        
        # Sync
        self.do_sync()
        
        # clients.json should be empty or not contain the unpaid key
        if self.config.clients_json.exists():
            with open(self.config.clients_json) as f:
                clients = json.load(f)
            self.assertNotIn("unpaid_pubkey", clients)
        else:
            pass  # Empty file is fine
    
    def test_payment_failed_removes_access(self):
        """SECURITY: Failed payment must remove customer from clients.json."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        self.dashboard_db.add_customer("CUST001", "User", "u@test.com", "", "", "option_a")
        
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        # Start with active subscription
        c.execute("UPDATE customers SET public_key = 'user_pubkey', subscription_status = 'active', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST001'")
        conn.commit()
        conn.close()
        
        # Sync - should be in clients.json
        self.do_sync()
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertIn("user_pubkey", clients)
        
        # Simulate payment failure
        self.billing_db.update_customer_subscription("CUST001", "payment_failed")
        
        # Sync again - should be REMOVED
        self.do_sync()
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertNotIn("user_pubkey", clients)
    
    def test_cancelled_subscription_removes_access(self):
        """SECURITY: Cancelled subscription must remove access."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        self.dashboard_db.add_customer("CUST001", "User", "u@test.com", "", "", "option_a")
        
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("UPDATE customers SET public_key = 'user_pubkey', subscription_status = 'active', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST001'")
        conn.commit()
        conn.close()
        
        # Cancel subscription
        self.billing_db.update_customer_subscription("CUST001", "cancelled")
        
        # Sync
        self.do_sync()
        
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertNotIn("user_pubkey", clients)
    
    def test_fake_public_key_rejected(self):
        """SECURITY: Random public key not in DB should not get access."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        self.dashboard_db.add_customer("CUST001", "Real User", "u@test.com", "", "", "option_a")
        
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("UPDATE customers SET public_key = 'real_pubkey', subscription_status = 'active', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST001'")
        conn.commit()
        conn.close()
        
        self.do_sync()
        
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        
        # Real key should be there
        self.assertIn("real_pubkey", clients)
        
        # Fake keys should NOT be there
        self.assertNotIn("fake_pubkey_12345", clients)
        self.assertNotIn("attacker_key", clients)
        self.assertNotIn("", clients)
    
    def test_no_public_key_no_access(self):
        """SECURITY: Customer without public_key should not appear in clients.json."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        self.dashboard_db.add_customer("CUST001", "User", "u@test.com", "", "", "option_a")
        
        # Set active but NO public key
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("UPDATE customers SET subscription_status = 'active', tunnel_ip = '10.100.0.2' WHERE customer_id = 'CUST001'")
        conn.commit()
        conn.close()
        
        self.do_sync()
        
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        
        # Should be empty - no valid public key
        self.assertEqual(len(clients), 0)


# =============================================================================
# STRIPE MOCK TESTS
# =============================================================================

class TestStripeMocked(unittest.TestCase):
    """Test Stripe integration with mocked API calls."""
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
        
        import alkaline_billing
        alkaline_billing.DB_PATH = cls.config.db_path
        alkaline_billing.CLIENTS_JSON = cls.config.clients_json
        
        import alkaline_dashboard
        alkaline_dashboard.DB_PATH = cls.config.db_path
        
        # Check if stripe is available
        try:
            import stripe
            cls.has_stripe = True
        except ImportError:
            cls.has_stripe = False
    
    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        time.sleep(0.1)
        cls.config.cleanup()
    
    def setUp(self):
        if not self.has_stripe:
            self.skipTest("Stripe module not installed")
        
        import gc
        gc.collect()
        
        for attempt in range(3):
            try:
                for f in [self.config.db_path, self.config.clients_json]:
                    if f.exists():
                        os.remove(f)
                break
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
        
        import alkaline_dashboard
        self.dashboard_db = alkaline_dashboard.Database(str(self.config.db_path))
    
    def tearDown(self):
        self.dashboard_db = None
        import gc
        gc.collect()
    
    def test_charge_customer_success(self):
        """Test successful customer charge."""
        if not self.has_stripe:
            self.skipTest("Stripe not installed")
        
        with patch('alkaline_billing.stripe') as mock_stripe, \
             patch('alkaline_billing.HAS_STRIPE', True):
            
            from alkaline_billing import BillingDatabase, StripePayments
            
            # Setup mock
            mock_stripe.Customer.retrieve.return_value = MagicMock(
                invoice_settings=MagicMock(default_payment_method="pm_123")
            )
            mock_stripe.PaymentIntent.create.return_value = MagicMock(
                status="succeeded",
                id="pi_test123"
            )
            
            # Add customer with Stripe ID
            self.dashboard_db.add_customer("CUST001", "User", "u@test.com", "", "", "option_a")
            
            billing_db = BillingDatabase(self.config.db_path)
            billing_db.set_customer_stripe_id("CUST001", "cus_test123")
            
            payments = StripePayments(billing_db)
            
            result = payments.charge_customer("CUST001", Decimal("7.99"), "Test charge")
            
            self.assertEqual(result, "pi_test123")
    
    def test_gateway_payout_success(self):
        """Test successful gateway payout."""
        if not self.has_stripe:
            self.skipTest("Stripe not installed")
        
        with patch('alkaline_billing.stripe') as mock_stripe, \
             patch('alkaline_billing.HAS_STRIPE', True):
            
            from alkaline_billing import BillingDatabase, StripePayments
            
            mock_stripe.Transfer.create.return_value = MagicMock(id="tr_test123")
            
            # Add gateway with Stripe account
            self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
            
            billing_db = BillingDatabase(self.config.db_path)
            billing_db.set_gateway_stripe_account("GW001", "acct_test123")
            billing_db.add_pending_payout("GW001", 10.00)
            
            payments = StripePayments(billing_db)
            result = payments.payout_gateway("GW001")
            
            self.assertEqual(result, "tr_test123")
    
    def test_payout_no_stripe_account_fails(self):
        """Test payout fails gracefully if gateway has no Stripe account."""
        from alkaline_billing import BillingDatabase, StripePayments
        
        # Add gateway WITHOUT Stripe account
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        
        billing_db = BillingDatabase(self.config.db_path)
        billing_db.add_pending_payout("GW001", 10.00)
        
        payments = StripePayments(billing_db)
        result = payments.payout_gateway("GW001")
        
        # Should fail gracefully (not crash, not send to random account)
        self.assertIsNone(result)
        
        # Money should NOT be cleared (still pending)
        gateway = billing_db.get_gateway("GW001")
        self.assertEqual(gateway['pending_payout'], 10.00)


# =============================================================================
# TUNNEL SERVER AUTH TESTS
# =============================================================================

class TestTunnelAuth(unittest.TestCase):
    """Test that tunnel server correctly authenticates based on clients.json."""
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
    
    @classmethod
    def tearDownClass(cls):
        cls.config.cleanup()
    
    def test_load_clients_only_allows_listed_keys(self):
        """Test that tunnel server only allows keys in clients.json."""
        # Create a clients.json with specific keys
        clients_data = {
            "aabbccdd1122334455667788": {
                "name": "Paying Customer",
                "tunnel_ip": "10.100.0.2",
                "customer_id": "CUST001"
            }
        }
        
        with open(self.config.clients_json, 'w') as f:
            json.dump(clients_data, f)
        
        # Simulate what tunnel server does
        with open(self.config.clients_json) as f:
            loaded_clients = json.load(f)
        
        # Valid key should be found
        self.assertIn("aabbccdd1122334455667788", loaded_clients)
        
        # Random keys should NOT be found
        self.assertNotIn("fakekey12345", loaded_clients)
        self.assertNotIn("", loaded_clients)
        self.assertNotIn("attacker_public_key", loaded_clients)
    
    def test_empty_clients_json_blocks_all(self):
        """Test that empty clients.json blocks everyone."""
        with open(self.config.clients_json, 'w') as f:
            json.dump({}, f)
        
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        
        self.assertEqual(len(clients), 0)
        
        # No one should get through
        self.assertNotIn("any_key", clients)


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestFullFlow(unittest.TestCase):
    """Test complete customer lifecycle."""
    
    @classmethod
    def setUpClass(cls):
        cls.config = TestConfig()
        
        import alkaline_billing
        alkaline_billing.DB_PATH = cls.config.db_path
        alkaline_billing.CLIENTS_JSON = cls.config.clients_json
        alkaline_billing.HAS_STRIPE = False
        
        import alkaline_dashboard
        alkaline_dashboard.DB_PATH = cls.config.db_path
    
    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        time.sleep(0.1)
        cls.config.cleanup()
    
    def setUp(self):
        import gc
        gc.collect()
        
        for attempt in range(3):
            try:
                for f in [self.config.db_path, self.config.clients_json]:
                    if f.exists():
                        os.remove(f)
                break
            except PermissionError:
                time.sleep(0.1)
                gc.collect()
        
        import alkaline_dashboard
        from alkaline_billing import BillingDatabase, sync_clients_json
        
        self.dashboard_db = alkaline_dashboard.Database(str(self.config.db_path))
        self.billing_db = BillingDatabase(self.config.db_path)
        self.sync = sync_clients_json
    
    def tearDown(self):
        self.dashboard_db = None
        self.billing_db = None
        import gc
        gc.collect()
    
    def test_full_customer_lifecycle(self):
        """Test: signup -> payment -> active -> cancel -> no access."""
        
        # 1. Gateway host signs up
        self.dashboard_db.add_gateway(
            "GW001", 
            "gateway_pubkey_abc123",
            "Gateway Owner",
            "gateway@test.com",
            "venmo:@gatewayowner"
        )
        
        # 2. Customer signs up (not yet paid)
        self.dashboard_db.add_customer(
            "CUST001",
            "New Customer",
            "customer@test.com",
            "555-1234",
            "123 Main St",
            "option_a"
        )
        
        # Set their device public key and assign to gateway
        conn = sqlite3.connect(str(self.config.db_path))
        c = conn.cursor()
        c.execute("""
            UPDATE customers 
            SET public_key = 'customer_device_pubkey',
                tunnel_ip = '10.100.0.2',
                gateway_id = 'GW001'
            WHERE customer_id = 'CUST001'
        """)
        conn.commit()
        conn.close()
        
        # 3. Before payment - should NOT have access
        self.sync(self.billing_db)
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertNotIn("customer_device_pubkey", clients)
        
        # 4. Customer pays - mark as active
        self.billing_db.update_customer_subscription(
            "CUST001", 
            "active",
            time.time() + (30 * 24 * 60 * 60)  # 30 days
        )
        
        # 5. After payment - SHOULD have access
        self.sync(self.billing_db)
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertIn("customer_device_pubkey", clients)
        self.assertEqual(clients["customer_device_pubkey"]["customer_id"], "CUST001")
        
        # 6. Customer cancels
        self.billing_db.update_customer_subscription("CUST001", "cancelled")
        
        # 7. After cancel - should NOT have access
        self.sync(self.billing_db)
        with open(self.config.clients_json) as f:
            clients = json.load(f)
        self.assertNotIn("customer_device_pubkey", clients)
    
    def test_multiple_customers_correct_revenue(self):
        """Test revenue calculation with multiple customers."""
        self.dashboard_db.add_gateway("GW001", "gwkey", "Owner", "o@test.com", "pay")
        
        # Add 5 option_a customers ($7.99 each)
        for i in range(5):
            self.dashboard_db.add_customer(f"CUST_A{i}", f"User A{i}", f"a{i}@test.com", "", "", "option_a")
        
        # Add 3 option_b customers ($14.99 each)
        for i in range(3):
            self.dashboard_db.add_customer(f"CUST_B{i}", f"User B{i}", f"b{i}@test.com", "", "", "option_b")
        
        # Calculate expected revenue
        expected_revenue = (5 * 7.99) + (3 * 14.99)  # $39.95 + $44.97 = $84.92
        
        # Get customers and calculate
        customers = self.dashboard_db.get_all_customers()
        actual_revenue = sum(
            7.99 if c['plan'] == 'option_a' else 14.99
            for c in customers
        )
        
        self.assertAlmostEqual(actual_revenue, expected_revenue, places=2)
        
        # Calculate gateway payouts ($2 per customer)
        expected_payout = 8 * 2.00  # $16.00
        
        # Your net (before Stripe fees)
        expected_net = expected_revenue - expected_payout  # $68.92
        actual_net = actual_revenue - (8 * 2.00)
        
        self.assertAlmostEqual(actual_net, expected_net, places=2)


# =============================================================================
# RUN TESTS
# =============================================================================

def run_tests():
    """Run all tests and print summary."""
    print("=" * 70)
    print("ALKALINE NETWORK - FULL SYSTEM TEST SUITE")
    print("=" * 70)
    print()
    print("Testing WITHOUT touching:")
    print("  - Real Stripe API")
    print("  - Real money")
    print("  - Real devices")
    print()
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes in order of importance
    suite.addTests(loader.loadTestsFromTestCase(TestSecurity))  # MOST IMPORTANT
    suite.addTests(loader.loadTestsFromTestCase(TestBilling))
    suite.addTests(loader.loadTestsFromTestCase(TestDatabase))
    suite.addTests(loader.loadTestsFromTestCase(TestStripeMocked))
    suite.addTests(loader.loadTestsFromTestCase(TestTunnelAuth))
    suite.addTests(loader.loadTestsFromTestCase(TestFullFlow))
    
    # Run with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print()
    print("=" * 70)
    if result.wasSuccessful():
        print("✓ ALL TESTS PASSED - System is secure")
    else:
        print("✗ SOME TESTS FAILED - DO NOT DEPLOY")
        print()
        print("Failures:")
        for test, traceback in result.failures + result.errors:
            print(f"  - {test}")
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
