# PR-0A 생애주기·활성화·도메인 무결성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 병원을 ACTIVE로 만드는 코드 경로를 하나로 묶고, 일시정지 우회·캐시 미갱신·"공개 중" 오표시·재개 버튼 실종을 없앤다 (검토 등록부 H-05, H-06, H-07, M-10, M-11, M-13).

**Architecture:** 백엔드는 `services/hospital_activation.py`가 "전환 가능한가"(`ensure_activatable`)와 "무엇을 바꾸는가"(`apply_activation_transition`)를 유일하게 결정하고, 도메인 검증·재개 경로는 그 함수를 호출만 한다. 상태를 바꾼 뒤에는 항상 공개 사이트 캐시를 갱신한다. Admin은 `lib/public-service-state.ts`의 `isPubliclyServing()` 하나로 "지금 공개 중인가"를 판정하고 `site_live`를 직접 읽지 않는다.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), pytest with SimpleNamespace fakes, Next.js 16 + `node:test` (admin). 근거 문서: [설계](2026-09-08-admin-hitl-simplification-design.md) §3 PR-0A, [검토](../reviews/2026-09-08-integrity-hitl-review.md) §2.

---

## 환경 준비 (모든 Task 공통)

브랜치 `claude/integrity-hitl-simplification`에서 작업한다. 백엔드 테스트는 실 PostgreSQL 두 개(5432 `postgres:postgres`, 5434 `reputation:reputation`)와 Redis 6379가 필요하다. 이 세션에서 이미 띄워둔 컨테이너: `reputation-ci-pg5432`, `reputation-db-1`, `reputation-redis-1`. 죽어 있으면:

```bash
cd /Users/woojinlee/Documents/projects/reputation && docker compose up -d db redis && docker start reputation-ci-pg5432
```

백엔드 테스트 실행 명령 (이 블록을 `backend/`에서 그대로 쓴다):

```bash
cd /Users/woojinlee/Documents/projects/reputation/backend && export APP_ENV=test ADMIN_SECRET_KEY=test-admin-key DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/reputation_test" SYNC_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/reputation_test" TASK22_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/reputation_test" INCIDENT_TEST_DATABASE_URL="postgresql+asyncpg://reputation:reputation@localhost:5434/reputation_test" OPERATIONS_TEST_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_test" MIGRATION_UPGRADE_DATABASE_URL="postgresql+psycopg2://reputation:reputation@localhost:5434/reputation_autonomy_migration" COST_GUARD_REDIS_URL="redis://localhost:6379/2" INTEGRATION_REDIS_URL="redis://localhost:6379/3" REQUIRE_PDF_RENDER=1 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib && .venv/bin/python -m pytest -q -p no:cacheprovider
```

단일 파일만 돌릴 때는 위 명령 끝에 `tests/파일명.py -v`를 붙인다. Admin 테스트는 `cd admin && npm test`, lint `npm run lint`, 타입 `npm run typecheck`.

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `backend/app/services/hospital_activation.py` | ACTIVE 전환 규칙의 유일한 소유자 | `ensure_activatable`, `apply_activation_transition` 공개 |
| `backend/app/api/admin/domain_verification.py` | 자기 도메인 DNS 확인 → 활성화 | 직접 상태 기록 제거, 가드 호출, 커밋 후 revalidate |
| `backend/app/api/admin/hospitals.py` | pause/resume/profile PATCH | 커밋 후 revalidate, 재개 시 live 증거 기록, 운영 중 `profile_complete` 해제 409 |
| `backend/app/api/admin/domain_setup.py` | 도메인 설정 체크리스트 | 인증서 단계가 배지와 같은 규칙 |
| `admin/lib/public-service-state.ts` (신규) | "지금 공개 중인가" 단일 판정 | 신규 |
| `admin/lib/hospital-header-progress.ts`, `hospital-activation.ts`, `domain-live-links.ts`, `hospital-domain-status.ts` | `site_live` 직접 읽던 lib | `isPubliclyServing` 사용 |
| `admin/app/hospitals/page.tsx`, `admin/app/hospitals/[id]/layout.tsx` | `site_live` 직접 읽던 화면, 재개 버튼 조건 | `isPubliclyServing` 사용, `schedule_set` 조건 제거 |
| 테스트 | 각 결함의 회귀 테스트 | `tests/test_admin_domain.py`, `tests/test_admin_hospital_lifecycle.py`, `tests/test_admin_hospitals_profile.py`, `tests/test_admin_domain_setup.py`, `admin/lib/public-service-state.test.ts`, 기존 admin lib 테스트 갱신 |

---

### Task 1: 도메인 검증이 PAUSED 병원을 되살리지 못하게 하고, 활성화 후 캐시를 갱신한다 (H-05)

