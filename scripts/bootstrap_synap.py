"""
Synap API Key Exchange — run once to trade a bootstrap token for a permanent API key.

Usage:
    uv run python scripts/bootstrap_synap.py

Set SYNAP_BOOTSTRAP_TOKEN, SYNAP_INSTANCE_ID, and SYNAP_BASE_URL in .env before running.
The script prints the resulting API key — copy it into .env as SYNAP_API_KEY.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
env_file = repo_root / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)

BOOTSTRAP_TOKEN = os.environ.get("SYNAP_BOOTSTRAP_TOKEN", "")
INSTANCE_ID     = os.environ.get("SYNAP_INSTANCE_ID", "")
BASE_URL        = os.environ.get("SYNAP_BASE_URL", "https://synap-cloud-prod.maximem.ai").rstrip("/")


def main():
    missing = []
    if not BOOTSTRAP_TOKEN:
        missing.append("SYNAP_BOOTSTRAP_TOKEN")
    if not INSTANCE_ID:
        missing.append("SYNAP_INSTANCE_ID")
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        sys.exit(1)

    print("=" * 50)
    print("  Synap API Key Exchange")
    print("=" * 50)
    print(f"  Instance ID:     {INSTANCE_ID}")
    print(f"  Bootstrap token: {BOOTSTRAP_TOKEN[:8]}...{BOOTSTRAP_TOKEN[-4:]}")
    print(f"  Base URL:        {BASE_URL}")
    print()

    import requests

    resp = requests.post(
        f"{BASE_URL}/api/v1/keys/bootstrap",
        json={"bootstrap_key": BOOTSTRAP_TOKEN, "instance_id": INSTANCE_ID, "label": "benchmark"},
        timeout=30,
    )

    if not resp.ok:
        print(f"ERROR: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    api_key = data.get("api_key") or data.get("key") or data.get("token")
    if not api_key:
        print(f"ERROR: Unexpected response shape: {data}")
        sys.exit(1)

    print("API key issued successfully.\n")
    print(f"  SYNAP_API_KEY={api_key}\n")
    print("Add the line above to your .env file, then remove SYNAP_BOOTSTRAP_TOKEN.")


if __name__ == "__main__":
    main()
