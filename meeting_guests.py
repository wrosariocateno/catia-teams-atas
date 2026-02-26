import requests
import traceback
import re

# URL base do Microsoft Graph API
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
HTTP_TIMEOUT = 15

def clean_meeting_name(name: str) -> str:
    """
    Limpa o nome do arquivo para tentar bater com o assunto do calendário.
    Ex: 'Pegar Participantes Reunião-20260225_161719-Meeting Recording.mp4' 
    vira 'Pegar Participantes Reunião'
    """
    if not name: return ""
    # Remove extensão
    name = name.rsplit('.', 1)[0]
    # Remove sufixos comuns de gravação do Teams
    name = name.replace("-Meeting Recording", "").replace("Meeting Recording", "")
    # Remove padrões de data/hora (Ex: 20260225_161719)
    name = re.sub(r'-\d{8}_\d{6}', '', name)
    name = re.sub(r'\d{8}_\d{6}', '', name)
    return name.strip()

def get_meeting_guests(token: str, join_url: str = None, file_name: str = None) -> list:
    """
    Busca os e-mails de todos os convidados e participantes de uma reunião Teams.
    Utiliza join_url (precisão) e file_name (busca por assunto) para encontrar convidados.
    
    Retorna uma lista de e-mails únicos (strings).
    """
    emails_convidados = set()
    emails_presentes = set()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # ESTRATÉGIA 1: Busca por Join URL no Calendário (Pega todos os convidados do Invite)
    if join_url:
        try:
            params = {
                "$top": 50,
                "$select": "subject,attendees,organizer,onlineMeeting",
                "$orderby": "start/dateTime DESC" 
            }
            resp = requests.get(f"{GRAPH_BASE_URL}/me/events", headers=headers, params=params, timeout=HTTP_TIMEOUT)
            
            if resp.status_code == 200:
                events = resp.json().get("value", [])
                for ev in events:
                    ev_join_url = ev.get("onlineMeeting", {}).get("joinUrl", "")
                    if ev_join_url == join_url:
                        org_email = ev.get("organizer", {}).get("emailAddress", {}).get("address")
                        if org_email: emails_convidados.add(org_email)
                        for att in ev.get("attendees", []):
                            addr = att.get("emailAddress", {}).get("address")
                            if addr: emails_convidados.add(addr)
        except Exception as e:
            print(f">>> Erro Estratégia 1 (URL): {e}")

    # ESTRATÉGIA 2: Relatórios de Presença (Pega quem REALMENTE entrou)
    if join_url:
        try:
            filter_query = f"joinWebUrl eq '{join_url}'"
            resp_m = requests.get(f"{GRAPH_BASE_URL}/me/onlineMeetings?$filter={filter_query}", headers=headers, timeout=HTTP_TIMEOUT)
            if resp_m.status_code == 200:
                meetings = resp_m.json().get("value", [])
                if meetings:
                    m_id = meetings[0].get("id")
                    rep_url = f"{GRAPH_BASE_URL}/me/onlineMeetings/{m_id}/attendanceReports"
                    resp_rep = requests.get(rep_url, headers=headers, timeout=HTTP_TIMEOUT)
                    if resp_rep.status_code == 200:
                        for report in resp_rep.json().get("value", []):
                            r_id = report.get("id")
                            rec_url = f"{GRAPH_BASE_URL}/me/onlineMeetings/{m_id}/attendanceReports/{r_id}/attendanceRecords"
                            resp_rec = requests.get(rec_url, headers=headers, timeout=HTTP_TIMEOUT)
                            if resp_rec.status_code == 200:
                                for rec in resp_rec.json().get("value", []):
                                    email = rec.get("emailAddress") or rec.get("upn")
                                    if email and "@" in email: emails_presentes.add(email)
        except Exception as e:
            print(f">>> Erro Estratégia 2 (Attendance): {e}")

    # ESTRATÉGIA 3: Busca por Assunto (Fallback pelo nome do arquivo limpo)
    if not emails_convidados and file_name:
        clean_name = clean_meeting_name(file_name)
        print(f">>> Tentando busca por assunto: '{clean_name}'")
        try:
            # Filtro para buscar pelo assunto do e-mail
            params = {
                "$filter": f"contains(subject, '{clean_name}')",
                "$select": "subject,attendees,organizer",
                "$top": 10
            }
            resp = requests.get(f"{GRAPH_BASE_URL}/me/events", headers=headers, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                events = resp.json().get("value", [])
                for ev in events:
                    org_email = ev.get("organizer", {}).get("emailAddress", {}).get("address")
                    if org_email: emails_convidados.add(org_email)
                    for att in ev.get("attendees", []):
                        addr = att.get("emailAddress", {}).get("address")
                        if addr: emails_convidados.add(addr)
        except Exception as e:
            print(f">>> Erro Estratégia 3 (Assunto): {e}")

    # Consolidação final
    todos_emails = emails_convidados.union(emails_presentes)
    
    # Filtro rigoroso para garantir que são e-mails válidos e sem espaços
    final_list = list({e.lower().strip() for e in todos_emails if e and "@" in str(e)})
    
    print(f">>> get_meeting_guests: Total único de {len(final_list)} destinatários.")
    print(f">>> Convidados (Invite): {list(emails_convidados)}")
    print(f">>> Participantes Reais (Relatório): {list(emails_presentes)}")
        
    return final_list