> **실행 기록 (2026-09-08):** 구현 `db9875f` → Codex 품질 검토 REQUEST_CHANGES → 후속 커밋에서 공개 API를 `ensure_activatable()` + **`transition_to_active()`**(가드+전환 단일 진입점)로 정리하고 원시 변경 `_apply_activation_transition`은 private으로 되돌렸다. revalidate는 인증서 작업 dispatch(및 그 커밋) **뒤**에 `finally`로 실행한다. 아래 Step 3~4의 `apply_activation_transition` 호출은 `transition_to_active`로 읽는다.

**Files:**
- Modify: `backend/app/services/hospital_activation.py:165-175` (`_apply_transition`)
- Modify: `backend/app/api/admin/domain_verification.py:36-40` (imports), `:107-127` (가드·전환), `:157` (커밋 후)
- Test: `backend/tests/test_admin_domain.py`

- [ ] **Step 1: 회귀 테스트 작성 — PAUSED 병원은 DNS가 맞아도 409, 상태 불변, 커밋 없음**

`backend/tests/test_admin_domain.py` 끝에 추가. `_hospital()`·`_patch_dns()`·`FakeDB`는 파일 상단에 이미 있다. `_hospital()`의 base dict에 `treatments`가 없으면 `treatments=[]`를 base에 추가한다(revalidate 호출이 `hospital.treatments`를 읽는다).

```python
from app.api.admin import domain_verification as domain_verification_module


@pytest.fixture(autouse=True)
def _record_site_revalidate(monkeypatch):
    """모든 verify 테스트에서 공개 사이트 캐시 갱신 호출을 기록만 하고 네트워크는 타지 않는다."""
    calls: list[tuple[str, str | None]] = []

    async def _fake(slug, treatments=None, *, hospital_name=None):
        calls.append((slug, hospital_name))
        return True

    monkeypatch.setattr(domain_verification_module, "trigger_hospital_site_revalidate_safe", _fake)
    return calls


async def test_verify_domain_does_not_reactivate_a_paused_hospital(monkeypatch, _record_site_revalidate):
    """H-05: PENDING_DOMAIN에서 일시정지된 병원(site_live=False)은 DNS 확인으로 되살아나면 안 된다."""
    hospital = _hospital(status=HospitalStatus.PAUSED, site_live=False)
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "STATUS_NOT_ACTIVATABLE"
    assert hospital.status == HospitalStatus.PAUSED
    assert hospital.site_live is False
    assert db.committed is False
    assert _record_site_revalidate == []


async def test_verify_domain_revalidates_public_site_once_after_activation(monkeypatch, _record_site_revalidate):
    """활성화는 커밋 뒤 공개 사이트 캐시를 정확히 한 번 갱신한다."""
    hospital = _hospital()
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    await domain_api.verify_domain(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert _record_site_revalidate == [(hospital.slug, hospital.name)]


async def test_verify_domain_does_not_revalidate_when_already_live(monkeypatch, _record_site_revalidate):
    """이미 공개 중인 병원의 재확인은 상태 전환이 없으므로 캐시도 건드리지 않는다."""
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True)
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    await domain_api.verify_domain(hospital.id, db=db)

    assert _record_site_revalidate == []
```

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_admin_domain.py -v -k "paused or revalidate"`
Expected: `test_verify_domain_does_not_reactivate_a_paused_hospital` FAIL — `DID NOT RAISE` (현재 코드는 PAUSED를 ACTIVE로 바꾼다). 나머지 두 개는 `AttributeError: module ... has no attribute 'trigger_hospital_site_revalidate_safe'`로 FAIL.

- [ ] **Step 3: `hospital_activation.py`에 가드와 전환 함수를 공개**

`_apply_transition`을 아래로 교체하고, 같은 파일 안의 두 호출(`activate_hospital`, `activate_hospital_sync`)을 `apply_activation_transition(hospital)`로 바꾼다. `_apply_transition`이라는 이름은 남기지 않는다.

```python
def ensure_activatable(hospital: Hospital) -> None:
    """ACTIVE로 바꿔도 되는 상태인지 확인한다. 아니면 `HospitalNotActivatable`.

    이미 ACTIVE면 통과(멱등). PAUSED 등 자동 전환 대상이 아닌 상태는 막는다 —
    도메인 검증·운영센터·워커가 각자 status를 쓰면 일시정지가 우회된다.
    """
    if hospital.status is HospitalStatus.ACTIVE:
        return
    if hospital.status not in AUTO_ACTIVATABLE_STATUSES:
        raise HospitalNotActivatable(hospital.status)


def apply_activation_transition(hospital: Hospital) -> str:
    """행을 ACTIVE·site_live로 바꾸고 직전 상태 문자열을 돌려준다.

    `status = ACTIVE` / `site_live = True`를 쓰는 곳은 이 함수와 `resume_hospital`뿐이어야 한다.
    """
    previous_status = (
        hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
    )
    hospital.status = HospitalStatus.ACTIVE
    hospital.site_live = True
    return previous_status
