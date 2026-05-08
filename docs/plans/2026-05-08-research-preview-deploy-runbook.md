# Re:putation Research Preview — 배포 Runbook

**대상:** main 브랜치 → Cloud Run + Vercel 첫 출시
**작성일:** 2026-05-08
**범위:** 코드 외 운영 인프라 게이트 (Bundle C). 코드 핫픽스(Bundle A/B)는 별도 commit/PR로 처리됨.

---

## 0.1 첫 셋업에서 자주 막히던 표면 (2026-05-08 패치 완료)

운영자가 `cp .env.example .env` 직후 `make setup`했을 때 그대로 통과하도록 코드에서 사전 보강.

| 표면 | 증상 | 패치 |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` placeholder | docker mount 실패로 api 컨테이너 부팅 거부 | `.env.example`에 빈 값으로 두면 docker-compose의 `${VAR:-/dev/null}` fallback이 활성. 실제 이미지 생성 사용 시에만 절대 경로 지정. |
| `ALLOWED_ORIGINS` comma-separated 파싱 실패 | pydantic-settings v2가 JSON 배열만 받아 SettingsError | `config.py`에 `field_validator(mode="before")` 추가 — comma-separated와 JSON 배열 모두 허용. |
| `alembic_version.version_num VARCHAR(32)` | 41자 revision id (예: `0012_add_exposure_content_link_uniqueness`) UPDATE 시 truncation 에러 | `alembic/env.py`의 `do_run_migrations`에서 `_ensure_version_table()`로 VARCHAR(255)로 사전 생성. |

위 3건은 첫 dev 환경 셋업 시 구체적 충돌이 확인된 표면. CI에서 `cp .env.example .env && make setup` 시나리오 1회 통과를 출시 게이트로 권장.

---

## 0. 출시 직전 체크리스트 (10분, 필수)

| 게이트 | 검증 방법 | 차단 |
|---|---|---|
| `ADMIN_SECRET_KEY` | 32자 이상, 1Password에 보관됨 | YES |
| `ALLOWED_ORIGINS` | `localhost`/`*` 미포함, 실 도메인만 | YES |
| `CNAME_TARGET` | Cloud Run 백엔드를 가리키는 실제 DNS | YES |
| `ADMIN_ACTOR_NAME` | 실 운영자 이름 (`AE` 디폴트는 audit 추적 불가) | YES |
| `LEAD_CONSENT_VERSION` | 처리방침 페이지(/privacy) 버전과 일치 | YES |
| `SLACK_WEBHOOK_URL` | 운영 알림 채널, 공개 채널 아님 | YES |
| `.next` 캐시 정리 | `rm -rf site/.next && cd site && npm run build` 신선 실행 | YES |
| Alembic 적용 | `alembic upgrade head` 후 `SELECT COUNT(*) FROM sales_leads WHERE retain_until IS NULL` = 0 | YES |
| e2e | `make demo-seed && bash scripts/test_e2e.sh` 회귀 0건 | YES |
| `purge_expired_leads` dry-run | 테스트 lead 1건 `retain_until`=어제로 UPDATE → 다음 날 04:00 KST 익명화 결과 확인 | NO (출시 후 1주) |

---

## 1. Cloud Run 서비스 분리 (P0)

### 1.1 API + Beat + Worker 컨테이너 분리

**문제:** `app/core/celery_app.py`의 7개 beat schedule이 worker 컨테이너 N개 중 어느 한 곳에서 단일 발사돼야 한다. 다중 beat 인스턴스는 중복 콘텐츠/리포트/Slack 송출 위험.

**해결:** 3개 서비스로 분리.

| 서비스 | 명령어 | min/max 인스턴스 | 비고 |
|---|---|---|---|
| `reputation-api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 0 / 4 | autoscale OK |
| `reputation-worker` | `celery -A app.core.celery_app worker --pool=prefork --concurrency=2` | 1 / 4 | autoscale OK |
| `reputation-beat` | `celery -A app.core.celery_app beat --pidfile /tmp/beat.pid` | **1 / 1** (강제) | **다중 인스턴스 금지** |

**검증:**
```bash
gcloud run services describe reputation-beat --format='value(spec.template.spec.containers[0].args)'
# beat command가 단일 인스턴스로만 실행되는지 확인
gcloud run services describe reputation-beat --format='value(spec.template.metadata.annotations)' | grep -E 'minScale|maxScale'
# autoscaling.knative.dev/minScale: "1"
# autoscaling.knative.dev/maxScale: "1"
```

### 1.2 Worker 풀 강제 (prefork)

`tasks.py:47-61` `_run_async`는 thread-local event loop를 가정한다. gevent/eventlet pool과 섞이면 깨진다. `--pool=prefork --concurrency=2`로 명시 강제.

---

## 2. Cloud SQL 백업 (P0)

### 2.1 자동 백업 활성

