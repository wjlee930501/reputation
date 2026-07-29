# 핸드오프 — 리드마그넷 퍼널 설계 착수 (2026-07-29)

> **다음 세션은 이 문서만 읽고 바로 시작할 수 있어야 한다.**
> 할 일은 **설계 7건**이다. 구현이 아니다. 설계가 끝나야 PRD가 "구현 착수 가능"으로 바뀐다.

---

## 0. 30초 요약

원장이 자기 병원 정보를 넣으면 → AI 답변에서 언급되는지 측정하고 → 일부 가린 리포트를 메일로 받고 →
관심 있으면 문의로 들어오는 **리드마그넷 퍼널**을 만들려 한다.

- **명세**: `docs/prd/REPUTATION-AI-DIAGNOSIS-FUNNEL-PRD-2026-07.md` (rev4, 2026-07-29)
- **현재 구현**: 랜딩 + 문의 폼 + 리드 저장/알림까지. **측정·리포트·메일은 전혀 없다.**
- **막고 있는 것**: PRD §12의 미해결 **7건**. 전부 엔지니어링 설계다.
- **이번 세션 목표**: 7건에 대한 설계를 써서 PRD 상태를 "구현 착수 불가" → "착수 가능"으로 바꾸는 것.

---

## 1. 지금 있는 것 / 없는 것

### 있다

| 파일 | 내용 |
|---|---|
| `site/app/page.tsx` (253줄) · `site/lib/landing-copy.ts` | 공개 랜딩 + 문의 폼 |
| `site/app/api/leads/route.ts` · `site/lib/leads-proxy.ts` · `site/lib/lead-safety.ts` | 폼 → 백엔드 프록시, 스팸·PII 방어 |
| `backend/app/api/public/leads.py` | 접수 API (honeypot, 레이트리밋, 환자 민감정보 거부) |
| `backend/app/models/lead.py` `SalesLead` | 저장 엔터티 |
| `backend/app/services/lead_privacy.py` | 180일 자동 파기, 동의 기록 |
| `admin/app/leads/page.tsx` · `backend/app/api/admin/leads.py` | 리드 목록 + 병원 전환 |

**PII 보호는 이미 견고하다.** 설계할 때 이 부분은 재발명하지 말고 재사용할 것.

### 없다 (PRD 요구사항 중)

- **F1** 공개 진단 신청 페이지 — 정식 병원명·지역·대표번호를 받지 않는다. 지금 폼은 4필드 문의다
- **F2** 질의 매핑 엔진
- **F5** 블러 리포트 생성·전달 (메일 발송 경로 자체가 없다)
- **F6** 큐·비용 가드
- **§4-1** 리드 진단 엔터티 — `lead_diagnosis*` 테이블이 없다. `SalesLead`에 얹으면 안 된다(PRD가 분리를 명시)

### 현재 랜딩의 정직성 (2026-07-29 조정 완료)

CTA가 "무료 AI 노출 진단"이라 자동 진단을 약속하는 것처럼 읽혔다. **"무료 AI 노출 진단 신청"**으로 바꾸고,
폼 옆에 "자동 발송이 아니라 사람이 확인해 회신한다"는 고지를 붙였다.
**회신 소요 시간(SLA)은 일부러 비워뒀다** — 대표 결정 사항이라 임의로 넣지 않았다. 정해지면 `site/app/page.tsx`
`lead-note` 문단과 접수 성공 메시지에 넣으면 된다.

---

## 2. 이번 세션에 설계할 7건

PRD §12 표가 정본이다. 아래는 착수용 요약이며, **반드시 PRD 원문을 함께 읽을 것.**

