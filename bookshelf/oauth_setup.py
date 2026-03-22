#!/usr/bin/env python3
"""
oauth_setup.py — Google OAuth 2.0 setup for Bookshelf Catalog Bot

Run this ONCE before starting the bot. It will:
1. Open a browser window for Google account authorization
2. Save the access token to bookshelf/token.json
3. Test the connection to Google Sheets

After this, the bot can access Google Sheets on your behalf.

Prerequisites:
    You need a Google Cloud project with Sheets API enabled.
    See bookshelf/README.md for step-by-step instructions.

Usage:
    python bookshelf/oauth_setup.py

Files:
    bookshelf/credentials.json  — OAuth client secrets (from Google Cloud Console)
    bookshelf/token.json        — Saved access token (created by this script)
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

CREDENTIALS_PATH = HERE / "credentials.json"
TOKEN_PATH = HERE / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def check_dependencies() -> None:
    """Check that required packages are installed."""
    missing = []
    try:
        import google.oauth2.credentials  # noqa
    except ImportError:
        missing.append("google-auth")

    try:
        import google_auth_oauthlib.flow  # noqa
    except ImportError:
        missing.append("google-auth-oauthlib")

    try:
        import gspread  # noqa
    except ImportError:
        missing.append("gspread")

    if missing:
        print(f"\n❌ Missing packages: {', '.join(missing)}")
        print("Install with:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)


def check_credentials() -> None:
    """Check that credentials.json exists and looks valid."""
    if not CREDENTIALS_PATH.exists():
        print(f"\n❌ credentials.json not found at: {CREDENTIALS_PATH}")
        print()
        print("To get credentials.json:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a new project (or select existing)")
        print("  3. Go to APIs & Services → Library")
        print("  4. Enable 'Google Sheets API'")
        print("  5. Enable 'Google Drive API'")
        print("  6. Go to APIs & Services → Credentials")
        print("  7. Click 'Create Credentials' → 'OAuth client ID'")
        print("  8. Choose 'Desktop app'")
        print("  9. Download and save as bookshelf/credentials.json")
        print()
        print("Full instructions: bookshelf/README.md")
        sys.exit(1)

    try:
        with open(CREDENTIALS_PATH) as f:
            data = json.load(f)
        if "installed" not in data and "web" not in data:
            print("❌ credentials.json seems invalid (missing 'installed' or 'web' key)")
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ credentials.json is not valid JSON: {e}")
        sys.exit(1)

    print("✅ credentials.json found and valid")


def authorize() -> None:
    """Run the OAuth flow and save token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("\n🔐 Starting OAuth authorization...")
    print("A browser window will open. Sign in with your Google account.")
    print("Grant access to Google Sheets and Google Drive.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
    )

    # Run local server to receive the OAuth callback
    creds = flow.run_local_server(
        port=0,  # Use any available port
        prompt="consent",
        access_type="offline",  # Get refresh token
    )

    # Save credentials
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Authorization successful! Token saved to: {TOKEN_PATH}")


def test_connection() -> None:
    """Test the connection to Google Sheets."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import gspread

    print("\n🧪 Testing Google Sheets connection...")

    if not TOKEN_PATH.exists():
        print("❌ token.json not found. Authorization may have failed.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    try:
        client = gspread.authorize(creds)
        # Try to list spreadsheets (just to verify the connection)
        spreadsheets = client.list_spreadsheet_files()
        print(f"✅ Connected to Google Sheets! Found {len(spreadsheets)} spreadsheet(s) in your Drive.")
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        sys.exit(1)


def main() -> None:
    print("=" * 50)
    print("  Bookshelf Catalog Bot — Google OAuth Setup")
    print("=" * 50)

    # Step 1: Check dependencies
    print("\n📦 Checking dependencies...")
    check_dependencies()
    print("✅ All dependencies installed")

    # Step 2: Check credentials.json
    print("\n📄 Checking credentials.json...")
    check_credentials()

    # Step 3: Run OAuth flow
    authorize()

    # Step 4: Test connection
    test_connection()

    print("\n" + "=" * 50)
    print("  Setup complete! You can now start the bot:")
    print("  python bookshelf/bot.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
