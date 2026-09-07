# 현재 배포와 헬스체크

문서 버전: **2.2** · 갱신일: **2026-09-07 (Asia/Seoul)**
소스 기준선: **`39dc1f8a98abe9193a8e2202395d2272c370fe8e`**
구현 상태: **기준선 위 작업 로컬 검증·독립 리뷰 완료. 커밋·운영 전환·배포 전**

이 문서는 현재 작업 코드의 배포 진입점과 운영 구성을 설명한다. `docs/plans`의 Vercel/Supabase 구성은 과거 대체 배포안이다. 마지막으로 완료가 확인된 운영 배포는 [릴리스 기록](../releases/2026-09-07-345a642.md)이며, 이번 변경의 증거는 [감사 후속 구현 기록](../reviews/2026-09-07-purpose-autonomy-efficiency-implementation.md)에 대기 상태로 분리했다.

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

```bash
make db-budget-guard
make copy-guard
bash scripts/deploy.sh all
```

이번 변경 전까지의 전체 배포 순서는 다음과 같다. 아래 기존 이미지 인증 gate가 배포 스크립트와 복구 작업에 연결된 뒤 그 gate를 포함한 최종 순서를 사용한다.

1. 어떤 변경보다 먼저 필수 설정·도메인·secret·저장소·런타임 조건을 검사한다.
2. 현재 서비스 5개의 리비전을 `.deploy-rollback`에 보관한다.
3. Backend/Site/Admin 이미지를 빌드하고 업로드한다.
4. 새 Backend 이미지로 마이그레이션을 실행한다. 기존 코드와 호환되는 추가형 스키마 변경을 전제로 한다.
5. Worker를 배포하고 Redis에 남은 RedBeat 스케줄을 재조정한다.
6. Beat를 배포한 뒤 production readiness Job으로 DB·Redis·현재 릴리스 큐 소비와 필수 설정을 확인한다.
7. API, Site, Admin을 배포한다.
8. 실제 트래픽·리비전과 외부 공개 표면을 별도로 검사한다. 앞선 readiness만으로 이후 프론트엔드까지 검증되었다고 보지 않는다.

이번 변경의 마이그레이션 체인은 `0065_provider_usage` → `0066_content_contracts` → `0067_measurement_slots` → `0068_lead_cost_deferral`이고 expected head는 `0068_lead_cost_deferral`이다. 공급자 시도 원장, 콘텐츠 revision·provenance·이미지 인증, 월간/V0 고정 관측 슬롯, 무료 진단 비용 차단 재개 시각을 추가한다. 배포 직전 이미지의 expected head, Alembic heads와 운영 DB current head를 다시 읽어 모두 일치시킨다.

### 이번 API rollout의 기존 공개 콘텐츠 gate

운영 read-only preflight는 공개 글 115건을 exact ID manifest로 고정했다. AI 검수는 미해결 22건(REVISE 19, UNAVAILABLE 3), AI 검수 레거시 기준 허용 93건(ABSENT 92, PASS 1)이고, FAQ 질문 끝 물음표만 고칠 대상은 3건이다. 이미지 인증은 109건이 새 내용·주제·정책 결합값이 없고 6건은 기존 검수 시각만 있다. 이 6건도 포함해 이미지 115건 모두를 실제 바이트에 묶는다. 상세 운영 ID와 실행 산출물은 추적하지 않는 `/private/tmp/reputation-autonomy-release` 아래에 두며 문서나 로그 요약에 ID를 나열하지 않는다.

순서는 다음과 같이 고정한다. 뒤 단계는 앞 단계의 complete 증거가 없으면 시작하지 않는다.

1. 최종 Backend immutable image를 만든 뒤 기존 5개 서비스 revision을 보관하고 migration expected head `0068_lead_cost_deferral`을 적용한다.
2. `control` 큐와 새 IndexNow·공급자 사용량 drain task를 소비하는 Worker를 Beat와 API보다 먼저 배포한다.
3. [FAQ 구두점 복구](../../backend/app/utils/repair_legacy_faq_questions.py)를 dry-run하여 revision·후보 hash에 묶인 exact 3건 plan을 만든다. 같은 plan을 CAS apply해 물음표만 수정하고, 3건 모두 독립 검수 대기 상태인지 확인한다.
4. [기존 공개 본문 재검수](../../backend/app/services/content_public_review_backfill.py)를 exact 22건 allowlist로 dry-run한 뒤 제한된 batch로 반복한다. 후보·brief·현재 Essence·source snapshot·revision CAS가 일치한 결과만 저장한다. ABSENT 92건과 PASS 1건은 레거시 상태라는 이유만으로 공급자 재검수하지 않는다.
5. [기존 이미지 인증](../../backend/app/services/content_image_certification.py)을 exact 115건 manifest로 dry-run한 뒤 제한된 batch로 반복한다. 실제 저장 이미지 바이트를 정책 검수하고 content-addressed 불변 사본과 byte-bound 인증을 CAS로 저장한다. 안전하지 않은 이미지는 교체하며, URL에서 만든 가짜 hash나 영구 레거시 허용값을 쓰지 않는다.
6. 레거시 이미지 시각 우회가 없는 새 엄격 공개 gate를 exact baseline ID manifest에 대해 read-only로 실행한다. ID 집합에 추가·누락·상태 drift가 없어야 하고, 115건의 허용·차단 사유와 공개 표면이 앞선 수리 결과와 정확히 맞아야 한다. 비공개 기존 행도 일반 생성·발행 경로의 엄격한 gate를 통과해야 한다.
7. 새 Worker의 7개 큐 canary와 새 정기 복구 task를 확인하고 RedBeat `2026-09-07.2`를 재조정한 뒤 Beat와 production readiness를 실행한다.
8. 그 뒤에만 API를 배포하고 Site/Admin, 실제 트래픽·revision과 외부 공개 표면을 검증한다.

