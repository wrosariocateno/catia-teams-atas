import os
import json
import requests
from flask import jsonify

# ------------------------------------------------------------------------------
# Config - vem das variáveis de ambiente do serviço
# ------------------------------------------------------------------------------

TENANT_ID = os.environ.get("TENANT_ID")
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
USER_UPN = os.environ.get("USER_UPN")  # ex: nelson.pinheiro@cateno.com.br

GRAPH_TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_graph_token() -> str:
    """
    Obtém um access token para o Microsoft Graph via client_credentials.
    """
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Variáveis TENANT_ID, CLIENT_ID ou CLIENT_SECRET não configuradas.")

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    resp = requests.post(GRAPH_TOKEN_URL, data=data)
    resp.raise_for_status()

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Token do Graph não retornado.")
    return token


def list_recordings_from_user(max_files: int = 5):
    """
    Lista arquivos .mp4 da pasta Recordings do OneDrive do usuário USER_UPN.
    """
    token = get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    # /users/{user}/drive/root:/Recordings:/children
    url = f"{GRAPH_BASE_URL}/users/{USER_UPN}/drive/root:/Recordings:/children"

    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    data = resp.json()
    items = data.get("value", [])

    mp4_items = [
        {
            "name": item.get("name"),
            "id": item.get("id"),
            "webUrl": item.get("webUrl"),
            "lastModifiedDateTime": item.get("lastModifiedDateTime"),
            "size": item.get("size"),
        }
        for item in items
        if item.get("file") and item.get("name", "").lower().endswith(".mp4")
    ]

    # Ordena por data de modificação (mais recentes primeiro)
    mp4_items.sort(
        key=lambda x: x.get("lastModifiedDateTime", ""),
        reverse=True,
    )

    return mp4_items[:max_files]


def list_shared_mp4_from_user(max_files: int = 5):
    """
    Lista arquivos .mp4 que foram compartilhados com o usuário USER_UPN
    (visão 'Compartilhados comigo' / sharedWithMe).
    """
    token = get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    # sharedWithMe = tudo que foi compartilhado com esse usuário
    url = f"{GRAPH_BASE_URL}/users/{USER_UPN}/drive/sharedWithMe"

    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    data = resp.json()
    items = data.get("value", [])

    mp4_items = []

    for item in items:
        # Em sharedWithMe, o item real costuma estar dentro de remoteItem
        remote = item.get("remoteItem") or item

        name = (remote.get("name") or "").lower()
        if not name.endswith(".mp4"):
            continue

        mp4_items.append(
            {
                "name": remote.get("name"),
                "id": remote.get("id"),
                "webUrl": remote.get("webUrl"),
                "lastModifiedDateTime": remote.get("lastModifiedDateTime"),
                "size": remote.get("size"),
            }
        )

    # Ordena por data de modificação (mais recentes primeiro)
    mp4_items.sort(
        key=lambda x: x.get("lastModifiedDateTime", ""),
        reverse=True,
    )

    return mp4_items[:max_files]


# ------------------------------------------------------------------------------
# Função de entrada HTTP (entrypoint) - NÃO TROCAR O NOME
# ------------------------------------------------------------------------------

def hello_http(request):
    """
    Função HTTP única.
    - GET /                        -> health check
    - GET /recordings-teste?limit=N -> lista .mp4 da pasta Recordings
    - GET /shared-teste?limit=N     -> lista .mp4 de 'Compartilhados comigo'
    """
    try:
        path = request.path or "/"
        limit_param = request.args.get("limit", "5")

        try:
            limit = int(limit_param)
        except ValueError:
            limit = 5

        if not USER_UPN:
            return jsonify({"error": "USER_UPN não configurado"}), 500

        # Rota de teste da visão "Compartilhados comigo"
        if path.endswith("/shared-teste"):
            shared_files = list_shared_mp4_from_user(max_files=limit)
            return jsonify(
                {
                    "user": USER_UPN,
                    "origem": "sharedWithMe",
                    "total_retornado": len(shared_files),
                    "arquivos": shared_files,
                }
            )

        # Rota de teste das gravações (pasta Recordings)
        if path.endswith("/recordings-teste"):
            recordings = list_recordings_from_user(max_files=limit)
            return jsonify(
                {
                    "user": USER_UPN,
                    "origem": "Recordings",
                    "total_retornado": len(recordings),
                    "arquivos": recordings,
                }
            )

        # Rota padrão: health
        return jsonify({"status": "ok", "msg": "catia-teams-atas2 rodando"}), 200

    except requests.HTTPError as e:
        # Erros vindos do Graph
        return jsonify(
            {
                "error": "Erro ao chamar Graph",
                "detalhe": str(e),
                "body": e.response.text,
            }
        ), 500
    except Exception as e:
        # Qualquer outro erro inesperado
        return jsonify({"error": "Erro inesperado", "detalhe": str(e)}), 500
