import os
import json
import secrets
import base64
import hmac
import hashlib
import traceback
import subprocess
import glob
from typing import Optional, Tuple

import requests
import msal
from flask import Flask, jsonify, redirect, make_response, request
from google.cloud import secretmanager
from google.cloud import speech

import vertexai
from vertexai.generative_models import GenerativeModel
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT
from weasyprint import HTML, CSS


app = Flask(__name__)

# ----------------------------
# ENV
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

# IMPORTANTE:
# Nao inclua 'openid', 'profile', 'offline_access' aqui.
# O MSAL considera esses "reserved" e pode falhar no initiate_auth_code_flow.
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
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))  # segundos (download pode demorar)


# ----------------------------
# Secret Manager
# ----------------------------
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
        # Sem cache ainda ou sem permissão -> retorna vazio e força /auth-start
        return ""


def write_token_cache(payload: str):
    parent = f"projects/{GCP_PROJECT}/secrets/{TOKEN_CACHE_SECRET_ID}"
    sm_client().add_secret_version(
        request={"parent": parent, "payload": {"data": payload.encode("utf-8")}}
    )


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


# ----------------------------
# MSAL helpers
# ----------------------------
def build_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cached = read_token_cache()
    if cached:
        cache.deserialize(cached)
    return cache


def save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        write_token_cache(cache.serialize())


def build_msal_app(cache: msal.SerializableTokenCache):
    return msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
        token_cache=cache,
    )


def acquire_delegated_token() -> Tuple[Optional[str], Optional[str]]:
    """
    Tenta obter access token delegated do cache (silent).
    Se não existir cache/conta, retorna erro pedindo /auth-start.
    """
    cache = build_cache()
    msal_app = build_msal_app(cache)

    accounts = msal_app.get_accounts()
    if not accounts:
        return None, "Nenhuma conta no cache. Rode /auth-start e faça login 1x."

    result = msal_app.acquire_token_silent(OAUTH_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        return None, f"Falha ao obter token silencioso: {result}"

    save_cache(cache)
    return result["access_token"], None


# ----------------------------
# Helpers: Graph / Download
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


def download_driveitem_to_tmp(token: str, drive_id: str, item_id: str, tmp_path: str = "/tmp/meeting.mp4") -> str:
    """
    Baixa o arquivo do Graph (driveItem) para /tmp.
    Usa /content (que normalmente responde com redirect Location).
    """
    headers = {"Authorization": f"Bearer {token}"}

    content_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
    r = requests.get(content_url, headers=headers, allow_redirects=False, timeout=HTTP_TIMEOUT)
    r.raise_for_status()

    download_url = r.headers.get("Location")
    if not download_url:
        # Em alguns casos, o Graph pode responder direto (sem redirect). Tenta allow_redirects=True:
        r2 = requests.get(content_url, headers=headers, stream=True, timeout=HTTP_TIMEOUT)
        r2.raise_for_status()
        download_url = r2.url
        # se r2 já estiver com stream aberto, vamos salvar a partir dele
        # mas para simplicidade: reaproveita r2 iter_content
        total = 0
        with open(tmp_path, "wb") as f:
            for chunk in r2.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Arquivo grande demais (>{MAX_DOWNLOAD_BYTES} bytes). Ajuste MAX_DOWNLOAD_BYTES.")
        return tmp_path

    total = 0
    with requests.get(download_url, stream=True, timeout=HTTP_TIMEOUT) as dl:
        dl.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Arquivo grande demais (>{MAX_DOWNLOAD_BYTES} bytes). Ajuste MAX_DOWNLOAD_BYTES.")

    return tmp_path


# ----------------------------
# Áudio / Speech
# ----------------------------
def extract_audio_wav(mp4_path: str) -> str:
    """
    Extrai WAV 16kHz mono PCM do MP4 usando ffmpeg.
    """
    wav_path = mp4_path.rsplit(".", 1)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-i", mp4_path, "-ac", "1", "-ar", "16000", "-f", "wav", wav_path]
    subprocess.check_call(cmd)
    return wav_path


def split_wav_ffmpeg(wav_path: str, segment_seconds: int = 55) -> list[str]:
    """
    Divide um WAV em segmentos menores (<60s) para usar recognize síncrono sem bucket.
    """
    base = wav_path.rsplit(".", 1)[0]
    out_pattern = f"{base}_part_%03d.wav"

    cmd = [
        "ffmpeg", "-y",
        "-i", wav_path,
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-c", "copy",
        out_pattern
    ]
    subprocess.check_call(cmd)

    parts = sorted(glob.glob(f"{base}_part_*.wav"))
    if not parts:
        raise RuntimeError("Falha ao dividir o WAV (nenhuma parte gerada).")
    return parts


def transcribe_wav_chunked(wav_path: str, language_code: str = "pt-BR", segment_seconds: int = 55) -> str:
    """
    Transcreve WAV dividindo em chunks para evitar limite de ~60s do recognize síncrono.
    """
    client = speech.SpeechClient()
    parts = split_wav_ffmpeg(wav_path, segment_seconds=segment_seconds)

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=language_code,
        enable_automatic_punctuation=True,
        # Se der erro com "latest_long" em recognize, comente a linha abaixo:
        model="latest_long",
    )

    transcripts = []
    for part in parts:
        with open(part, "rb") as f:
            audio_content = f.read()

        audio = speech.RecognitionAudio(content=audio_content)
        resp = client.recognize(config=config, audio=audio)

        chunk_text = " ".join([r.alternatives[0].transcript for r in resp.results]).strip()
        if chunk_text:
            transcripts.append(chunk_text)

    return "\n".join(transcripts).strip()


