#!/usr/bin/env python3
"""
Enable Banking Client (enablebanking-client)
--------------------------------------------
Designed for both interactive personal use and automated 24/7 AI agent (Hermes) execution.

Usage:
  # 1. Generate authorization URL (user logs in via browser):
  python enable_banking.py auth

  # 2. Complete authorization using the code returned in the callback URL:
  python enable_banking.py session --code "<AUTH_CODE_OR_CALLBACK_URL>"

  # 3. Check current session status and linked accounts:
  python enable_banking.py session

  # 4. Fetch balances:
  python enable_banking.py balances

  # 5. Fetch transactions (JSON or CSV):
  python enable_banking.py transactions [--days 90] [--csv output.csv]

  # 6. Hermes tool helper: output clean JSON for AI consumption
  python enable_banking.py json
"""

import argparse
import csv
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import jwt
import requests

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
KEY_FILE = BASE_DIR / "private.pem"
SESSION_FILE = BASE_DIR / "session.json"

API_BASE_URL = "https://api.enablebanking.com"
DEFAULT_BANK_NAME = "ABN AMRO"
DEFAULT_BANK_COUNTRY = "NL"


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val


def load_config() -> dict:
    load_env_file()
    app_id = os.getenv("ENABLE_BANKING_APP_ID")
    redirect_url = os.getenv("ENABLE_BANKING_REDIRECT_URL")
    key_path_env = os.getenv("ENABLE_BANKING_KEY_PATH")
    bank_name = os.getenv("ENABLE_BANKING_BANK")
    bank_country = os.getenv("ENABLE_BANKING_COUNTRY")

    key_path = Path(key_path_env) if key_path_env else KEY_FILE

    if not app_id:
        # Check if an existing session or PEM file has the KID
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r") as f:
                    s_data = json.load(f)
                    app_id = s_data.get("app_id") or s_data.get("kid")
                    if not bank_name and s_data.get("aspsp", {}).get("name"):
                        bank_name = s_data["aspsp"]["name"]
                    if not bank_country and s_data.get("aspsp", {}).get("country"):
                        bank_country = s_data["aspsp"]["country"]
            except Exception:
                pass

        if not app_id:
            # Check if there is a single UUID .pem file in directory
            pem_files = list(BASE_DIR.glob("*-*-*-*-*.pem"))
            if pem_files:
                app_id = pem_files[0].stem

    if not app_id:
        raise ValueError(
            "Application ID (app_id / kid) not found. "
            "Please provide it in .env or via the ENABLE_BANKING_APP_ID env variable."
        )

    if not redirect_url:
        redirect_url = "https://localhost/callback"

    if not bank_name:
        bank_name = DEFAULT_BANK_NAME
    if not bank_country:
        bank_country = DEFAULT_BANK_COUNTRY

    if not key_path.exists():
        # Fallback to <app_id>.pem if private.pem does not exist
        fallback_key = BASE_DIR / f"{app_id}.pem"
        if fallback_key.exists():
            key_path = fallback_key
        else:
            raise FileNotFoundError(
                f"Private key file not found at {key_path}. "
                "Ensure your RSA private key (e.g. private.pem) is placed in this directory "
                "or set via ENABLE_BANKING_KEY_PATH."
            )

    with open(key_path, "r") as f:
        private_key = f.read().strip()

    return {
        "app_id": app_id,
        "redirect_url": redirect_url,
        "private_key": private_key,
        "bank_name": bank_name,
        "bank_country": bank_country,
    }


def generate_jwt(app_id: str, private_key: str, ttl: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + ttl,
    }
    jwt_headers = {
        "alg": "RS256",
        "kid": app_id,
        "typ": "JWT",
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers=jwt_headers)


def get_headers(cfg: dict) -> dict:
    token = generate_jwt(cfg["app_id"], cfg["private_key"])
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def load_session() -> dict | None:
    if not SESSION_FILE.exists():
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_session(session_data: dict) -> None:
    with open(SESSION_FILE, "w") as f:
        json.dump(session_data, f, indent=2)


