FROM python:3.12-slim

WORKDIR /app

# System deps for lxml + spacy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spacy model
RUN python -m spacy download en_core_web_sm

COPY . .

# Persistent volumes (vault, audio, ai-knowledge, data)
VOLUME ["/app/vault", "/app/audio", "/app/ai-knowledge", "/app/data"]

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
