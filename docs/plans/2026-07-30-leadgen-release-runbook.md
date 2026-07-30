# 배포 런북 — 리드마그넷 무료 진단 (2026-07-30)

> 대상 커밋: `main` (리드마그넷 퍼널 머지 + CI ruff 수정)
> 이 문서는 **순서대로 그대로 따라 하면 되는** 절차다. 판단이 필요한 곳은 ⚠️로 표시했다.

---

## 0. 배포 완료 기록 (2026-07-30)

| | 결과 |
|---|---|
| 시크릿 | `LEAD_LOCK_HASH_PEPPER`·`LEAD_REPORT_TOKEN_SECRET` 생성 + 권한 부여. `RESEND_API_KEY`는 존재했으나 **값이 `PLACEHOLDER`였다**(2026-02 생성) — §2-3 참조 |
| 마이그레이션 | `0034`→`0035`→`0036` 적용 완료 (0034는 7월 29일 미배포분) |
| backend | `reputation-api-00052` · `worker-00035` · `beat-00034` |
| site | `reputation-site-00041` |
| admin | 미배포 (이 릴리스에 변경 없음) |
| 직전 배포 | 2026-07-28 23:21 — 즉 7월 29~30일 작업 전체가 이번에 나갔다 |

**배포 중 발견해 고친 것**: BFF 프록시가 `/api/v1/public/**public**/diagnosis`로 요청해
404가 났다(`getApiBase()`가 이미 `/public`을 포함). 커밋 `1c5afd5`에서 수정 후 재배포.

되돌리기: `bash scripts/deploy.sh rollback` (기준 리비전은 `.deploy-rollback`).

> 이 저장소는 **자동 배포(CD)가 없다.** `main`에 푸시해도 아무것도 나가지 않는다.

### 배포 전 상태였던 것 (참고 — 이제 완료)

원래 이 문서는 배포 절차 안내였다. 아래 1~4절은 **다음 환경 구축이나 재현 시** 쓴다.

---

## 1. 준비 — 인증

```bash
gcloud auth login
gcloud config set project mso-platform-481505
gcloud auth configure-docker asia-northeast3-docker.pkg.dev
```

---

## 2. 시크릿 3종 등록 (배포 전 필수)

**없으면 백엔드가 부팅 자체를 실패한다** (`config.py`가 dev 기본값을 프로덕션에서 거부).

### 2-1. 잠금 해시 pepper

무료 진단 1회 제한(전화번호·이메일)의 해시에 섞는 값이다.
이게 없으면 전화번호가 무지개표로 역산돼 잠금 해시가 개인정보가 된다.

> ⚠️ **한 번 정하면 절대 바꾸지 않는다.** 값이 바뀌면 기존 잠금이 **전부 풀려서**
> 이미 무료 진단을 받은 병원이 다시 신청할 수 있게 된다.

```bash
gcloud secrets create LEAD_LOCK_HASH_PEPPER --replication-policy=automatic 2>/dev/null || true
openssl rand -base64 48 | gcloud secrets versions add LEAD_LOCK_HASH_PEPPER --data-file=-
```

(값을 화면에 찍지 않고 바로 저장한다 — 셸 히스토리와 터미널 스크롤백에 남지 않는다.)

### 2-2. 리포트 열람 토큰 pepper

원장에게 보내는 리포트 링크의 해시에 섞는 값이다. 없으면 링크를 위조할 수 있다.

```bash
gcloud secrets create LEAD_REPORT_TOKEN_SECRET --replication-policy=automatic 2>/dev/null || true
openssl rand -base64 48 | gcloud secrets versions add LEAD_REPORT_TOKEN_SECRET --data-file=-
```

> ⚠️ 이것도 바꾸면 **이미 보낸 메일의 링크가 전부 죽는다.** 로테이션 금지.

### 2-3. Resend API 키 — ⚠️ 사람이 발급해야 한다

> **2026-07-30 확인: 기존 값은 `PLACEHOLDER` 문자열이었다.** 시크릿이 존재한다는 사실만으로
> 넘어가지 말 것 — 실제 발송으로만 유효성을 알 수 있다(401은 발송 시점에 나온다).

**Resend는 메일 보내주는 외부 서비스다.** 우리가 원장에게 리포트 링크를 메일로 보낼 때
쓴다. 지금 코드에 메일 발송 경로가 처음 들어갔고, 이 키가 없으면 측정·리포트는 되는데
**아무도 리포트를 받지 못한다.**

1. https://resend.com 로그인 → **API Keys** → 새 키 생성 (권한: Sending access)
2. 저장:

