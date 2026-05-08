# Re:putation 1.0 — 출시 준비 노트 (Readiness Note)

**작성일:** 2026-05-08
**브랜치:** `codex/v1-remediation-p0` → `main`
**범위:** P0 안정화 + 진짜 P0 (Essence/llms.txt) + Sales Leads 도메인을 1.0 MVP에 포함

---

## 1. 결정 (Decisions)

| 항목 | 선택 | 사유 |
|---|---|---|
| **MVP 범위** | 현재 브랜치 + 진짜 P0까지 | Sales Leads/Operations/Audit + Essence/llms.txt P0를 함께 출시. 영업 깔때기 1단 + 컴플라이언스 둘 다 필요. |
| **머지 전략** | main 직접 머지 + push | AE 1명, 단일 운영자. 보호 브랜치 없음. |
| **운영 모델** | AE 1명 + 단일 Admin Key | NextAuth 도입은 1.0 후속. actor는 ENV `ADMIN_ACTOR_NAME`로 단일화. |

---

## 2. 머지된 변경 요약

### Phase 1 — P0 보안 컴플라이언스 (4건)
1. **Public Lead 폼 rate-limit 적용** — `@limiter.limit("5/minute;30/hour;100/day")` 데코레이터 + 허니팟 hidden field. 봇 1대로 DB·Slack 도배 시나리오 차단.
2. **Audit actor 위조 방지** — `X-Admin-Actor` 헤더 신뢰 모델 폐기, ENV `ADMIN_ACTOR_NAME`만 신뢰. (`backend/app/services/audit_log.py`, `backend/app/api/admin/operations.py`)
3. **Slack PII 마스킹** — `notifier.mask_contact()` 도입. lead 알림에서 전화/이메일 마스킹. 환자 질문 본문은 Slack 송출 제거 → Admin UI deep-link만.
4. **Audit transaction 순서 수정** — `apply_async() → write_audit_log() → commit()` 안티패턴을 `write_audit_log() → commit() → apply_async()`로 뒤집음. 큐는 audit row가 durable해진 후에만 enqueue됨.

### Phase 2 — 진짜 P0 (Essence Impact Review가 가리킨 항목)
5. **llms.txt에서 raw `director_philosophy` 제거** — 검수되지 않은 자유 입력이 AI 크롤러에 노출되던 표면 차단. 백엔드 `/public/site`도 이미 None 반환으로 일치.
6. **Publish/Reject content audit log** — 콘텐츠 발행·반려는 의료광고 책임 흐름. 누가/언제/무엇을 발행했는지 audit_log에 기록.
7. **Approve philosophy audit log** — `confirm_evidence_reviewed=True` 강제 + audit row. "근거를 검토했다"는 약속이 추적 가능해짐.
8. **Update exposure action audit log** — status/owner/due_month/linked_content_id 변경에 before/after diff 기록.

### Phase 3 — 개인정보보호법 컴플라이언스 (제15조 / 제21조)
9. **`sales_leads` 보관기간 컬럼 추가** — `consent_version`, `consent_ip`, `retain_until`, `purged_at`. Migration `0015_add_lead_retention_columns`. 기존 row backfill 포함.
10. **`purge_expired_leads` Celery beat** — 매일 04:00 KST, `retain_until` 도달 lead의 PII를 `[purged]`로 익명화 + `purged_at` 기록. 통계용 메타(clinic_type, source_path)는 유지.
11. **Migration 0012 preflight** — 중복 데이터 사전 검증. partial unique index 생성 전에 `GROUP BY HAVING COUNT(*) > 1`로 충돌 검사. 발견 시 명확한 에러 + cleanup runbook.

### Phase 4 — Frontend 컴플라이언스 + UX
12. **동의 4요소화** — `site/app/page.tsx` 폼에 `<details>` 펼침으로 (수집 목적 / 항목 / 보유기간 / 거부권) 명시. 처리방침 버전 hidden field로 backend 전달.
13. **`/privacy` 처리방침 페이지** — 9개 섹션. 수집 항목·이용 목적·보유기간·동의 거부권·제3자 제공·국외이전(Slack)·안전조치·정보주체 권리·보호책임자.
14. **`/terms` 이용약관 페이지** — 회사가 보장하지 않는 사항(노출 순위/환자 유입/치료 효과)을 의료광고법 안전 지대에 명시. 의료광고 자율심의는 의료기관 본인 책임.
15. **Footer 사업자 정보** — 운영사·대표·주소·사업자등록번호 라인 추가. 처리방침/이용약관/문의 링크.
16. **Admin 대시보드 감사 로그 섹션** — `admin/app/hospitals/[id]/dashboard/page.tsx`에 최근 운영 액션 20건. 만들어 둔 audit-logs API의 첫 호출자.
17. **Honeypot + payload size limit** — `site/app/api/leads/route.ts`에 64KB 본문 한계, 봇 자동 채움 silently 200, x-forwarded-for 백엔드 전달.

