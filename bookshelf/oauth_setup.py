"""
oauth_setup.py — Google OAuth Setup

Run this ONCE to authenticate with Google and save credentials to token.json:
    python oauth_setup.py

After running this, the bot can access Google Sheets automatically.
See README.md for the full setup guide.
"""
# This is the canonical entry point for OAuth setup.
# The implementation lives in auth_google.py.
from auth_google import main

if __name__ == "__main__":
    main()