```

- [ ] **Step 4: `domain_verification.py`가 가드·전환·revalidate를 쓰게 한다**

import 블록에 추가:

```python
from app.services.hospital_activation import (
    HospitalNotActivatable,
    apply_activation_transition,
    ensure_activatable,
)
from app.services.site_revalidate import trigger_hospital_site_revalidate_safe
```

`HospitalStatus` import는 더 이상 쓰지 않으면 제거한다(ruff F401).

게이트 검사 직후(`if not gate["ready"]: raise ...` 다음)에 가드를 넣는다:

```python
    gate = await dependencies.evaluate_gate(db, hospital)
    if not gate["ready"]:
        raise HTTPException(status_code=409, detail=activation_gate_error(gate))
    if not hospital.site_live:
        try:
            ensure_activatable(hospital)
        except HospitalNotActivatable as exc:
            raise HTTPException(status_code=409, detail=exc.as_detail()) from exc
```

직접 기록 블록을 교체한다:

```python
    previous_site_live = bool(hospital.site_live)
    if not hospital.site_live:
        previous_status = apply_activation_transition(hospital)
        await open_service_interval(db, hospital.id, ServiceIntervalProvenance.ACTIVATION)
    else:
        previous_status = (
            hospital.status.value if hasattr(hospital.status, "value") else str(hospital.status)
        )
```

(기존의 `previous_status = (...)` 한 줄은 위 블록으로 흡수한다.)

`await db.commit()` 바로 다음에:

```python
    await db.commit()
    if not previous_site_live:
        # 커밋 이후이므로 실패해도 raise하지 않는다 — 활성화는 이미 성공했다.
        await trigger_hospital_site_revalidate_safe(
            hospital.slug, hospital.treatments, hospital_name=hospital.name
        )
```

- [ ] **Step 5: 통과 확인**

Run: (환경 준비 명령) `tests/test_admin_domain.py tests/test_hospital_lifecycle.py tests/test_admin_hospital_lifecycle.py -v`
Expected: 전부 PASS. 특히 `test_verify_domain_activates_when_all_prerequisites_met`(PENDING_DOMAIN → ACTIVE)이 계속 통과해야 한다.

- [ ] **Step 6: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/services/hospital_activation.py backend/app/api/admin/domain_verification.py backend/tests/test_admin_domain.py && git commit -m "fix: route custom-domain activation through the activation guard

Domain verification wrote status=ACTIVE directly, so a hospital paused from
PENDING_DOMAIN could be resurrected by a DNS check. It now uses
ensure_activatable/apply_activation_transition and revalidates the public
site once after the transition commits. (H-05)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 일시정지·재개가 공개 캐시를 갱신하고, 재개는 자기 도메인 확인 증거를 남긴다 (H-06 후반, M-11)

**Files:**
- Modify: `backend/app/api/admin/hospitals.py` — `pause_hospital` (≈1090-1118), `resume_hospital` (≈1121-1168), import 블록
- Test: `backend/tests/test_admin_hospital_lifecycle.py`

- [ ] **Step 1: 회귀 테스트 작성**

`backend/tests/test_admin_hospital_lifecycle.py`에 추가. `_LifecycleDB`, `_full_hospital`는 파일에 이미 있다. 기존 `test_resume_custom_domain_requires_current_dns`(≈443행)가 `check_domain_dns`를 어떻게 monkeypatch하는지 그대로 따른다 — 그 테스트를 열어 성공 케이스에 쓰는 fake의 이름을 확인하고 아래 `_dns_ok`에 같은 방식을 쓴다.

```python
@pytest.fixture(autouse=True)
def _record_site_revalidate(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    async def _fake(slug, treatments=None, *, hospital_name=None):
        calls.append((slug, hospital_name))
        return True

    monkeypatch.setattr(hospitals_api, "trigger_hospital_site_revalidate_safe", _fake)
    return calls


async def test_pause_revalidates_public_site_after_commit(_record_site_revalidate):
    """H-06: 일시정지 뒤 공개 페이지가 최대 30분 더 보이면 안 된다."""
    hospital = _full_hospital(status=HospitalStatus.ACTIVE, site_live=True)
    db = _LifecycleDB(hospital)

    await hospitals_api.pause_hospital(hospital.id, db=db)

    assert db.committed is True
    assert _record_site_revalidate == [(hospital.slug, hospital.name)]


async def test_resume_revalidates_public_site_after_commit(_record_site_revalidate):
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=True)
    db = _LifecycleDB(hospital)

    await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert _record_site_revalidate == [(hospital.slug, hospital.name)]


