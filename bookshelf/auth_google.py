#!/usr/bin/env python3
"""
Google OAuth Setup Script
==========================
Run this ONCE to authenticate with Google and save credentials to token.json.

Prerequisites:
    1. Create a Google Cloud project
    2. Enable Google Sheets API and Google Drive API
    3. Create OAuth 2.0 credentials (Desktop app type)
    4. Download client_secret.json to this directory

Then run:
    python auth_google.py

A browser window will open for authentication. After approval, token.json
is saved and the bot can use Google Sheets without further interaction.

Step-by-step guide is printed when you run this script.
"""
import json
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SETUP_GUIDE = """
╔══════════════════════════════════════════════════════════════════╗
║           Google OAuth Setup — Step by Step Guide               ║
╚══════════════════════════════════════════════════════════════════╝

Before running this script, you need to set up a Google Cloud project.
This is a ONE-TIME setup — after that, the bot works automatically.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1: Create a Google Cloud Project
  1. Go to: https://console.cloud.google.com/
  2. Click "Select a project" → "New Project"
  3. Name it "Bookshelf Catalog" → Create

STEP 2: Enable APIs
  1. In your project, go to "APIs & Services" → "Library"
  2. Search and enable: "Google Sheets API"
  3. Search and enable: "Google Drive API"

STEP 3: Create OAuth Credentials
  1. Go to "APIs & Services" → "Credentials"
  2. Click "+ CREATE CREDENTIALS" → "OAuth client ID"
  3. If prompted, configure consent screen:
     - User type: External → Create
     - App name: "Bookshelf Catalog"
     - User support email: your email
     - Developer contact: your email → Save
     - Scopes: skip (Next)
     - Test users: Add YOUR Google account email → Save
  4. Back to "Create OAuth client ID":
     - Application type: "Desktop app"
     - Name: "Bookshelf Bot"
     - Click "CREATE"
  5. Click "DOWNLOAD JSON" on the created credential
  6. Save the file as: client_secret.json (in this directory)

STEP 4: Run this script again
  python auth_google.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def check_client_secret():
    """Check if client_secret.json exists and is valid."""
    path = Path("client_secret.json")
    if not path.exists():
        print(SETUP_GUIDE)
        print("❌ client_secret.json not found!")
        print("   Follow the guide above, then run this script again.")
        sys.exit(1)

    try:
        data = json.loads(path.read_text())
        # Validate it's a proper OAuth client secret
        if "installed" not in data and "web" not in data:
            print("❌ client_secret.json doesn't look right.")
            print("   Make sure you downloaded 'Desktop app' type credentials.")
            sys.exit(1)
        print("✅ client_secret.json found and looks valid.")
        return path
    except json.JSONDecodeError:
        print("❌ client_secret.json is not valid JSON.")
        sys.exit(1)


def run_oauth_flow(client_secret_path: Path):
    """Run the OAuth flow and save token.json."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ Missing dependency. Run: pip install google-auth-oauthlib")
        sys.exit(1)

    print("\n🔐 Starting OAuth authentication flow...")
    print("   A browser window will open. Please log in with your Google account.")
    print("   Grant access to Google Sheets and Google Drive.\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        SCOPES,
    )

    # Use local server flow — opens browser automatically
    creds = flow.run_local_server(
        port=0,
        prompt="consent",  # always show consent screen to get refresh_token
        access_type="offline",
    )

    # Save token
    token_path = Path("token.json")
    token_path.write_text(creds.to_json())
    print(f"\n✅ Authentication successful!")
    print(f"   Token saved to: {token_path.absolute()}")
    print("\n   You can now run the bot: python bot.py")


def verify_token():
    """Quick verification that token works with Google Sheets."""
    try:
        import gspread
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        print("\n🔍 Verifying token with Google Sheets API...")
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                Path("token.json").write_text(creds.to_json())

        client = gspread.authorize(creds)
        # Try listing spreadsheets (basic API call)
        sheets = client.list_spreadsheet_files()
        print(f"✅ Token works! Found {len(sheets)} spreadsheet(s) in your Drive.")
        return True
    except Exception as e:
        print(f"⚠️  Token verification warning: {e}")
        print("   Token may still work — try running the bot.")
        return False


def main():
    print("🔧 Bookshelf Catalog — Google OAuth Setup\n")

    client_secret_path = check_client_secret()
    run_oauth_flow(client_secret_path)
    verify_token()


if __name__ == "__main__":
    main()
