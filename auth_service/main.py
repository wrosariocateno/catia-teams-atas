import os
import json
import secrets
import base64
import hmac
import hashlib

import msal
from flask import Flask, jsonify, redirect, make_response, request
from google.cloud import secretmanager

app = Flask(__name__)

TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

# IMPORTANTE: este REDIRECT_URI deve apontar para ESTE serviço (auth) /auth-callback
REDIRECT_URI = os.environ.get("REDIRECT_URI")

GCP_PROJECT = os.environ.get("GCP_PROJECT")
TOKEN_CACHE_SECRET_ID = os.environ.get("TOKEN_CACHE_SECRET_ID", "msal-token-cache")

# Scopes Graph (NÃO inclua openid/profile/offline_access aqui)
GRAPH_SCOPES = os.environ.get(
    "GRAPH_SCOPES",
    "User.Read Files.Read.All Sites.Read.All Mail.Send Chat.ReadWrite.All"
).split()

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

COOKIE_SECRET = os.environ.get("COOKIE_SECRET")
FLOW_COOKIE_NAME = "msal_flow"


# ---------- Secret Manager ----------
def sm_client():
    return secretmanager.SecretManagerServiceClient()


def _secret_latest_name(secret_id: str) -> str:
    return f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"


def read_token_cache() -> str:
    try:
        resp = sm_client().access_secret_version(
            request={"name": _secret_latest_name(TOKEN_CACHE_SECRET_ID)}
        )
        return resp.payload.data.decode("utf-8")
    except Exception:
        return ""


def write_token_cache(payload: str):
    parent = f"projects/{GCP_PROJECT}/secrets/{TOKEN_CACHE_SECRET_ID}"
    sm_client().add_secret_version(
        request={"parent": parent, "payload": {"data": payload.encode("utf-8")}}
    )


# ---------- Cookie signing ----------
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


# ---------- MSAL ----------
def build_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cached = read_token_cache()
    if cached:
        cache.deserialize(cached)
    return cache


def save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        write_token_cache(cache.serialize())


def build_app(cache: msal.SerializableTokenCache):
    return msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
        token_cache=cache,
    )


@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "catia-teams-atas-auth"}), 200


@app.get("/auth-start")
def auth_start():
    needed = [TENANT_ID, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, GCP_PROJECT, COOKIE_SECRET]
    if not all(needed):
        return jsonify({"error": "Faltam env vars", "needed": [
            "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "GCP_PROJECT", "COOKIE_SECRET"
        ]}), 500

    cache = build_cache()
    msal_app = build_app(cache)

    state = secrets.token_urlsafe(16)
    flow = msal_app.initiate_auth_code_flow(
        scopes=GRAPH_SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state,
    )

    cookie_value = sign_payload(flow)
    resp = make_response(redirect(flow["auth_uri"], code=302))
    resp.set_cookie(
        FLOW_COOKIE_NAME,
        cookie_value,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=10 * 60,
    )
    return resp


def save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        write_token_cache(cache.serialize())
        app.logger.info("cache saved")


@app.get("/auth-callback")
def auth_callback():
    cookie = request.cookies.get(FLOW_COOKIE_NAME)
    if not cookie:
        return jsonify({"error": "Cookie do flow nao encontrado. Rode /auth-start novamente."}), 400

    flow = verify_and_load(cookie)
    if not flow:
        return jsonify({"error": "Cookie do flow invalido/alterado. Rode /auth-start novamente."}), 400

    auth_response = request.args.to_dict(flat=True)

    cache = build_cache()
    msal_app = build_app(cache)

    result = msal_app.acquire_token_by_auth_code_flow(flow, auth_response)
    if "access_token" not in result:
        return jsonify({
            "error": "Falha ao obter token",
            "msal_error": result.get("error"),
            "msal_error_description": result.get("error_description"),
        }), 500

    save_cache(cache)

    resp = make_response(jsonify({
        "status": "ok",
        "msg": "Autenticado! Token cache (com refresh) salvo no Secret Manager.",
        "scope": result.get("scope"),
        "expires_in": result.get("expires_in"),
    }))
    resp.set_cookie(FLOW_COOKIE_NAME, "", max_age=0)
    return resp
