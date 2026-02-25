import vertexai
from vertexai.generative_models import GenerativeModel
from weasyprint import HTML
from config import VERTEX_PROJECT, VERTEX_LOCATION, GEMINI_MODEL

# ----------------------------
# Gemini e PDF (ata)
# ----------------------------
def generate_minutes_with_gemini(transcript: str, meeting_title: str = "", meeting_url: str = "", participants: str = "A implementar") -> str:
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
- Participantes extraídos do sistema: {participants}

Gere uma ATA em português, com esta estrutura EXATA:

CABEÇALHO
- Assunto: {meeting_title}
- Data:
- Horário:
- Arquivo de origem: {meeting_title}
- Link da gravação: {meeting_url}
- Participantes: {participants}

1) Resumo executivo (5-10 linhas)
2) Participantes (Liste os nomes fornecidos acima. Se a transcrição indicar que mais alguém participou, adicione-os aqui)
3) Pauta / tópicos discutidos (bullet points)
4) Decisões tomadas (bullet points; se não houver, diga "não identificado")
5) Ações e responsáveis (tabela: Ação | Responsável | Prazo | Status)
6) Pendências / riscos / próximos passos

Transcrição:
\"\"\"
{transcript}
\"\"\"
""".strip()

    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        print(f">>> Erro ao gerar ata com Gemini: {e}")
        return f"Erro ao gerar conteúdo da ata automaticamente. Detalhes: {str(e)}"

def generate_pdf_minutes(minutes_text: str, out_path: str = "/tmp/ATA_TESTE_POC.pdf", title: str = "Ata de Reunião (POC - CatIA)") -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = (minutes_text or "").splitlines()
    html_parts = []
    in_ul = False

    for raw in lines:
        line = raw.strip()
        if line and set(line) <= {"*", "-", "_"}: continue

        if not line:
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            html_parts.append("<div class='spacer'></div>")
            continue

        if line.startswith("### "):
            if in_ul: html_parts.append("</ul>"); in_ul = False
            html_parts.append(f"<h3>{esc(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            if in_ul: html_parts.append("</ul>"); in_ul = False
            html_parts.append(f"<h2>{esc(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            if in_ul: html_parts.append("</ul>"); in_ul = False
            html_parts.append(f"<h1>{esc(line[2:])}</h1>")
            continue

        if line.startswith(("* ", "- ")):
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{esc(line[2:])}</li>")
            continue

        if in_ul:
            html_parts.append("</ul>")
            in_ul = False
        html_parts.append(f"<p>{esc(line)}</p>")

    if in_ul:
        html_parts.append("</ul>")

    html_body = "\n".join(html_parts)

    html_doc = f"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    @page {{ size: A4; margin: 20mm; @bottom-right {{ content: "Página " counter(page) " de " counter(pages); font-size: 9pt; color: #6b7280; }} }}
    body {{ font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.35; font-size: 11pt; }}
    .header {{ border-bottom: 2px solid #111827; padding-bottom: 8px; margin-bottom: 14px; }}
    .header .title {{ font-size: 20pt; font-weight: 700; margin: 0; }}
    .spacer {{ height: 8px; }}
    h1, h2, h3 {{ margin: 14px 0 6px 0; font-weight: 700; color: #111827; }}
    h1 {{ font-size: 18pt; }} h2 {{ font-size: 14pt; }} h3 {{ font-size: 12pt; }}
    p {{ margin: 0 0 6px 0; white-space: pre-wrap; }}
    ul {{ margin: 0 0 6px 18px; padding: 0; }} li {{ margin: 0 0 4px 0; }}
    .content {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 14px 10px 14px; background: #ffffff; }}
    footer {{ position: running(footer); font-size: 9pt; color: #6b7280; }}
  </style>
</head>
<body>
  <div class="header"><p class="title">{title}</p></div>
  <div class="content">{html_body}</div>
</body>
</html>"""

    HTML(string=html_doc).write_pdf(out_path)
    return out_path