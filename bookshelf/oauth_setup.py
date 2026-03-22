#!/usr/bin/env python3
"""
oauth_setup.py — One-time Google OAuth 2.0 authorization

Run this script ONCE before starting the bot to authorize
Google Sheets access. It will:

1. Open a browser window asking you to log in with Google
2. Ask for permission to access Google Sheets and Drive
3. Save the authorization token to bookshelf/token.json

After running this script, the bot can access Google Sheets
on your behalf without re-authorization (tokens auto-refresh).

Usage:
    python bookshelf/oauth_setup.py

Prerequisites:
    1. Create a Google Cloud project:
       https://console.cloud.google.com/projectcreate

    2. Enable Google Sheets API:
       https://console.cloud.google.com/apis/library/sheets.googleapis.com

    3. Enable Google Drive API:
       https://console.cloud.google.com/apis/library/drive.googleapis.com

    4. Create OAuth 2.0 credentials:
       → APIs & Services → Credentials → Create Credentials → OAuth client ID
       → Application type: Desktop app
       → Download JSON → Save as bookshelf/credentials.json

    5. Run this script:
       python bookshelf/oauth_setup.py
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
    for package in ["google.auth", "google_auth_oauthlib", "gspread"]:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing.append(package)

    if missing:
        print("❌ Missing required packages. Install them:")
        print(f"   pip install -r bookshelf/requirements.txt")
        sys.exit(1)


def main() -> None:
    """Run the OAuth authorization flow."""
    print("🔐 Google Sheets OAuth Setup")
    print("=" * 40)

    # Check dependencies
    check_dependencies()

    from google_auth_oauthlib.flow import InstalledAppFlow
    import gspread

    # Check credentials.json
    if not CREDENTIALS_PATH.exists():
        print(f"\n❌ credentials.json not found at: {CREDENTIALS_PATH}")
        print("\nTo create it:")
        print("  1. Go to: https://console.cloud.google.com/")
        print("  2. Create a project (or select existing)")
        print("  3. Enable Google Sheets API:")
        print("     https://console.cloud.google.com/apis/library/sheets.googleapis.com")
        print("  4. Enable Google Drive API:")
        print("     https://console.cloud.google.com/apis/library/drive.googleapis.com")
        print("  5. Go to: APIs & Services → Credentials")
        print("  6. Create Credentials → OAuth client ID")
        print("  7. Application type: Desktop app")
        print("  8. Download JSON and save as: bookshelf/credentials.json")
        print("\nThen run this script again.")
        sys.exit(1)

    # Validate credentials.json format
    try:
        with open(CREDENTIALS_PATH) as f:
            creds_data = json.load(f)

        if "installed" not in creds_data and "web" not in creds_data:
            print("❌ Invalid credentials.json format")
            print("   Make sure you downloaded 'Desktop app' OAuth credentials")
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ credentials.json is not valid JSON: {e}")
        sys.exit(1)

    print(f"\n📄 Found credentials.json: {CREDENTIALS_PATH}")
    print("\n🌐 Opening browser for Google authorization...")
    print("   (If browser doesn't open, check your terminal for a URL)\n")

    # Run OAuth flow
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH),
            scopes=SCOPES,
        )
        # run_local_server opens browser + handles redirect
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )
    except Exception as e:
        print(f"\n❌ Authorization failed: {e}")
        print("\nTroubleshooting:")
        print("  • Make sure you enabled Sheets and Drive APIs in Google Cloud")
        print("  • Add your Google account as a test user if the app is in 'Testing' mode")
        print("    (APIs & Services → OAuth consent screen → Test users)")
        sys.exit(1)

    # Save token
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Authorization successful!")
    print(f"   Token saved to: {TOKEN_PATH}")

    # Test connection
    print("\n🔍 Testing Google Sheets connection...")
    try:
        client = gspread.authorize(creds)
        spreadsheets = client.list_spreadsheet_files()
        count = len(spreadsheets)
        print(f"✅ Connection successful! ({count} spreadsheet(s) accessible)")
    except Exception as e:
        print(f"⚠️  Connection test failed: {e}")
        print("   Token was saved, but there may be a permission issue.")
        print("   Make sure you enabled the Google Sheets API.")

    print("\n🚀 Setup complete! You can now run the bot:")
    print("   python bookshelf/bot.py")


if __name__ == "__main__":
    main()
