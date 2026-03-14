# GCP 배포 설정 가이드

## 전제 조건

```bash
# gcloud CLI 설치 후
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

---

## Phase 2: Cloud SQL + Secret Manager

### 2-1. Cloud SQL 인스턴스 생성

```bash
# PostgreSQL 16 인스턴스 (db-f1-micro ≈ $10/월)
gcloud sql instances create mer-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=asia-northeast3 \
  --storage-type=SSD \
  --storage-size=10GB \
  --no-backup

# 데이터베이스 생성
gcloud sql databases create mer_pipeline --instance=mer-db

# 사용자 생성
gcloud sql users create mer \
  --instance=mer-db \
  --password=STRONG_PASSWORD_HERE
```

### 2-2. pgvector 확장 활성화

```bash
gcloud sql connect mer-db --user=mer --database=mer_pipeline
```

```sql
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

### 2-3. DB 스키마 적용

```bash
# Cloud SQL Proxy 임시 실행 후 로컬에서 연결
cloud-sql-proxy YOUR_PROJECT_ID:asia-northeast3:mer-db &
psql -h 127.0.0.1 -U mer -d mer_pipeline < scripts/init_db.sql
```

### 2-4. vector 차원 마이그레이션 (기존 로컬 DB에서 전환 시)

```bash
psql -h 127.0.0.1 -U mer -d mer_pipeline < scripts/migrate_vector_dim.sql
```

### 2-5. Secret Manager 등록

```bash
# 필수 시크릿
echo -n "postgresql+asyncpg://mer:PASSWORD@/mer_pipeline?host=/cloudsql/PROJECT:REGION:mer-db" \
  | gcloud secrets create DATABASE_URL --data-file=-

echo -n "sk-ant-..." \
  | gcloud secrets create ANTHROPIC_API_KEY --data-file=-

echo -n "YOUR_BOT_TOKEN" \
  | gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-

echo -n "YOUR_CHAT_ID" \
  | gcloud secrets create TELEGRAM_TIER1_CHAT_ID --data-file=-

echo -n "YOUR_CHAT_ID" \
  | gcloud secrets create TELEGRAM_TIER2_CHAT_ID --data-file=-

# 선택적
echo -n "YOUR_FRED_KEY" | gcloud secrets create FRED_API_KEY --data-file=-
echo -n "YOUR_BOK_KEY"  | gcloud secrets create BOK_API_KEY  --data-file=-
```

---

## Phase 3: Cloud Run Jobs 배포

### 3-1. Artifact Registry 저장소 생성

```bash
gcloud artifacts repositories create mer-pipeline \
  --repository-format=docker \
  --location=asia-northeast3
```

### 3-2. 서비스 계정 생성 및 권한 부여

```bash
gcloud iam service-accounts create mer-runner \
  --display-name="MER Pipeline Runner"

SA="mer-runner@YOUR_PROJECT_ID.iam.gserviceaccount.com"

# Cloud SQL 접속
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/cloudsql.client"

# Secret Manager 읽기
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor"

# Vertex AI 임베딩 호출
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/aiplatform.user"
```

### 3-3. 이미지 빌드 & 푸시

```bash
IMAGE="asia-northeast3-docker.pkg.dev/YOUR_PROJECT_ID/mer-pipeline/app:latest"

docker build -t $IMAGE .
docker push $IMAGE
```

### 3-4. Cloud Run Jobs 배포

```bash
# 공통 환경변수 (시크릿은 --set-secrets로 주입)
COMMON_ARGS="
  --image=$IMAGE
  --region=asia-northeast3
  --service-account=$SA
  --set-secrets=DATABASE_URL=DATABASE_URL:latest
  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest
  --set-secrets=TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest
  --set-secrets=TELEGRAM_TIER1_CHAT_ID=TELEGRAM_TIER1_CHAT_ID:latest
  --set-secrets=TELEGRAM_TIER2_CHAT_ID=TELEGRAM_TIER2_CHAT_ID:latest
  --set-env-vars=GCP_PROJECT_ID=YOUR_PROJECT_ID
  --add-cloudsql-instances=YOUR_PROJECT_ID:asia-northeast3:mer-db
  --memory=512Mi
"

# 메르 글 감지 잡
gcloud run jobs create mer-checker \
  $COMMON_ARGS \
  --command=python --args="-m,scripts.run_mer_check"

# DART 공시 잡
gcloud run jobs create dart-checker \
  $COMMON_ARGS \
  --command=python --args="-m,scripts.run_dart_check"

# 매크로/뉴스 잡
gcloud run jobs create macro-checker \
  $COMMON_ARGS \
  --command=python --args="-m,scripts.run_macro_check"

# 리포트 잡
gcloud run jobs create report-generator \
  $COMMON_ARGS \
  --command=python --args="-m,scripts.run_report"
```