async def test_resume_records_live_domain_evidence_for_custom_domain(monkeypatch, _record_site_revalidate):
    """M-11: 재개가 DNS를 확인했으면 배지가 읽는 관측 필드에도 남겨야 A-1이 재발하지 않는다."""
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        site_live=True,
        aeo_domain="clinic.example.com",
        domain_last_checked_at=None,
        domain_last_check_ok=None,
        domain_last_check_reason=None,
    )
    db = _LifecycleDB(hospital)

    async def _dns_ok(domain, strategy):
        return SimpleNamespace(verified=True, cname_value="target.motionlabs.io", address_values=[])

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _dns_ok)

    await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    # DNS-only 관측은 서빙을 증명하지 않으므로 ok는 None이다(domain_live_status.py:79).
    # 배지를 '확인 대기'에서 'DNS 확인 완료'로 옮기는 값은 dns_verified_at이다.
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_cert_dns_verified_at is not None
    assert hospital.domain_last_check_reason == "dns_ok"
    assert hospital.domain_last_checked_at is not None
```

> **실행 기록 (2026-09-08):** 구현 `444d1e5`. 원래 계획의 `domain_last_check_ok is True` assertion은 `apply_live_domain_check`의 계약(HTTPS 테넌트 마커 확인만 ok를 쓴다)과 맞지 않아 구현자가 위와 같이 정정했고 검수에서 수용했다.

`_full_hospital`의 base dict에 `domain_last_checked_at=None, domain_last_check_ok=None, domain_last_check_reason=None`이 없으면 추가한다(`apply_live_domain_check`가 이 속성을 쓴다).

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_admin_hospital_lifecycle.py -v -k "revalidate or live_domain_evidence"`
Expected: 세 개 모두 FAIL (`_record_site_revalidate == []`, `domain_last_check_ok is None`).

- [ ] **Step 3: 구현**

`hospitals.py` import에 추가 (이미 있으면 생략):

```python
from datetime import UTC, datetime

from app.services.domain_live_status import LiveDomainCheck, apply_live_domain_check
```

`pause_hospital`의 끝을 이렇게 바꾼다:

```python
    await db.commit()
    await db.refresh(h)
    # 커밋 이후이므로 실패해도 raise하지 않는다 — 일시정지는 이미 성공했다.
    await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)
    return _serialize(h)
```

`resume_hospital`의 DNS 확인 블록을 이렇게 바꾼다:

```python
    if h.aeo_domain:
        dns_check = await check_domain_dns(h.aeo_domain, domain_dns_strategy_for_hospital(h))
        if not dns_check.verified:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DOMAIN_NOT_READY",
                    "message": "재개 전 사용자 도메인의 DNS 설정을 확인해 주세요.",
                },
            )
        # 배지·목록이 읽는 관측 필드에 남긴다. 확인만 하고 기록하지 않으면
        # 살아 있는 주소가 '확인 대기'로 표시된다(A-1 재발 경로).
        apply_live_domain_check(
            h,
            LiveDomainCheck(
                domain=h.aeo_domain, healthy=True, reason="dns_ok", checked_at=datetime.now(UTC)
            ),
        )
```

`resume_hospital`의 끝을 이렇게 바꾼다:

```python
    await db.commit()
    await db.refresh(h)
    await trigger_hospital_site_revalidate_safe(h.slug, h.treatments, hospital_name=h.name)
    return _serialize(h)
```

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/test_admin_hospital_lifecycle.py -v`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/hospitals.py backend/tests/test_admin_hospital_lifecycle.py && git commit -m "fix: revalidate the public site on pause/resume and record resume DNS evidence

Pause and resume changed hospital status without purging the site cache, so a
paused hospital stayed public for up to the ISR TTL. Resume also verified DNS
without writing the observation the badges read. (H-06, M-11)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 공개 운영 중인 병원의 `profile_complete` 해제를 막는다 (M-13)

**Files:**
- Modify: `backend/app/api/admin/hospitals.py` `update_profile` (≈798-835, 필드 루프 직후)
- Test: `backend/tests/test_admin_hospitals_profile.py`

- [ ] **Step 1: 회귀 테스트 작성**

`backend/tests/test_admin_hospitals_profile.py`에 추가 (`_hospital`, `FakeDB`는 파일에 있음; `HospitalStatus`를 import한다):

```python
from app.models.hospital import HospitalStatus


async def test_patch_cannot_unset_profile_complete_while_publicly_serving():
    """M-13: 공개 게이트가 profile_complete를 요구하므로 해제하면 공개 페이지가 조용히 404가 된다."""
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True, profile_complete=True)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(profile_complete=False)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PROFILE_COMPLETE_REQUIRED_WHILE_LIVE"
    assert db.committed is False


async def test_patch_can_unset_profile_complete_when_paused():
    hospital = _hospital(status=HospitalStatus.PAUSED, site_live=True, profile_complete=True)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(profile_complete=False)

    await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert hospital.profile_complete is False
    assert db.committed is True
```

`_hospital()` base에 `site_live`, `site_built`, `treatments`, `profile_complete`가 없으면 `site_live=False, site_built=False, treatments=[], profile_complete=True`를 추가한다.

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_admin_hospitals_profile.py -v -k profile_complete`
Expected: 첫 테스트 FAIL (`DID NOT RAISE`).

- [ ] **Step 3: 구현**

`update_profile`에서 필드 루프(`for field, value in update_data.items(): ... setattr(h, field, value)`) 바로 다음, `if h.profile_complete:` 블록 앞에:

