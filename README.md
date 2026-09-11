# Enable Banking Client (`enablebanking-client`)

A lightweight, production-ready Python CLI tool to connect to European banks (ABN AMRO, ING, Rabobank, Revolut, N26, Nordea, and 2,500+ others) to fetch account balances and transaction ledgers via the [Enable Banking](https://enablebanking.com) PSD2 Open Banking API.

---

## 1. Prerequisites: Enable Banking Setup

Enable Banking provides free open-banking access for personal testing and developer integration under European PSD2 regulations.

1. **Sign Up & Open the Control Panel**:
   - Go to [Enable Banking Control Panel](https://enablebanking.com/cp/applications).
   - Sign in or create an account.

2. **Generate your RSA Key Pair**:
   Run the following in your terminal to generate a 4096-bit private key and self-signed certificate:
   ```bash
   openssl req -x509 -newkey rsa:4096 -keyout private.pem -out public.crt -days 365 -nodes -subj "/CN=enablebanking-client"
   ```
   Keep `private.pem` safe! Never commit it to GitHub.

3. **Register an Application**:
   - In the [Applications Control Panel](https://enablebanking.com/cp/applications), click **"Create application"** (or use the API).
   - Set **Environment** to `PRODUCTION`.
   - Choose service: `Account Information (AIS)`.
   - Upload your `public.crt` certificate.
   - Set a **Redirect URL** (e.g. `https://localhost/callback` or your own domain).
   - Add your IBAN under **Linked accounts** (Enable Banking operates in restricted mode for sandbox/developer accounts until un-restriction is requested).

4. **Note Your Application ID**:
   Once created, you will receive an Application ID (`kid` / UUID, e.g. `e82f6e88-f0e8-4634-be3d-136c93faae9d`).

---

## 2. Installation & Setup

Ensure you have [`uv`](https://docs.astral.sh/uv/) installed:
```bash
git clone https://github.com/<your-username>/enablebanking-client.git
cd enablebanking-client

# Setup the virtual environment and install dependencies:
uv sync
```
*(Note: In `uv`, `uv sync` manages the virtual environment and installs the dependencies declared in `pyproject.toml`).*

### Configure Credentials:
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your details:
```bash
ENABLE_BANKING_APP_ID="YOUR_APP_ID"
ENABLE_BANKING_REDIRECT_URL="https://yourdomain.com/callback"
ENABLE_BANKING_KEY_PATH="private.pem"
```

---

## 3. Usage Guide

You can run commands using `uv run python enable_banking.py` (or activate `.venv` and run `python enable_banking.py`):

### Discover Supported Banks
Check available banks for your country (e.g. `NL`, `DE`, `FR`, `FI`, `ES`, etc.):
```bash
uv run python enable_banking.py banks --country NL
```

### Step 1: Initiate Bank Authorization (Every 180 Days)
PSD2 regulations require Strong Customer Authentication (SCA) with your bank once every ~180 days:
```bash
# Authorize default bank (configured in .env or defaults to ABN AMRO):
uv run python enable_banking.py auth

# Or specify a different bank / country explicitly:
uv run python enable_banking.py auth --bank "ING" --country "NL"
uv run python enable_banking.py auth --bank "Revolut" --country "NL"
```
- Open the printed link in your web browser.
- Log into your bank and grant permission for your account.
- Paste the redirected callback URL directly into the interactive prompt (or press Enter to finish later).

### Step 2: Complete Session Authorization (If not done interactively)
Copy either the `code` or the full redirected URL and run:
```bash
uv run python enable_banking.py session --code "<PASTE_CODE_OR_URL_HERE>"
```
This stores your active session ID and linked account UIDs in `session.json`.

---

### Querying Data (Autonomous & Scriptable)

Once authorized, no further browser interactions or phone approvals are needed until consent expires:

- **Check Current Balances**:
  ```bash
  uv run python enable_banking.py balances
  ```

- **Export Transactions to CSV**:
  ```bash
  uv run python enable_banking.py transactions --days 90 --csv transactions.csv
  ```

- **Output Structured JSON (Ideal for Scripts & AI Agents)**:
  ```bash
  uv run python enable_banking.py json --days 30
  ```

---

## 4. Running Tests

Unit tests are implemented with `pytest` and `responses` to mock HTTP interactions and verify JWT signing, config loading, session persistence, pagination, and CSV exports:

```bash
# Run the test suite
uv run pytest -v
```

---

## 5. Security & Privacy

- **Never commit `.pem`, `.crt`, `.env`, `session.json`, or exported `.csv` files** to version control.
- An inclusive `.gitignore` is provided to keep your personal banking data and private keys local.
- For reference on the underlying protocol, see the official [Enable Banking API Reference & Flow Diagrams](https://enablebanking.com/docs/api/reference/#flow-diagrams).
- Official multi-language examples and Python sample code: [enablebanking-api-samples](https://github.com/enablebanking/enablebanking-api-samples/tree/master/python_example).
