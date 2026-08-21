FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the index at image build time so the container answers immediately
# with no volume, no credentials, and no network.
RUN python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl

RUN useradd --create-home --uid 10001 grounded-rag-service \
    && chown -R grounded-rag-service:grounded-rag-service /app
USER grounded-rag-service

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