def start_auth(cfg: dict, bank_name: str | None = None, bank_country: str | None = None, psu_type: str = "personal") -> dict:
    headers = get_headers(cfg)
    target_bank = bank_name or cfg.get("bank_name", DEFAULT_BANK_NAME)
    target_country = bank_country or cfg.get("bank_country", DEFAULT_BANK_COUNTRY)

    body = {
        "aspsp": {
            "name": target_bank,
            "country": target_country,
        },
        "redirect_url": cfg["redirect_url"],
        "access": {
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=179)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "psu_type": psu_type,
        "state": str(uuid.uuid4()),
    }

    res = requests.post(f"{API_BASE_URL}/auth", json=body, headers=headers)
    if res.status_code != 200:
        raise RuntimeError(f"Start auth failed ({res.status_code}): {res.text}")

    data = res.json()
    return data


def extract_code(raw_input: str) -> str:
    raw_input = raw_input.strip()
    if raw_input.startswith("http://") or raw_input.startswith("https://"):
        parsed = urlparse(raw_input)
        params = parse_qs(parsed.query)
        if "code" in params:
            return params["code"][0]
    return raw_input


def authorize_session(cfg: dict, code: str) -> dict:
    clean_code = extract_code(code)
    headers = get_headers(cfg)
    body = {"code": clean_code}

    res = requests.post(f"{API_BASE_URL}/sessions", json=body, headers=headers)
    if res.status_code != 200:
        raise RuntimeError(f"Session authorization failed ({res.status_code}): {res.text}")

    data = res.json()
    save_session(data)
    return data


def get_session_info(cfg: dict) -> dict:
    session = load_session()
    if not session or "session_id" not in session:
        raise RuntimeError("No active session found in session.json. Run auth and authorize first.")

    session_id = session["session_id"]
    headers = get_headers(cfg)
    res = requests.get(f"{API_BASE_URL}/sessions/{session_id}", headers=headers)
    if res.status_code != 200:
        raise RuntimeError(f"Failed to fetch session ({res.status_code}): {res.text}")
    return res.json()


def get_balances(cfg: dict) -> list[dict]:
    session = load_session()
    if not session or "accounts" not in session:
        raise RuntimeError("No session or accounts found. Please authenticate first.")

    headers = get_headers(cfg)
    results = []
    for acc in session["accounts"]:
        acc_uid = acc.get("uid")
        iban = acc.get("account_id", {}).get("iban", "Unknown")
        name = acc.get("name", iban)

        res = requests.get(f"{API_BASE_URL}/accounts/{acc_uid}/balances", headers=headers)
        if res.status_code == 200:
            bal_data = res.json()
            results.append({
                "account_uid": acc_uid,
                "account_name": name,
                "iban": iban,
                "balances": bal_data.get("balances", []),
            })
        else:
            results.append({
                "account_uid": acc_uid,
                "account_name": name,
                "iban": iban,
                "error": f"{res.status_code}: {res.text}",
            })
    return results