```bash
gcloud secrets create RESEND_API_KEY --replication-policy=automatic 2>/dev/null || true
gcloud secrets versions add RESEND_API_KEY --data-file=-   # 붙여넣고 Ctrl+D
```

3. **발신 도메인 인증** — Resend → Domains → `motionlabs.kr` (또는
   `reputation.motionlabs.kr`) 추가 → 안내받은 DNS 레코드(SPF·DKIM) 등록.
   인증 안 하면 메일이 스팸으로 가거나 아예 거부된다.
   현재 코드의 발신 주소는 `LEAD_MAIL_FROM=Re:putation <noreply@reputation.motionlabs.kr>`이며,
   인증한 도메인과 일치해야 한다.

### 2-4. 접근 권한 부여

```bash
SA=reputation-sa@mso-platform-481505.iam.gserviceaccount.com
for S in LEAD_LOCK_HASH_PEPPER LEAD_REPORT_TOKEN_SECRET RESEND_API_KEY; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${SA}" \
    --role=roles/secretmanager.secretAccessor --quiet
done
```

### 2-5. 확인

```bash
for S in LEAD_LOCK_HASH_PEPPER LEAD_REPORT_TOKEN_SECRET RESEND_API_KEY; do
  printf "%s -> " "$S"
  gcloud secrets versions list "$S" --limit=1 --format="value(name,state)" 2>&1 | head -1
done
```

세 줄 모두 버전 번호와 `ENABLED`가 나와야 한다.

---

## 3. DB 마이그레이션 (0035 · 0036)

**"마이그레이션"은 DB 표 구조를 바꾸는 작업이다.** 이번에는 무료 진단용 표 7개를 새로
만들고(진단·측정결과·질의캐시·토큰·리포트파일·발송이력·자리카운터) 기존 `sales_leads`에
칸 6개를 추가한다. 이 작업 없이 새 코드를 띄우면 **없는 표를 찾다가 전부 실패**한다.

```bash
cd /Users/woojinlee_officemacmini/Documents/projects/reputation
bash scripts/deploy.sh migrate
```

> 로컬에서 업/다운 왕복을 확인해뒀다. 기존 데이터는 건드리지 않는다 —
> 새 표를 만들고, `sales_leads.question`의 NOT NULL만 푼다(기존 행은 전부 값이 있어 무손실).

되돌리려면: `alembic downgrade 0034_add_query_intent` (새 표만 지운다).

---

## 4. 배포

### 4-1. 워커 큐 — 무엇이 바뀌었나

**"워커"는 화면 뒤에서 시간 오래 걸리는 일을 처리하는 프로세스다.** 콘텐츠 생성, AI 측정,
리포트 만들기가 다 거기서 돈다. 일을 종류별로 "큐"에 나눠 담는데, 무료 진단용으로
`leadgen` 큐를 새로 만들었다.

**이 큐를 워커가 읽도록 인자를 추가하지 않으면, 무료 진단이 큐에 쌓이기만 하고 영원히
실행되지 않는다.** 코드(`docker-entrypoint.sh`)에는 이미 반영돼 있으므로 **워커를
재배포하면 자동으로 적용**된다 — 따로 할 일은 없다. (`tests/test_celery_routing.py`가
이 값을 실제 파일에서 읽어 대조하므로 다시 어긋나면 CI가 잡는다.)

### 4-2. 실행

```bash
PUBLIC_DOMAIN=reputation.motionlabs.kr \
ADMIN_DOMAIN=admin.reputation.motionlabs.kr \
  bash scripts/deploy.sh all
```

`all` = 마이그레이션 → backend(api·worker·beat) → site → admin 순서.
3번을 이미 했으면 마이그레이션은 "적용할 것 없음"으로 지나간다.

중간에 실패하면: `bash scripts/deploy.sh rollback` (트래픽만 이전 리비전으로 되돌린다 —
DB 마이그레이션은 별도 판단).

---

## 5. 배포 후 확인 — 2026-07-30 실행 결과

| 항목 | 결과 |
|---|---|
| 랜딩 `/ai-diagnosis` | ✅ 200 |
| 남은 자리 API | ✅ `{"date":"2026-07-30","total":20,"used":0,"remaining":20}` |
| 개인 표면 `no-store` + `noindex` | ✅ |
| 상태 페이지 (없는 토큰) | ✅ 200 (「유효하지 않은 링크」 화면) |
| 폴러 `drain_lead_diagnoses` | ✅ 1분마다 실행 확인 |
| 검증 거부 경로 | ✅ 병원명 키워드 400 · 동의 누락 400, **자리 소비 없음** |
| **실제 신청 1건** | ✅ 실행 — 측정·리포트 통과, **메일만 실패** (아래) |

