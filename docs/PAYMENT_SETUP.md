# Alkaline Network - Payment Setup Guide

This guide walks you through setting up Stripe for customer billing and gateway host payouts.

## Overview

**Money Flow:**
```
Customer pays $7.99/mo
        ↓
    Stripe
        ↓
    Your Stripe Account
        ↓
    $2/customer → Gateway Host (via Stripe Connect)
    $5.99/customer → You (minus Stripe fees ~3%)
        ↓
    Your Bank Account (via Stripe Payouts)
```

**Stripe Fees (approximate):**
- 2.9% + $0.30 per customer charge
- For $7.99: ~$0.53 in fees
- Your net per customer: ~$5.46 after gateway payout and fees

## Step 1: Create Stripe Account

1. Go to https://stripe.com and sign up
2. Complete identity verification (takes 1-2 days)
3. Add your bank account for payouts

## Step 2: Get API Keys

1. Go to https://dashboard.stripe.com/apikeys
2. Copy your **Secret Key** (starts with `sk_live_` or `sk_test_`)
3. Save it securely - this is your main API key

## Step 3: Set Up Webhook

1. Go to https://dashboard.stripe.com/webhooks
2. Click "Add endpoint"
3. Enter your URL: `https://your-server.com:8080/webhook/stripe`
4. Select these events:
   - `checkout.session.completed`
   - `invoice.paid`
   - `invoice.payment_failed`
   - `customer.subscription.deleted`
5. Click "Add endpoint"
6. Copy the **Signing Secret** (starts with `whsec_`)

## Step 4: Enable Stripe Connect (for gateway payouts)

1. Go to https://dashboard.stripe.com/connect/accounts/overview
2. Click "Get started" to enable Connect
3. Choose "Express" account type (easiest for gateway hosts)
4. Complete the Connect setup

## Step 5: Configure Alkaline

Set these environment variables on your server:

```bash
# Add to /etc/environment or ~/.bashrc
export STRIPE_SECRET_KEY="sk_live_your_key_here"
export STRIPE_WEBHOOK_SECRET="whsec_your_secret_here"
```

Or create a config file at `/etc/alkaline/stripe.env`:
```bash
STRIPE_SECRET_KEY=sk_live_your_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_secret_here
```

Then source it before running:
```bash
source /etc/alkaline/stripe.env
python alkaline_dashboard.py
```

## Step 6: Test the Setup

### Test Mode
Use test keys (starting with `sk_test_`) first:
- Test card: `4242 4242 4242 4242` (any future date, any CVC)
- This won't charge real money

### Verify Webhook
```bash
# Check Stripe is receiving webhooks
python alkaline_billing.py --summary
```

### Create Test Customer
1. Add a customer in the dashboard
2. Run: `python alkaline_billing.py --status CUSTOMER_ID`

## Step 7: Gateway Host Onboarding

When a gateway host signs up:

1. Add them in the dashboard with their email
2. Run the onboarding:
   ```python
   from alkaline_billing import BillingDatabase, StripePayments
   
   db = BillingDatabase()
   payments = StripePayments(db)
   
   # Get onboarding link
   link = payments.get_connect_onboarding_link(
       "gateway_001",
       return_url="https://your-site.com/gateway/setup-complete",
       refresh_url="https://your-site.com/gateway/setup"
   )
   print(f"Send this to gateway host: {link}")
   ```
3. Gateway host clicks link, enters their bank info
4. Once verified, they'll receive payouts automatically

## Step 8: Monthly Billing

Set up a cron job to run billing on the 1st of each month:

```bash
# Add to crontab (crontab -e)
0 0 1 * * cd /opt/alkaline && source /etc/alkaline/stripe.env && python alkaline_billing.py --run-billing >> /var/log/alkaline/billing.log 2>&1
```

This will:
1. Charge all active customers
2. Mark failed payments (customer loses internet)
3. Calculate gateway payouts
4. Send payouts to gateway hosts
5. Sync active customers to tunnel server

## Pricing Breakdown

**Customer pays $7.99/mo (with $100 deposit):**
```
$7.99 gross
-$0.53 Stripe fees (2.9% + $0.30)
-$2.00 Gateway host payout
= $5.46 net to you
```

**Customer pays $14.99/mo (no deposit):**
```
$14.99 gross
-$0.73 Stripe fees
-$2.00 Gateway host payout
= $12.26 net to you
```

## Withdrawing to Your Bank

Stripe automatically pays out to your bank account:
- Default: 2-day rolling payouts
- Can change to weekly/monthly in Stripe dashboard
- Go to https://dashboard.stripe.com/settings/payouts

## Handling Disputes

If a customer disputes a charge:
1. You'll get an email from Stripe
2. Go to https://dashboard.stripe.com/disputes
3. Provide evidence (service logs, terms of service)
4. Stripe decides within 60-75 days

## Security Notes

- **Never share your secret key**
- Use environment variables, not hardcoded keys
- Test mode keys start with `sk_test_`, live keys with `sk_live_`
- Rotate keys if compromised: Dashboard → API Keys → Roll Key

## Troubleshooting

**"Stripe not configured"**
- Check environment variables are set
- Run: `echo $STRIPE_SECRET_KEY` (should show your key)

**Webhook not working**
- Check URL is accessible from internet (not localhost)
- Verify webhook secret matches
- Check Stripe dashboard for failed webhook attempts

**Payout failed**
- Gateway host needs to complete Stripe Connect onboarding
- Check their account status in Stripe Connect dashboard

## Support

- Stripe Docs: https://stripe.com/docs
- Stripe Support: https://support.stripe.com
