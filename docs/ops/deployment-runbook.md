# 현재 배포와 헬스체크

문서 버전: **2.5** · 갱신일: **2026-09-08 (Asia/Seoul)**
소스 기준선: **`31129d9911910b82c1161829d922a9760fac13a1`**
구현 상태: **기준선 위 V0 공개 게이트 분리·자동 이어가기 구현 및 검증 중. 운영 배포 전**

이 문서는 현재 작업 코드의 배포 진입점과 운영 구성을 설명한다. `docs/plans`의 Vercel/Supabase 구성은 과거 대체 배포안이다. 마지막으로 완료가 확인된 전체 운영 전환은 [2026-09-07~08 릴리스 기록](../releases/2026-09-07-eb55518-partial.md)에 있다. 현재 기준선 위 V0 공개 게이트 분리·자동 이어가기 변경은 아직 운영에 배포하지 않았다.

## 운영 구성

| 항목 | 확인된 구성 |
|---|---|
| GCP project / region | `mso-platform-481505` / `asia-northeast3` |
| 플랫폼 / Admin | `reputation.motionlabs.kr` / `admin.reputation.motionlabs.kr` |
| HTTPS Load Balancer IP | `34.117.192.90` |
| Cloud Run 서비스 | `reputation-api`, `reputation-worker`, `reputation-beat`, `reputation-site`, `reputation-admin` |
| Backend 이미지 | API/Worker/Beat 공통 이미지, `SERVICE`로 실행 역할 선택 |
| Frontend 이미지 | Site/Admin 각각 Next standalone 이미지 |
| DB / broker | Cloud SQL PostgreSQL 16 / Memorystore Redis |
| 저장소 / 인증서 | GCS, Load Balancer·Certificate Manager 관련 Terraform 구성 |
| 배포 검사 Job | `reputation-migrate`, `reputation-redbeat-reconcile`, `reputation-production-readiness` |

구성 정본은 [Terraform](../../terraform/cloudrun.tf), [DB](../../terraform/cloudsql.tf), [배포 스크립트](../../scripts/deploy.sh)다. 인프라 선언과 실서비스의 차이가 있을 수 있으므로 다음 배포에서는 다시 조회한다. Secret Manager 값·환경변수 전체를 출력하지 말고 필요한 비밀이 아닌 설정과 secret 참조의 보존 여부만 확인한다.

## 배포 진입점과 실제 순서

저장소 루트의 `.env.production`과 접근 가능한 Secret Manager 설정을 준비한다. 비밀 값을 추적 파일에 넣지 않는다. `scripts/deploy.sh`는 Git 소스 버전을 고정하고 필수 환경·secret·DB 연결·공개 도메인 등을 검사한다.

**(완료 2026-09-08 — 체크포인트 2 배포 전에 아래 절차로 생성했다. 새 프로젝트/환경에서만 다시 필요하다.)** **`BFF_ACTOR_SECRET` 생성(H-10).** 백엔드는 사람이 일으키는 admin 변경(POST/PATCH/PUT/DELETE)에 Admin BFF가 서명한 actor 단언을 요구하고, 이 값이 비어 있으면 API가 프로덕션에서 부팅에 실패한다. API와 Admin이 **같은 값**을 읽어야 서명이 검증된다. Terraform이 secret 리소스와 두 서비스 주입·IAM을 선언하고(`terraform/secretmanager.tf`), `scripts/deploy.sh`도 API·Admin 필수 시크릿 목록에 넣어 값이 없으면 배포 전 검사에서 멈춘다. 배포 전에 값만 만들어 둔다.

