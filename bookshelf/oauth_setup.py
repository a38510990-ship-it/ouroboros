#!/usr/bin/env python3
"""
oauth_setup.py — Google OAuth 2.0 Setup for Bookshelf Catalog

Run this script ONCE to authorize Google Sheets access.
It will open a browser window, ask you to sign in to Google,
and save the token to bookshelf/token.json.

Usage:
    cd bookshelf
    python oauth_setup.py

Prerequisites:
    1. Create a Google Cloud Project (free):
       https://console.cloud.google.com/projectcreate

    2. Enable Google Sheets API:
       https://console.cloud.google.com/apis/library/sheets.googleapis.com

    3. Enable Google Drive API (for creating files):
       https://console.cloud.google.com/apis/library/drive.googleapis.com

    4. Create OAuth 2.0 credentials:
       https://console.cloud.google.com/apis/credentials
       - Application type: Desktop app
       - Download the JSON file
       - Save it as bookshelf/credentials.json

    5. Run this script:
       python oauth_setup.py
"""

import sys
from pathlib import Path

# ── Check dependencies ────────────────────────────────────────────────────────

try:
    import gspread
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("Missing dependencies. Install them:")
    print("  pip install gspread google-auth-oauthlib")
    sys.exit(1)

HERE = Path(__file__).parent

CREDENTIALS_PATH = HERE / "credentials.json"
TOKEN_PATH = HERE / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def main():
    print("=" * 60)
    print("  Bookshelf Catalog — Google OAuth Setup")
    print("=" * 60)
    print()

    # Step 1: Check credentials.json
    if not CREDENTIALS_PATH.exists():
        print("❌ credentials.json not found!")
        print()
        print("Please follow these steps:")
        print()
        print("1. Go to: https://console.cloud.google.com/projectcreate")
        print("   Create a new project (free)")
        print()
        print("2. Enable Google Sheets API:")
        print("   https://console.cloud.google.com/apis/library/sheets.googleapis.com")
        print()
        print("3. Enable Google Drive API:")
        print("   https://console.cloud.google.com/apis/library/drive.googleapis.com")
        print()
        print("4. Create OAuth credentials:")
        print("   https://console.cloud.google.com/apis/credentials")
        print("   → Create Credentials → OAuth client ID")
        print("   → Application type: Desktop app")
        print("   → Download JSON")
        print(f"   → Save as: {CREDENTIALS_PATH}")
        print()
        sys.exit(1)

    print(f"✅ Found credentials.json")

    # Step 2: Check for existing valid token
    creds = None
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            if creds.valid:
                print(f"✅ Token is already valid: {TOKEN_PATH}")
                _test_connection(creds)
                return
            elif creds.expired and creds.refresh_token:
                print("🔄 Token expired, refreshing...")
                creds.refresh(Request())
                with open(TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
                print(f"✅ Token refreshed: {TOKEN_PATH}")
                _test_connection(creds)
                return
        except Exception as e:
            print(f"⚠️ Could not use existing token: {e}")
            creds = None

    # Step 3: Start OAuth flow
    print()
    print("🌐 Starting OAuth authorization...")
    print("A browser window will open. Sign in to your Google account")
    print("and grant access to Google Sheets and Drive.")
    print()

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(port=0)
    except Exception as e:
        print(f"❌ Authorization failed: {e}")
        print()
        print("If you're running in a headless environment (Colab, SSH),")
        print("try running this script locally on your machine instead.")
        sys.exit(1)

    # Step 4: Save token
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    print(f"✅ Authorization successful! Token saved to: {TOKEN_PATH}")

    # Step 5: Test connection
    _test_connection(creds)


def _test_connection(creds: "Credentials") -> None:
    """Test that we can actually connect to Google Sheets."""
    print()
    print("🔗 Testing Google Sheets connection...")

    try:
        client = gspread.authorize(creds)
        sheets = client.openall()
        print(f"✅ Connected! You have access to {len(sheets)} spreadsheet(s).")
        print()
        print("🎉 Setup complete! You can now run the bot:")
        print("   python bookshelf/bot.py")
    except Exception as e:
        print(f"⚠️ Connection test failed: {e}")
        print("The token was saved but there may be an issue with the API.")
        print("Check that Google Sheets API and Drive API are enabled in your project.")


if __name__ == "__main__":
    main()