### 실제 신청 결과 (진단 `5e2275e5-8c30-4444-895e-629973d6c7ad`)

신청 `01:12:52` → 측정 완료 `01:13:38`(45초, 18/18 성공) → 리포트 `01:14:0x`(5초) →
**READY `01:14:35`**. 전체 **약 100초** — SLA 15분 대비 크게 여유.

리포트 PDF(66KB, 2쪽) 검증:
- ✅ 공개해야 할 것 전부 있음 — 플랫폼별 `9회 중 0회`, 질의 원문 3개, 시스템 지시문 전문,
  답변/판정 모델명, 질의별 측정 일시, 표본 한계 고지, F5-5 고지 2종
- ✅ 가려야 할 것 없음 — 경쟁 병원명·답변 원문 발췌·개선 액션 어느 것도 나타나지 않음
  (추출 텍스트 939자가 전부 위 항목으로 설명된다)
- ✅ 플랫폼 표기가 `OpenAI API · gpt-5.6-luna` 형식 (F5-1)
- ✅ 응답 헤더 `no-store` + `noindex`

**메일 — 두 단계로 막혔고 둘 다 해결했다.**

1. `resend_error_401: API key is invalid` — 시크릿 값이 실제 키가 아니라 문자열
   `PLACEHOLDER`였다(11자, 2026-02-13 생성). 유효한 키를 등록하고 **워커를 재배포**했다.
   ⚠️ Cloud Run은 `secretKeyRef`를 **인스턴스 시작 시 한 번만** 읽는다 — 새 시크릿 버전을
   추가해도 돌고 있는 워커는 옛 값을 계속 쓴다. 재배포가 필수다.
2. `resend_error_403: reputation.motionlabs.kr domain is not verified` — Resend에 발신
   도메인이 등록되지 않았다. Route53에 DKIM(TXT)·SPF(MX+TXT) 3개를 넣고 Resend에서
   Verify를 실행해 통과(02:33 UTC). 별도 테스트 발송이 HTTP 200을 받아 확인됐다.

> **401 → 403 → 200의 진행이 발송 로직 자체가 정상임을 증명한다.** 인증을 통과하고
> Resend까지 요청이 제대로 갔으며, 막힌 것은 매번 우리 쪽 설정이었다.

이전 기록:
`RESEND_API_KEY`의 값이 실제 키가 아니라 문자열 `PLACEHOLDER`였다(11자, 2026-02-13 생성).
키가 **존재하는 것**과 **유효한 것**은 다르다 — 존재 여부만 확인하고 넘어갔던 것이 이 릴리스의
유일한 미검출 결함이었고, E2E 신청 1건이 그것을 잡았다.

> ⏱ **재시도 창**: 발송은 5분·30분·4시간 후 자동 재시도되고, 그 안에 유효한 키가 들어오면
> **메일이 저절로 도착한다.** 4시간이 지나면 `FAILED` + Slack으로 넘어가 수동 처리가 필요하다
> (중복 발송 위험 때문에 자동 재발송을 하지 않는다 — 설계 §5-4).

## 5-1. 확인 절차 (원문)

```bash
# ① 랜딩이 뜨는가
curl -s -o /dev/null -w "%{http_code}\n" https://reputation.motionlabs.kr/ai-diagnosis   # 200

# ② 남은 자리 카운터가 실제 값을 주는가
curl -s https://reputation.motionlabs.kr/api/diagnosis/slots                              # {"remaining":20,...}

# ③ 개인 리포트 표면이 캐시되지 않는가 (CDN에 남으면 남의 리포트가 새어나간다)
curl -sI https://reputation.motionlabs.kr/api/diagnosis/xxx/status | grep -i "cache-control"
#   → no-store 여야 한다

# ④ beat 스케줄이 새 태스크를 들고 있는가 (1분마다 도는 폴러)
gcloud run services logs read reputation-beat --region=asia-northeast3 --limit=50 \
  | grep -i drain_lead_diagnoses
```

### ⑤ 실제 신청 1건 — **가장 중요하다**

랜딩에서 **우리 회사 정보로** 한 건 신청한다(테스트용 전화번호·이메일).

> ⚠️ 전화번호와 이메일은 **영구 1회 제한**이다. 테스트에 실제 병원 번호를 쓰지 말 것.
> 테스트로 소진한 잠금은 Admin에서 풀 수 있다:
> `POST /api/v1/admin/leads/{lead_id}/release-lock` (사유 필수)