```bash
# 개행 없이 저장한다(openssl 출력 끝의 줄바꿈이 값에 들어가면 안 된다 — API·Admin 모두 값을 trim하지만 저장값 자체를 깨끗하게 둔다).
gcloud secrets create BFF_ACTOR_SECRET --project mso-platform-481505 --replication-policy=automatic --data-file=<(openssl rand -hex 32 | tr -d '\n')
# 접근 권한은 ADMIN_SESSION_SECRET과 같은 두 서비스 계정에 준다(terraform apply가 동일하게 부여한다).
gcloud secrets add-iam-policy-binding BFF_ACTOR_SECRET --project mso-platform-481505 \
  --member="serviceAccount:$(terraform -chdir=terraform output -raw service_account_email)" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding BFF_ACTOR_SECRET --project mso-platform-481505 \
  --member="serviceAccount:$(terraform -chdir=terraform output -raw frontend_service_account_email)" --role=roles/secretmanager.secretAccessor
```

배포 순서상 API가 Admin보다 먼저 새 리비전을 받는다. 단언은 브라우저가 아니라 Admin BFF(서버)가 서명하므로, **새 API가 뜬 뒤 새 Admin 리비전이 트래픽을 받기 전까지(보통 수 분) 모든 admin 변경 요청이 403 `ACTOR_ASSERTION_REQUIRED`("관리 화면을 새로고침한 뒤 다시 시도해 주세요")를 받는다.** Admin 리비전 교체가 끝나면 열려 있던 탭도 그대로 복구된다(새로고침 불필요). 다만 옛 콘텐츠 화면을 열어 둔 탭은 반려 요청에 사유 본문이 없어 새 API에서 422를 받으므로 새로고침이 필요하다 — 롤아웃 직후 운영자에게 두 사실을 알린다. 배치·CLI 호출은 `X-Admin-Actor-System: <job>` 헤더로 통과하며 감사 기록에는 `system:<job>`으로 남는다. 이 시스템 호출에는 **사람 계정이 없다** — 함께 보낸 `X-Admin-Actor`는 인가·감사 어디에도 채택되지 않고, 활성 운영자 계정을 요구하는 라우트(계정 관리·인수·운영센터 조치 등)는 403 `SYSTEM_ACTOR_NOT_ALLOWED`로 거부된다. 사람 요청에서 `X-Admin-Actor`가 단언의 이메일과 다르면 403 `ACTOR_ASSERTION_MISMATCH`다(BFF는 두 값을 같은 세션 이메일로 채우므로 정상 트래픽은 걸리지 않는다).

```bash
make db-budget-guard
make copy-guard
bash scripts/deploy.sh all
```

전체 배포의 표준 순서는 다음과 같다.

1. 어떤 변경보다 먼저 필수 설정·도메인·secret·저장소·런타임 조건을 검사한다.
2. 현재 서비스 5개의 리비전을 `.deploy-rollback`에 보관한다.
3. Backend/Site/Admin 이미지를 빌드하고 업로드한다.
4. 새 Backend 이미지로 마이그레이션을 실행한다. 기존 코드와 호환되는 추가형 스키마 변경을 전제로 한다.
5. Worker를 배포하고 Redis에 남은 RedBeat 스케줄을 재조정한다.
6. Beat를 배포한 뒤 production readiness Job으로 DB·Redis·현재 릴리스 큐 소비와 필수 설정을 확인한다.
7. API, Site, Admin을 배포한다.
8. 실제 트래픽·리비전과 외부 공개 표면을 별도로 검사한다. 앞선 readiness만으로 이후 프론트엔드까지 검증되었다고 보지 않는다.