```python
    # 공개 게이트(api/public/site.py)는 profile_complete를 요구한다. 운영 중에 해제하면
    # 화면은 계속 '운영 중'인데 공개 페이지만 404가 된다. 먼저 일시정지하게 한다.
    if was_complete and not h.profile_complete and _has_public_site(h):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_COMPLETE_REQUIRED_WHILE_LIVE",
                "message": "공개 운영 중인 병원은 기본 정보 완료를 해제할 수 없습니다. 먼저 운영을 일시정지해 주세요.",
            },
        )
```

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/test_admin_hospitals_profile.py -v`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/hospitals.py backend/tests/test_admin_hospitals_profile.py && git commit -m "fix: refuse to unset profile_complete on a publicly serving hospital

The public gate requires profile_complete, so unsetting it on an ACTIVE
hospital silently 404s the site while admin still says it is live. (M-13)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 도메인 체크리스트의 인증서 단계가 배지와 같은 규칙을 쓴다 (M-10)

**Files:**
- Modify: `backend/app/api/admin/domain_setup.py:216-230` (`_checklist`)
- Test: `backend/tests/test_admin_domain_setup.py`

- [ ] **Step 1: 회귀 테스트 작성**

`backend/tests/test_admin_domain_setup.py`에 추가 (`_hospital`, `_get_setup`, `settings` 사용):

```python
def test_domain_setup_certificate_step_stays_waiting_while_issuing_even_if_live(monkeypatch):
    """M-10: 배지는 '인증서 발급 중'인데 체크리스트만 DONE이면 운영자는 어느 쪽도 믿지 않는다."""
    monkeypatch.setattr(settings, "CNAME_TARGET", "target.motionlabs.example")
    hospital = _hospital(
        site_live=True,
        domain_cert_dns_verified_at=datetime(2026, 8, 22, 2, 0, tzinfo=timezone.utc),
        domain_cert_job_state="ISSUING",
        domain_last_checked_at=datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc),
        domain_last_check_ok=True,
        domain_last_check_reason="https_ok",
    )

    response = _get_setup(hospital, monkeypatch)

    statuses = {item["key"]: item["status"] for item in response.json()["checklist"]}
    assert statuses["dns_verified"] == "DONE"
    assert statuses["certificate_ready"] == "WAITING"


def test_domain_setup_certificate_step_is_done_when_live_and_no_job_state(monkeypatch):
    """A-1 유지: cert 컬럼이 초기화됐어도 주소가 실제로 응답하면 완료로 본다."""
    monkeypatch.setattr(settings, "CNAME_TARGET", "target.motionlabs.example")
    hospital = _hospital(
        site_live=True,
        domain_cert_dns_verified_at=None,
        domain_cert_job_state=None,
        domain_last_checked_at=datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc),
        domain_last_check_ok=True,
        domain_last_check_reason="https_ok",
    )

    response = _get_setup(hospital, monkeypatch)

    statuses = {item["key"]: item["status"] for item in response.json()["checklist"]}
    assert statuses["certificate_ready"] == "DONE"
```

- [ ] **Step 2: 실패 확인**

Run: (환경 준비 명령) `tests/test_admin_domain_setup.py -v -k certificate_step`
Expected: 첫 테스트 FAIL (`'DONE' == 'WAITING'`), 둘째 PASS.

- [ ] **Step 3: 구현**

`_checklist`의 `cert_done` 계산을 교체:

```python
    live_ok = state.last_check_ok is True
    dns_verified = bool(state.dns_verified_at) or live_ok
    # 배지(admin/lib/hospital-domain-status.ts)와 같은 규칙: 발급 중·실패는 관측이
    # 정상이어도 그대로 드러낸다. 관측은 인증서 상태가 비어 있거나 DONE일 때만 완료 근거다.
    cert_in_progress = state.cert_job_state in (
        DomainCertJobState.ISSUING.value,
        DomainCertJobState.FAILED.value,
    )
    cert_done = state.cert_job_state == DomainCertJobState.DONE.value or (
        live_ok and not cert_in_progress
    )
```

- [ ] **Step 4: 통과 확인**

Run: (환경 준비 명령) `tests/test_admin_domain_setup.py -v`
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add backend/app/api/admin/domain_setup.py backend/tests/test_admin_domain_setup.py && git commit -m "fix: keep the certificate checklist step in sync with the domain badge

The checklist marked HTTPS DONE from a live check even while the certificate
job was ISSUING, contradicting the header badge. (M-10)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Admin이 `site_live` 대신 `isPubliclyServing()` 하나로 "공개 중"을 판정한다 (H-06)

**Files:**
- Create: `admin/lib/public-service-state.ts`, `admin/lib/public-service-state.test.ts`
- Modify: `admin/lib/hospital-header-progress.ts:14-45`, `admin/lib/hospital-activation.ts:66-68`, `admin/lib/domain-live-links.ts`, `admin/lib/hospital-domain-status.ts` (`readHospitalDomainStatus`의 도메인 없음 분기, `domainHeaderStatus:242-250`, `DomainHeaderInput`), `admin/app/hospitals/page.tsx:329`, `admin/app/hospitals/[id]/layout.tsx:180,264`
- Test 갱신: `admin/lib/hospital-header-progress.test.ts`, `admin/lib/hospital-activation.test.ts:21-24`, `admin/lib/hospital-domain-status.test.ts`, (있으면) `admin/lib/domain-live-links.test.ts`

- [ ] **Step 1: 판정 함수 테스트 작성**

`admin/lib/public-service-state.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'

