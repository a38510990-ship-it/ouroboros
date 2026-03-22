"""
oauth_setup.py — First-time Google OAuth authorization

Run this ONCE to create token.json:
    python bookshelf/oauth_setup.py

After that, bot.py and sheets.py will use token.json automatically.
Token refreshes itself when expired — no need to re-run this script
unless you revoke access or delete token.json.

Requirements:
    - credentials.json must be in the bookshelf/ directory
    - credentials.json is downloaded from Google Cloud Console
      (APIs & Services → Credentials → OAuth 2.0 Client IDs → Download)
"""

import sys
from pathlib import Path

# Add parent dir to path so we can import sheets.py
sys.path.insert(0, str(Path(__file__).parent))

from sheets import get_credentials, SCOPES


def main():
    script_dir = Path(__file__).parent
    token_path = script_dir / "token.json"
    creds_path = script_dir / "credentials.json"

    print("🔐 Google OAuth Setup for Bookshelf Catalog Bot")
    print("=" * 50)

    if not creds_path.exists():
        print(f"\n❌ credentials.json not found at: {creds_path}")
        print("\nTo fix this:")
        print("1. Go to https://console.cloud.google.com")
        print("2. Create/select a project")
        print("3. Enable Google Sheets API and Google Drive API")
        print("4. Go to APIs & Services → Credentials")
        print("5. Create OAuth 2.0 Client ID (Desktop app)")
        print("6. Download JSON → rename to credentials.json")
        print(f"7. Place it at: {creds_path}")
        sys.exit(1)

    if token_path.exists():
        print(f"\n⚠️  token.json already exists at: {token_path}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)
        token_path.unlink()

    print(f"\n✅ Found credentials.json at: {creds_path}")
    print("Opening browser for authorization...")
    print("(If browser doesn't open, copy the URL shown in terminal)")
    print()

    try:
        creds = get_credentials(
            token_path=str(token_path),
            credentials_path=str(creds_path),
        )
        print(f"\n✅ Authorization successful!")
        print(f"📄 Token saved to: {token_path}")
        print()
        print("You can now run the bot:")
        print("    python bookshelf/bot.py")

    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
