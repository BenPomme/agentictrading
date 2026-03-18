# Betfair Client Certificates

This directory must contain your Betfair API client certificate files for Betfair paper/live trading.

## Required Files

Betfair requires X.509 client certificates for API authentication.

To obtain:
1. Log in to betfair.com
2. Go to: Account > Settings > API Access
3. Download your application certificate (`client-2048.crt`, `client-2048.key`, or similar)
4. Place the certificate and key files in this directory

## Expected Filenames (may vary)

- `client-2048.crt` — public certificate
- `client-2048.key` — private key
- Or: `betfair_cert.pem` + `betfair_key.pem`

## Configuration

`BF_CERTS_PATH=./certs` in .env.staging points here.

## Status

Betfair is currently BLOCKED for paper trading until these certificates are installed.
All other venues (Binance, Polymarket, Yahoo/Alpaca) are active.