| # | 설계할 것 | 핵심 난점 |
|---|---|---|
| **12-3** | 이메일 확인 상태·토큰 | `PENDING_VERIFICATION` 상태, 확인 토큰(리포트 토큰과 **별개**), 만료·재발송, 메일 스캐너가 링크를 미리 눌러버리는 오탐 |
| **12-4** | 상태 모델 분리 | 단일 `status`가 실행·리포트·전달·토큰을 섞는다. `PARTIAL` 결과로 리포트를 만들면 `REPORT_READY`가 되며 부분 실패 정보가 사라진다. `execution_status`/`report_status`/`delivery_status`로 쪼개고 **전이표·재시도·DLQ**까지 |
| **12-5** | 멱등성 키의 실체 | "정규화된 (이메일, 병원명, 기간 버킷) unique"라고만 적혀 있고 그 컬럼이 어느 테이블에도 없다. input fingerprint 컬럼 + task idempotency key를 스키마에 |
| **12-6** | dual-write 복구 | DB 커밋 후 브로커 publish 실패 → 영구 `QUEUED`. 메일 발송 성공 후 delivery 커밋 실패 → 중복 발송. **transactional outbox + reconciler** |
| **12-7** | 리포트 artifact 모델 | F5-3이 "서버에서 제거 가능한 별도 artifact"를 요구하는데 storage key·hash·version·생성/삭제 시각 테이블이 없다 |
| **12-9** | 성공 판정 계측 | **대표 지시로 보류.** 전환율은 지금 다루지 않는다. 건드리지 말 것 |
| **12-10** | SLA 기점과 적체 정책 | 신청 시점부터 재면 사용자의 메일 확인 지연으로 실패한다. 기점을 확인 완료로 옮기고, `DEFERRED` 무제한 제외를 막을 최대 queue age·oldest-first drain·만료 통지 |

**즉 실제로 설계할 것은 6건**(12-9 제외)이다.

### 권장 순서

```
12-4 (상태 모델)  ──┬─→ 12-3 (확인 토큰)   ← 상태 정의가 선행되어야 토큰 상태를 못 섞는다
                    ├─→ 12-10 (SLA 기점)   ← 어느 상태를 기점으로 삼을지가 12-4에 달렸다
                    └─→ 12-7 (artifact)    ← report_status와 짝을 이룬다
12-5 (멱등성) ──→ 12-6 (outbox)            ← 멱등성 키가 있어야 재처리가 안전하다
```

12-4가 나머지 대부분의 전제다. **여기부터 시작할 것.**

---

## 3. 설계 전 반드시 알아야 할 것 — 오늘 바뀐 전제

PRD rev4에 반영돼 있지만, 놓치면 잘못 설계한다.

**측정 모델이 바뀌었다.** `gpt-5-mini-2025-08-07` → **`gpt-5.6-luna`**.
실측 p50 62.8s → 24.7s (2.5배 빠름). 리드 진단의 큐 설계·SLA 산정은 **luna 기준 p50 25s / p90 35s**로 잡을 것.
Gemini는 `gemini-3.6-flash`, p50 7.9s.

**타임아웃·동시성이 바뀌었다.** OpenAI 30→120s, Gemini 30→60s, 동시성 5→10
(`sov_engine.py`의 `OPENAI_TIMEOUT_SECONDS`/`GEMINI_TIMEOUT_SECONDS`/`SOV_PROVIDER_CONCURRENCY`).
큐 처리량 계산 시 이 값을 쓸 것. **테스트가 이 값들과 물량·태스크 한도의 관계를 고정하고 있다**
(`backend/tests/test_sov_timing_budget.py`) — 물량을 늘리면 그 테스트가 먼저 깨진다. 그게 의도다.

**언급률 분모가 바뀌었다.** 지역 없는 의학 설명 질문(INFO)은 분모에서 빠진다(`QueryMatrix.query_intent`).
리드 진단이 보여줄 숫자도 **LOCAL 질문 기준**이어야 유료 리포트와 어긋나지 않는다(PRD §7-4).

**무료 진단이 파는 숫자는 기술통계다** (구 12-8, rev4에서 확정).
"이 15개 질문에 5번씩 물어 75번 중 N번" 형태. **모집단 추정·신뢰구간을 쓰지 않는다.**
F3-6 부트스트랩은 무료 진단 범위에서 제외됐다.

**모델 고정 규칙이 바뀌었다.** "날짜 접미사 필수"가 아니라 "공급자가 재해석하는 ID만 거부"
(`-latest` 계열 + 버전 없는 맨 계열명). `deploy.sh:require_pinned_measurement_models`.

---

## 4. 설계에 쓸 기존 자산

재발명하지 말 것.

