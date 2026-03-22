#!/usr/bin/env python3
"""
oauth_setup.py — First-time Google OAuth 2.0 authorization

Run this script ONCE to authorize access to your Google account.
It will open a browser window and ask you to log in.
After authorization, a token.json file is saved for future use.

Usage:
    python bookshelf/oauth_setup.py

Prerequisites:
    1. Create a Google Cloud project (see README.md for instructions)
    2. Enable Google Sheets API and Google Drive API
    3. Create OAuth 2.0 credentials (Desktop app type)
    4. Download credentials.json to the bookshelf/ folder
"""

import json
import sys
from pathlib import Path

# Paths
HERE = Path(__file__).parent
CREDENTIALS_PATH = HERE / "credentials.json"
TOKEN_PATH = HERE / "token.json"

# OAuth scopes needed
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def check_dependencies() -> bool:
    """Check that required packages are installed."""
    missing = []
    try:
        import google.auth
    except ImportError:
        missing.append("google-auth")
    try:
        import google_auth_oauthlib
    except ImportError:
        missing.append("google-auth-oauthlib")
    try:
        import gspread
    except ImportError:
        missing.append("gspread")

    if missing:
        print("❌ Missing dependencies. Install them with:")
        print(f"   pip install {' '.join(missing)}")
        return False
    return True


def check_credentials() -> bool:
    """Check that credentials.json exists and looks valid."""
    if not CREDENTIALS_PATH.exists():
        print(f"❌ credentials.json not found at: {CREDENTIALS_PATH}")
        print()
        print("Please follow these steps:")
        print()
        print("1. Go to: https://console.cloud.google.com/")
        print("2. Create a new project (or select existing)")
        print("3. Enable APIs:")
        print("   - Go to 'APIs & Services' → 'Enable APIs'")
        print("   - Enable 'Google Sheets API'")
        print("   - Enable 'Google Drive API'")
        print("4. Create OAuth credentials:")
        print("   - Go to 'APIs & Services' → 'Credentials'")
        print("   - Click 'Create Credentials' → 'OAuth 2.0 Client ID'")
        print("   - Application type: 'Desktop app'")
        print("   - Name: 'Bookshelf Bot' (or anything)")
        print("   - Click 'Create'")
        print("5. Download the credentials:")
        print("   - Click the download icon next to your new credential")
        print(f"   - Save the file as: {CREDENTIALS_PATH}")
        print()
        print("Then run this script again.")
        return False

    # Basic validation
    try:
        with open(CREDENTIALS_PATH) as f:
            creds_data = json.load(f)

        if "installed" not in creds_data and "web" not in creds_data:
            print("❌ credentials.json doesn't look like an OAuth 2.0 credentials file.")
            print("   Make sure you downloaded 'OAuth 2.0 Client ID' credentials,")
            print("   not a service account key.")
            return False
    except json.JSONDecodeError:
        print("❌ credentials.json is not valid JSON.")
        return False

    return True


def authorize() -> None:
    """Run the OAuth flow and save token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    print()
    print("🔐 Starting Google OAuth 2.0 authorization...")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
    )

    # Try local server first (opens browser automatically)
    # Falls back to console flow if no browser available
    try:
        print("Opening browser for authorization...")
        print("(If browser doesn't open, copy the URL shown and paste it manually)")
        print()
        creds = flow.run_local_server(port=0)
    except Exception as e:
        print(f"Browser auth failed ({e}), trying console flow...")
        creds = flow.run_console()

    # Save credentials
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print()
    print(f"✅ Authorization successful!")
    print(f"   Token saved to: {TOKEN_PATH}")
    print()
    print("🚀 You can now run the bot:")
    print("   python bookshelf/bot.py")


def test_connection() -> bool:
    """Test that the saved token actually works."""
    try:
        import gspread
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        client = gspread.authorize(creds)

        # Try to list spreadsheets (minimal permission check)
        client.list_spreadsheet_files()

        print("✅ Google Sheets connection: OK")
        return True

    except Exception as e:
        print(f"⚠️  Connection test failed: {e}")
        print("   The token was saved but there might be an issue.")
        print("   Try running the bot and see if it works.")
        return False


def main() -> None:
    """Main entry point."""
    print("=" * 50)
    print("  Bookshelf Catalog — Google OAuth Setup")
    print("=" * 50)

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)

    # Check credentials file
    if not check_credentials():
        sys.exit(1)

    # Check if already authorized
    if TOKEN_PATH.exists():
        print(f"ℹ️  token.json already exists at: {TOKEN_PATH}")
        answer = input("   Re-authorize? [y/N]: ").strip().lower()
        if answer != "y":
            print("Skipping authorization.")
            test_connection()
            return

    # Run authorization
    try:
        authorize()
        test_connection()
    except KeyboardInterrupt:
        print("\n\nAuthorization cancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
