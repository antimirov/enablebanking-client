import os
import json
import tempfile
import pytest
import responses
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

import enable_banking


@pytest.fixture
def dummy_rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    return pem.decode("utf-8")


@pytest.fixture
def mock_env(tmp_path, dummy_rsa_key, monkeypatch):
    key_file = tmp_path / "test_private.pem"
    key_file.write_text(dummy_rsa_key)

    env_file = tmp_path / ".env"
    env_file.write_text(
        f'ENABLE_BANKING_APP_ID="test-app-id-123"\n'
        f'ENABLE_BANKING_REDIRECT_URL="https://example.com/callback"\n'
        f'ENABLE_BANKING_KEY_PATH="{key_file}"\n'
        f'ENABLE_BANKING_BANK="Test Bank"\n'
        f'ENABLE_BANKING_COUNTRY="NL"\n'
    )

    # Patch paths inside enable_banking module
    monkeypatch.setattr(enable_banking, "ENV_FILE", env_file)
    monkeypatch.setattr(enable_banking, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(enable_banking, "KEY_FILE", key_file)
    monkeypatch.setattr(enable_banking, "BASE_DIR", tmp_path)

    # Clear any host env vars
    for k in ["ENABLE_BANKING_APP_ID", "ENABLE_BANKING_REDIRECT_URL", "ENABLE_BANKING_KEY_PATH", "ENABLE_BANKING_BANK", "ENABLE_BANKING_COUNTRY"]:
        monkeypatch.delenv(k, raising=False)

    return tmp_path


def test_extract_code():
    assert enable_banking.extract_code("my_code_123") == "my_code_123"
    assert enable_banking.extract_code("https://example.com/callback?state=xyz&code=auth-code-456") == "auth-code-456"
    assert enable_banking.extract_code("http://localhost:8080/redirect?code=another-code-789") == "another-code-789"
    assert enable_banking.extract_code("  code_with_spaces  ") == "code_with_spaces"


def test_load_config(mock_env):
    cfg = enable_banking.load_config()
    assert cfg["app_id"] == "test-app-id-123"
    assert cfg["redirect_url"] == "https://example.com/callback"
    assert cfg["bank_name"] == "Test Bank"
    assert cfg["bank_country"] == "NL"
    assert "BEGIN PRIVATE KEY" in cfg["private_key"]


def test_generate_jwt(dummy_rsa_key):
    token = enable_banking.generate_jwt("test-app-id", dummy_rsa_key, ttl=3600)
    assert isinstance(token, str)
    assert len(token.split(".")) == 3  # Header, payload, signature


@responses.activate
def test_start_auth(mock_env):
    cfg = enable_banking.load_config()
    responses.add(
        responses.POST,
        "https://api.enablebanking.com/auth",
        json={"url": "https://auth.enablebanking.com/start", "authorization_id": "auth-123"},
        status=200,
    )

    res = enable_banking.start_auth(cfg, bank_name="Custom Bank", bank_country="DE")
    assert res["url"] == "https://auth.enablebanking.com/start"
    assert res["authorization_id"] == "auth-123"
    assert len(responses.calls) == 1

    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["aspsp"]["name"] == "Custom Bank"
    assert req_body["aspsp"]["country"] == "DE"


@responses.activate
def test_authorize_session(mock_env):
    cfg = enable_banking.load_config()
    mock_session_resp = {
        "session_id": "sess-xyz",
        "accounts": [
            {
                "uid": "acc-123",
                "name": "Checking",
                "account_id": {"iban": "NL00BANK0123456789"}
            }
        ]
    }
    responses.add(
        responses.POST,
        "https://api.enablebanking.com/sessions",
        json=mock_session_resp,
        status=200,
    )

    session = enable_banking.authorize_session(cfg, "my-auth-code")
    assert session["session_id"] == "sess-xyz"
    assert session["accounts"][0]["uid"] == "acc-123"

    # Verify session was persisted to file
    session_file = mock_env / "session.json"
    assert session_file.exists()
    saved = json.loads(session_file.read_text())
    assert saved["session_id"] == "sess-xyz"


@responses.activate
def test_get_balances(mock_env):
    cfg = enable_banking.load_config()
    # Save active session first
    enable_banking.save_session({
        "session_id": "sess-xyz",
        "accounts": [
            {
                "uid": "acc-123",
                "name": "Checking",
                "account_id": {"iban": "NL00BANK0123456789"}
            }
        ]
    })

    responses.add(
        responses.GET,
        "https://api.enablebanking.com/accounts/acc-123/balances",
        json={
            "balances": [
                {
                    "balance_amount": {"currency": "EUR", "amount": "1500.00"},
                    "balance_type": "ITBD"
                }
            ]
        },
        status=200,
    )

    balances = enable_banking.get_balances(cfg)
    assert len(balances) == 1
    assert balances[0]["iban"] == "NL00BANK0123456789"
    assert balances[0]["balances"][0]["balance_amount"]["amount"] == "1500.00"


@responses.activate
def test_get_transactions_and_csv_export(mock_env, tmp_path):
    cfg = enable_banking.load_config()
    enable_banking.save_session({
        "session_id": "sess-xyz",
        "accounts": [
            {
                "uid": "acc-123",
                "name": "Checking",
                "account_id": {"iban": "NL00BANK0123456789"}
            }
        ]
    })

    # Test pagination: 1st page has continuation_key, 2nd page finishes
    responses.add(
        responses.GET,
        "https://api.enablebanking.com/accounts/acc-123/transactions",
        json={
            "transactions": [
                {
                    "entry_reference": "TX1",
                    "transaction_amount": {"currency": "EUR", "amount": "25.50"},
                    "credit_debit_indicator": "DBIT",
                    "booking_date": "2026-09-10",
                    "creditor": {"name": "Supermarket"},
                    "remittance_information": ["Groceries"]
                }
            ],
            "continuation_key": "page-2-key"
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.enablebanking.com/accounts/acc-123/transactions",
        json={
            "transactions": [
                {
                    "entry_reference": "TX2",
                    "transaction_amount": {"currency": "EUR", "amount": "100.00"},
                    "credit_debit_indicator": "CRDT",
                    "booking_date": "2026-09-09",
                    "debtor": {"name": "Employer"},
                    "remittance_information": ["Salary"]
                }
            ]
        },
        status=200,
    )

    tx_data = enable_banking.get_transactions(cfg, days=30)
    assert len(tx_data) == 1
    assert tx_data[0]["count"] == 2

    # Test CSV export
    csv_file = tmp_path / "test_transactions.csv"
    enable_banking.export_transactions_csv(tx_data, str(csv_file))
    assert csv_file.exists()

    content = csv_file.read_text()
    assert "Supermarket" in content
    assert "25.50" in content
    assert "Employer" in content
    assert "100.00" in content
