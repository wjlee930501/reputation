# PR-1A Admin 공통 기반: 3상태·용어 사전·overview API 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin의 모든 화면(목록·헤더·현황)이 같은 세 함수로 병원 상태를 말하고, 한 번의 `overview` 호출로 현황 화면이 채워지며, 용어 사전이 하나가 되게 한다 — Phase 1(PR-1B~1E)이 이 위에서 화면을 갈아끼운다.

**Architecture:** 상태 판정은 백엔드가 계산해 내려주고(`HospitalListItem`·`overview`), admin은 그 값을 **라벨로 바꾸기만** 하는 순수 함수(`admin/lib/hospital-states.ts`)를 쓴다. 현재의 5개 점(`hospital-header-progress.ts`)·`STATUS_LABELS` 기반 표시는 이 PR에서 3상태로 교체되고, 탭·라우트는 건드리지 않는다(PR-1E). 용어는 `admin/lib/admin-copy.ts`가 유일한 사전이며, 새 화면만 검사하는 금지어 가드를 붙인다(옛 페이지는 PR-1E 삭제 시 검사 범위에 들어간다).

**Tech Stack:** FastAPI/SQLAlchemy async (backend), Next.js 16 App Router + node:test (admin), Python stdlib 가드(`scripts/check_user_facing_terms.py`).

**설계 근거:** [설계 §4.1~4.5](2026-09-08-admin-hitl-simplification-design.md), [검토 §4 용어](../reviews/2026-09-08-integrity-hitl-review.md). 코드 사실은 2026-09-08 탐색 결과 기준(`hospital-header-progress.ts:41-50`, `layout.tsx:23-37`, `hospitals.py:1249-1480`, `essence_readiness.py:38-62`, `operations_center_read_routes.py:40-96`).

---

## 환경 준비

PR-0C 계획의 "환경 준비" 명령을 그대로 쓴다(5432 `reputation_test` head `0070`, 5434, Redis). backend 테스트는 그 env로만 실행하고 전체 suite는 Task 6에서만 돈다. admin은 `cd admin && npm test && npm run lint && npm run typecheck`.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/app/services/hospital_states.py` (신규) | 병원 1곳 또는 N곳의 **콘텐츠 준비 상태**를 계산(배치 2~3쿼리). 공개 서비스·도메인 상태는 기존 컬럼에서 파생 |
| `backend/app/schemas/hospital_overview.py` (신규) | `HospitalOverviewResponse`와 하위 모델 |
| `backend/app/api/admin/hospital_overview.py` (신규) | `GET /admin/hospitals/{id}/overview` |
| `backend/app/api/admin/hospitals.py` | `HospitalListItem`에 `content_state`·`open_exception_count`·`ae_owner` 추가 |
| `admin/lib/hospital-states.ts` (신규) | 서버 상태값 → 라벨/톤/남은 조건 문구. `summarizeHeaderProgress` 대체 |
| `admin/lib/admin-copy.ts` | 통일 용어 8개 추가 |
| `scripts/check_user_facing_terms.py` | 새 화면 경로에 대해 금지 변형 검사 추가 |
| `admin/app/hospitals/[id]/layout.tsx`, `admin/app/hospitals/page.tsx` | 5개 점·CheckCell 3종 → 3상태 |
| `admin/types/index.ts` | 새 응답 타입 |

---

### Task 1: 백엔드 3상태 계산 — `hospital_states.py`

**Files:**
- Create: `backend/app/services/hospital_states.py`
- Test: `backend/tests/test_hospital_states.py` (신규, 단위) · `backend/tests/integration/test_hospital_states_postgres.py` (신규, 배치 쿼리 수)

- [ ] **Step 1: 단위 테스트**

```python
"""3상태는 한 곳에서만 계산한다 — 목록·헤더·현황이 다른 답을 내면 사람이 화면을 못 믿는다."""

from types import SimpleNamespace

from app.models.hospital import HospitalStatus
from app.services.hospital_states import (
    ContentState,
    DomainState,
    PublicServiceState,
    content_state,
    domain_state,
    public_service_state,
)