import { isPubliclyServing, publicServiceState } from './public-service-state.ts'

test('ACTIVE with site_live is the only live state', () => {
  assert.equal(publicServiceState({ status: 'ACTIVE', site_live: true }), 'live')
  assert.equal(isPubliclyServing({ status: 'ACTIVE', site_live: true }), true)
})

test('a paused hospital is paused even though pause leaves site_live true', () => {
  assert.equal(publicServiceState({ status: 'PAUSED', site_live: true }), 'paused')
  assert.equal(isPubliclyServing({ status: 'PAUSED', site_live: true }), false)
})

test('every other combination is not live', () => {
  assert.equal(publicServiceState({ status: 'ACTIVE', site_live: false }), 'not_live')
  assert.equal(publicServiceState({ status: 'PENDING_DOMAIN', site_live: true }), 'not_live')
  assert.equal(publicServiceState({ status: 'ONBOARDING', site_live: false }), 'not_live')
  assert.equal(publicServiceState({ site_live: true }), 'not_live')
  assert.equal(publicServiceState(null), 'not_live')
  assert.equal(publicServiceState(undefined), 'not_live')
})
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "public-service-state|fail"`
Expected: 모듈을 찾지 못해 FAIL.

- [ ] **Step 3: 판정 함수 구현**

`admin/lib/public-service-state.ts`:

```ts
/**
 * 병원 공개 페이지가 지금 실제로 제공되는가 — 목록·헤더·패널·링크가 모두 이 함수 하나를 쓴다.
 *
 * 백엔드 `_has_public_site`(api/admin/hospitals.py) 및 공개 게이트(api/public/site.py)와
 * 같은 판정: status === 'ACTIVE' && site_live. `site_live`만 보면 안 된다 — 일시정지는
 * status만 PAUSED로 바꾸고 site_live는 그대로 두므로, site_live만 읽는 화면은 정지된
 * 병원을 "공개 중"이라고 말한다.
 */
export type PublicServiceState = 'live' | 'paused' | 'not_live'

export interface PublicServiceInput {
  status?: string | null
  site_live?: boolean | null
}

export function publicServiceState(hospital: PublicServiceInput | null | undefined): PublicServiceState {
  if (!hospital) return 'not_live'
  if (hospital.status === 'PAUSED') return 'paused'
  if (hospital.status === 'ACTIVE' && hospital.site_live === true) return 'live'
  return 'not_live'
}

export function isPubliclyServing(hospital: PublicServiceInput | null | undefined): boolean {
  return publicServiceState(hospital) === 'live'
}
```

- [ ] **Step 4: 통과 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "public-service-state|ℹ fail"`
Expected: 3개 PASS, `ℹ fail 0`.

- [ ] **Step 5: 기존 lib 테스트에 PAUSED 케이스를 먼저 추가 (실패하는 상태로)**

`admin/lib/hospital-activation.test.ts`의 `site_built preview never makes ...` 테스트(≈21-24행)를 교체:

```ts
test('the platform address is browsable only while ACTIVE and live — pause hides it', () => {
  assert.equal(isPlatformAddressBrowsable({ site_live: false, status: 'ACTIVE' }), false)
  assert.equal(isPlatformAddressBrowsable({ site_live: true, status: 'ACTIVE' }), true)
  assert.equal(isPlatformAddressBrowsable({ site_live: true, status: 'PAUSED' }), false)
})
```

`admin/lib/hospital-header-progress.test.ts`: 기존 테스트의 fixture 중 `site_live: true`로 "공개 중"을 의도한 것에 `status: 'ACTIVE'`를 추가하고, 새 테스트를 추가:

```ts
test('a paused hospital does not count its public page as done even though site_live stays true', () => {
  const summary = summarizeHeaderProgress({
    status: 'PAUSED',
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
    schedule_set: true,
    site_live: true,
  })

  assert.equal(summary.doneCount, 4)
  assert.deepEqual(summary.pendingLabels, ['병원 정보 허브'])
})
```

`admin/lib/hospital-domain-status.test.ts`에 추가 (기존 import에 `domainHeaderStatus`, `readHospitalDomainStatus`가 있는지 확인하고 없으면 추가):

