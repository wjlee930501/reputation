# 핸드오프 — 리드마그넷 잔여 작업 (2026-07-30)

> 설계 정본: `docs/plans/2026-07-29-lead-diagnosis-funnel-design.md` (rev3)
> 명세: `docs/prd/REPUTATION-AI-DIAGNOSIS-FUNNEL-PRD-2026-07.md` (rev7)
> 적대적 검토: Codex GPT-5.6 (2026-07-30) — BLOCKER 5 + HIGH 8 + MEDIUM 3건 반영

---

## 0. 30초 요약

무료 AI 노출 진단 퍼널(1단)을 **접수 → 측정 → 리포트 → 발송 → 파기**까지 구현했다.
남은 것은 **Admin 화면 1개**와 **배포 준비**뿐이다.

커밋 8개, `backend 1038 passed · site 140 · admin 144 · scripts 46`, ruff·tsc·eslint 통과.
Codex GPT-5.6 적대적 검토를 돌려 나온 16건도 반영·검증 완료(§4).

---

## 1. 지금 되는 것

```
랜딩 /ai-diagnosis
  "오늘 남은 자리 N/20"  ← 실제 카운터
    ↓ 폼 작성 → [확인 모달] "이 정보가 정확한가요?"
접수  POST /api/v1/public/diagnosis
    · 선착순 자리 1칸 (DB가 배정, UNIQUE(slot_date, slot_no))
    · 전화번호 + 이메일 이중 영구 잠금 (해시, 부분 유니크 인덱스)
    · 질의 3개 생성 (병원명은 절대 안 들어감)
    ↓
폴러  drain_lead_diagnoses (1분마다)  ← DB가 큐다. outbox 없음
    ↓
측정  질의 3 × 플랫폼 2 × 반복 3 = 18측정
    · 질의 단위 공유 캐시 (같은 질의 두 번째 병원은 약 5원)
    · leadgen 전용 세마포어 (유료 측정과 경합 안 함)
    ↓
리포트 블러 PDF → GCS → lead_report_artifacts
    ↓
발송  Resend (Idempotency-Key = delivery 행 id)
    ↓
열람  /ai-diagnosis/status/{token}  (폴링 → 리포트)
    ↓
파기  매일 04:00, 180일 경과분 (GCS 삭제 → DB 커밋 순서)
```

---

## 2. 남은 작업

### 2-1. Admin (설계 §11 10번) — **유일한 필수 잔여**

| 화면 | 무엇을 | 상태 |
|---|---|---|
| 리드 목록에 진단 요약 | 플랫폼별 측정/언급 횟수, 상태 3축 | 미착수 (PRD F7-1) |
| **잠금 해제 버튼** | 사유 입력 + `POST /api/admin/leads/{id}/release-lock` 호출 | **API 완료**, UI 미착수 |
| 실패 목록 (DLQ) | `execution_status=FAILED` · `report_status=BLOCKED` + 재실행 | 미착수 |