### 3-5. Cloud Scheduler 설정

```bash
# 메르 글 감지: 5분마다
gcloud scheduler jobs create http mer-checker-schedule \
  --location=asia-northeast3 \
  --schedule="*/5 * * * *" \
  --uri="https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/mer-checker:run" \
  --oauth-service-account-email=$SA

# DART: 평일 8-18시 10분마다
gcloud scheduler jobs create http dart-checker-schedule \
  --location=asia-northeast3 \
  --schedule="*/10 8-18 * * 1-5" \
  --uri="https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/dart-checker:run" \
  --oauth-service-account-email=$SA

# 매크로/뉴스: 30분마다
gcloud scheduler jobs create http macro-checker-schedule \
  --location=asia-northeast3 \
  --schedule="*/30 * * * *" \
  --uri="https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/macro-checker:run" \
  --oauth-service-account-email=$SA

# 일간 리포트: 매일 21:00 KST
gcloud scheduler jobs create http daily-report-schedule \
  --location=asia-northeast3 \
  --schedule="0 12 * * *" \
  --uri="https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/report-generator:run" \
  --message-body='{"args":["--type","daily"]}' \
  --oauth-service-account-email=$SA

# 주간: 월요일 8:00 KST (UTC 23:00 일요일)
gcloud scheduler jobs create http weekly-report-schedule \
  --location=asia-northeast3 \
  --schedule="0 23 * * 0" \
  --uri="https://asia-northeast3-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/report-generator:run" \
  --message-body='{"args":["--type","weekly"]}' \
  --oauth-service-account-email=$SA
```

### 3-6. Streamlit 대시보드 (Cloud Run Service)

```bash
gcloud run deploy mer-dashboard \
  --image=$IMAGE \
  --region=asia-northeast3 \
  --service-account=$SA \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest \
  --set-env-vars=GCP_PROJECT_ID=YOUR_PROJECT_ID \
  --add-cloudsql-instances=YOUR_PROJECT_ID:asia-northeast3:mer-db \
  --command=streamlit \
  --args="run,src/dashboard/observability.py,--server.port=8080,--server.address=0.0.0.0" \
  --min-instances=1 \
  --memory=512Mi \
  --port=8080 \
  --no-allow-unauthenticated   # 인증 필요 (포트폴리오 공개 시 제거)
```

---

## 재임베딩 (로컬 데이터 → Cloud SQL 전환 시)

```bash
# Cloud SQL Proxy 실행
cloud-sql-proxy YOUR_PROJECT_ID:asia-northeast3:mer-db &

# 환경변수 설정
export DATABASE_URL="postgresql://mer:PASSWORD@127.0.0.1:5432/mer_pipeline"
export GCP_PROJECT_ID="YOUR_PROJECT_ID"
export ANTHROPIC_API_KEY="sk-ant-..."

# 재임베딩 실행
python -m scripts.reembed_all
```

---

## 환경변수 요약 (.env 로컬 / Secret Manager 운영)

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `GCP_PROJECT_ID` | ✓ | GCP 프로젝트 ID |
| `GCP_LOCATION` | - | Vertex AI 리전 (기본: us-central1) |
| `TELEGRAM_BOT_TOKEN` | ✓ | 텔레그램 봇 토큰 |
| `TELEGRAM_TIER1_CHAT_ID` | ✓ | 무료 채널 ID |
| `TELEGRAM_TIER2_CHAT_ID` | - | 프리미엄 채널 ID |
| `FRED_API_KEY` | - | FRED 거시경제 데이터 |
| `BOK_API_KEY` | - | 한국은행 ECOS API |