체크포인트 2(release `a774851`, 2026-09-08 21:11Z)는 [기록](../releases/2026-09-09-checkpoint-2.md)대로 head `0071`을 적용했다. 현재 운영 DB의 마이그레이션 체인은 `0065_provider_usage` → `0066_content_contracts` → `0067_measurement_slots` → `0068_lead_cost_deferral` → `0069_content_first_publication` → `0070_essence_evidence_noise_hash` → `0071_plan_enum_cleanup`이고 expected head는 `0071_plan_enum_cleanup`다. `0071`은 남은 `PLAN_8` 행을 `PLAN_12`로 옮긴 뒤 `hospitals.plan`·`content_schedules.plan`에 12/16/20 CHECK만 건다. `plan` enum 타입은 손대지 않고 폐기 label도 타입에 남긴다 — 값을 지우려면 타입 rename-swap이 필요한데 그러면 타입 OID가 바뀌어, 롤링 중 아직 도는 옛 API·Worker 리비전의 asyncpg/psycopg2 연결 풀이 들고 있는 타입 OID·prepared statement 캐시가 깨진다(stale type OID / `InvalidCachedStatementError`). 제약 추가는 타입 정체성을 바꾸지 않으므로 롤링 중 실행해도 안전하고, 두 컬럼 모두 CHECK가 막으므로 어떤 행도 `PLAN_8`을 가질 수 없다(코드의 `Plan`도 12/16/20뿐). `0070`은 승인 당시 노이즈로 제외한 근거 노트 집합 hash를 기록한다 — 기존 승인 행은 NULL이며 다음 재조정에서 병원당 1회 유료 재검수가 발생한다(운영 7곳). 공급자 시도 원장, 콘텐츠 revision·provenance·이미지 인증, 월간/V0 고정 관측 슬롯, 무료 진단 비용 차단 재개 시각, 최초 공개 시각·주체를 추가했다. `0069`는 남아 있던 `published_at`·`published_by`만 최초 공개 사실로 백필했다. 이전 수동 반려가 이미 지운 과거 값은 추정하지 않고 NULL로 남겼다. 배포 직전 이미지의 expected head, Alembic heads와 운영 DB current head를 다시 읽어 모두 일치시킨다.

### 2026-09-07~08 기존 공개 콘텐츠 전환

이 일회성 전환은 완료된 운영 이력이며 반복 실행 절차가 아니다. 공개 글 115건을 exact manifest로 고정한 뒤 FAQ 3건을 CAS로 수리하고 본문 22건을 독립 검수했으며, 기존 이미지 115건을 실제 바이트에 묶어 인증했다. 최종 결과는 7개 tenant·공개 글 115건 모두 public safe, 누락·미인증·미해결 0건이었다. 이미지 79건은 기존 바이트를 유지하고 36건은 안전한 바이트로 교체했으며, 배포 후 115건 모두 HTTP 200과 실제 바이트 SHA-256·URL 인증 hash 일치를 확인했다.

마이그레이션 → Worker → 콘텐츠·이미지 전환과 새 strict public gate → RedBeat·Beat·readiness → API·Site·Admin → 공개 HTTP 검증 순서를 사용했다. 상세 결과와 당시 소스·digest·비용 범위는 [2026-09-07~08 운영 전환 기록](../releases/2026-09-07-eb55518-partial.md)에 보존한다. 이후 배포에서 이 backfill을 다시 실행하지 말고 현재 공개 gate를 회귀 검사한다. 상세 운영 ID나 비공개 payload를 문서·로그 요약에 나열하지 않는다.

`backend`는 Backend 3종 배포다. **`api`도 Worker와 Beat를 먼저 함께 갱신하는 경로**이므로 API만 바뀐다고 가정하지 않는다. `worker`, `beat`, `site`, `admin`, `migrate` 개별 대상도 제공한다. 정확한 범위는 스크립트의 마지막 case문을 확인한다.

2026-09-07~08 전환은 로컬 업로드 지연 때문에 Cloud Build와 digest 기반 rollout을 사용했고 기존 서비스 환경·secret 참조 hash 보존을 별도로 검증했다. 이 실행을 `scripts/deploy.sh`의 기본 Cloud Build 기능으로 오해하지 않는다. 반복 가능한 기본 진입점은 위 스크립트이며 대체 배포도 같은 마이그레이션·Worker/Beat·readiness 순서를 지켜야 한다.

## 파이프라인 외부 감시 (Cloud Scheduler)