```ts
test('a paused hospital on the platform address is never labelled 운영 중', () => {
  assert.equal(domainHeaderStatus({ aeo_domain: null, site_live: true, status: 'PAUSED' }), '운영 일시 정지')
  assert.equal(domainHeaderStatus({ aeo_domain: null, site_live: true, status: 'ACTIVE' }), '운영 중')
  assert.notEqual(readHospitalDomainStatus({ aeo_domain: null, site_live: true, status: 'PAUSED' }).label, '운영 중')
})
```

Run: `npm test 2>&1 | grep -E "✖|ℹ fail"` → Expected: 위 세 테스트 FAIL.

- [ ] **Step 6: lib 배선**

`admin/lib/hospital-header-progress.ts`:

```ts
import { isPubliclyServing } from './public-service-state.ts'

export interface HeaderProgressHospital {
  status?: string | null
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
  schedule_set?: boolean | null
  site_live?: boolean | null
}
```

items의 마지막 항목을 `{ label: '병원 정보 허브', done: isPubliclyServing(hospital) }`로.

`admin/lib/hospital-activation.ts`:

```ts
import { isPubliclyServing, type PublicServiceInput } from './public-service-state.ts'

export function isPlatformAddressBrowsable(hospital: PublicServiceInput): boolean {
  return isPubliclyServing(hospital)
}
```

`admin/lib/domain-live-links.ts`:

```ts
import { isPubliclyServing } from './public-service-state.ts'

export function customDomainLiveUrl(input: {
  status?: string | null
  site_live?: boolean | null
  aeo_domain?: string | null
  hasUnsavedChange: boolean
}): string | null {
  const domain = input.aeo_domain?.trim() ?? ''
  if (!isPubliclyServing(input) || input.hasUnsavedChange || !domain) return null
  return `https://${domain}`
}
```

유일한 호출자 `admin/app/hospitals/[id]/DomainSetupPanel.tsx:118`의 인자 객체에 `status: profile.status,`를 추가한다(`profile`은 같은 컴포넌트가 이미 갖고 있는 병원 프로파일 prop; `profile.site_live`를 넘기는 줄 바로 옆). `admin/lib/domain-live-links.test.ts`의 fixture 중 URL이 나오길 기대하는 케이스에 `status: 'ACTIVE'`를 추가하고, 다음 테스트를 추가한다:

```ts
test('a paused hospital has no live custom-domain link even though site_live stays true', () => {
  assert.equal(
    customDomainLiveUrl({ status: 'PAUSED', site_live: true, aeo_domain: 'clinic.example.com', hasUnsavedChange: false }),
    null,
  )
})
```

`admin/lib/hospital-domain-status.ts`:
- `HospitalDomainInput`과 `DomainHeaderInput`에 `status?: string | null` 추가.
- `HospitalDomainStatus['label']` 유니온에 `'운영 일시 정지'` 추가.
- `domainHeaderStatus`의 도메인 없음 분기를 교체:

```ts
  if (!profile.aeo_domain) {
    if (profile.status === 'PAUSED') return '운영 일시 정지'
    return isPubliclyServing(profile) ? '운영 중' : '공개 주소 확인 대기'
  }
```

- `readHospitalDomainStatus`의 도메인 없음 분기(≈135-148행)는 `site_built || site_live`로 `'기본 주소'` 라벨을 고를 뿐 `'운영 중'`을 주장하지 않으므로 **바꾸지 않는다**. `'운영 일시 정지'` 라벨 유니온 추가는 `domainHeaderStatus`용이다. `grep -n site_live admin/lib/hospital-domain-status.ts`로 확인했을 때 `'운영 중'`을 결정하는 `site_live` 읽기는 `domainHeaderStatus`의 도메인 없음 분기 하나뿐이어야 한다.

- [ ] **Step 7: 화면 배선**

`admin/app/hospitals/page.tsx:329`: `<CheckCell done={h.site_live} />` → `<CheckCell done={isPubliclyServing(h)} />` (import 추가).

`admin/app/hospitals/[id]/layout.tsx`: import 추가 후 `:180` `done={hospital.site_live}` → `done={isPubliclyServing(hospital)}`, `:264` `) : hospital.site_live ? (` → `) : isPubliclyServing(hospital) ? (`. 그 `else` 분기가 `null`이면 PAUSED일 때 아무것도 안 보이므로 다음으로 바꾼다:

```tsx
                ) : isPubliclyServing(hospital) ? (
                  <span className="inline-flex items-center gap-1 text-emerald-600 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    정보 허브 운영 중
                  </span>
                ) : hospital.status === 'PAUSED' ? (
                  <span className="inline-flex items-center gap-1 text-slate-500">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                    공개 페이지 일시정지 중
                  </span>
                ) : null}
```

`grep -rn "site_live" admin/app --include='*.tsx' | grep -v test`로 남은 직접 읽기를 확인한다. `DomainSetupPanel.tsx`의 `site_live: profile.site_live`(:119)와 `onProfileChange({ site_live: true, status: 'ACTIVE' })`(:348, :424)는 상태 전달이지 판정이 아니므로 그대로 둔다.

- [ ] **Step 8: 통과·타입·lint 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "ℹ (tests|pass|fail)" && npm run lint && npm run typecheck`
Expected: `ℹ fail 0`, lint 종료 0, typecheck 종료 0.

