"""
oauth_setup.py — One-time Google OAuth authorization

Run this script ONCE to authorize the bot to access your Google Sheets.
It will open a browser window asking you to log in with your Google account.
After authorization, a token.json file will be saved in bookshelf/.

Usage:
    python bookshelf/oauth_setup.py

Prerequisites:
    1. Create a Google Cloud project (see bookshelf/README.md)
    2. Enable Google Sheets API and Google Drive API
    3. Create OAuth 2.0 credentials (Desktop application)
    4. Download credentials.json to bookshelf/credentials.json
    5. Run this script
"""

import sys
from pathlib import Path

# Ensure imports work from any working directory
sys.path.insert(0, str(Path(__file__).parent))

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

BOOKSHELF_DIR = Path(__file__).parent
CREDENTIALS_FILE = BOOKSHELF_DIR / "credentials.json"
TOKEN_FILE = BOOKSHELF_DIR / "token.json"


def main():
    print("=" * 60)
    print("Bookshelf Catalog — Google OAuth Setup")
    print("=" * 60)
    print()

    # Check prerequisites
    if not CREDENTIALS_FILE.exists():
        print("❌ ERROR: credentials.json not found!")
        print()
        print("Steps to fix:")
        print("1. Go to: https://console.cloud.google.com/")
        print("2. Create a project (or select existing)")
        print("3. Enable APIs: Sheets API + Drive API")
        print("4. Create credentials: APIs & Services → Credentials")
        print("   → Create Credentials → OAuth Client ID")
        print("   → Application type: Desktop app")
        print("5. Download JSON → rename to credentials.json")
        print(f"6. Place in: {CREDENTIALS_FILE}")
        print()
        sys.exit(1)

    print(f"✅ Found credentials.json")

    # Check if token already exists
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            if creds.valid:
                print("✅ token.json already exists and is valid!")
                print()
                print("You're all set. You can run the bot:")
                print("  python bookshelf/bot.py")
                return
            elif creds.expired and creds.refresh_token:
                print("🔄 Token expired, refreshing...")
                creds.refresh(Request())
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
                print("✅ Token refreshed successfully!")
                print()
                print("You can now run the bot:")
                print("  python bookshelf/bot.py")
                return
        except Exception as e:
            print(f"⚠️  Existing token.json is invalid ({e}), re-authorizing...")

    # Run OAuth flow
    print()
    print("🌐 Opening browser for Google authorization...")
    print("   (If browser doesn't open, check the URL printed below)")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    # Save token
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print()
    print(f"✅ Authorization successful!")
    print(f"✅ Token saved to: {TOKEN_FILE}")
    print()
    print("You can now run the bot:")
    print("  python bookshelf/bot.py")
    print()
    print("Note: token.json is in .gitignore — it will NOT be committed to git.")


if __name__ == "__main__":
    main()