Beat나 Worker가 죽으면 23:00 생성과 08:00 발행이 조용히 멈춘다. 그 사실을 알릴 작업도 같은 Beat 위에 있으므로 감시는 Celery 밖에 둔다. Cloud Scheduler 두 개가 API를 직접 호출한다 — `reputation-watchdog-heartbeat`(5분마다)와 `reputation-watchdog-publish-check`(매일 08:30 KST). 둘 다 공개 LB를 거쳐 `POST https://reputation.motionlabs.kr/api/v1/admin/watchdog/pipeline/alert`를 부르고 `X-Watchdog-Token` 헤더로 인증한다(선언: [terraform/watchdog.tf](../../terraform/watchdog.tf), 판정: [pipeline_watchdog.py](../../backend/app/services/pipeline_watchdog.py)). 새 Celery 태스크나 Beat 스케줄은 추가하지 않았다.

**무엇을 보는가.** (1) 큐 canary 신선도 — Redis에 남은 큐별 canary가 현재 릴리스로 15분 안에 갱신됐는지. (2) 예약 실행기 생존 — RedBeat 분산 락이 잡혀 있고 5분 주기 canary 스케줄이 15분 안에 실제로 실행됐는지. 락만으로는 "누군가 dispatcher 자리를 잡았다"까지만 증명되므로 두 근거를 모두 요구한다. (3) 당일 발행 — KST 오늘 발행 예정으로 남은 슬롯 수와 08:00 이후 공개된 글 수. (4) 마지막 야간 생성 배치 `OperationRun`의 나이(26시간). 판정만 읽으려면 `GET /api/v1/admin/watchdog/pipeline`을 같은 토큰이나 admin 키로 호출한다.

**알림 규칙과 채널.** `control`·`content` 큐 정지, 예약 실행기 정지, 08:30까지 당일 발행 0건 중 하나라도 성립할 때만 Slack을 보낸다. notification outbox를 쓰지 않고 webhook으로 직접 보낸다 — 그 outbox를 비우는 Worker가 죽었을 수 있다. 수신자는 사실별로 나뉜다: 실행기·대기열 정지는 개발 채널(`SLACK_WEBHOOK_URL_DEV`), 당일 발행 0건은 운영 채널(`SLACK_WEBHOOK_URL`)이다. **이 부품만의 예외로 개발 채널이 설정돼 있지 않으면 인프라 알림을 HOLD로 남기지 않고 운영 채널로 내려보낸다** — 침묵이 바로 이 부품이 막으려는 실패다([알림 정책](slack-notification-policy.md)). 같은 원인 집합은 수신자별로 KST 시간당 한 건이고, 조건이 사라지면 복구 한 건이 같은 채널로 간다. 정상 상태·보조 큐 지연·일부만 발행된 상태·예산 안의 자동 재시도는 알리지 않는다. 문구는 `무슨 문제인지 → 고객 영향 → 지금 할 일` 순서이며 개인정보와 내부 코드가 없다.

**경보를 받으면 먼저 볼 세 가지.** (1) Cloud Run `reputation-beat`·`reputation-worker`가 Ready이고 최근 재시작·OOM이 없는지. (2) Memorystore Redis 연결과 `GET /api/v1/admin/watchdog/pipeline`의 `beat_lock_held`·`beat_last_schedule_run_at`·`stale_queues`. (3) 운영센터 `/operations`의 오늘 발행 큐 — 남은 슬롯이 자동 복구(재시도 중)인지 사람의 일인지. 복구 뒤 다음 하트비트(최대 5분)에서 복구 메시지가 오는지 확인한다.

