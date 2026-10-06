"""
src/auth.py
Akamai EdgeAuth URL Token Generator for ESPNcricinfo internal consumer API.
"""
import hashlib
import hmac
import os
import time

# Default key used by ESPNcricinfo consumer client
_DEFAULT_KEY_HEX = "9ced54a89687e1173e91c1f225fc02abf275a119fda8a41d731d2b04dac95ff5"
_RAW_KEY = os.environ.get("AKAMAI_KEY", _DEFAULT_KEY_HEX)
AKAMAI_KEY = bytes.fromhex(_RAW_KEY)
SAFE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_!().\x27")

def js_escape_early(s: str) -> str:
    """Exact JavaScript encodeURIComponent and replace logic for Akamai HMAC."""
    return "".join(chr(b) if chr(b) in SAFE_CHARS else f"%{b:02x}" for b in s.encode("utf-8"))

def generate_url_token(path: str, window_seconds: int = 90) -> str:
    """
    Generate an Akamai HMAC-SHA256 signature (x-hsci-auth-token) for a target URL path.
    """
    exp = int(time.time()) + window_seconds
    o = [f"exp={exp}"]
    a = list(o)
    a.append(f"url={js_escape_early(path)}")
    data_to_sign = "~".join(a).encode("utf-8")
    sig = hmac.new(AKAMAI_KEY, data_to_sign, hashlib.sha256).hexdigest()
    o.append(f"hmac={sig}")
    return "~".join(o)