**잠금 해제 API는 구현·검증 완료다** (`release_diagnosis_lock`, 사유 필수 + 감사 로그,
`test_lead_diagnosis_review_fixes.py::TestAdminReleaseLock`이 "해제 후 실제로 재신청이
된다"까지 확인). 남은 것은 Admin UI에서 그 API를 부르는 버튼뿐이다.

전화번호 잠금은 제3자가 먼저 신청해 원장의 기회를 소진시킬 수 있고, 그때 푸는 유일한
경로가 이것이다 — UI가 없으면 AE가 API를 직접 호출해야 한다.

### 2-2. 배포 전 필수

| 항목 | 상태 |
|---|---|
| 마이그레이션 `0035`·`0036` 프로덕션 적용 | **미적용** |
| `LEAD_LOCK_HASH_PEPPER` · `LEAD_REPORT_TOKEN_SECRET` · `RESEND_API_KEY` Secret Manager 등록 | **미등록** — 없으면 부팅 실패 |
| 워커 `-Q ...,leadgen` | 코드 반영 완료 (entrypoint·compose) |
| `REDBEAT_SCHEDULE_VERSION` | `2026-07-30.1`로 상향 완료 |
| `LEAD_MAIL_FROM` 도메인 Resend 인증 | **미확인** |

> ⚠️ **`LEAD_LOCK_HASH_PEPPER`는 한 번 정하면 바꾸지 않는다.**
> 값이 바뀌면 기존 잠금이 전부 풀려 이미 신청한 병원이 다시 신청할 수 있게 된다.

### 2-3. 출시 게이트 (PRD §8)

- **부하 시험 미실시** — 20건을 5분 안에 몰아넣고 P95 15분, 그 시험 중 유료 경로
  지연 불변. `LEADGEN_PROVIDER_CONCURRENCY` 기본 15는 **실측 전 추정치**다.
- shadow pilot 30건 미실시
- 법무 검토 — 대표가 직접 처리 (2026-07-30 지시로 이 트랙에서 제외)

### 2-4. 미구현으로 남긴 것 (의도)

- **PRD F3-7** (`AMBIGUOUS` 3값 + 판정에 지역 주입) — `mention_verdict` 컬럼 자리만
  만들었고 구현하지 않았다. 동명 의원이 흔해 진단 정확도의 선행 조건일 수 있다.
- **PDF 텍스트 추출 검사** — 로컬에 weasyprint 네이티브 의존성이 없어 HTML 층까지만
  돈다. CI는 `REQUIRE_PDF_RENDER=1`로 강제하고 apt 의존성을 설치한다.

---

## 3. 구현하며 내린 판단 (설계와 다른 것)

| 무엇 | 설계 | 실제 | 이유 |
|---|---|---|---|
| 중복 신청 응답 | 기존 행 200 반환 | **409, 상태 URL 미반환** | 영구 잠금 아래에서는 남의 대표번호만 알면 그 병원 리포트 링크를 얻는 경로가 된다 |
| 리포트 토큰 | 난수 + 해시 저장 | **HMAC(secret, diagnosis_id)** | 메일은 측정이 끝난 나중에 같은 링크를 실어야 하는데 난수는 원문 복원이 불가능하다. 진단당 토큰 2개를 만들면 폐기·만료를 두 벌 관리하게 된다 |
| `request_fingerprint` | 별도 컬럼 | **제거** | 영구 잠금이 이미 연타 방지를 한다 |

---

## 4. 적대적 검토에서 나온 것 (전부 수정됨 — 커밋 5d1f591)

Codex GPT-5.6에 브랜치 전체를 던져 19건을 받았고, 그중 실제 결함 16건을 고쳤다.
**전부 테스트가 통과하는데도 남아 있던 문제다.**

가장 무거운 셋:

1. **캐시 충돌이 측정 결과 18행을 삼켰다** — `store_answer`의 세션 전체 rollback.
   공유 캐시가 정확히 유도하는 경합이라 발생 확률이 높았다. SAVEPOINT로 격리.
2. **종결 상태(`PURGED`·`BLOCKED`)가 CHECK에 걸려** 파기 배치 전체를 매일 롤백시켰다.
   같은 독성 행이 계속 재선택되어 법정 파기 의무가 조용히 멈추는 구조였다.
3. **`/erase`가 진단 산출물을 전혀 파기하지 않았다** — PDF와 활성 토큰이 남는데
   `purged_at`만 찍혀, 이후 배치가 그 리드를 영원히 건너뛴다.

미해결로 남긴 것 2건 (설계 판단 필요, §2-5 참조):
- **공유 캐시가 single-flight가 아니다** — 같은 지역 20건이 동시에 시작하면 전부
  cache miss를 읽고 각자 유료 호출을 한다. "두 번째 병원 약 5원"은 순차 실행 전제다.
- **슬롯이 공급자 호출 수의 상한은 아니다** — 재시도 × 실행 재시도로 최악 162콜.

## 5. 그 전에 구현하며 발견한 결함 (전부 수정됨)

1. **`+82 02-123-4567`이 다른 잠금 키가 됐다** — 표기만 바꾼 재신청이 통했다
2. **진료과 == 키워드면 질의가 2개만 생성** — 측정 수가 흔들리면 원가도 흔들린다
3. **잠금 위반이 500으로 샜다** — `commit()`이 아니라 `flush()`에서 터진다
4. **CDN이 개인 리포트를 캐시할 수 있었다** — `PublicApiCacheMiddleware`가
   `/api/v1/public/*`에 `public, max-age=300`을 붙이고 있었다
5. **큐만 나눠서는 F6-1이 안 지켜졌다** — `sov_engine` 세마포어가 전역이라
   무료 진단이 몰리면 유료 측정이 굶는다
6. **PRD §6 원가표가 `gpt-5-mini` 기준으로 낡아 있었다** — 실제 원가 8% 과소

---

## 6. 검증 명령

```bash
cd backend && .venv/bin/python -m ruff check app tests && .venv/bin/python -m pytest -q   # 1038
cd .. && backend/.venv/bin/python -m pytest scripts/ -q                                   # 46
cd site && npm run typecheck && npm run lint && npm test                                  # 140
cd ../admin && npm run typecheck && npm run lint && npm test                              # 144
backend/.venv/bin/python scripts/check_user_facing_terms.py
bash -n scripts/deploy.sh && bash -n scripts/setup-gcp.sh
```

통합 테스트는 로컬 Postgres **5434**를 쓴다. `reputation`과 `reputation_test`
**둘 다** 마이그레이션돼 있어야 한다(기본 실행은 `reputation_test`를 본다):

```bash
cd backend && APP_ENV=development \
  DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test" \
  SYNC_DATABASE_URL="postgresql://reputation:reputation@localhost:5434/reputation_test" \
  .venv/bin/python -m alembic upgrade head
```

---

## 7. 새 코드 지도

**백엔드**

| 파일 | 역할 |
|---|---|
| `models/lead_diagnosis.py` | 6개 테이블 + 상태 3축 enum |
| `alembic/versions/0035_add_lead_diagnosis.py` | 스키마 (부분 유니크 인덱스는 raw DDL) |
| `alembic/versions/0036_lead_diagnosis_hardening.py` | 검토 반영 — 자리 카운터·CHECK·발송 UNIQUE |
| `api/admin/leads.py` | 즉시 파기(오케스트레이터 경유) · **잠금 해제** |
| `api/public/diagnosis.py` | 접수 · 남은 자리 · 상태 · 리포트 |
| `services/lead_diagnosis_identity.py` | 전화·이메일 정규화 + 잠금 해시 |
| `services/query_mapper.py` | 질의 3개 생성 |
| `services/lead_diagnosis_engine.py` | 18측정 실행 + 상태 판정 |
| `services/lead_query_cache.py` | 질의 단위 공유 캐시 |
| `services/lead_report.py` | payload allowlist + 렌더 + 저장/삭제 |
| `services/mailer.py` · `lead_delivery.py` | Resend + 발송 오케스트레이션 |
| `services/lead_privacy.py` | 파기 cascade (확장) |
| `workers/lead_diagnosis_tasks.py` | 폴러 + 실행/리포트/발송 태스크 |
| `templates/lead_report.html` | 블러 리포트 |

**사이트**

| 파일 | 역할 |
|---|---|
| `app/ai-diagnosis/page.tsx` · `DiagnosisForm.tsx` | 랜딩 + 폼 + 확인 모달 |
| `app/ai-diagnosis/status/[token]/` | 상태 페이지 (폴링) |
| `app/api/diagnosis/**` | BFF 프록시 4종 |
| `lib/diagnosis-form.ts` | 폼 순수 로직 |

---

## 8. 테스트를 쓸 때 지킨 규칙

핸드오프(2026-07-29)의 원칙을 이어받았다 — **자기참조를 피하고 독립적으로 정해지는
값 사이의 제약을 건다.** 이번에 그 원칙으로 바꾼 기존 테스트 2개:

- `test_celery_routing.py` — 큐 목록을 하드코딩하다가 **`docker-entrypoint.sh`에서
  파싱**하도록. 하드코딩이면 두 곳을 같이 고치는 한 통과해 드리프트를 못 잡는다.
- `test_sov_timing_budget.py` — 소스 텍스트 grep에서 **실제 세마포어 용량 검사**로.
- `test_lead_diagnosis_constraints.py` — `MAX(slot_no)+1`을 "접수 API가 쓰는 바로 그
  SQL"이라고 적어놨으나 API는 그것을 쓰지 않았다. 검증한다고 **주장한 것**과 코드가
  달라서 동시 접수가 무너지는 것을 아무도 못 봤다 — 실제 배정 SQL을 보도록 교체.

새로 건 관계 제약의 예: 메일 재시도 일정 합 < Resend 멱등성 창(24시간).
상수 하나만 늘려도 실패한다.