| 필요한 것 | 이미 있는 것 |
|---|---|
| 측정 실행 | `sov_engine.run_single_query()` — 반복·실패 라벨링·source_urls 수집 포함 |
| 언급 판정 | `sov_engine._parse_mention()` — 단 **PRD F3-7 미구현**: 지역을 안 받고 `AMBIGUOUS` 3값이 없다. 리드 진단은 동명 의원이 흔해 이게 선행 조건일 수 있다 |
| 질의 생성 | `sov_engine.generate_query_matrix_specs()` — (텍스트, 유형) 반환 |
| 비용 가드 | `services/cost_guard.py` + `tasks.sov_budget_units()` |
| PII·동의·파기 | `services/lead_privacy.py`, `SalesLead.retain_until/purged_at/consent_*` |
| 리포트 PDF | `services/report_engine.py` — 블러 리포트는 별도 템플릿이 필요할 것 |
| Slack 알림 | `services/notifier.py` |
| **메일 발송** | **없다.** 12-3·12-5 설계 전에 발송 채널부터 정해야 한다 |

---

## 5. 이번 세션 산출물

1. `docs/plans/` 아래 설계 문서 — 6건 각각에 대해 **스키마·상태 전이표·실패 경로·재시도 정책**
2. PRD §12에서 해결된 항목을 취소선 처리하고 §11에 rev5 기록
3. PRD 헤더 상태를 **"구현 착수 가능"**으로 (6건 전부 닫혔을 때만)

**설계 없이 코드부터 쓰지 말 것.** PRD가 rev3에서 "한 줄 요구만 있고 설계가 없다"는 2차 검토를 받아
착수가 막힌 것이 바로 이 지점이다.

---

## 6. 검증 명령 (코드를 건드릴 경우)

```bash
cd backend && .venv/bin/python -m ruff check app tests && .venv/bin/python -m pytest -q   # 793
cd .. && backend/.venv/bin/python -m pytest scripts/ -q                                   # 46
cd site && npm run typecheck && npm run lint && npm test                                  # 121
cd ../admin && npm run typecheck && npm run lint && npm test                              # 144
backend/.venv/bin/python scripts/check_user_facing_terms.py
bash -n scripts/deploy.sh
```

통합 테스트(로컬 Postgres는 **5434 포트**):

```bash
cd backend && APP_ENV=development \
  DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation" \
  SYNC_DATABASE_URL="postgresql://reputation:reputation@localhost:5434/reputation" \
  .venv/bin/python -m alembic upgrade head
```

**테스트를 새로 쓸 때**: 자기참조를 피할 것. 오늘 `assert config.MODEL == "같은 문자열"` 형태의
테스트가 모델 실재를 전혀 증명하지 못하고 있던 것이 드러났다. **독립적으로 정해지는 값 사이의 제약**을
걸고, 반드시 **뮤테이션으로 탐지되는지 확인**할 것 (`test_sov_timing_budget.py`가 예시).

---

## 7. 미배포 상태 — 다음 배포 전 확인

오늘 커밋은 전부 `main`에 푸시됐으나 **배포되지 않았다.**

- **마이그레이션 `0034_add_query_intent`를 프로덕션에 적용**해야 한다 (백필 포함)
- `.env.production`은 git에 없다. 로컬에서 `OPENAI_MODEL_QUERY=gpt-5.6-luna`로 바꾸고
  `SOV_REPEAT_COUNT` 줄을 지웠으나, **다른 배포 머신에는 반영되지 않았다**
- `SITE_REVALIDATE_SECRET`이 이제 backend 필수다. Secret Manager에 없으면 배포가 막힌다
- **중첩 측정 미수행**: mini → luna 이전 시 구·신 병행을 돌리지 않았다(PRD §2-1-3 위반).
  기존 ACTIVE 병원이 있다면 다음 주기에 1회 병행이 필요하다

## 8. 오늘 커밋

```
42ee1b9  docs(prd): rev4 — 당일 실측 반영, 미해결 9→7건
17a15e5  fix(sov): 언급률 분모 분리 + V0 표본 확대 + 죽은 설정·시크릿 불일치 정리
f8422ef  fix(sov): 측정 타임아웃·모델·플랫폼 비대칭 — 실측 기반 재설정
```

백업 브랜치 `backup/before-rollback-20260729`는 오늘 롤백을 검토하다 만든 것으로,
`80f1ba7` 시점을 가리킨다. 불필요하면 지워도 된다.
