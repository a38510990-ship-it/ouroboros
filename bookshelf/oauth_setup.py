#!/usr/bin/env python3
"""
oauth_setup.py — First-time Google OAuth 2.0 authorization

Run this ONCE to authorize the bot to access your Google Sheets.
This will open a browser window where you log in with your Google account.
After authorization, a token.json file is saved for future use.

Usage:
    python bookshelf/oauth_setup.py

Requirements:
    - credentials.json must exist in bookshelf/ directory
      (downloaded from Google Cloud Console)
    - Run on a machine with a browser available (or use the printed URL)

The bot will be able to:
    - Create and edit Google Sheets in YOUR Google account
    - No card required, no billing — just free Google account
"""

import json
import sys
from pathlib import Path

# Ensure we can find the .env in bookshelf/
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")


def main():
    print("=" * 60)
    print("Bookshelf Catalog — Google OAuth Setup")
    print("=" * 60)

    # Check for credentials.json
    credentials_path = Path(__file__).parent / "credentials.json"
    token_path = Path(__file__).parent / "token.json"

    if not credentials_path.exists():
        print("\n❌ credentials.json not found!")
        print("\nTo create it:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create a new project (or select existing)")
        print("3. Enable 'Google Sheets API' and 'Google Drive API'")
        print("   → APIs & Services → Enable APIs → search 'Sheets'")
        print("4. Create OAuth credentials:")
        print("   → APIs & Services → Credentials → Create Credentials")
        print("   → OAuth 2.0 Client ID → Desktop App")
        print("5. Download JSON → rename to credentials.json")
        print(f"6. Place it here: {credentials_path}")
        print("\n💡 Note: Google Cloud free tier is sufficient — no billing required!")
        sys.exit(1)

    if token_path.exists():
        print(f"\n⚠️  token.json already exists at {token_path}")
        answer = input("Re-authorize? This will replace the existing token. [y/N]: ").strip().lower()
        if answer != "y":
            print("Aborted. Existing token.json kept.")
            sys.exit(0)

    # Perform OAuth flow
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("\n❌ Missing dependency: google-auth-oauthlib")
        print("Run: pip install google-auth-oauthlib")
        sys.exit(1)

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    print("\n🌐 Opening browser for Google authorization...")
    print("   (If browser doesn't open, check the URL printed below)")
    print()

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path),
            scopes=SCOPES,
        )
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )
    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        print("\nIf you're on a headless server, use:")
        print("    flow.run_console()  # instead of run_local_server")
        sys.exit(1)

    # Save token
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Authorization successful!")
    print(f"   Token saved to: {token_path}")
    print()
    print("You can now start the bot:")
    print("    python bookshelf/bot.py")


if __name__ == "__main__":
    main()