def _hospital(**overrides):
    base = dict(
        status=HospitalStatus.ACTIVE, site_live=True, site_built=True, profile_complete=True,
        schedule_set=True, aeo_domain=None, domain_cert_job_state=None,
        domain_cert_dns_verified_at=None, domain_last_check_ok=None, domain_last_checked_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_public_service_mirrors_the_serving_gate():
    assert public_service_state(_hospital()) == PublicServiceState(kind="live", remaining=())
    assert public_service_state(_hospital(status=HospitalStatus.PAUSED)).kind == "paused"
    not_live = public_service_state(_hospital(status=HospitalStatus.BUILDING, site_live=False, site_built=False, profile_complete=False))
    assert not_live.kind == "not_live"
    assert not_live.remaining == ("profile_complete", "site_built")


def test_content_state_needs_schedule_and_a_current_essence():
    ready = content_state(_hospital(), essence_current=True, unprocessed_sources=0, escalated_draft=False)
    assert ready == ContentState(kind="auto", remaining=())
    waiting = content_state(_hospital(schedule_set=False), essence_current=False, unprocessed_sources=2, escalated_draft=False)
    assert waiting.kind == "preparing"
    assert waiting.remaining == ("schedule", "sources:2", "essence_review")
    assert content_state(_hospital(), essence_current=False, unprocessed_sources=0, escalated_draft=True).kind == "exception"


def test_domain_state_only_when_a_custom_domain_exists():
    assert domain_state(_hospital()).kind == "unused"
    assert domain_state(_hospital(aeo_domain="clinic.example.com", domain_last_check_ok=True)).kind == "connected"
    checking = domain_state(_hospital(aeo_domain="clinic.example.com", domain_cert_job_state="ISSUING"))
    assert checking.kind == "checking"
    failed = domain_state(_hospital(aeo_domain="clinic.example.com", domain_cert_job_state="FAILED"))
    assert failed.kind == "problem" and failed.reason
```

- [ ] **Step 2: 실패 확인** → `ModuleNotFoundError`.

- [ ] **Step 3: 구현**

```python
"""병원 3상태 — 공개 서비스 · 콘텐츠 준비 · 자기 도메인 (설계 §4.2).

admin 목록·헤더·현황이 전부 이 값을 받아 라벨만 붙인다. 판정 규칙을 admin에 두면
화면마다 갈라진다(PR-0A H-06의 재발).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import AUTO_REVIEW_GAP_FIELD, HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import HospitalStatus
from app.services.essence_readiness import get_essence_readiness_states  # Task 1 Step 3b


@dataclass(frozen=True, slots=True)
class PublicServiceState:
    kind: Literal["live", "paused", "not_live"]
    remaining: tuple[str, ...]  # 사람이 채울 조건 키: profile_complete · site_built


@dataclass(frozen=True, slots=True)
class ContentState:
    kind: Literal["auto", "preparing", "exception"]
    remaining: tuple[str, ...]  # schedule · sources:N · essence_review


@dataclass(frozen=True, slots=True)
class DomainState:
    kind: Literal["connected", "checking", "problem", "unused"]
    reason: str | None = None
    last_checked_at: Any = None


def public_service_state(hospital: Any) -> PublicServiceState:
    """`_has_public_site`(hospitals.py)와 같은 게이트. PAUSED는 남은 조건이 아니라 상태다."""
    status = getattr(hospital, "status", None)
    status = getattr(status, "value", status)
    if status == HospitalStatus.PAUSED.value:
        return PublicServiceState("paused", ())
    if status == HospitalStatus.ACTIVE.value and bool(getattr(hospital, "site_live", False)):
        return PublicServiceState("live", ())
    remaining = tuple(
        key for key in ("profile_complete", "site_built") if not bool(getattr(hospital, key, False))
    )
    return PublicServiceState("not_live", remaining)


def content_state(
    hospital: Any, *, essence_current: bool, unprocessed_sources: int, escalated_draft: bool
) -> ContentState:
    """`schedule_set && essence_readiness.current` (설계 §4.2). 예외는 준비 중보다 앞선다."""
    if escalated_draft:
        return ContentState("exception", ())
    remaining: list[str] = []
    if not bool(getattr(hospital, "schedule_set", False)):
        remaining.append("schedule")
    if unprocessed_sources > 0:
        remaining.append(f"sources:{unprocessed_sources}")
    if not essence_current:
        remaining.append("essence_review")
    return ContentState("auto" if not remaining else "preparing", tuple(remaining))


def domain_state(hospital: Any) -> DomainState:
    """자기 도메인이 있을 때만 의미가 있다. 순서는 `readHospitalDomainStatus`(admin)와 같다."""
    if not getattr(hospital, "aeo_domain", None):
        return DomainState("unused")
    job = getattr(hospital, "domain_cert_job_state", None)
    job = getattr(job, "value", job)
    checked_at = getattr(hospital, "domain_last_checked_at", None)
    if job == "FAILED":
        return DomainState("problem", reason="인증서 발급 실패", last_checked_at=checked_at)
    if getattr(hospital, "domain_last_check_ok", None) is True:
        return DomainState("connected", last_checked_at=checked_at)
    if getattr(hospital, "domain_last_check_ok", None) is False:
        return DomainState("problem", reason=getattr(hospital, "domain_last_check_reason", None) or "DNS 확인 실패", last_checked_at=checked_at)
    return DomainState("checking", last_checked_at=checked_at)
```

- [ ] **Step 3b: 배치 콘텐츠 준비 조회** — `backend/app/services/essence_readiness.py`에 추가(단건 `get_essence_readiness`와 같은 규칙, `get_public_approved_philosophy_ids`와 같은 묶음 방식):

```python
@dataclass(frozen=True, slots=True)
class EssenceReadinessState:
    current: bool
    unprocessed_sources: int
    escalated_draft: bool


async def get_essence_readiness_states(
    db: AsyncSession, hospital_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, EssenceReadinessState]:
    """N개 병원의 콘텐츠 준비 상태를 3쿼리로 — 승인 행 · 필수 자료 · 노이즈 제외 집합 · 예외 초안.

    `current`는 엄격한 판정이므로 노이즈 집합을 반드시 읽는다(`include_noise=True` 규칙).
    """
```
구현: (1) APPROVED 행 `IN`, (2) 필수 텍스트 자료 `IN`, (3) 노이즈 제외 해시 계산에 필요한 근거 노트 집합 `IN`(단건 경로가 쓰는 함수를 그대로 병원별 그룹으로 호출), (4) `DRAFT`이면서 `unsupported_gaps`에 `AUTO_REVIEW_GAP_FIELD`가 있는 초안 존재 여부 `IN` — 총 4쿼리, 병원 수 무관. 각 병원은 `_resolve_lightweight_readiness`(단건과 공유) → `EssenceReadiness.current is not None`. 단건 `get_essence_readiness`와의 동치 테스트를 `tests/test_essence_readiness.py`에 추가(신선/stale/미처리/예외 초안 4케이스).

- [ ] **Step 4: 통합 테스트(쿼리 수)** — `tests/integration/test_hospital_states_postgres.py`: 병원 1곳 vs 12곳에 대해 `get_essence_readiness_states` 문장 수가 같음(`before_cursor_execute` 패턴, `tests/integration/test_attention_queue.py` 참고). Run → PASS. `ruff check .` clean.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/hospital_states.py backend/app/services/essence_readiness.py backend/tests/test_hospital_states.py backend/tests/test_essence_readiness.py backend/tests/integration/test_hospital_states_postgres.py
git commit -m "feat: compute the three hospital states in one backend module

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 목록 API가 3상태·예외 수·담당 AE를 내려준다

**Files:**
- Modify: `backend/app/api/admin/hospitals.py` — `list_hospitals`(≈624-635)·`_serialize_list`(≈1589)
- Modify: `backend/app/schemas/hospital.py` `HospitalListItem`
- Test: `backend/tests/integration/test_hospital_list_states_postgres.py` (신규)

- [ ] **Step 1: 테스트** — 병원 3곳(운영 중/일시정지/준비 중 + 예외 초안 1곳 + open 인시던트 2건 1곳)을 시드하고 `GET /admin/hospitals`가 각 행에 `public_service_state`, `content_state`(kind·remaining), `domain_state`, `open_exception_count`, `ae_owner{ id, name }`를 내려주며, 50행에서도 문장 수가 상수인지 확인(`_ATTENTION_STATEMENT_BUDGET` 방식).

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `list_hospitals`에서 페이지의 병원 id로 (a) `get_essence_readiness_states`, (b) open 예외 수: `Incident.hospital_id IN … AND state IN (OPEN, RETRYING, ACKNOWLEDGED)` 그룹 카운트 + 예외 초안(위 (a)의 `escalated_draft`)를 더함, (c) `HospitalHandoff.ae_owner_id IN …` → `AdminUser` 이름. `_serialize_list`에 세 상태를 `{"kind": ..., "remaining": [...]}`/`{"kind","reason","last_checked_at"}`로 직렬화. `HospitalListItem`에 필드 추가(`Optional` 아님).

- [ ] **Step 4: 통과 확인** — 위 테스트 + `tests/test_admin_hospitals*.py`. **Step 5: 커밋** `feat: hospital list carries the three states, open exceptions and the AE owner`.

---

### Task 3: `GET /admin/hospitals/{id}/overview`

**Files:**
- Create: `backend/app/schemas/hospital_overview.py`, `backend/app/api/admin/hospital_overview.py`
- Modify: `backend/app/main.py` 라우터 등록(다른 admin 라우터와 같은 `Depends` 묶음)
- Test: `backend/tests/integration/test_hospital_overview_postgres.py`

- [ ] **Step 1: 응답 모델**

```python
class RemainingCondition(BaseModel):
    key: str            # profile_complete · site_built · schedule · sources · essence_review
    label: str          # 사람이 읽는 문구
    actor: Literal["human", "system"]
    href: str | None    # 사람 몫일 때만: /hospitals/{id}/info 등

class StateCard(BaseModel):
    kind: str
    label: str
    remaining: list[RemainingCondition]
    reason: str | None = None
    last_checked_at: datetime | None = None

class ExceptionCard(BaseModel):
    kind: Literal["incident", "escalated_draft"]
    id: str
    title: str          # 사유
    evidence: str | None
    next_action: str
    allowed_actions: list[str]   # 서버가 허용한 행동 코드만 (incident: OperationsQueueRow.action 기준)
    href: str

class MonthSummary(BaseModel):
    year: int; month: int
    published_count: int; public_count: int; withheld_count: int; planned_total: int
    mention_rate: float | None      # 최신 주간 측정, 없으면 None
    next_report_date: date

class HospitalOverviewResponse(BaseModel):
    hospital_id: uuid.UUID
    public_service: StateCard
    content: StateCard
    domain: StateCard
    exceptions: list[ExceptionCard]
    month: MonthSummary
```

- [ ] **Step 2: 테스트** — 시드: ACTIVE·site_live 병원, 승인 기준 fresh, 일정 있음, 이번 달 PUBLISHED 3건(1건 보류), open 인시던트 1건(허용 행동 있음), 예외 초안 없음 → 응답의 세 카드 kind/라벨/남은 조건, `exceptions` 1건에 `allowed_actions`가 `OperationsQueueRow.action`과 같은 코드, `month.public_count == 2`, `withheld_count == 1`, `planned_total == schedule.plan 편수`, `next_report_date == 다음 달 1일`. 문장 수 상수(≤ 12) 단언.

- [ ] **Step 3: 구현** — 상태: Task 1 함수. 남은 조건 라벨/actor 매핑(한 곳):
  `profile_complete → ("필수 병원 정보 입력", human, /hospitals/{id}/profile)`, `site_built → ("공개 페이지 준비", system, None)`, `schedule → ("발행 요일 설정", human, /hospitals/{id}/schedule)`, `sources:N → ("근거 자료 처리 N건", system, None)`, `essence_review → ("콘텐츠 운영 기준 자동 검수", system, None)`. (PR-1B~1C에서 href만 `/info`·`/content`로 바뀐다.)
  예외: 인시던트는 `operations_center_incident_queries`의 병원 필터 조회를 재사용해 `OperationsQueueRow`로 직렬화한 뒤 `ExceptionCard`로 축약(`allowed_actions = [row.action.kind] if row.action and row.action.enabled else []`); 예외 초안은 `DRAFT` + `AUTO_REVIEW_GAP_FIELD` 초안의 finding 목록을 `evidence`로, `href=/hospitals/{id}/essence`, `allowed_actions=["re_review","approve_with_override"]`.
  이번 달: `ContentItem` 이번 달 집계(+ `assess_sampled_visibility`로 보류 수 — 표본이 아니라 이번 달 발행분이므로 `visibility_load_only`), `planned_total`은 활성 일정의 plan 편수(`models/content.py` 배분표), `mention_rate`는 `sov.py:get_sov_trend`가 쓰는 함수를 서비스로 추출해 마지막 점만, `next_report_date`는 다음 달 1일.

- [ ] **Step 4: 통과 확인** — 위 테스트 + `tests/test_production_readiness.py`(라우터 수 고정이면 갱신). **Step 5: 커밋** `feat: one overview call for the hospital status screen`.

---

### Task 4: 용어 사전과 가드

**Files:**
- Modify: `admin/lib/admin-copy.ts`, `admin/lib/admin-copy.test.ts`
- Modify: `scripts/check_user_facing_terms.py`
- Modify: `docs/ops/admin-korean-language-guide.md`

- [ ] **Step 1: 테스트** — `admin-copy.test.ts`에 8개 키 값 고정: `publicPage:'병원 공개 페이지'`, `publicUrl:'공개 주소'`, `operatingStandard:'콘텐츠 운영 기준'`, `initialReport:'초기 진단 보고서'`, `monthlyReport:'보고서'`, `evidence:'근거 자료'`(기존), `evidenceNote:'근거 노트'`(기존), `plan:'요금제'`, `monthlyArticles:'월 발행 편수'`, `operator:'운영자'`, `aeOwner:'담당 AE'`, `postPublishReview:'공개 후 확인'`, `postPublishReviewed:'확인 완료'`. `scripts/test_copy_contracts.py`에 새 테스트: `check_user_facing_terms.py`의 `NEW_SURFACE_PATHS`에 든 파일에 금지 변형(`공개 표면`, `정보 허브`, `콘텐츠 허브`, `병원 정보 허브`, `리포트`, `스케줄`, `slug`, `월간 운영량`, `월간 발행량`, `사후검수`, `후행 확인`, `초기 진단 리포트`, `AI 진단 분석 중`, `자료 모음`)이 없어야 한다 — `NEW_SURFACE_PATHS = ["admin/app/hospitals/page.tsx", "admin/app/hospitals/[id]/layout.tsx", "admin/lib/hospital-states.ts", "admin/lib/admin-copy.ts"]`로 시작(PR-1B~1E가 화면을 갈아끼울 때마다 경로를 추가하고, PR-1E에서 전역으로 바꾼다).

- [ ] **Step 2: 실패 확인** (사전 키 없음 / layout.tsx의 `콘텐츠 허브 준비` 등이 걸림 → Task 5에서 해소되므로 이 Task에서는 가드를 **추가만** 하고 `NEW_SURFACE_PATHS`는 Task 5 커밋에서 채운다).

- [ ] **Step 3: 구현** — `ADMIN_COPY` 확장; `check_user_facing_terms.py`에 `NEW_SURFACE_BANNED_PATTERNS`·`NEW_SURFACE_PATHS`와 두 번째 스캔 루프(기존 `BANNED_PATTERNS`는 그대로); 언어 가이드에 §4 통일안 8행을 표로 추가하고 "새 화면은 `ADMIN_COPY` 키만 쓴다" 규칙 명시.

- [ ] **Step 4: 통과** — `make copy-guard`, admin `npm test`. **Step 5: 커밋** `feat: one admin terminology dictionary with a guard for the new surfaces`.

---

### Task 5: 헤더·목록이 3상태를 쓴다 (5개 점·CheckCell 교체)

**Files:**
- Create: `admin/lib/hospital-states.ts`, `admin/lib/hospital-states.test.ts`
- Modify: `admin/types/index.ts` (`HospitalListItem`/`Hospital`에 세 상태 타입, `HospitalOverview`)
- Modify: `admin/app/hospitals/[id]/layout.tsx` (≈177-183 모바일 팝오버, ≈286-296 데스크톱 점, ≈244-275 공개 주소 줄)
- Modify: `admin/app/hospitals/page.tsx` (≈256-263 컬럼, ≈332-348 CheckCell 3종)
- Delete: `admin/lib/hospital-header-progress.ts`, `admin/lib/hospital-header-progress.test.ts`
- Modify: `admin/lib/operations-journey.test.ts`(L115-130 layout 단언), `admin/lib/fable5-ui-contract.test.ts`(L14-25), `admin/lib/admin-ux-r1.test.ts`(L25-28)
- Modify: `scripts/check_user_facing_terms.py` `NEW_SURFACE_PATHS` 채움

- [ ] **Step 1: 테스트** — `hospital-states.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { describeContentState, describeDomainState, describePublicService, stateTone } from './hospital-states'

test('public service labels follow the server kind and list only human work', () => {
  assert.deepEqual(describePublicService({ kind: 'live', remaining: [] }), { label: '공개 중', detail: null })
  assert.deepEqual(describePublicService({ kind: 'paused', remaining: [] }), { label: '일시 정지', detail: null })
  assert.deepEqual(
    describePublicService({ kind: 'not_live', remaining: [{ key: 'profile_complete', label: '필수 병원 정보 입력', actor: 'human', href: '/hospitals/h1/profile' }, { key: 'site_built', label: '공개 페이지 준비', actor: 'system', href: null }] }),
    { label: '준비 중', detail: '할 일: 필수 병원 정보 입력 · 시스템 처리 중: 공개 페이지 준비' },
  )
})

test('content state', () => {
  assert.equal(describeContentState({ kind: 'auto', remaining: [] }).label, '자동 발행 중')
  assert.equal(describeContentState({ kind: 'exception', remaining: [] }).label, '예외 있음')
  assert.equal(describeContentState({ kind: 'preparing', remaining: [{ key: 'sources', label: '근거 자료 처리 2건', actor: 'system', href: null }] }).detail, '시스템 처리 중: 근거 자료 처리 2건')
})

test('domain state is silent without a custom domain', () => {
  assert.equal(describeDomainState({ kind: 'unused' }), null)
  assert.equal(describeDomainState({ kind: 'problem', reason: '인증서 발급 실패' })?.label, '문제: 인증서 발급 실패')
  assert.equal(stateTone('exception'), 'warn')
})
```
목록 페이지 소스 계약(기존 `operations-journey.test.ts` 방식): `hospitals/page.tsx`에 `summarizeHeaderProgress`·`CheckCell(h.profile_complete)`·`isPubliclyServing(h)`·`h.schedule_set` 셀이 없고 `describePublicService`·`describeContentState`·`open_exception_count`·`ae_owner`가 있다. layout 소스 계약: `summarizeHeaderProgress`·`ProgressDot` 없음, `describePublicService`·`describeContentState`·`describeDomainState` 있음, 5개 점 라벨 문자열 없음.

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현** — `hospital-states.ts`: 위 세 `describe*`와 `stateTone(kind) → 'good'|'neutral'|'warn'|'paused'`; `remaining`을 actor별로 나눠 "할 일: … · 시스템 처리 중: …" 문구(사람 몫에만 링크는 컴포넌트가 `href`로 붙임). layout: 헤더 오른쪽을 3개 카드(라벨+detail, 사람 몫 링크)로, 모바일 팝오버도 같은 3개; 공개 주소 줄은 유지하되 `domainHeaderStatus` 자유 문자열 대신 `describeDomainState`(없으면 `공개 주소 {detail}`만). 목록: 컬럼을 `병원(+담당 AE) / 공개 서비스 / 콘텐츠 준비 / 자기 도메인 / 예외 / 요금제`로, `열기 →`는 그대로 `/dashboard`(PR-1D에서 `/hospitals/{id}`). 옛 테스트 3파일의 단언을 새 계약으로 바꾸고 `hospital-header-progress.*` 삭제. `NEW_SURFACE_PATHS`에 4개 경로를 넣는다.

- [ ] **Step 4: 통과** — admin `npm test/lint/typecheck`, `make copy-guard`. **Step 5: 커밋** `feat: header and hospital list speak the three states`.

---

### Task 6: 전체 검증·기록

- [ ] 깨끗한 5432 DB 재생성 → backend 전체 → admin/site → copy-guard → db-budget-guard. 결과와 커밋을 이 문서 "실행 결과" 표에 기록하고 등록부 처리 현황을 갱신.

---

## 자기 점검

- 설계 §4.2 3상태 ↔ Task 1/5; §4.5 overview ↔ Task 3; §4.4 사전·가드 ↔ Task 4; 목록 요구(공개 서비스·콘텐츠 준비·예외 수·담당 AE) ↔ Task 2/5.
- 이 PR은 탭·라우트·온보딩 10단계·readiness checks를 **삭제하지 않는다**(PR-1B~1E). 옛 페이지가 계속 `STATUS_LABELS`를 쓰는 것은 허용되며, 가드는 새 화면 4경로만 본다.
- 타입 일관성: 백엔드 `kind` 리터럴 = admin 유니온(`'live'|'paused'|'not_live'`, `'auto'|'preparing'|'exception'`, `'connected'|'checking'|'problem'|'unused'`); `remaining[].actor` = `'human'|'system'`.
