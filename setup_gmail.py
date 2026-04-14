"""
ResolveIQ — Gmail OAuth2 Setup
Run this ONCE before starting the app to authenticate with Gmail.

Usage:
  python setup_gmail.py

This will:
  1. Check for credentials.json
  2. Open a browser for Google OAuth consent
  3. Save token.json for future use
  4. Test the connection by reading your inbox profile
"""

import os
import sys


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║          ResolveIQ — Gmail Setup Wizard                  ║
╠══════════════════════════════════════════════════════════╣
║  This will connect ResolveIQ to your Gmail account so    ║
║  it can read helpdesk emails and send replies.           ║
╚══════════════════════════════════════════════════════════╝
""")


def check_credentials():
    if os.path.exists("credentials.json"):
        print("✅  credentials.json found.\n")
        return True

    print("""
❌  credentials.json NOT found.

You need to create a Google Cloud project and download OAuth2 credentials.
Follow these steps:

  1. Go to: https://console.cloud.google.com/
  2. Create a new project (or select an existing one)
  3. Navigate to: APIs & Services → Library
  4. Search for "Gmail API" and click Enable
  5. Navigate to: APIs & Services → Credentials
  6. Click "Create Credentials" → "OAuth 2.0 Client IDs"
  7. Application type: Desktop app
  8. Name it: ResolveIQ
  9. Click Create, then Download JSON
  10. Rename the downloaded file to: credentials.json
  11. Move it into this folder (ResolveIQ/)
  12. Run this script again: python setup_gmail.py

⚠️  You also need to add your Gmail address as a Test User:
  APIs & Services → OAuth consent screen → Test users → Add Users
""")
    return False


def run_auth():
    try:
        from gmail_integration import authenticate, HELPDESK_LABEL, _get_or_create_label
    except ImportError as e:
        print(f"❌  Import error: {e}")
        print("Make sure you're running this from the ResolveIQ/ folder.")
        sys.exit(1)

    print("🔐  Starting OAuth2 authentication...")
    print("    A browser window will open. Sign in and grant the requested permissions.\n")

    try:
        service = authenticate()
    except Exception as e:
        print(f"\n❌  Authentication failed: {e}")
        sys.exit(1)

    # Get profile
    try:
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown")
        print(f"✅  Authenticated as: {email}")
    except Exception as e:
        print(f"⚠️  Could not fetch profile: {e}")
        email = "unknown"

    # Create helpdesk label
    print(f"\n🏷️  Setting up Gmail label: '{HELPDESK_LABEL}'")
    try:
        label_id = _get_or_create_label(service, HELPDESK_LABEL)
        print(f"✅  Label ready (id: {label_id})")
    except Exception as e:
        print(f"⚠️  Could not create label: {e}")

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  ✅  Gmail setup complete!                               ║
╠══════════════════════════════════════════════════════════╣
║  Account: {email:<46}  ║
║  Label:   {HELPDESK_LABEL:<46}  ║
╠══════════════════════════════════════════════════════════╣
║  Next steps:                                             ║
║  1. Start the server:  python app.py                     ║
║  2. Open:              http://localhost:5000             ║
║  3. In the Gmail panel, click "▶ Start Watcher"          ║
║                                                          ║
║  To test: In Gmail, apply the label "{HELPDESK_LABEL}"   ║
║  to any email. ResolveIQ will process it automatically.  ║
╚══════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    print_banner()

    # Check dependencies
    missing = []
    for pkg in ["google.auth", "google_auth_oauthlib", "googleapiclient"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"❌  Missing packages: {', '.join(missing)}")
        print("\nRun:  pip install google-auth google-auth-oauthlib google-api-python-client\n")
        sys.exit(1)

    if not check_credentials():
        sys.exit(1)

    run_auth()
