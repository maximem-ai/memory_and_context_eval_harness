"""
Bootstrap Synap SDK — run once to exchange the bootstrap token for permanent credentials.

Usage:
    uv run python scripts/bootstrap_synap.py

After this succeeds the SDK stores its credentials locally and subsequent runs
(including the live benchmark) will use them without needing the bootstrap token again.
"""

import asyncio
import os
import sys

# Load .env from repo root
from pathlib import Path
repo_root = Path(__file__).parent.parent
env_file = repo_root / ".env"
if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file)
    print(f"Loaded credentials from {env_file}")

INSTANCE_ID     = os.environ.get("SYNAP_INSTANCE_ID", "")
BOOTSTRAP_TOKEN = os.environ.get("SYNAP_BOOTSTRAP_TOKEN", "")
CLIENT_ID       = os.environ.get("SYNAP_CLIENT_ID", "")
BASE_URL        = os.environ.get("SYNAP_BASE_URL", "")
GRPC_HOST       = os.environ.get("SYNAP_GRPC_HOST", "")
GRPC_PORT       = 443


def check_credentials():
    missing = []
    if not INSTANCE_ID:
        missing.append("SYNAP_INSTANCE_ID")
    if not BOOTSTRAP_TOKEN:
        missing.append("SYNAP_BOOTSTRAP_TOKEN")
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        sys.exit(1)
    print(f"  Instance ID:     {INSTANCE_ID}")
    print(f"  Bootstrap token: {BOOTSTRAP_TOKEN[:8]}...{BOOTSTRAP_TOKEN[-4:]}")
    if CLIENT_ID:
        print(f"  Client ID:       {CLIENT_ID}")
    if BASE_URL:
        print(f"  Base URL:        {BASE_URL}")
    print()


async def bootstrap():
    from maximem_synap import MaximemSynapSDK, SDKConfig

    sdk_config = SDKConfig(
        api_base_url=BASE_URL or None,
        grpc_host=GRPC_HOST or None,
        grpc_port=GRPC_PORT or None,
        grpc_use_tls=True,
    )

    sdk = MaximemSynapSDK(
        instance_id=INSTANCE_ID,
        bootstrap_token=BOOTSTRAP_TOKEN,
        config=sdk_config,
    )

    print("Calling sdk.initialize() — this exchanges the bootstrap token for permanent credentials...")
    await sdk.initialize()
    print("sdk.initialize() completed successfully.")

    # Verify we can reach the instance
    print("\nVerifying instance connection...")
    try:
        info = await sdk.instance.get()
        print(f"  Instance info: {info}")
    except Exception as e:
        print(f"  Instance.get() raised: {e} (non-fatal — credentials were stored)")

    print("\nBootstrap complete. Permanent credentials have been stored by the SDK.")
    print("You can now run the benchmark without the bootstrap token.")


if __name__ == "__main__":
    print("=" * 50)
    print("  Synap SDK Bootstrap")
    print("=" * 50)
    check_credentials()
    asyncio.run(bootstrap())
