#!/usr/bin/env python3
"""
oauth_setup.py — Google OAuth 2.0 Setup

Run this script ONCE to authorize the bot to access your Google Sheets.
It opens a browser window for you to sign in with your Google account.
After authorization, it saves token.json in the bookshelf/ directory.

The token is automatically refreshed when it expires — you only need to run
this script once.

Usage:
    python bookshelf/oauth_setup.py

Requirements:
    - credentials.json in bookshelf/ directory
      (downloaded from Google Cloud Console)
    - Run from anywhere; the script uses paths relative to its own location

What this script does:
    1. Reads credentials.json
    2. Opens browser for Google sign-in
    3. Saves token.json with access + refresh tokens
    4. Verifies connection to Google Sheets API
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main() -> None:
    print()
    print("=" * 60)
    print("  Bookshelf Bot — Google OAuth Setup")
    print("=" * 60)
    print()

    # ── Step 1: Check dependencies ─────────────────────────────────
    print("Checking dependencies...")
    try:
        import gspread
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        print("✅ Dependencies OK")
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print()
        print("Install with:")
        print("    pip install -r bookshelf/requirements.txt")
        sys.exit(1)

    print()

    # ── Step 2: Check for credentials.json ────────────────────────
    creds_path = HERE / "credentials.json"
    if not creds_path.exists():
        print("❌ credentials.json not found!")
        print()
        print("To get credentials.json:")
        print()
        print("1. Go to: https://console.cloud.google.com/")
        print("2. Create a new project (or select existing)")
        print("3. Enable APIs:")
        print("   - Google Sheets API")
        print("   - Google Drive API")
        print("   (APIs & Services → Enable APIs → search for each)")
        print()
        print("4. Create OAuth credentials:")
        print("   - APIs & Services → Credentials")
        print("   - Create Credentials → OAuth client ID")
        print("   - Application type: Desktop App")
        print("   - Name: Bookshelf Bot (or anything)")
        print("   - Click Create → Download JSON")
        print()
        print("5. Rename the downloaded file to: credentials.json")
        print(f"6. Place it in: {HERE}/")
        print()
        sys.exit(1)

    print(f"✅ credentials.json found: {creds_path}")
    print()

    # ── Step 3: Run OAuth flow ─────────────────────────────────────
    token_path = HERE / "token.json"
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    if token_path.exists():
        print(f"⚠️  token.json already exists at: {token_path}")
        answer = input("Re-authorize? [y/N]: ").strip().lower()
        if answer != "y":
            print("Keeping existing token.")
            _verify_connection(token_path, scopes)
            return
        token_path.unlink()

    print("Opening browser for Google sign-in...")
    print("(If the browser doesn't open automatically, check the terminal for a URL)")
    print()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as e:
        print(f"❌ OAuth flow failed: {e}")
        print()
        print("Common issues:")
        print("- Browser didn't open: copy the URL from the terminal and open manually")
        print("- 'redirect_uri_mismatch': add 'http://localhost' to authorized redirect URIs")
        print("  in Google Cloud Console (APIs & Services → Credentials → your OAuth client)")
        sys.exit(1)

    # Save token
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    print(f"✅ Token saved to: {token_path}")
    print()

    # ── Step 4: Verify connection ──────────────────────────────────
    _verify_connection(token_path, scopes)


def _verify_connection(token_path: Path, scopes: list) -> None:
    """Test the credentials by listing spreadsheets."""
    print("Testing connection to Google Sheets...")

    try:
        import gspread
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        client = gspread.authorize(creds)

        # Try to list spreadsheets (just a connectivity test)
        spreadsheets = client.list_spreadsheet_files()
        count = len(spreadsheets)

        print(f"✅ Connection successful!")
        if count > 0:
            print(f"   Found {count} existing spreadsheet(s) in your Drive")
        print()
        print("You're all set! Now you can start the bot:")
        print("    python bookshelf/bot.py")
        print()

    except FileNotFoundError:
        print(f"❌ token.json not found at {token_path}")
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        print()
        print("The token was saved, but the connection test failed.")
        print("Common cause: Google Cloud project configuration issue.")
        print("Make sure Sheets API and Drive API are enabled.")


if __name__ == "__main__":
    main()
