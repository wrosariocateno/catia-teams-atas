from typing import Optional, Tuple
import msal
from config import CLIENT_ID, CLIENT_SECRET, AUTHORITY, OAUTH_SCOPES
from secrets_manager import read_token_cache, write_token_cache

# ----------------------------
# MSAL helpers
# ----------------------------
def build_cache(user_key: Optional[str] = None) -> msal.SerializableTokenCache:
    """Inicia o cache MSAL carregando dados do Secret Manager."""
    cache = msal.SerializableTokenCache()
    cached = read_token_cache(user_key)
    if cached:
        cache.deserialize(cached)
    return cache

def save_cache(cache: msal.SerializableTokenCache, user_key: Optional[str] = None):
    """Persiste alterações no cache."""
    if cache.has_state_changed:
        write_token_cache(cache.serialize(), user_key)

def build_msal_app(cache: msal.SerializableTokenCache):
    return msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
        token_cache=cache,
    )

def acquire_delegated_token(user_key: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Obtém o token silenciosamente.
    """
    print(f">>> MSAL: Iniciando busca de token para: {user_key or 'GLOBAL'}")

    cache = build_cache(user_key)
    msal_app = build_msal_app(cache)
    accounts = msal_app.get_accounts()
    
    # Se não achar pela chave do usuário, tenta no cache global como fallback
    if not accounts and user_key:
        print(f">>> MSAL: Tentando fallback para cache global...")
        cache = build_cache(None)
        msal_app = build_msal_app(cache)
        accounts = msal_app.get_accounts()

    if not accounts:
        return None, "Nenhuma conta no cache. Rode /auth-start."

    # Tenta encontrar a conta que bate com o e-mail ou pega a primeira
    chosen_account = accounts[0]
    if user_key:
        for acc in accounts:
            if acc.get("username") == user_key:
                chosen_account = acc
                break

    result = msal_app.acquire_token_silent(OAUTH_SCOPES, account=chosen_account)
    
    if not result or "access_token" not in result:
        return None, f"Falha ao obter token silencioso para {user_key}."

    if cache.has_state_changed:
        save_cache(cache, user_key)

    return result["access_token"], None