# ----------------------------
# Gemini (ata)
# ----------------------------
def generate_minutes_with_gemini(transcript: str, meeting_title: str = "", meeting_url: str = "") -> str:
    vertexai.init(project=VERTEX_PROJECT, location=VERTEX_LOCATION)
    model = GenerativeModel(GEMINI_MODEL)

    prompt = f"""
Você é um assistente que escreve ATAS de reunião de forma objetiva e profissional.

IMPORTANTE:
- Sempre inclua no topo da ata o campo "Link da gravação:".
- Se o link não estiver disponível, escreva "A implementar".

Contexto:
- Título/arquivo: {meeting_title}
- Link da gravação: {meeting_url}

Gere uma ATA em português, com esta estrutura EXATA:

CABEÇALHO
- Assunto:
- Data:
- Horário:
- Arquivo de origem:
- Link da gravação:

1) Resumo executivo (5-10 linhas)
2) Participantes (se não estiver explícito, escreva "A implementar")
3) Pauta / tópicos discutidos (bullet points)
4) Decisões tomadas (bullet points; se não houver, diga "não identificado")
5) Ações e responsáveis (tabela: Ação | Responsável | Prazo | Status)
6) Pendências / riscos / próximos passos

Transcrição:
\"\"\"
{transcript}
\"\"\"
""".strip()

    resp = model.generate_content(prompt)
    return (getattr(resp, "text", "") or "").strip()

