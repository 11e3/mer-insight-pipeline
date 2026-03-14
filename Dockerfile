FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV TZ=Asia/Seoul
# Cloud Run Jobs: run_job.py --job <name>
# Cloud Run Service (Streamlit): streamlit run src/dashboard/observability.py
CMD ["python", "-m", "scripts.run_job", "--job", "mer_check"]
