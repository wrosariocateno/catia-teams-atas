import secrets
import traceback
import requests
import sys
import os
from flask import Flask, jsonify, redirect, make_response, request
from urllib.parse import unquote

# Importações explícitas dos módulos refatorados
from config import *
from utils import sign_payload, verify_and_load
from msal_auth import build_cache, build_msal_app, acquire_delegated_token, save_cache
from graph_api import graph_get_json, get_participants_from_meeting, download_driveitem_to_tmp, send_email_graph
from audio_processing import extract_audio_wav, transcribe_wav_chunked
from gemini_ata import generate_minutes_with_gemini, generate_pdf_minutes

# NOVO MÓDULO DE CONVIDADOS
from meeting_guests import get_meeting_guests

app = Flask(__name__)

# Forçar o log para o stdout imediatamente para debug no Cloud Run
def log_debug(msg):
    print(f">>> DEBUG: {msg}", file=sys.stdout)
    sys.stdout.flush()

# ----------------------------
# Rotas de Saúde e Autenticação
# ----------------------------

@app.get("/")
def health():
    return jsonify({
        "status": "ok", 
        "service": "catia-teams-atas2",
        "config": {
            "gemini_model": GEMINI_MODEL,
            "vertex_location": VERTEX_LOCATION
        }
    }), 200

@app.get("/auth-start")
def auth_start():
    try:
        cache = build_cache()
        msal_app = build_msal_app(cache)
        state = secrets.token_urlsafe(16)
        flow = msal_app.initiate_auth_code_flow(scopes=OAUTH_SCOPES, redirect_uri=REDIRECT_URI, state=state)
        cookie_value = sign_payload(flow)
        resp = make_response(redirect(flow["auth_uri"], code=302))
        resp.set_cookie(FLOW_COOKIE_NAME, cookie_value, httponly=True, secure=True, samesite="Lax", max_age=600)
        return resp
    except Exception as e:
        return jsonify({"error": "Falha ao iniciar auth", "detail": str(e)}), 500

@app.get("/auth-callback")
def auth_callback():
    try:
        cookie = request.cookies.get(FLOW_COOKIE_NAME)
        flow = verify_and_load(cookie) if cookie else None
        if not flow: return jsonify({"error": "Sessão expirada ou cookie inválido"}), 400
        
        auth_response = request.args.to_dict(flat=True)
        cache = build_cache() 
        msal_app = build_msal_app(cache)
        result = msal_app.acquire_token_by_auth_code_flow(flow, auth_response)
        
        if "access_token" not in result: 
            return jsonify({"error": "Falha ao obter token", "details": result.get("error_description")}), 500

        user_email = result.get("id_token_claims", {}).get("preferred_username")
        save_cache(cache, user_key=user_email)
        
        resp = make_response(jsonify({"status": "ok", "user": user_email}))
        resp.set_cookie(FLOW_COOKIE_NAME, "", max_age=0)
        return resp
    except Exception as e:
        return jsonify({"error": "Falha no callback", "detail": str(e)}), 500

@app.get("/whoami")
def whoami():
    user_key = request.args.get("user")
    token, err = acquire_delegated_token(user_key)
    if err: return jsonify({"error": err}), 401
    
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{GRAPH_BASE_URL}/me", headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return jsonify(r.json()), 200

# ----------------------------
# Rotas de Busca e Operação
# ----------------------------

