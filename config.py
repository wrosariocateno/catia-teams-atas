import os

# ----------------------------
# ENV / Configurações
# ----------------------------
TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI")

GCP_PROJECT = os.environ.get("GCP_PROJECT")
TOKEN_CACHE_SECRET_ID = os.environ.get("TOKEN_CACHE_SECRET_ID", "msal-token-cache")

# Vertex AI / Gemini
VERTEX_PROJECT = os.environ.get("VERTEX_PROJECT") or GCP_PROJECT
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "southamerica-east1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

# IMPORTANTE: Nao inclua 'openid', 'profile', 'offline_access' aqui.
OAUTH_SCOPES = os.environ.get(
    "OAUTH_SCOPES",
    "User.Read Files.Read.All Mail.Send"
).split()

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

COOKIE_SECRET = os.environ.get("COOKIE_SECRET")
FLOW_COOKIE_NAME = "msal_flow"

# Limites / timeouts
MAX_DOWNLOAD_BYTES = int(os.environ.get("MAX_DOWNLOAD_BYTES", str(60 * 1024 * 1024)))  # 60MB default
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))  # segundos