def get_transactions(
    cfg: dict,
    days: int = 90,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    session = load_session()
    if not session or "accounts" not in session:
        raise RuntimeError("No session or accounts found. Please authenticate first.")

    headers = get_headers(cfg)
    if not date_from:
        d_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    else:
        d_from = date_from

    params = {"date_from": d_from}
    if date_to:
        params["date_to"] = date_to

    all_tx_by_account = []
    for acc in session["accounts"]:
        acc_uid = acc.get("uid")
        iban = acc.get("account_id", {}).get("iban", "Unknown")
        name = acc.get("name", iban)

        tx_list = []
        continuation_key = None

        while True:
            cur_params = dict(params)
            if continuation_key:
                cur_params["continuation_key"] = continuation_key

            res = requests.get(
                f"{API_BASE_URL}/accounts/{acc_uid}/transactions",
                headers=headers,
                params=cur_params,
            )
            if res.status_code != 200:
                print(f"Error fetching transactions for {iban}: {res.status_code} - {res.text}", file=sys.stderr)
                break

            data = res.json()
            tx_list.extend(data.get("transactions", []))
            continuation_key = data.get("continuation_key")
            if not continuation_key:
                break

        all_tx_by_account.append({
            "account_uid": acc_uid,
            "account_name": name,
            "iban": iban,
            "count": len(tx_list),
            "transactions": tx_list,
        })

    return all_tx_by_account


def export_transactions_csv(account_data_list: list[dict], filepath: str) -> None:
    rows = []
    for acc in account_data_list:
        iban = acc.get("iban", "")
        for tx in acc.get("transactions", []):
            amount_info = tx.get("transaction_amount", {})
            amount = amount_info.get("amount", "")
            currency = amount_info.get("currency", "EUR")
            indicator = tx.get("credit_debit_indicator", "")

            booking_date = tx.get("booking_date") or tx.get("transaction_date") or tx.get("value_date", "")

            debtor_info = tx.get("debtor") or {}
            creditor_info = tx.get("creditor") or {}
            debtor_acc = tx.get("debtor_account") or {}
            creditor_acc = tx.get("creditor_account") or {}

            counterparty = creditor_info.get("name") if indicator == "DBIT" else debtor_info.get("name")
            if not counterparty:
                counterparty = debtor_acc.get("iban") or creditor_acc.get("iban") or ""

            remittance = " ".join(tx.get("remittance_information", []) or [])
            note = tx.get("note", "")

            rows.append({
                "account_iban": iban,
                "date": booking_date,
                "amount": amount,
                "currency": currency,
                "type": indicator,
                "counterparty": counterparty,
                "remittance": remittance,
                "note": note,
                "status": tx.get("status", ""),
                "entry_reference": tx.get("entry_reference", ""),
            })

    # Sort newest first
    rows.sort(key=lambda r: r["date"] or "", reverse=True)

    fieldnames = [
        "account_iban", "date", "amount", "currency", "type",
        "counterparty", "remittance", "note", "status", "entry_reference"
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Enable Banking Client (enablebanking-client) CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # banks (List available banks for country)
    banks_parser = subparsers.add_parser("banks", help="List supported banks for a country")
    banks_parser.add_argument("--country", default=DEFAULT_BANK_COUNTRY, help=f"Country code (ISO 3166-1 alpha-2, default: {DEFAULT_BANK_COUNTRY})")

    # auth
    auth_parser = subparsers.add_parser("auth", help="Generate authorization URL for bank login")
    auth_parser.add_argument("--bank", help=f"Target bank name (default from config/env, or {DEFAULT_BANK_NAME})")
    auth_parser.add_argument("--country", help=f"Target bank country code (default from config/env, or {DEFAULT_BANK_COUNTRY})")
    auth_parser.add_argument("--psu-type", default="personal", choices=["personal", "business"], help="Account type (default: personal)")

    # session
    session_parser = subparsers.add_parser("session", help="Authorize session with returned code or view current session")
    session_parser.add_argument("--code", help="Authorization code or full callback URL containing ?code=...")

    # balances
    subparsers.add_parser("balances", help="Fetch current balances")

    # transactions
    tx_parser = subparsers.add_parser("transactions", help="Fetch account transactions")
    tx_parser.add_argument("--days", type=int, default=90, help="Days of history to fetch (default: 90)")
    tx_parser.add_argument("--from-date", help="Start date (YYYY-MM-DD)")
    tx_parser.add_argument("--to-date", help="End date (YYYY-MM-DD)")
    tx_parser.add_argument("--csv", help="Export transactions to specified CSV file path")

    # json (Agent tool mode)
    json_parser = subparsers.add_parser("json", help="Dump all current balances and transactions as structured JSON (ideal for AI Agent)")
    json_parser.add_argument("--days", type=int, default=30, help="Days of transaction history to include (default: 30)")

    args = parser.parse_args()
    cfg = load_config()

    if args.command == "banks":
        headers = get_headers(cfg)
        res = requests.get(f"{API_BASE_URL}/aspsps?country={args.country}", headers=headers)
        if res.status_code != 200:
            print(f"Failed to fetch banks for {args.country}: {res.status_code} - {res.text}", file=sys.stderr)
            sys.exit(1)
        aspsps = res.json().get("aspsps", [])
        print(f"\nSupported banks in {args.country} ({len(aspsps)} found):")
        for b in sorted(aspsps, key=lambda x: x.get("name", "")):
            print(f" - {b.get('name')}")
        print()

    elif args.command == "auth":
        bank_name = getattr(args, "bank", None) or cfg.get("bank_name", DEFAULT_BANK_NAME)
        bank_country = getattr(args, "country", None) or cfg.get("bank_country", DEFAULT_BANK_COUNTRY)

        res = start_auth(cfg, bank_name=bank_name, bank_country=bank_country, psu_type=args.psu_type)
        print("\n" + "=" * 64)
        print(f"🔗 {bank_name} ({bank_country}) AUTHORIZATION LINK")
        print("=" * 64)
        print("1. Open this link in your browser:")
        print(f"\n   {res['url']}\n")
        print(f"2. Log into {bank_name} and confirm consent.")
        print("3. You will be redirected to your callback URL.")
        print("=" * 64 + "\n")

        # Interactive convenience: allow user to immediately paste the callback URL or exit
        if sys.stdin.isatty():
            try:
                redirected_url = input("Paste redirected callback URL or code here (or press Enter to finish later): ").strip()
                if redirected_url:
                    session = authorize_session(cfg, redirected_url)
                    print("\n✅ Session successfully authorized and stored in session.json!")
                    print(f"Session ID: {session.get('session_id')}")
                    for acc in session.get("accounts", []):
                        iban = acc.get("account_id", {}).get("iban", "N/A")
                        print(f" - Account: {acc.get('name', 'Main')} | IBAN: {iban} | UID: {acc.get('uid')}")
                    return
            except (KeyboardInterrupt, EOFError):
                print()

        print("To authorize later, run:")
        print("   uv run python enable_banking.py session --code \"<PASTE_CODE_OR_URL_HERE>\"\n")

    elif args.command == "session":
        if args.code:
            print("Authorizing session with provided code...")
            session = authorize_session(cfg, args.code)
            print("✅ Session successfully authorized and stored in session.json!")
            print(f"Session ID: {session.get('session_id')}")
            for acc in session.get("accounts", []):
                iban = acc.get("account_id", {}).get("iban", "N/A")
                print(f" - Account: {acc.get('name', 'Main')} | IBAN: {iban} | UID: {acc.get('uid')}")
        else:
            try:
                info = get_session_info(cfg)
                print(json.dumps(info, indent=2))
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

    elif args.command == "balances":
        balances = get_balances(cfg)
        print(json.dumps(balances, indent=2))

    elif args.command == "transactions":
        tx_data = get_transactions(cfg, days=args.days, date_from=args.from_date, date_to=args.to_date)
        if args.csv:
            export_transactions_csv(tx_data, args.csv)
            total = sum(acc.get("count", 0) for acc in tx_data)
            print(f"✅ Exported {total} transactions across {len(tx_data)} account(s) to {args.csv}")
        else:
            print(json.dumps(tx_data, indent=2))

    elif args.command == "json":
        # Hermes agent aggregate payload
        balances = get_balances(cfg)
        tx_data = get_transactions(cfg, days=args.days)
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bank": cfg.get("bank_name", DEFAULT_BANK_NAME),
            "country": cfg.get("bank_country", DEFAULT_BANK_COUNTRY),
            "history_days": args.days,
            "balances": balances,
            "recent_transactions": tx_data,
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
