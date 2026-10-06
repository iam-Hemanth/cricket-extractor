#!/usr/bin/env python3
"""
scripts/refresh_akamai_key.py
Automated Akamai EdgeAuth Encryption Key Extractor for ESPNcricinfo.
Directly inspects the live Next.js client-side bundle and extracts the
active HMAC-SHA256 secret key from the client webpack bundle.
"""
import re
import sys
from curl_cffi import requests

MATCH_URL = "https://www.espncricinfo.com/series/icc-men-s-t20-world-cup-2024-1411166/india-vs-australia-51st-match-super-eights-group-1-1415751/ball-by-ball-commentary"


def extract_current_cricinfo_key():
    print("Inspecting live ESPNcricinfo client bundle for Akamai EdgeAuth key...")
    try:
        r = requests.get(MATCH_URL, impersonate="chrome124", timeout=15)
        if r.status_code != 200:
            return None, f"Failed fetching match page: HTTP {r.status_code}"

        # 1. Locate the active _app chunk URL
        m_app = re.search(
            r"src=[\x22\x27](https://wassets\.hscicdn\.com/_next/static/chunks/pages/_app-[^\x22\x27]+\.js)[\x22\x27]",
            r.text
        )
        if not m_app:
            return None, "Could not locate _app bundle script in HTML"

        app_url = m_app.group(1)
        print(f"Found active bundle: {app_url}")

        # 2. Fetch the bundle script
        app_js = requests.get(app_url, impersonate="chrome124", timeout=15).text

        # 3. Extract the edgeAuth encryptionKey
        m_key = re.search(r"edgeAuth:\s*\{\s*encryptionKey:\s*[\x22\x27]([a-f0-9]{64})[\x22\x27]", app_js)
        if not m_key:
            return None, "Could not find edgeAuth.encryptionKey pattern in bundle"

        key = m_key.group(1)
        return key, None

    except Exception as e:
        return None, str(e)


def main():
    key, err = extract_current_cricinfo_key()
    if err or not key:
        print(f"❌ Failed to extract key: {err}", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("✅ ACTIVE ESPNcricinfo AKAMAI EDGEAUTH KEY:")
    print(key)
    print("=" * 70)
    print("\nIf this key changed, update it in:")
    print("1. Local .env file: AKAMAI_KEY=" + key)
    print("2. GitHub Repository Secrets: https://github.com/iam-Hemanth/cricket-extractor/settings/secrets/actions")


if __name__ == "__main__":
    main()
