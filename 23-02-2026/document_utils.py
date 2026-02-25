import vertexai
from vertexai.generative_models import GenerativeModel
from weasyprint import HTML

def generate_minutes_with_gemini(transcript, meeting_title, meeting_url, participants, project, location, model_name):
    vertexai.init(project=project, location=location)
    model = GenerativeModel(model_name)
    prompt = f"""
    Você é um assistente profissional. Gere uma ATA em português para a reunião: {meeting_title}.
    Link da gravação: {meeting_url}
    Participantes: {participants}
    
    Transcrição:
    {transcript}
    """
    try:
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except Exception as e:
        return f"Erro ao gerar ata: {str(e)}"

def generate_pdf_minutes(minutes_text, out_path, title):
    html_content = f"""
    <html>
    <head><style>body {{ font-family: Arial; padding: 20px; }}</style></head>
    <body>
        <h1>{title}</h1>
        <div style="white-space: pre-wrap;">{minutes_text}</div>
    </body>
    </html>
    """
    HTML(string=html_content).write_pdf(out_path)
    return out_path