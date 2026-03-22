#!/usr/bin/env python3
"""
Google OAuth Setup Script
=========================
Run this ONCE to authenticate with Google OAuth.
It will open a browser, ask you to log in, and save credentials to token.json.

Usage:
    python auth_google.py
"""
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main() -> None:
    client_secret_path = Path("client_secret.json")

    if not client_secret_path.exists():
        print("❌ ERROR: client_secret.json not found!\n")
        print("To get it:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project (or select existing)")
        print("  3. Enable APIs: Google Sheets API + Google Drive API")
        print("  4. Go to APIs & Services → Credentials")
        print("  5. Create OAuth 2.0 Client ID (type: Desktop app)")
        print("  6. Download JSON → rename to client_secret.json")
        print("  7. Place client_secret.json in this directory")
        print("  8. Run this script again")
        return

    token_path = Path("token.json")

    # Check if already authenticated
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.valid:
            print("✅ Already authenticated! token.json is valid.")
            print("You can run the bot: python bot.py")
            return
        if creds.expired and creds.refresh_token:
            print("🔄 Token expired, refreshing...")
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            print("✅ Token refreshed! You can run the bot: python bot.py")
            return

    print("🌐 Opening browser for Google OAuth authentication...")
    print("   (If browser doesn't open, copy the URL from the terminal)\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    print(f"\n✅ Authentication successful!")
    print(f"   Credentials saved to: {token_path.resolve()}")
    print(f"\nYou can now run the bot:")
    print(f"   python bot.py")


if __name__ == "__main__":
    main()