- [ ] **Step 9: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add admin/lib/public-service-state.ts admin/lib/public-service-state.test.ts admin/lib/hospital-header-progress.ts admin/lib/hospital-header-progress.test.ts admin/lib/hospital-activation.ts admin/lib/hospital-activation.test.ts admin/lib/domain-live-links.ts admin/lib/hospital-domain-status.ts admin/lib/hospital-domain-status.test.ts admin/app/hospitals/page.tsx 'admin/app/hospitals/[id]/layout.tsx' && git status --porcelain admin && git commit -m "fix(admin): judge 'publicly serving' from status+site_live in one place

Six screens read site_live alone and called a PAUSED hospital live (pause keeps
site_live). isPubliclyServing() mirrors the backend gate. (H-06)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: 일정 없이 일시정지된 병원도 재개할 수 있다 (H-07)

**Files:**
- Modify: `admin/app/hospitals/[id]/layout.tsx:114`
- Test: `admin/lib/hospital-lifecycle.test.ts`

- [ ] **Step 1: 소스 계약 테스트 작성**

`admin/lib/hospital-lifecycle.test.ts` 끝에 추가 (이 저장소의 `hospital-header-progress.test.ts`가 layout 소스를 읽는 것과 같은 방식):

```ts
import { readFileSync } from 'node:fs'

const layoutSource = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)

test('the header never hides resume behind schedule_set — the backend resume gate does not require a schedule', () => {
  assert.doesNotMatch(layoutSource, /resume'\s*&&\s*!hospital\?\.schedule_set/)
  assert.doesNotMatch(layoutSource, /schedule_set\s*\?\s*null/)
})
```

- [ ] **Step 2: 실패 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "never hides resume|ℹ fail"`
Expected: FAIL.

- [ ] **Step 3: 구현**

`layout.tsx:114`의

```ts
  const visibleLifecycleAction = lifecycleAction === 'resume' && !hospital?.schedule_set ? null : lifecycleAction
```

을 삭제하고, 파일 안의 `visibleLifecycleAction` 사용처를 모두 `lifecycleAction`으로 바꾼다 (`grep -n visibleLifecycleAction 'admin/app/hospitals/[id]/layout.tsx'`로 확인).

- [ ] **Step 4: 통과·타입·lint 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/admin && npm test 2>&1 | grep -E "ℹ (tests|pass|fail)" && npm run lint && npm run typecheck`
Expected: `ℹ fail 0`, lint·typecheck 종료 0.

- [ ] **Step 5: 커밋**

```bash
cd /Users/woojinlee/Documents/projects/reputation && git add 'admin/app/hospitals/[id]/layout.tsx' admin/lib/hospital-lifecycle.test.ts && git commit -m "fix(admin): always offer resume for a paused hospital

The header hid resume when schedule_set was false, but the backend resume gate
never required a schedule, so such hospitals were unrecoverable from the UI. (H-07)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 전체 검증

**Files:** 없음 (검증만)

- [ ] **Step 1: 백엔드 전체**

Run: (환경 준비 명령 그대로, 파일 지정 없이)
Expected: `0 failed`. 기준선은 3,110 passed였다 — 이 PR이 추가한 테스트만큼 늘어야 한다.

- [ ] **Step 2: ruff**

Run: `cd /Users/woojinlee/Documents/projects/reputation/backend && .venv/bin/python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: 프론트 전체**

Run: `cd /Users/woojinlee/Documents/projects/reputation && make test-frontend`
Expected: admin·site 테스트 `ℹ fail 0`, lint·typecheck 종료 0.

- [ ] **Step 4: 직접 ACTIVE 기록이 두 곳뿐인지 확인**

Run: `cd /Users/woojinlee/Documents/projects/reputation/backend && grep -rnE '\.status\s*=\s*HospitalStatus\.ACTIVE|\.site_live\s*=\s*True' app`
Expected: `app/services/hospital_activation.py`(`apply_activation_transition`)와 `app/api/admin/hospitals.py`(`resume_hospital`)만. `app/api/admin/content.py:311`은 `previous_hospital_status == ACTIVE`일 때만 ACTIVE를 다시 쓰는 no-op 가드이므로 허용하되, 이 grep 결과를 PR 설명에 붙인다.

- [ ] **Step 5: 검토 요청**

브랜치를 push하지 않는다. 완료 보고에 각 Task의 테스트 수·결과, Step 4의 grep 출력, 바뀐 파일 목록을 적는다. Fable이 결함 ID(H-05, H-06, H-07, M-10, M-11, M-13) 기준으로 검수하고 Codex sol high가 교차 검토한 뒤 push·PR 여부를 사용자가 정한다.
