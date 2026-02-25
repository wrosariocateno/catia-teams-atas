import requests
import base64
from typing import Optional
from config import GRAPH_BASE_URL, HTTP_TIMEOUT, MAX_DOWNLOAD_BYTES

# ----------------------------
# Helpers: Graph / Download / Email
# ----------------------------
def graph_get_json(url: str, token: str, params: dict = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()

def graph_post_json(url: str, token: str, payload: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json() if r.content else {}

def get_participants_from_meeting(token: str, join_url: str) -> str:
    """Busca os participantes configurados na reunião via JoinWebUrl."""
    if not join_url:
        return "Não identificado"
    
    try:
        url = f"{GRAPH_BASE_URL}/me/onlineMeetings"
        params = {"$filter": f"JoinWebUrl eq '{join_url}'"}
        
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        
        data = r.json()
        meetings = data.get("value", [])
        
        if not meetings:
            return "Não encontrado no Graph"

        meeting = meetings[0]
        parts = []
        
        org = meeting.get("participants", {}).get("organizer", {}).get("upn")
        if org: parts.append(f"{org} (Organizador)")
        
        attendees = meeting.get("participants", {}).get("attendees", [])
        for a in attendees:
            name = a.get("identity", {}).get("user", {}).get("displayName")
            if name: parts.append(name)
            
        return ", ".join(parts) if parts else "A implementar"
    except Exception as e:
        print(f">>> Erro ao buscar participantes: {e}")
        return "Erro ao recuperar participantes"

def download_driveitem_to_tmp(token: str, drive_id: str, item_id: str, tmp_path: str = "/tmp/meeting.mp4") -> str:
    """Baixa o arquivo do Graph (driveItem) para /tmp."""
    headers = {"Authorization": f"Bearer {token}"}
    content_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
    
    r = requests.get(content_url, headers=headers, allow_redirects=False, timeout=HTTP_TIMEOUT)
    r.raise_for_status()

    download_url = r.headers.get("Location")
    if not download_url:
        r2 = requests.get(content_url, headers=headers, stream=True, timeout=HTTP_TIMEOUT)
        r2.raise_for_status()
        download_url = r2.url
        total = 0
        with open(tmp_path, "wb") as f:
            for chunk in r2.iter_content(chunk_size=1024 * 1024):
                if not chunk: continue
                f.write(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Arquivo grande demais (>{MAX_DOWNLOAD_BYTES} bytes).")
        return tmp_path

    total = 0
    with requests.get(download_url, stream=True, timeout=HTTP_TIMEOUT) as dl:
        dl.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                if not chunk: continue
                f.write(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Arquivo grande demais (>{MAX_DOWNLOAD_BYTES} bytes).")

    return tmp_path

def send_email_graph(token: str, to_email: str, subject: str, body_text: str,
                     attachment_path: Optional[str] = None, attachment_name: str = "ata.pdf"):
    url = f"{GRAPH_BASE_URL}/me/sendMail"
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }

    if attachment_path:
        with open(attachment_path, "rb") as f:
            content_bytes = base64.b64encode(f.read()).decode("utf-8")
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment_name,
            "contentType": "application/pdf",
            "contentBytes": content_bytes
        }]

    payload = {"message": message, "saveToSentItems": True}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return True