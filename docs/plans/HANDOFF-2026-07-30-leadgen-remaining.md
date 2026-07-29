# 핸드오프 — 리드마그넷 잔여 작업 (2026-07-30)

> 설계 정본: `docs/plans/2026-07-29-lead-diagnosis-funnel-design.md` (rev3)
> 명세: `docs/prd/REPUTATION-AI-DIAGNOSIS-FUNNEL-PRD-2026-07.md` (rev7)
> 브랜치: **`feat/lead-diagnosis-funnel`** (main에 머지 안 됨)

---

## 0. 30초 요약

무료 AI 노출 진단 퍼널(1단)을 **접수 → 측정 → 리포트 → 발송 → 파기**까지 구현했다.
남은 것은 **Admin 화면 1개**와 **배포 준비**뿐이다.

커밋 6개, `backend 1007 passed · site 140 · admin 144 · scripts 46`, ruff·tsc·eslint 통과.

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

| 화면 | 무엇을 | 왜 |
|---|---|---|
| 리드 목록에 진단 요약 | 플랫폼별 측정/언급 횟수, 상태 3축 | PRD F7-1 |
| **잠금 해제** | `POST /api/admin/leads/{id}/release-lock` + 버튼 | **F1-7 — 없으면 F1-6이 리드 차단 장치가 된다** |
| 실패 목록 (DLQ) | `execution_status=FAILED` · `report_status=BLOCKED` + 재실행 | 별도 DLQ 큐가 없으므로 이 화면이 죽은 편지함이다 |

**잠금 해제가 가장 중요하다.** 전화번호 잠금은 제3자가 먼저 신청해 원장의 기회를
소진시킬 수 있고, 그때 푸는 유일한 경로가 이 버튼이다. 백엔드는
`lock_released_at/by/reason` 컬럼과 부분 유니크 인덱스 조건이 이미 준비돼 있다
(`tests/integration/test_lead_diagnosis_constraints.py::TestDualLock::test_released_lock_frees_both_keys`가
동작을 고정하고 있으니 API만 얹으면 된다).

### 2-2. 배포 전 필수

| 항목 | 상태 |
|---|---|
| 마이그레이션 `0035` 프로덕션 적용 | **미적용** |
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

## 4. 구현하며 발견한 결함 (전부 수정됨)

1. **`+82 02-123-4567`이 다른 잠금 키가 됐다** — 표기만 바꾼 재신청이 통했다
2. **진료과 == 키워드면 질의가 2개만 생성** — 측정 수가 흔들리면 원가도 흔들린다
3. **잠금 위반이 500으로 샜다** — `commit()`이 아니라 `flush()`에서 터진다
4. **CDN이 개인 리포트를 캐시할 수 있었다** — `PublicApiCacheMiddleware`가
   `/api/v1/public/*`에 `public, max-age=300`을 붙이고 있었다
5. **큐만 나눠서는 F6-1이 안 지켜졌다** — `sov_engine` 세마포어가 전역이라
   무료 진단이 몰리면 유료 측정이 굶는다
6. **PRD §6 원가표가 `gpt-5-mini` 기준으로 낡아 있었다** — 실제 원가 8% 과소

---

## 5. 검증 명령

```bash
cd backend && .venv/bin/python -m ruff check app tests && .venv/bin/python -m pytest -q   # 1007
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

## 6. 새 코드 지도

**백엔드**

| 파일 | 역할 |
|---|---|
| `models/lead_diagnosis.py` | 6개 테이블 + 상태 3축 enum |
| `alembic/versions/0035_add_lead_diagnosis.py` | 스키마 (부분 유니크 인덱스는 raw DDL) |
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

## 7. 테스트를 쓸 때 지킨 규칙

핸드오프(2026-07-29)의 원칙을 이어받았다 — **자기참조를 피하고 독립적으로 정해지는
값 사이의 제약을 건다.** 이번에 그 원칙으로 바꾼 기존 테스트 2개:

- `test_celery_routing.py` — 큐 목록을 하드코딩하다가 **`docker-entrypoint.sh`에서
  파싱**하도록. 하드코딩이면 두 곳을 같이 고치는 한 통과해 드리프트를 못 잡는다.
- `test_sov_timing_budget.py` — 소스 텍스트 grep에서 **실제 세마포어 용량 검사**로.

새로 건 관계 제약의 예: 메일 재시도 일정 합 < Resend 멱등성 창(24시간).
상수 하나만 늘려도 실패한다.
