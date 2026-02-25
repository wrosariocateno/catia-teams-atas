from typing import Optional
from google.cloud import secretmanager
from config import GCP_PROJECT, TOKEN_CACHE_SECRET_ID

# ----------------------------
# Secret Manager
# ----------------------------
def sm_client():
    return secretmanager.SecretManagerServiceClient()

def _secret_latest_name(secret_id: str) -> str:
    return f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"

def read_token_cache(user_key: Optional[str] = None) -> str:
    if user_key:
        # Sanitiza o identificador do usuário 
        secret_id = f"msal-cache-{user_key.replace('@', '-').replace('.', '-')}"
        try:
            name = f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"
            resp = sm_client().access_secret_version(request={"name": name})
            return resp.payload.data.decode("utf-8")
        except Exception:
            pass # Fallback para o cache global se o específico falhar

    try:
        # Tenta o segredo global definido nas ENV vars 
        resp = sm_client().access_secret_version(
            request={"name": _secret_latest_name(TOKEN_CACHE_SECRET_ID)}
        )
        return resp.payload.data.decode("utf-8")
    except Exception:
        return ""

def write_token_cache(payload: str, user_key: Optional[str] = None):
    # Define o ID do segredo: específico do usuário ou global 
    if user_key:
        secret_id = f"msal-cache-{user_key.replace('@', '-').replace('.', '-')}"
    else:
        secret_id = TOKEN_CACHE_SECRET_ID

    parent = f"projects/{GCP_PROJECT}"
    secret_path = f"{parent}/secrets/{secret_id}" 

    try:
        sm_client().get_secret(request={"name": secret_path})
    except Exception:
        # Cria o segredo caso não exista 
        sm_client().create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}}, 
            }
        )

    sm_client().add_secret_version(
        request={"parent": secret_path, "payload": {"data": payload.encode("utf-8")}}
    )