### Phase 5 — 테스트
- `backend/tests/test_admin_operations.py` — 새 시그니처 + audit→commit→apply_async 순서 검증 + `ADMIN_ACTOR_NAME` 우선 검증.
- `backend/tests/test_public_leads.py` — retention/consent 컬럼 채워지는지, honeypot silently 200, 형식 검증, mask_contact 단위 테스트.
- `backend/tests/test_exposure_actions.py` — `_MutatingDB`에 `add()` 추가해 audit row 수용.
- 전체 76 테스트 통과.

---

## 3. 변경된 환경 변수

`.env.example` 갱신 권장 항목 (실 .env는 별도 관리):

```
# Admin
ADMIN_ACTOR_NAME=AE                   # audit_log actor — 단일 운영자 이름

# Site (public)
SITE_BASE_URL=https://reputation.co.kr

# Lead
LEAD_RETENTION_DAYS=180               # 자동 파기까지 일수 (개인정보보호법 제21조)
LEAD_CONSENT_VERSION=v1.2026-05       # 처리방침 버전 — 변경 시 재동의 필요
PUBLIC_LEAD_RATE_LIMIT=5/minute;30/hour;100/day
```

---

## 4. 1.0 PRD must-pass 충족 상태 (REPUTATION-1.0-PRD.md L421-434 기준)

| 검증 항목 | 상태 | 참고 |
|---|---|---|
| seed data로 로컬 E2E 통과 | ⏳ | `scripts/test_e2e.sh` 1.0 출시 전 1회 실행 필요. `make demo-seed` → 한 사이클 검증. |
| 공개 site SSR | ✅ | `/site/app/[slug]` 기존대로 작동. `/privacy`, `/terms` 신규 SSR. |
| robots.txt OAI-SearchBot | ✅ | 기존 유지. |
| sitemap | ✅ | 기존 유지. |
| llms.txt | ✅ | director_philosophy 자유 입력 제거. 발행 콘텐츠 + 검수된 정보만 노출. |
| 의료광고 금지표현 필터 | ✅ | publish/patch 양쪽에서 검사. |
| 콘텐츠 자동 생성 사이클 | ✅ | nightly + morning 알림 beat 유지. |
| 월간 리포트 자동화 | ✅ | dedupe 가드 유지. |

⏳ **1.0 출시 전 마지막 게이트:** Docker compose 환경에서 `make test` + `bash scripts/test_e2e.sh` 1회 실행하여 실제 Postgres + Celery 에서 회귀 0건 확인.

---

## 5. 알려진 후속 작업 (1.0 출시 후 다음 사이클)

| 우선순위 | 항목 | 사유 |
|---|---|---|
| P1 | **NextAuth + 다중 actor** | 단일 ENV actor는 첫 10병원까지의 임시 모델. 운영자 2명 이상 시 즉시 도입 필요. |
| P1 | **Audit log append-only DB role** | 현재는 앱 user가 UPDATE/DELETE 가능. PG role 분리 또는 row hash chain으로 변조 차단. |
| P2 | **`Hospital.site_built` / `site_live` rename** | 의미가 변경됐는데 필드명은 legacy 유지. 신규 컨트리뷰터 혼란 요인. |
| P2 | **랜딩 hero 가짜 진단 카드 → 실제 데모 병원 V0** | 신뢰 신호 강화. 시연용 데모 병원 데이터를 SSR로 hero에 노출. |
| P2 | **regenerate_content_item 강제 forbidden 검사** | 현재는 publish 시점에 차단. 생성 직후에도 essence_status 강등 필요. |
| P3 | **AE 횡단 운영 큐 (다음 주 발행 캘린더)** | 병원 20개 운영 시 병원별 진입이 아닌 글로벌 우선순위 뷰 필요. |

---

## 6. 머지 결정 근거 — 4명 리뷰어 합의 → 본 패치 매핑

| 리뷰어 P0 지목 | 본 패치 | 상태 |
|---|---|---|
| Rate-limit 미적용 (보안 V-001 / 코드 #2) | Phase 1.1 | ✅ |
| X-Admin-Actor 위조 (보안 V-002 / 코드 #3) | Phase 1.2 | ✅ |
| 동의 4요소 미준수 (critic 페르소나 3) | Phase 4.12-13 | ✅ |
| Slack PII 평문 (보안 V-004) | Phase 1.3 | ✅ |
| Audit transaction 순서 (코드 #3) | Phase 1.4 | ✅ |
| llms.txt director_philosophy (architect) | Phase 2.5 | ✅ |
| Publish/Reject audit log (코드 #7-8) | Phase 2.6 | ✅ |
| Approve philosophy audit (Impact Review) | Phase 2.7 | ✅ |
| Update exposure action audit (코드 #7) | Phase 2.8 | ✅ |
| 보관기간/파기 (보안 V-005) | Phase 3.9-10 | ✅ |
| Migration 0012 preflight (코드 #5) | Phase 3.11 | ✅ |
| Operations API dead — 진입점 부재 (critic) | Phase 4.16 (대시보드 audit log 섹션) | ✅ |

후속 PR 권장: V-006(open redirect 표면), V-007(CORS 검증), 코드 #6(IntegrityError → 409), V-013(Slack host whitelist).

---

**서명:** v1.0 출시 가능 (단, Docker compose에서 `make test` + e2e 회귀 베이스라인 1회 확인 후 push).
