#Usar a versão oficial e estável do Python 3.11
FROM python:3.11-slim

# 2. Definir o diretório de trabalho
WORKDIR /app

# 3. Instalar ffmpeg + dependências do WeasyPrint (HTML->PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# 4. Copiar APENAS o arquivo de requisitos primeiro
COPY requirements.txt ./

# 5. Instalar as dependências do Python (isso evita reconstruir tudo a cada mudança de código)
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copiar todo o resto do seu código
COPY . .

# 7. Definir a porta padrão que o Cloud Run usará
ENV PORT 8080

# 8. Comando para iniciar sua aplicação
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app", "--timeout", "900"]
