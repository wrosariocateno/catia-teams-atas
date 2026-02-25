import requests
import base64

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

def download_driveitem_to_tmp(token, drive_id, item_id, tmp_path):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
    r = requests.get(url, headers=headers, stream=True)
    r.raise_for_status()
    with open(tmp_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            f.write(chunk)
    return tmp_path

def send_email_graph(token, to_email, subject, body_text, attachment_path=None):
    url = f"{GRAPH_BASE_URL}/me/sendMail"
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if attachment_path:
        with open(attachment_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "ata.pdf",
            "contentBytes": content
        }]
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={"message": message})
    r.raise_for_status()