```bash
gcloud sql instances patch reputation-pg \
  --backup-start-time=18:00 \
  --enable-bin-log \
  --retained-backups-count=7
```

7일 PITR(point-in-time recovery) 보장. 단일 디스크 장애 = 영업 자산 + 법적 책임 추적 동시 손실 방지.

### 2.2 admin_audit_logs 일일 GCS export

```bash
# Cloud Scheduler → Cloud Run job
gcloud scheduler jobs create http audit-log-export \
  --schedule="0 5 * * *" \
  --uri="https://reputation-api.run.app/internal/audit-export" \
  --http-method=POST
```

(엔드포인트 별도 구현 — Research Preview 출시 후 1주 내 P1)

---

## 3. Sentry Alert Rules (P0)

`SENTRY_DSN` 설정만으로는 새벽 사고를 모른다. 최소 3개 룰.

### 3.1 leads 5xx 폭증
- 조건: `tags.endpoint == "/api/v1/public/leads"` AND HTTP status >= 500
- 임계: 5건 / 5분
- 행동: PagerDuty + Slack #incidents

### 3.2 Celery task 실패율
- 조건: `tags.celery_task` 발생 + level == error
- 임계: 실패율 > 50% / 1시간
- 행동: Slack #incidents

### 3.3 purge_expired_leads 미실행
- 조건: 24시간 내 `purge_expired_leads` 성공 신호 0건
- 임계: 24시간
- 행동: PagerDuty (개인정보보호법 제21조 위반 위험)

---

## 4. 비용 가드 (P1, 출시 후 1주 내)

**현황:** `regenerate_content_item` retry + image quota 영구 실패 조합 시 매일 같은 item을 재시도 → 비용 폭주 표면.

**Research Preview 임시 가드:** GCP Billing budget alert를 OpenAI/Anthropic/Vertex 각각 설정.

| 서비스 | 일일 한도 (첫 10병원) | 알림 임계 |
|---|---|---|
| OpenAI (gpt-4o + mini) | $20 | 50% / 90% / 100% |
| Anthropic (Claude Sonnet) | $30 | 50% / 90% / 100% |
| Vertex AI (Imagen 3) | $10 | 50% / 90% / 100% |
| Gemini API | $5 | 50% / 90% / 100% |

**100% 도달 시 자동 차단**은 GCP Billing의 `disable_billing_pubsub` 트리거로 구현 (P1).

---

## 5. 도메인·DNS 설정 (P0)

### 5.1 Site (Vercel)
- `reputation.co.kr` → Vercel
- `*.reputation.co.kr` → Vercel (병원별 서브도메인 노출)

### 5.2 Backend (Cloud Run)
- `api.reputation.co.kr` → `reputation-api.run.app`
- 첫 시연 병원이 `clinic.example.com → CNAME → aeo.motionlabs.io` 설정 시 `aeo.motionlabs.io`가 실제 Cloud Run 서비스를 가리켜야 한다.

```bash
# Cloud Run domain mapping
gcloud run domain-mappings create --service=reputation-api --domain=aeo.motionlabs.io
# 결과 CNAME 값을 settings.CNAME_TARGET 환경변수에 넣는다 (현재 디폴트: aeo.motionlabs.io)
```

---

## 6. 출시 후 7일 모니터링 항목

| 항목 | 첫 24h | Day 2-3 | Week 1 |
|---|---|---|---|
| `/api/v1/public/leads` 5xx 비율 | 매시 확인 | 4시간마다 | 1일 |
| Celery task 실패 로그 | 매시 | 4시간마다 | 1일 |
| `purge_expired_leads` 일일 Slack 알림 | 04:00 KST 직후 확인 | 매일 | 매일 |
| LLM 비용 (Billing) | 1일 1회 | 1일 1회 | 1주 |
| `admin_audit_logs.actor` distinct count | — | — | NextAuth 도입 트리거 |

**경보 발생 시 첫 액션:** Cloud Run 로그 → Sentry 이벤트 → audit log 시간순 조회 → 운영자 Slack에서 사고 보고서 시작.

---

## 7. 출시 후 P1 이슈 트래킹

GitHub Issues에 다음 6개를 출시 직후 즉시 생성:

1. **NextAuth + 다중 actor** — AE 2번째 합류 또는 5번째 병원 계약 전 완료
2. **Audit append-only DB role** — `audit_writer` PG role + revoke UPDATE/DELETE
3. **regenerate_content_item에 의료광고 필터 강제** — essence_status 강등 로직 추가
4. **per-hospital per-day LLM cost cap** — 코드 레벨 가드
5. **Hospital.site_built/site_live rename** — `content_hub_ready/live`로
6. **랜딩 hero 가짜 진단 카드 → 실제 데모 병원 V0 SSR**

---

**책임:** 본 runbook의 P0 항목이 모두 GREEN이 되기 전에는 첫 시범 병원을 라이브 트래픽에 노출하지 않는다.