def generate_pdf_minutes(minutes_text: str, out_path: str = "/tmp/ATA_TESTE_POC.pdf", title: str = "Ata de Reunião (POC - CatIA)") -> str:
    # Converte texto simples em HTML básico com quebras e bullets
    # (Sem depender de markdown. Se quiser, o Gemini pode gerar HTML direto no futuro.)
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = (minutes_text or "").splitlines()

    html_parts = []
    in_ul = False

    for raw in lines:
        line = raw.strip()
		# remove separadores tipo "***" ou "---"
        if line and set(line) <= {"*", "-", "_"}:
            continue

        if not line:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append("<div class='spacer'></div>")
            continue

        # títulos simples
        if line.startswith("### "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(f"<h3>{esc(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(f"<h2>{esc(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append(f"<h1>{esc(line[2:])}</h1>")
            continue

        # bullets
        if line.startswith(("* ", "- ")):
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{esc(line[2:])}</li>")
            continue

        # texto normal
        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        html_parts.append(f"<p>{esc(line)}</p>")

    if in_ul:
        html_parts.append("</ul>")

    html_body = "\n".join(html_parts)

    html_doc = f"""
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    @page {{
      size: A4;
      margin: 20mm;
    }}
    body {{
      font-family: Arial, Helvetica, sans-serif;
      color: #1f2937;
      line-height: 1.35;
      font-size: 11pt;
    }}
    .header {{
      border-bottom: 2px solid #111827;
      padding-bottom: 8px;
      margin-bottom: 14px;
    }}
    .header .title {{
      font-size: 20pt;
      font-weight: 700;
      margin: 0;
    }}
    .spacer {{
      height: 8px;
    }}
    h1, h2, h3 {{
      margin: 14px 0 6px 0;
      font-weight: 700;
      color: #111827;
    }}
    h1 {{ font-size: 18pt; }}
    h2 {{ font-size: 14pt; }}
    h3 {{ font-size: 12pt; }}
    p {{
      margin: 0 0 6px 0;
      white-space: pre-wrap;
    }}
    ul {{
      margin: 0 0 6px 18px;
      padding: 0;
    }}
    li {{
      margin: 0 0 4px 0;
    }}
    /* “Card” leve para destacar o cabeçalho gerado pelo Gemini */
    .content {{
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 14px 14px 10px 14px;
      background: #ffffff;
    }}
    /* Rodapé com numeração */
    footer {{
      position: running(footer);
      font-size: 9pt;
      color: #6b7280;
    }}
    @page {{
      @bottom-right {{
        content: "Página " counter(page) " de " counter(pages);
        font-size: 9pt;
        color: #6b7280;
      }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <p class="title">{title}</p>
  </div>

  <div class="content">
    {html_body}
  </div>
</body>
</html>
""".strip()

    HTML(string=html_doc).write_pdf(out_path)
    return out_path

# ----------------------------
# E-mail (Graph)
# ----------------------------
def send_email_graph(token: str, to_email: str, subject: str, body_text: str,
                     attachment_path: Optional[str] = None,
                     attachment_name: str = "ata.pdf"):
    url = f"{GRAPH_BASE_URL}/me/sendMail"

    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }

    if attachment_path:
        with open(attachment_path, "rb") as f:
            content_bytes = base64.b64encode(f.read()).decode("utf-8")

        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": attachment_name,
                "contentType": "application/pdf",
                "contentBytes": content_bytes
            }
        ]

    payload = {"message": message, "saveToSentItems": True}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return True



# ----------------------------
# Rotas
# ----------------------------
@app.get("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "catia-teams-atas2",
        "vertex_project": VERTEX_PROJECT,
        "vertex_location": VERTEX_LOCATION,
        "gemini_model": GEMINI_MODEL,
        "max_download_bytes": MAX_DOWNLOAD_BYTES
    }), 200


@app.get("/auth-start")
def auth_start():
    needed = [TENANT_ID, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, GCP_PROJECT, COOKIE_SECRET]
    if not all(needed):
        return jsonify({
            "error": "Faltam env vars",
            "needed": ["TENANT_ID", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "GCP_PROJECT", "COOKIE_SECRET"]
        }), 500

    cache = build_cache()
    msal_app = build_msal_app(cache)

    state = secrets.token_urlsafe(16)
    flow = msal_app.initiate_auth_code_flow(
        scopes=OAUTH_SCOPES,
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
    msal_app = build_msal_app(cache)

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
        "msg": "Autenticado! Token cache salvo no Secret Manager.",
        "scope": result.get("scope"),
        "expires_in": result.get("expires_in"),
    }))
    resp.set_cookie(FLOW_COOKIE_NAME, "", max_age=0)
    return resp


@app.get("/whoami")
def whoami():
    token, err = acquire_delegated_token()
    if err:
        return jsonify({"error": err}), 401

    url = f"{GRAPH_BASE_URL}/me"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return jsonify(r.json()), 200


