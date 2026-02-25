import json
import base64
import hmac
import hashlib
from config import COOKIE_SECRET

# ----------------------------
# Cookie signing (state/flow)
# ----------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

def sign_payload(payload_dict: dict) -> str:
    raw = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(COOKIE_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url_encode(raw)}.{_b64url_encode(sig)}"

def verify_and_load(token: str):
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64url_decode(raw_b64)
        sig = _b64url_decode(sig_b64)
        expected = hmac.new(COOKIE_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None