확인할 것:
1. 확인 모달에 입력값이 그대로 뜨는가
2. 접수 후 상태 페이지로 이동하는가
3. 15분 안에 메일이 오는가 ← **여기가 이번 배포의 핵심 리스크다** (메일 경로가 처음 나간다)
4. 리포트 PDF에 경쟁 병원명·답변 원문이 **없는가**
5. 리포트에 질의 원문·모델명·측정일시가 **있는가**

---

## 6. 남은 위험 (배포해도 해결되지 않는 것)

| # | 무엇 | 판단 필요 |
|---|---|---|
| 1 | **`LEADGEN_PROVIDER_CONCURRENCY=15`는 실측 전 추정치**다. 20건이 동시에 몰리면 15분 SLA를 못 지킬 수 있다. 또한 세마포어는 **프로세스별**이므로 `-c 2`에서 실효 동시성은 30이다 | 출시 전 부하 시험 |
| 2 | **공유 캐시가 single-flight가 아니다.** 같은 질의를 동시에 시작한 두 진단은 각자 유료 호출을 한다 | **보류 결정** — 아래 6-1 |
| ~~3~~ | ~~자리 20건이 호출 상한은 아니다~~ → **해결(2026-07-30)**: `cost_guard`에 `leadgen` 카테고리 추가. 예약 단위는 캐시 미적중 답변 호출 수이며 일 500 / 월 12,000. 초과 시 측정을 시작하지 않고 FAILED + Slack | — |
| ~~4~~ | ~~Admin UI 미착수~~ → **해결(2026-07-30)**: 리드 목록에 3축 상태·확인 필요 필터·재발송·1회 제한 해제 추가 | — |
| ~~4-1~~ | ~~발송 수동 재시도 도구가 없다~~ → **해결(2026-07-30)**: `POST /admin/leads/{id}/retry-report-delivery`. 멱등성 키(행 id)를 유지하므로 24시간 안에는 중복이 구조적으로 불가능하고, 창을 넘긴 건만 운영자 동의를 요구한다 | — |
| 5 | **npm audit 4건(site)** — 재확인 결과 **Next 16 마이그레이션은 불필요**하다. `npm audit fix` dry-run이 7패키지 갱신으로 next·postcss·brace-expansion을 해소하고, 남는 4건은 sharp→libvips로 Next 16을 쓰는 admin도 동일한 하한이다 | 패치 적용 + 배포 |

### 6-1. single-flight를 왜 지금 안 하는가

절감 상한이 작고 구현 대가가 크다.

- 손실은 **동시 시작**한 두 진단이 같은 질의를 쓸 때만 발생한다 — 같은 날, 같은 지역·진료과·모델,
  그리고 공급자 호출이 겹치는 1분 남짓의 창. 하루 20건 규모에서 기대 절감은 하루 수천 원 수준이다.
- 효과를 내려면 **승자가 답변을 얻는 즉시 캐시에 공개**해야 한다. 그런데 캐시 적재는 3단계
  설계상 18건을 모두 모은 **뒤** 단일 커밋으로 일어난다(2단계에서 공유 `AsyncSession`을 건드리지
  않는 것이 이 분리의 이유다). 즉 즉시 공개는 측정마다 별도 세션을 요구한다.
- 그 별도 세션이 문제다. `scripts/check_db_connection_budget.py` 기준 현재 여유가
  **75/80 커넥션**뿐이라, 측정당 단기 세션을 버스트로 여는 설계는 연결 예산을 깨뜨린다.
- 그리고 락은 프로세스를 넘어야 한다(Celery prefork). 공급자 호출 시간 내내 유지되는
  크로스 프로세스 락 + 캐시 재조회 대기 루프가 필요하다.

**대신 호출 상한(위 #3)이 폭주를 막는다.** 중복 지불은 상한 안에서 일어나고 `leadgen` 카운터에
그대로 보이므로, 자리 수를 늘리거나 캐시 적중률이 낮게 관측될 때 다시 판단한다.

---

## 7. 배포 안 하고 미루는 경우

지금 살아 있는 사이트는 이전 리비전이고 **랜딩에 무료 진단 입구가 없다.** 즉 배포를
미루면 사용자에게 보이는 것은 아무것도 바뀌지 않는다 — 위험 없이 미룰 수 있다.

단, 다음 배포 때 함께 나가는 것들이 있다:
- `0034_add_query_intent` 마이그레이션 (언급률 분모 분리, 7월 29일 작업분)
- 측정 모델 `gpt-5.6-luna` 전환 및 타임아웃 상향
- `SITE_REVALIDATE_SECRET`이 backend 필수로 승격된 변경

이들은 **이미 `main`에 있고 아직 배포되지 않았다.** 즉 이번 배포는 리드마그넷만이
아니라 7월 29~30일 작업 전체를 내보내는 것이다.