**토큰 교체.** `PIPELINE_WATCHDOG_TOKEN`은 API와 Cloud Scheduler가 같은 값을 읽는다. **비어 있어도 API는 정상 부팅한다** — 감시 토큰이 없다고 배포를 막으면 이 부품이 잡으려는 바로 그 정지를 배포가 스스로 만들기 때문이다. 대신 부팅 로그에 경고가 남고 `GET /api/v1/admin/watchdog/pipeline`의 `token_configured`가 `false`가 된다(그 상태에서 스케줄러 호출은 401이고 외부 감시는 실제로 꺼져 있다. admin 키 호출은 계속 된다). 교체할 때는 새 버전을 올린 뒤 API를 재배포해 새 값을 읽게 하고, 그 다음 `terraform apply`로 두 스케줄러 job의 헤더를 갱신한다(순서가 반대면 그 사이 호출이 401로 떨어진다).

```bash
gcloud secrets versions add PIPELINE_WATCHDOG_TOKEN --project mso-platform-481505 --data-file=<(openssl rand -hex 32 | tr -d '\n')
bash scripts/deploy.sh api
terraform -chdir=terraform apply -target=google_cloud_scheduler_job.watchdog_heartbeat -target=google_cloud_scheduler_job.watchdog_publish_check
```

첫 배포 전 준비: secret 컨테이너와 값을 먼저 만든다(`scripts/setup-gcp.sh`가 컨테이너를 생성한다). Cloud Run 주입은 두 경로 모두 준비돼 있다 — `scripts/deploy.sh`의 backend 필수 시크릿 목록과 `terraform/secretmanager.tf`의 `local.app_secret_env`.

## 검증할 증거

| 층 | 완료 판단 |
|---|---|
| 소스 | PR/main CI, 실제 배포 소스 Git SHA와 이미지 digest 대응 |
| 런타임 | 5개 서비스 Ready, 의도한 revision에 트래픽, 환경·secret 참조 보존 |
| DB | Alembic 현재 revision과 이미지의 expected head 일치 |
| 작업 | 현재 릴리스의 7개 큐 canary, 필수 task routing·priority, RedBeat schedule `2026-09-07.2`, queue wait |
| API / Admin | health와 로그인 표면 정상, 인증·오류 경계 유지 |
| 병원 공개 표면 | 홈페이지와 대표 글, 이미지 SSR, 정확한 병원·canonical·공개 주소 |
| 크롤러 표면 | host별 sitemap, robots, llms.txt의 404/503·no-store, 부분 sitemap 금지, 구조화 데이터와 공개 URL 범위 |
| 복구 | IndexNow intent와 provider usage spool의 1분 drain, retry 상한·중복 억제·정상 무알림 |
| V0 백그라운드 | V0 미완료 병원의 프로파일·허브·주소 조건에 따른 공개, 같은 run의 짧은 구간 이어가기, 완료 슬롯 비재구매, 만료 RUNNING lease 인수, ACTIVE·PAUSED 상태 보존 |
| 공개 본문 | 현재 strict public gate, 후보 hash·근거·검수 상태와 공개 API 결과 일치 |
| 기존 이미지 | 실제 바이트·내용 hash·주제 hash·정책 버전 인증과 프록시 URL 버전 일치 |
| 로그 | 새 revision의 실제 오류와 작업 실패 여부, control/background queue wait; 조회 시점·범위 기록 |

병원 헬스는 `https://{병원 공개 호스트}/.well-known/reputation-health`다. HTTP 200뿐 아니라 `hospital_id`, `slug`, `canonical_host`, `release`(Cloud Run에서는 `K_REVISION`)가 해당 병원·배포와 맞아야 한다. 기본 주소와 자기 도메인을 모두 사용하는 운영 집합을 대상으로 확인한다.

`python -m app.utils.production_readiness`와 대기형 `python -m app.utils.wait_production_readiness`는 배포 이미지·운영 연결에서 실행하는 검사다. 공개 페이지 200만으로 DB 연결·스케줄 실행·실제 외부 AI 생성 성공까지 증명하지 않는다. 모델·이메일·Slack의 실제 호출은 별도의 명시된 검증 범위로 기록한다.