@app.post("/auto-process-latest")
def auto_process_latest():
    """
    Encontra o último vídeo .mp4 não processado, gera a ata e envia por e-mail.
    """
    user_key = request.args.get("user")
    token, err = acquire_delegated_token(user_key)
    if err: return jsonify({"error": err}), 401

    language = request.args.get("language", "pt-BR")

    try:
        # 1. Buscar arquivos MP4 recentes
        search_url = f"{GRAPH_BASE_URL}/search/query"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        search_body = {
            "requests": [{
                "entityTypes": ["driveItem"],
                "query": {"queryString": "filetype:mp4"},
                "from": 0, "size": 10,
                "fields": ["id", "name", "description", "parentReference", "onlineMeetingInfo"]
            }]
        }
        
        r_search = requests.post(search_url, headers=headers, json=search_body, timeout=HTTP_TIMEOUT)
        r_search.raise_for_status()
        
        hits = r_search.json().get("value", [{}])[0].get("hitsContainers", [{}])[0].get("hits", [])
        
        target_item = None
        for h in hits:
            res = h.get("resource", {})
            # Verificamos se a descrição contém nossa "tag" de processado
            description = res.get("description") or ""
            if "PROCESSED_BY_CATIA" not in description:
                target_item = res
                break
        
        if not target_item:
            return jsonify({"status": "no_new_files", "message": "Nenhum vídeo novo para processar."}), 200

        # 2. Extrair dados do item encontrado
        drive_id = target_item.get("parentReference", {}).get("driveId")
        item_id = target_item.get("id")
        name = target_item.get("name")
        join_url = target_item.get("onlineMeetingInfo", {}).get("joinUrl")
        
        log_debug(f"Processando automaticamente: {name} (ID: {item_id})")

        # 3. Buscar convidados
        lista_emails = get_meeting_guests(token, join_url, name)
        if not lista_emails:
            # Fallback: Se não achar convidados, envia para o próprio usuário logado
            lista_emails = [user_key]
        
        destinatarios_str = "; ".join(lista_emails)

        # 4. Processamento (Download -> Transcrição -> Gemini -> PDF)
        mp4_path = f"/tmp/auto_{item_id}.mp4"
        download_driveitem_to_tmp(token, drive_id, item_id, tmp_path=mp4_path)
        wav_path = extract_audio_wav(mp4_path)
        transcript = transcribe_wav_chunked(wav_path, language_code=language)
        
        #participants = get_participants_from_meeting(token, join_url) if join_url else "Não identificados"
        participants = destinatarios_str

        minutes_text = generate_minutes_with_gemini(
            transcript=transcript, 
            meeting_title=name, 
            meeting_url="Link automático", 
            participants=participants
        )

        pdf_path = f"/tmp/ata_auto_{item_id}.pdf"
        generate_pdf_minutes(minutes_text, out_path=pdf_path, title=f"Ata: {name}")

        # 5. Enviar E-mail
        email_body = f"Olá,\n\nEsta é uma ata gerada automaticamente para a reunião: {name}\n\nO PDF segue em anexo."
        send_email_graph(token, to_email=destinatarios_str, subject=f"Ata Automática: {name}", body_text=email_body, attachment_path=pdf_path)

        # 6. MARCAR COMO PROCESSADO (A "Tag")
        # Atualizamos a descrição do arquivo no SharePoint/OneDrive para não repetir
        patch_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        patch_body = {"description": "PROCESSED_BY_CATIA - Ata enviada com sucesso."}
        requests.patch(patch_url, headers=headers, json=patch_body, timeout=HTTP_TIMEOUT)

        return jsonify({
            "status": "success",
            "file_processed": name,
            "sent_to": destinatarios_str
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.get("/shared-search-teste")
def shared_search_teste():
    raw_user = request.args.get("user", "")
    user_key = unquote(raw_user).strip()
    
    log_debug(f"shared-search-teste: User recebido='{user_key}'")
    
    if not user_key:
        return jsonify({"error": "Parâmetro 'user' (email) é obrigatório"}), 400

    try:
        token, err = acquire_delegated_token(user_key)
        if err: 
            log_debug(f"Erro MSAL para {user_key}: {err}")
            return jsonify({"error": "Token MSAL indisponível", "detail": err}), 401

        limit = min(int(request.args.get("limit", "20")), 50)
        q = request.args.get("q") or "filetype:mp4"

        url = f"{GRAPH_BASE_URL}/search/query"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "requests": [{
                "entityTypes": ["driveItem"], 
                "query": {"queryString": q},
                "from": 0, "size": limit,
                "fields": ["id", "name", "webUrl", "lastModifiedDateTime", "size", "parentReference"]
            }]
        }

        r = requests.post(url, headers=headers, json=body, timeout=HTTP_TIMEOUT)
        
        if r.status_code != 200:
            log_debug(f"Graph Error {r.status_code}: {r.text}")
            return jsonify({
                "error": "Erro no Microsoft Graph",
                "status_code": r.status_code,
                "graph_payload": r.text[:500]
            }), r.status_code
            
        data = r.json()
        hits = data.get("value", [{}])[0].get("hitsContainers", [{}])[0].get("hits", [])
        
        results = []
        for h in hits:
            res = h.get("resource", {})
            parent = res.get("parentReference", {})
            results.append({
                "name": res.get("name"), 
                "webUrl": res.get("webUrl"),
                "lastModifiedDateTime": res.get("lastModifiedDateTime"),
                "driveId": parent.get("driveId"), 
                "itemId": res.get("id"),
            })

        return jsonify({"arquivos": results}), 200
    
    except Exception as e:
        log_debug(f"Exceção em shared-search: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500

@app.post("/generate-minutes-and-email")
def generate_minutes_and_email():
    user_key = request.args.get("user")
    token, err = acquire_delegated_token(user_key)
    if err: return jsonify({"error": err}), 401

    payload = request.get_json(silent=True) or {}
    drive_id = (payload.get("driveId") or "").strip()
    item_id = (payload.get("itemId") or "").strip()
    to_email = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "Ata automática").strip()
    language = (payload.get("language") or "pt-BR").strip()

    if not drive_id or not item_id:
        return jsonify({"error": "Parâmetros driveId e itemId são obrigatórios."}), 400

    try:
        meta_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}?$select=id,name,webUrl,onlineMeetingInfo"
        item = graph_get_json(meta_url, token)
        name = item.get("name")
        web_url = item.get("webUrl")
        join_url = item.get("onlineMeetingInfo", {}).get("joinUrl")
        participants = get_meeting_guests(token, join_url, name) if join_url else "Não identificados"

        lista_emails_reuniao = get_meeting_guests(token, join_url, name)
        todos_destinatarios = set(lista_emails_reuniao)
        
        if to_email:
            for email_avulso in to_email.replace(',', ';').split(';'):
                if email_avulso.strip():
                    todos_destinatarios.add(email_avulso.strip())

        if not todos_destinatarios:
             return jsonify({"error": "Não foi possível encontrar convidados."}), 400

        destinatarios_str = "; ".join(todos_destinatarios)
        mp4_path = f"/tmp/{item_id}.mp4"
        download_driveitem_to_tmp(token, drive_id, item_id, tmp_path=mp4_path)
        wav_path = extract_audio_wav(mp4_path)

        transcript = transcribe_wav_chunked(wav_path, language_code=language)
        minutes_text = generate_minutes_with_gemini(
            transcript=transcript, 
            meeting_title=name, 
            meeting_url=web_url, 
            participants=participants
        )

        pdf_path = f"/tmp/ata_{item_id}.pdf"
        generate_pdf_minutes(minutes_text, out_path=pdf_path, title=f"Ata: {name}")

        email_body = (
            f"Olá,\n\nAta gerada para a reunião: {name}\n"
            f"Link da gravação: {web_url}\n"
            f"Participantes: {participants}\n\n"
            "O arquivo PDF segue em anexo."
        )

        send_email_graph(token, to_email=destinatarios_str, subject=subject, body_text=email_body, attachment_path=pdf_path)

        return jsonify({"status": "sucesso", "email_enviado_para": destinatarios_str}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.post("/transcribe-only")
def transcribe_only():
    user_key = request.args.get("user")
    token, err = acquire_delegated_token(user_key)
    if err: return jsonify({"error": err}), 401

    payload = request.get_json(silent=True) or {}
    drive_id = payload.get("driveId")
    item_id = payload.get("itemId")
    language = payload.get("language", "pt-BR")

    if not drive_id or not item_id:
        return jsonify({"error": "driveId e itemId são necessários."}), 400

    try:
        mp4_path = f"/tmp/transcribe_{item_id}.mp4"
        download_driveitem_to_tmp(token, drive_id, item_id, tmp_path=mp4_path)
        wav_path = extract_audio_wav(mp4_path)
        transcript = transcribe_wav_chunked(wav_path, language_code=language)

        return jsonify({
            "status": "ok",
            "transcript": transcript
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    err_msg = str(e)
    print(f"ERRO CRÍTICO NO HANDLER: {err_msg}")
    traceback.print_exc()
    return make_response(jsonify({
        "error": "Server Error", 
        "type": type(e).__name__,
        "detail": err_msg
    }), 500)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)