@app.get("/shared-search-teste")
def shared_search_teste():
    """
    Busca driveItems via Microsoft Search.
    Ex:
      /shared-search-teste?limit=20&q=Grava%C3%A7%C3%A3o%20de%20Reuni%C3%A3o%20mp4
    """
    token, err = acquire_delegated_token()
    if err:
        return jsonify({"error": err}), 401

    limit = int(request.args.get("limit", "20"))
    limit = max(1, min(limit, 50))

    q = request.args.get("q") or 'filetype:mp4 AND ("Gravação de Reunião" OR "Meeting Recording" OR Recordings)'

    url = f"{GRAPH_BASE_URL}/search/query"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    body = {
        "requests": [
            {
                "entityTypes": ["driveItem"],
                "query": {"queryString": q},
                "from": 0,
                "size": limit,
                "fields": ["id", "name", "webUrl", "lastModifiedDateTime", "size", "parentReference"]
            }
        ]
    }

    r = requests.post(url, headers=headers, json=body, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    hits = data.get("value", [{}])[0].get("hitsContainers", [{}])[0].get("hits", [])

    results = []
    for h in hits:
        res = h.get("resource", {}) or {}
        name = (res.get("name") or "")
        parent = res.get("parentReference") or {}
        results.append({
            "name": name,
            "webUrl": res.get("webUrl"),
            "lastModifiedDateTime": res.get("lastModifiedDateTime"),
            "size": res.get("size"),
            "driveId": parent.get("driveId"),
            "itemId": res.get("id"),
        })

    # ordena por data desc
    results.sort(key=lambda x: x.get("lastModifiedDateTime", ""), reverse=True)

    return jsonify({
        "origem": "Microsoft Search (driveItem)",
        "query": q,
        "total": len(results),
        "arquivos": results
    }), 200


@app.post("/generate-minutes-and-email")
def generate_minutes_and_email():
    """
    Body JSON:
      {
        "driveId": "...",
        "itemId": "...",
        "to": "email@dominio.com",
        "subject": "Ata ...",
        "language": "pt-BR" (opcional)
      }
    """
    token, err = acquire_delegated_token()
    if err:
        return jsonify({"error": err}), 401

    payload = request.get_json(silent=True) or {}
    drive_id = (payload.get("driveId") or "").strip()
    item_id = (payload.get("itemId") or "").strip()
    to_email = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "Ata automática").strip()
    language = (payload.get("language") or "pt-BR").strip()

    if not drive_id or not item_id:
        return jsonify({"error": "Passe driveId e itemId no JSON."}), 400
    if not to_email:
        return jsonify({"error": "Passe o campo 'to' com o e-mail de destino."}), 400

    try:
        # 1) Metadados (nome, webUrl, size)
        meta_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        item = graph_get_json(meta_url, token)

        name = item.get("name")
        web_url = item.get("webUrl")
        size = item.get("size")

        # 2) Download MP4
        mp4_path = "/tmp/meeting.mp4"
        download_driveitem_to_tmp(token, drive_id, item_id, tmp_path=mp4_path)

        # 3) Extrai WAV
        wav_path = extract_audio_wav(mp4_path)

        # 4) Transcreve (sem bucket) usando chunks
        transcript = transcribe_wav_chunked(wav_path, language_code=language, segment_seconds=55)
        if not transcript:
            transcript = "(transcrição vazia / não reconhecida)"

        # 5) Gera ata com Gemini
        minutes_text = generate_minutes_with_gemini(
            transcript=transcript,
            meeting_title=name or "",
            meeting_url=web_url or ""
        )

        # 5.1) Gera PDF da ata
        pdf_path = "/tmp/ata.pdf"
        generate_pdf_minutes(minutes_text, out_path=pdf_path, title="Ata de Reunião")

        # 6) Envia e-mail COM ANEXO (corpo curto)
        email_body = (
            "Olá!\n\n"
            "Segue em anexo a ata gerada automaticamente a partir da gravação da reunião.\n\n"
            f"Arquivo: {name or '(não identificado)'}\n"
            f"Link da gravação: {web_url or 'A implementar'}\n\n"
            "Att."
        )

        send_email_graph(
            token,
            to_email=to_email,
            subject=subject,
            body_text=email_body,
            attachment_path=pdf_path,
            attachment_name="ata.pdf"
        )


        return jsonify({
            "status": "ok",
            "sent_to": to_email,
            "subject": subject,
            "driveId": drive_id,
            "itemId": item_id,
            "fileName": name,
            "fileSize": size,
            "webUrl": web_url,
            "transcript_chars": len(transcript or ""),
            "minutes_chars": len(minutes_text or ""),
			"pdf_generated": True

        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "Falha ao processar áudio (ffmpeg).",
            "detail": str(e),
            "hint": "Garanta que o ffmpeg está instalado na imagem (use Dockerfile com apt-get ffmpeg)."
        }), 500

    except requests.HTTPError as e:
        return jsonify({
            "error": "HTTPError ao chamar APIs externas (Graph/download/sendMail).",
            "detail": str(e),
            "status_code": getattr(e.response, "status_code", None),
            "body": getattr(e.response, "text", None),
        }), 500

    except Exception as e:
        return jsonify({
            "error": "Falha ao gerar e enviar ata.",
            "type": type(e).__name__,
            "detail": str(e),
        }), 500