Worker는 `control,default,content,sov,reports,leadgen,certificates` 7개 큐를 하나의 서비스에서 소비한다. 2026-09-09 체크포인트에서 `content` 큐에 `recertify_published_content_image`(공개 글 제목 편집 후 이미지 재인증, 유료 호출 (글, 주제)당 최대 3회)와 `fetch_channel_source`(프로필의 공식 채널 URL을 근거 자료로 가져오기, 자료당 3회 예산 뒤 인시던트) 두 task가 추가됐다. 둘 다 `production_readiness.EXPECTED_TASKS`에 등록되어 있고 beat 스케줄은 바뀌지 않았다(재시도 sweep은 기존 `autonomous_recovery.reconcile`·자료 처리 복구 sweep 안에서 돈다). 발행·캐시 복구·자율 복구가 control priority 0으로 들어가고 IndexNow·usage spool은 낮은 우선순위다. control canary를 포함해 큐별 최신 canary가 현재 release를 가리키는지 확인한다. control은 전용 Worker가 아니므로 부하 중 30초 기준을 충족했는지는 queue wait 구조화 로그로 따로 증명한다.

RedBeat `2026-09-07.2`에는 IndexNow retry와 provider usage spool drain이 매 1분 추가된다. 영속 스케줄 재조정 뒤 등록 이름·task route·서명 목적이 새 이미지와 맞는지 확인한다. 정상 drain·복구를 Slack 메시지로 시험하지 말고 DB 실행 상태와 구조화 로그를 사용한다. 2026-09-07~08 기존 이미지 인증은 주기 작업이 아니라 완료 후 제거한 일회성 Job/CLI였다. 최종 실패나 사람이 결정할 예외만 [알림 정책](slack-notification-policy.md)에 따라 확인한다.

2026-09-08 운영 전환 runtime `ede3d8f5a8adec849c987d21c1491afef03edbca`의 [PR #86 CI](https://github.com/wjlee930501/reputation/actions/runs/34142628859)는 9/9 통과했다. Backend는 3,095 passed, 0 failed, 0 skipped, 17 warnings, coverage 83.03%였고 Admin·Site 검사와 컨테이너 빌드도 통과했다. 운영에서는 5개 서비스·3개 영속 Job, DB head, 7개 큐 readiness, 공개 115건과 실제 이미지 바이트를 확인했다. 이 완료 범위와 남은 장기 WATCH는 [후속 구현 기록](../reviews/2026-09-07-purpose-autonomy-efficiency-implementation.md)에 분리한다.

2026-09-09 체크포인트 1 runtime `4bd1e0312a9178ad53c2f1065ec42798d81d7c7c`(PR #91, CI 9/9)는 `scripts/deploy.sh all`로 마이그레이션(0070) → Worker → RedBeat 재조정 → Beat → readiness(7개 큐 canary, `recertify_candidate_count 0`, `null_noise_hash_approvals 8`) → API/Site/Admin 순으로 배포했고, 8개 병원 헬스·대표 글·이미지 200과 새 리비전 오류 0건을 확인했다. 상세는 [체크포인트 1 기록](../releases/2026-09-09-checkpoint-1.md).

## 롤백과 문서 변경

```bash
bash scripts/deploy.sh rollback
```

롤백은 `.deploy-rollback`에 저장된 revision으로 **트래픽을 복귀**한다. 이미 적용된 DB 마이그레이션은 되돌리지 않는다. 스키마가 구버전과 호환되는지 먼저 판단하며 무조건 `alembic downgrade`하지 않는다. 롤백 뒤에도 현재 큐·API·공개 표면을 다시 확인한다.

문서만 바뀐 릴리스는 기존 런타임이 그대로임을 기록한다. 문서 Git SHA를 기존 이미지의 소스 SHA로 바꿔 적거나 불필요한 전체 재배포를 수행하지 않는다.