FAQ·본문·이미지 복구 도구는 로컬 검증을 통과했지만 이 gate의 운영 실행 증거는 아직 없다. 유료 공급자 호출이 포함된 본문·이미지 backfill도 실행하지 않았다. 현재 문서는 배포 허가가 아니며, 최종 단계별 JSON·로그·readiness 결과는 `/private/tmp/reputation-autonomy-release`에서 확인하고 실행 후 검증표에 요약한다.

`backend`는 Backend 3종 배포다. **`api`도 Worker와 Beat를 먼저 함께 갱신하는 경로**이므로 API만 바뀐다고 가정하지 않는다. `worker`, `beat`, `site`, `admin`, `migrate` 개별 대상도 제공한다. 정확한 범위는 스크립트의 마지막 case문을 확인한다.

2026-09-07 배포는 로컬 업로드 지연 때문에 Cloud Build와 digest 기반 rollout을 사용했고 기존 서비스 환경·secret 참조 hash 보존을 별도로 검증했다. 이 실행을 `scripts/deploy.sh`의 기본 Cloud Build 기능으로 오해하지 않는다. 반복 가능한 기본 진입점은 위 스크립트이며 대체 배포도 같은 마이그레이션·Worker/Beat·readiness 순서를 지켜야 한다.

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
| 공개 본문 | FAQ exact 3건 CAS 수리 뒤 검수 대기, 미해결 exact 22건 독립 재검수 종결, AI 검수 레거시 93건을 과거 상태만으로 재호출하지 않았는지 확인 |
| 기존 이미지 | 공개 115건 전체의 실제 바이트 재검수, 109건 인증 공백과 6건 검수 시각 전용 상태 해소, 불변 사본·CAS writeback Job/CLI 종결 |
| 로그 | 새 revision의 실제 오류와 작업 실패 여부, control/background queue wait; 조회 시점·범위 기록 |

병원 헬스는 `https://{병원 공개 호스트}/.well-known/reputation-health`다. HTTP 200뿐 아니라 `hospital_id`, `slug`, `canonical_host`, `release`(Cloud Run에서는 `K_REVISION`)가 해당 병원·배포와 맞아야 한다. 기본 주소와 자기 도메인을 모두 사용하는 운영 집합을 대상으로 확인한다.

`python -m app.utils.production_readiness`와 대기형 `python -m app.utils.wait_production_readiness`는 배포 이미지·운영 연결에서 실행하는 검사다. 공개 페이지 200만으로 DB 연결·스케줄 실행·실제 외부 AI 생성 성공까지 증명하지 않는다. 모델·이메일·Slack의 실제 호출은 별도의 명시된 검증 범위로 기록한다.

Worker는 `control,default,content,sov,reports,leadgen,certificates` 7개 큐를 하나의 서비스에서 소비한다. 발행·캐시 복구·자율 복구가 control priority 0으로 들어가고 IndexNow·usage spool은 낮은 우선순위다. control canary를 포함해 큐별 최신 canary가 현재 release를 가리키는지 확인한다. control은 전용 Worker가 아니므로 부하 중 30초 기준을 충족했는지는 queue wait 구조화 로그로 따로 증명한다.

RedBeat `2026-09-07.2`에는 IndexNow retry와 provider usage spool drain이 매 1분 추가된다. 영속 스케줄 재조정 뒤 등록 이름·task route·서명 목적이 새 이미지와 맞는지 확인한다. 정상 drain·복구를 Slack 메시지로 시험하지 말고 DB 실행 상태와 구조화 로그를 사용한다. 배포 전 기존 이미지 인증은 이 주기 작업이 아니라 별도 일회성 Job/CLI로 실행한다. 최종 실패나 사람이 결정할 예외만 [알림 정책](slack-notification-policy.md)에 따라 확인한다.

동결된 Backend 전체 검증은 3,079 passed, 0 failed, 0 skipped, 18 warnings, coverage 83.06%, 95.36초다. 실제 PostgreSQL·Redis·native PDF, fresh schema와 데이터가 채워진 `0064`에서 `0068`로의 upgrade를 포함한다. Ruff·diff·copy guard도 통과했고 DB 연결 예산은 75/80이다. 독립 검토 결과는 code APPROVE, architecture CLEAR이며 P0/P1은 0건이다.

Admin은 전체 536/536 tests·typecheck·lint를 통과한 뒤 마지막 raw fallback 수정의 관련 19 tests·typecheck도 통과했다. 앞선 production build는 통과했으며 최종 소스의 CI lint·build가 남았다. Site는 310 tests·typecheck·lint를 통과했고 로컬 production build의 EPERM 때문에 Linux CI build가 남았다. 이 로컬 결과는 운영 migration·backfill·canary·배포 성공을 뜻하지 않는다. 남은 증거는 [후속 구현 기록](../reviews/2026-09-07-purpose-autonomy-efficiency-implementation.md)에 분리한다.

## 롤백과 문서 변경

```bash
bash scripts/deploy.sh rollback
```

롤백은 `.deploy-rollback`에 저장된 revision으로 **트래픽을 복귀**한다. 이미 적용된 DB 마이그레이션은 되돌리지 않는다. 스키마가 구버전과 호환되는지 먼저 판단하며 무조건 `alembic downgrade`하지 않는다. 롤백 뒤에도 현재 큐·API·공개 표면을 다시 확인한다.

문서만 바뀐 릴리스는 기존 런타임이 그대로임을 기록한다. 문서 Git SHA를 기존 이미지의 소스 SHA로 바꿔 적거나 불필요한 전체 재배포를 수행하지 않는다.