@app.post("/transcribe-only")
def transcribe_only():
    """
    Body JSON:
      {
        "driveId": "...",
        "itemId": "...",
        "language": "pt-BR" (opcional),
        "segmentSeconds": 55 (opcional)
      }

    Retorna a transcrição (sem Gemini e sem envio de e-mail).
    NÃO altera nada do fluxo que já funciona.
    """
    token, err = acquire_delegated_token()
    if err:
        return jsonify({"error": err}), 401

    payload = request.get_json(silent=True) or {}
    drive_id = (payload.get("driveId") or "").strip()
    item_id = (payload.get("itemId") or "").strip()
    language = (payload.get("language") or "pt-BR").strip()
    segment_seconds = int(payload.get("segmentSeconds") or 55)

    if not drive_id or not item_id:
        return jsonify({"error": "Passe driveId e itemId no JSON."}), 400

    try:
        # 1) Metadados (opcional, mas útil pro retorno)
        meta_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        item = graph_get_json(meta_url, token)
        name = item.get("name")
        web_url = item.get("webUrl")
        size = item.get("size")

        # 2) Download MP4
        mp4_path = "/tmp/meeting.mp4"
        download_driveitem_to_tmp(token, drive_id, item_id, tmp_path=mp4_path)

        # 3) Extrai WAV
        wav_path = extract_audio_wav(mp4_path)

        # 4) Transcreve (chunked, sem bucket)
        transcript = transcribe_wav_chunked(
            wav_path,
            language_code=language,
            segment_seconds=segment_seconds
        )

        return jsonify({
            "status": "ok",
            "driveId": drive_id,
            "itemId": item_id,
            "fileName": name,
            "fileSize": size,
            "webUrl": web_url,
            "language": language,
            "segmentSeconds": segment_seconds,
            "transcript_chars": len(transcript or ""),
            "transcript": transcript or ""
        }), 200

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "Falha ao processar áudio (ffmpeg).",
            "detail": str(e),
            "hint": "Garanta que o ffmpeg está instalado na imagem."
        }), 500

    except requests.HTTPError as e:
        return jsonify({
            "error": "HTTPError ao chamar APIs externas (Graph/download).",
            "detail": str(e),
            "status_code": getattr(e.response, "status_code", None),
            "body": getattr(e.response, "text", None),
        }), 500

    except Exception as e:
        return jsonify({
            "error": "Falha ao transcrever.",
            "type": type(e).__name__,
            "detail": str(e),
        }), 500

# ----------------------------
# Error handler (JSON)
# ----------------------------
@app.errorhandler(Exception)
def handle_exception(e):
    print("UNHANDLED_EXCEPTION:", repr(e))
    traceback.print_exc()
    return jsonify({
        "error": "Unhandled exception",
        "type": type(e).__name__,
        "detail": str(e),
    }), 500
