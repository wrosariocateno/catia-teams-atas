import os
import json
import msal
from google.cloud import secretmanager

GCP_PROJECT = os.environ.get("GCP_PROJECT")
TOKEN_CACHE_SECRET_ID = os.environ.get("TOKEN_CACHE_SECRET_ID", "msal-token-cache")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
TENANT_ID = os.environ.get("TENANT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
OAUTH_SCOPES = os.environ.get("OAUTH_SCOPES", "User.Read Files.Read.All Mail.Send").split()

def sm_client():
    return secretmanager.SecretManagerServiceClient()

def read_token_cache(user_key=None):
    if user_key:
        secret_id = f"msal-cache-{user_key.replace('@', '-').replace('.', '-')}"
        try:
            name = f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"
            resp = sm_client().access_secret_version(request={"name": name})
            return resp.payload.data.decode("utf-8")
        except Exception:
            pass
    try:
        name = f"projects/{GCP_PROJECT}/secrets/{TOKEN_CACHE_SECRET_ID}/versions/latest"
        resp = sm_client().access_secret_version(request={"name": name})
        return resp.payload.data.decode("utf-8")
    except Exception:
        return ""

def write_token_cache(payload, user_key=None):
    secret_id = f"msal-cache-{user_key.replace('@', '-').replace('.', '-')}" if user_key else TOKEN_CACHE_SECRET_ID
    parent = f"projects/{GCP_PROJECT}"
    secret_path = f"{parent}/secrets/{secret_id}"
    try:
        sm_client().get_secret(request={"name": secret_path})
    except Exception:
        sm_client().create_secret(request={"parent": parent, "secret_id": secret_id, "secret": {"replication": {"automatic": {}}}})
    sm_client().add_secret_version(request={"parent": secret_path, "payload": {"data": payload.encode("utf-8")}})

def acquire_delegated_token(user_key=None):
    cache = msal.SerializableTokenCache()
    cached = read_token_cache(user_key)
    if cached:
        cache.deserialize(cached)
    
    msal_app = msal.ConfidentialClientApplication(
        CLIENT_ID, client_credential=CLIENT_SECRET, authority=AUTHORITY, token_cache=cache
    )
    
    accounts = msal_app.get_accounts()
    if not accounts:
        return None, "Nenhuma conta no cache. Rode /auth-start."
    
    result = msal_app.acquire_token_silent(OAUTH_SCOPES, account=accounts[0])
    if cache.has_state_changed:
        write_token_cache(cache.serialize(), user_key)
        
    return result.get("access_token"), None if result else "Erro ao obter token."