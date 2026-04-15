# Dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    azure-identity \
    azure-search-documents \
    azure-storage-blob

COPY scripts/ scripts/

ENTRYPOINT ["python"]
