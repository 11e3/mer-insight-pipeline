FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV TZ=Asia/Seoul
# Cloud Run Jobs: ENTRYPOINT + --args="--job,<name>"
# Cloud Run Service (Streamlit): override command
ENTRYPOINT ["python", "-m", "scripts.run_job"]
CMD ["--job", "mer_check"]
