# Phase 2 — 인프라·자기 감시·배포 코드화 (감사 §4 항목 2-6 ~ 2-10)

기준 소스: main `8c8f6e60`. 모든 경로는 저장소 루트 기준이다. 백엔드 명령은 `cd backend`에서,
`scripts/` 테스트는 저장소 루트에서 `backend/.venv/bin/python -m pytest scripts/...`로 돈다
(`pytest.ini`가 루트에 있고 `from scripts.x import ...`가 이 방식으로 동작한다 — 실제 확인).

공통 주의:

- 백엔드 테스트 가운데 Redis/DB를 실제로 여는 경로는 README·CI backend 잡의 env(`REDIS_URL`,
  `NOTIFICATION_OUTBOX_DATABASE_URL` 등)를 export해야 한다. 지금도 `tests/test_cost_guard.py::
  test_image_generation_skipped_when_cost_guard_blocks`는 `REDIS_URL`이 없으면 실패한다(확인함 —
  기존 실패이며 이 계획과 무관).
- 로컬 Terraform 1.5.7에서 `terraform validate`는 **지금 main에서도 실패한다**(`secretmanager.tf:58`의
  `import` 블록에 변수 사용 — 1.6 이상 필요, 오류 4개). CI(`hashicorp/setup-terraform@v4`, 최신)는
  통과한다. 아래 Terraform 단계의 validate는 1.6 이상(`brew upgrade terraform`)에서 실행한다.
  `main.tf:6`의 `required_version = ">= 1.5"`는 사실과 다르다 — 별도 정리 대상(이 계획 밖).

순서: 2-6a → 2-6b → 2-6c → 2-6d → 2-7 → 2-8a → 2-8b → 2-9-0 → 2-9a → 2-9b → 2-9c → 2-9d → 2-9e →
2-10(개요). 2-9의 각 단계는 같은 파일(`scripts/deploy.sh`, `scripts/test_deploy_runtime.py`)을
고치므로 반드시 이 순서로 한다.

---

### Task 2-6a: readiness에 `pipeline_watchdog_token_configured` 추가

**Files:**
- Modify: `backend/app/utils/production_readiness.py:212-243` (`_configuration_facts`)
- Test: `backend/tests/test_production_readiness.py`

**조사 결과:**
- 감시 엔드포인트 인증(`backend/app/api/admin/watchdog.py:34-46`)은 토큰이 비어 있으면 admin 키로만
  통과한다. Cloud Scheduler는 `X-Watchdog-Token`만 보내므로(`terraform/watchdog.tf:40-46`) 토큰이 비면
  매 호출 401이다. API는 토큰이 비어도 부팅한다(`config.py:206` 경고만, `watchdog.py:62-67` 주석).
- readiness 검사(`production_readiness.py:322-345`)에 이 항목이 없다 — 감사 주장 그대로 확인.
- 다만 `scripts/deploy.sh:180-182`가 `PIPELINE_WATCHDOG_TOKEN`을 backend **필수 시크릿**으로 두어
  `require_secret_versions`가 latest 버전 활성 여부를 배포 전에 본다. 남는 구멍은 "버전은 있는데 값이
  빈 문자열"과 "readiness Job이 실제로 받은 값"이다. 그래서 검사는 싸고 의미가 있다.
- `_configuration_facts`의 다른 항목(`release_revision_configured`)처럼 production이 아닐 때는 통과시킨다.
- 이 검사는 `wait_production_readiness`가 쓰는 같은 `build_report`로 흐르므로 배포 readiness Job에서
  실패하면 배포가 멈춘다(2-9c 뒤에는 자동 롤백). 운영 readiness 검사 수는 17 → 18이 된다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_production_readiness.py` 끝에 추가

```python
def test_watchdog_token_is_a_production_readiness_check(monkeypatch) -> None:
    """토큰이 비면 Cloud Scheduler 감시 호출이 전부 401이다 — 외부 감시가 꺼진 채 배포되지 않게 한다."""
    settings = production_readiness.settings
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "PIPELINE_WATCHDOG_TOKEN", "   ")

    assert production_readiness._configuration_facts()["pipeline_watchdog_token_configured"] is False

    monkeypatch.setattr(settings, "PIPELINE_WATCHDOG_TOKEN", "watchdog-token")
    assert production_readiness._configuration_facts()["pipeline_watchdog_token_configured"] is True

    # 운영이 아닌 환경은 감시가 없으므로 막지 않는다(release_revision_configured와 같은 규칙).
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "PIPELINE_WATCHDOG_TOKEN", "")
    assert production_readiness._configuration_facts()["pipeline_watchdog_token_configured"] is True
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_production_readiness.py::test_watchdog_token_is_a_production_readiness_check -v`
  Expected: FAIL with `KeyError: 'pipeline_watchdog_token_configured'`

- [ ] **Step 3: 최소 구현** — `production_readiness.py`의 `_configuration_facts` 반환 dict 마지막 항목
  (`"release_revision_configured": ...`) 바로 뒤에 추가

```python
        # Cloud Scheduler 외부 감시의 유일한 자격 증명. 비면 감시 호출이 전부 401이고 API는 그래도
        # 뜨므로(부팅을 막지 않는다) 외부 감시가 조용히 꺼진다(2026-10 자율 운영 감사 F8).
        "pipeline_watchdog_token_configured": bool(settings.PIPELINE_WATCHDOG_TOKEN.strip())
        or settings.APP_ENV != "production",
```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_production_readiness.py -v`
  Expected: 새 테스트 PASS. 기존 `test_build_report_requires_schema_owner_and_runtime_dependencies`는
  `_configuration_facts`를 통째로 대체하므로 영향 없음(PASS).

- [ ] **Step 5: 커밋**
  `git add backend/app/utils/production_readiness.py backend/tests/test_production_readiness.py && git commit -m "fix(readiness): 외부 감시 토큰이 비면 배포 준비 확인을 통과하지 않는다"`

**계약 영향:** CLAUDE.md 최상단 배포 절의 "readiness 17개 검사"는 다음 배포 기록부터 18개다. CLAUDE.md
"Celery 밖의 외부 감시…감시 토큰이 비어 있어도 부팅은 막지 않는다" 문장 뒤에 "다만 배포 readiness는
토큰이 비면 통과하지 않는다(`pipeline_watchdog_token_configured`)."를 덧붙인다.
`docs/ops/deployment-runbook.md`의 readiness 항목 수 서술도 같이 고친다.

**위험·롤백:** 운영 토큰이 실제로 비어 있으면 첫 배포가 readiness에서 멈춘다(그것이 목적). 배포 전
`gcloud secrets versions access latest --secret=PIPELINE_WATCHDOG_TOKEN | wc -c`로 값이 있는지 본다.
롤백은 커밋 revert.

---

### Task 2-6b: Slack을 거치지 않는 GCP 이메일 경보 — Scheduler 실패·감시 전송 실패·운영 채널 사망

**Files:**
- Modify: `terraform/monitoring.tf` (파일 끝, `operator_5xx` 정책 뒤에 추가)
- Test: `scripts/test_terraform_static.py`

**조사 결과:**
- GCP 이메일 채널은 이미 있다: `google_monitoring_notification_channel.email`(`monitoring.tf:31-39`,
  `var.alert_email` 필수, 예시 `ops@motionlabs.kr` — `terraform/terraform.tfvars.example:73`).
- 로그 기반 ERROR 경보는 worker/beat만 본다(`monitoring.tf:213-245`) — 감사 주장 확인.
- **감사와 일부 다르다:** 단일 채널 모드에서 운영 웹훅이 죽으면 Worker의 발송기가
  `logger.error("No live Slack channel; holding notification ...")`(`notification_delivery.py:165-169`)를
  매 행마다 남긴다. 이 줄은 이미 `worker_beat_errors` 정책으로 이메일이 간다(시간당 1건). 즉
  "운영 채널 사망"의 이메일 경로는 지금도 **있지만** 모든 worker ERROR에 섞여 식별되지 않는다.
- **감사 그대로:** 외부 감시는 API에서 돈다(`api/admin/watchdog.py`). Slack 전송 실패는
  `logger.error("pipeline watchdog: Slack delivery failed ...")`(`pipeline_watchdog.py:1268-1291`)로 API
  로그에만 남고 API ERROR 경보는 없다(`operator_5xx`는 HTTP 5xx만). Cloud Scheduler 호출 실패(401 등)도
  경보가 없다.
- 로그는 JSON(`core/observability.py:35-42`, `jsonPayload.message`)이므로 필터는 `jsonPayload.message:"..."`.
- GCP 로그 기반 경보 정책은 **조건 하나만** 허용한다. 그래서 정책 둘로 나누고, 두 번째는 OR 필터 하나로 묶는다.
- Scheduler 작업 이름은 `watchdog.tf`의 `google_cloud_scheduler_job.watchdog_heartbeat.name`,
  `...watchdog_publish_check.name`(같은 모듈이라 참조 가능). 실패 시도는 `resource.type="cloud_scheduler_job"`,
  `severity>=ERROR`로 기록된다.
- 기존 정적 테스트(`scripts/test_terraform_static.py:79-97`)가 `monitoring.tf` 문자열을 검사하는 방식이다.

- [ ] **Step 1: 실패하는 테스트 작성** — `scripts/test_terraform_static.py` 끝에 추가

```python
def test_watchdog_and_slack_path_failures_alert_by_email() -> None:
    """Slack이 죽거나 외부 감시가 멈춘 사실은 Slack이 아닌 이메일로 알려야 한다(감사 F8)."""
    monitoring = (PROJECT_ROOT / "terraform" / "monitoring.tf").read_text()

    scheduler = terraform_block(
        monitoring, 'resource "google_monitoring_alert_policy" "watchdog_scheduler_failures"'
    )
    assert r'resource.type=\"cloud_scheduler_job\"' in scheduler
    assert "google_cloud_scheduler_job.watchdog_heartbeat.name" in scheduler
    assert "google_cloud_scheduler_job.watchdog_publish_check.name" in scheduler
    assert "google_monitoring_notification_channel.email[0].id" in scheduler
    assert 'period = "3600s"' in scheduler

    slack_path = terraform_block(
        monitoring, 'resource "google_monitoring_alert_policy" "slack_path_failures"'
    )
    assert r'jsonPayload.message:\"pipeline watchdog\"' in slack_path
    assert r'jsonPayload.message:\"No live Slack channel\"' in slack_path
    assert '${var.app_name}-api' in slack_path
    assert '${var.app_name}-worker' in slack_path
    assert "google_monitoring_notification_channel.email[0].id" in slack_path
    # 로그 기반 정책은 조건 하나만 허용한다.
    assert slack_path.count("condition_matched_log") == 1
```

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_terraform_static.py::test_watchdog_and_slack_path_failures_alert_by_email -v`
  Expected: FAIL with `ValueError: substring not found` (`terraform_block`의 `text.index`)

- [ ] **Step 3: 최소 구현** — `terraform/monitoring.tf` 끝에 추가

```hcl

# ── 자기 감시 경로의 실패 (2026-10 자율 운영 감사 F8) ─────────────────
# 운영 Slack 채널 하나로 운영하므로 Slack 자체나 외부 감시가 멈추면 그 사실을 전할 곳이 없다.
# 두 경보 모두 Slack을 거치지 않고 필수 ops 이메일 채널로 간다.
#
# 1) Cloud Scheduler가 감시 엔드포인트를 부르지 못했다(토큰 공백 401, LB·API 장애).
resource "google_monitoring_alert_policy" "watchdog_scheduler_failures" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name} pipeline watchdog scheduler failures"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Scheduler watchdog attempt failed"
    condition_matched_log {
      filter = "resource.type=\"cloud_scheduler_job\" AND severity>=ERROR AND (resource.labels.job_id=\"${google_cloud_scheduler_job.watchdog_heartbeat.name}\" OR resource.labels.job_id=\"${google_cloud_scheduler_job.watchdog_publish_check.name}\")"
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }

  notification_channels = distinct(concat(
    [google_monitoring_notification_channel.email[0].id],
    var.notification_channels,
  ))

  documentation {
    content = "외부 파이프라인 감시(Cloud Scheduler → /api/v1/admin/watchdog/pipeline/alert) 호출이 실패했습니다. 401이면 PIPELINE_WATCHDOG_TOKEN 값과 Scheduler 헤더를, 5xx면 API 리비전 로그를 확인하세요. 감시가 멈춘 동안 Beat·Worker 정지와 발행 0건이 Slack으로 알려지지 않습니다."
  }
}

# 2) 감시 결과나 운영 알림이 Slack에 닿지 않았다 — API의 감시 전송 실패, Worker의 '보낼 채널 없음'.
#    로그 기반 정책은 조건 하나만 허용하므로 필터 하나에 OR로 묶는다.
resource "google_monitoring_alert_policy" "slack_path_failures" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name} Slack alert path failures"
  combiner     = "OR"

  conditions {
    display_name = "Watchdog or operator notifications not delivered to Slack"
    condition_matched_log {
      filter = "resource.type=\"cloud_run_revision\" AND severity>=ERROR AND ((resource.labels.service_name=\"${var.app_name}-api\" AND jsonPayload.message:\"pipeline watchdog\") OR (resource.labels.service_name=\"${var.app_name}-worker\" AND jsonPayload.message:\"No live Slack channel\"))"
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }

  notification_channels = distinct(concat(
    [google_monitoring_notification_channel.email[0].id],
    var.notification_channels,
  ))

  documentation {
    content = "운영 Slack 채널로 알림이 전달되지 않습니다. 'No live Slack channel'이면 SLACK_WEBHOOK_URL이 죽었습니다(웹훅을 다시 발급해 Secret Manager에 새 버전을 넣으면 한 시간 안에 자동 복구됩니다). 'pipeline watchdog'이면 외부 감시 경보가 Slack에 닿지 않았거나 감시가 Redis·DB를 읽지 못했습니다."
  }
}
```

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_terraform_static.py -v` → 전부 PASS.
  Run(Terraform ≥1.6): `cd terraform && terraform fmt -check monitoring.tf && terraform init -backend=false >/dev/null && terraform validate`
  Expected: fmt 출력 없음, `Success! The configuration is valid.`
  운영 반영 전 `terraform plan -target=google_monitoring_alert_policy.watchdog_scheduler_failures -target=google_monitoring_alert_policy.slack_path_failures`
  Expected: `Plan: 2 to add, 0 to change, 0 to destroy.` (다른 변경이 섞이면 적용하지 않는다)

- [ ] **Step 5: 커밋**
  `git add terraform/monitoring.tf scripts/test_terraform_static.py && git commit -m "feat(monitoring): 외부 감시·Slack 경로 실패를 이메일 경보로 승격한다"`

**계약 영향:** CLAUDE.md "Slack 채널은 운영 채널 하나다" 문단 끝에 "Slack 경로 자체의 실패(운영 웹훅 사망·
외부 감시 전송 실패·Scheduler 호출 실패)는 GCP 로그 기반 경보가 ops 이메일로 알린다."를 추가.
`docs/ops/slack-notification-policy.md`에 같은 2차 경로를 적는다.

**위험·롤백:** 감시 실패가 지속되면 시간당 1통. 운영 Redis 장애 시 API가 `pipeline watchdog: Redis
unavailable ...` ERROR를 남겨 이 경보가 함께 울린다(의도된 동작 — 같은 장애의 Slack 경로가 불확실하다).
롤백: 두 리소스를 지우고 `terraform apply -target=...`.

---

### Task 2-6c: `OPS_ALERT_EMAIL` + 외부 감시 경보를 Slack에 못 보내면 Resend 메일로 보낸다

**Files:**
- Modify: `backend/app/core/config.py:707` 근처(`RESEND_API_KEY` 아래)
- Modify: `.env.example`, `.env.production.example` (`LEAD_MAIL_REPLY_TO=` 줄 바로 뒤)
- Create: `backend/app/services/ops_alert_email.py`
- Modify: `backend/app/services/pipeline_watchdog.py:1296-1297`(파일 끝)
- Modify: `backend/app/api/admin/watchdog.py:74-93`
- Modify: `backend/app/utils/production_readiness.py:322-345`(facts)
- Test: `backend/tests/test_pipeline_watchdog.py`, `backend/tests/test_admin_watchdog.py`, `backend/tests/test_ops_alert_email.py`

**조사 결과:**
- `mailer.send_email`(`services/mailer.py:51-98`)은 Resend `Idempotency-Key`를 받는다. Resend는 같은 키를
  24시간 보관하고, 같은 키에 다른 본문이 오면 409를 준다 → `RuntimeError("resend_idempotency_conflict:...")`
  (`mailer.py:91-94`). 409는 "그 키로 이미 처리했거나 처리 중"이므로 2차 경로에서는 **보낸 것으로** 본다.
- `RESEND_API_KEY`·`LEAD_MAIL_FROM`은 운영 필수다(`config.py:304-315`, deploy.sh 필수 시크릿 `:197`). 새 시크릿 불필요.
- 감시 경보는 Redis 에피소드별 식별자 `episode_id`(opened_at)를 갖는다(`AlertDecision.episode_id`,
  `pipeline_watchdog.py:245-257`). 전달 실패한 ALERT는 15분마다 재시도된다(`_RETRY_INTERVAL`). 따라서
  `watchdog:{audience}:{episode_id}`를 키로 쓰면 에피소드당 메일이 24시간에 최대 1통이다. Redis가 죽은
  fail-open 경로는 `episode_id=None`(`_fail_open`, `:1050-1064`) — 그때는 `KST 날짜 + 조건 서명`으로 키를 만든다.
- `AlertDecision.text`는 보고서 숫자를 포함해 회차마다 바뀔 수 있다 → 같은 키 다른 본문 = 409 = 이미 보냄. 의도대로 동작.
- 기존 엔드포인트 테스트(`tests/test_admin_watchdog.py:100-146`)가 응답의 `alerts` 항목을 dict 전체로
  비교한다 → 새 키 `emailed`를 넣으면 그 기대값도 고쳐야 한다(Step 3에 포함).
- 받는 주소는 새 설정 `OPS_ALERT_EMAIL`(기본 빈 값 = 끔). 빈 값이면 ERROR 로그만 남긴다 — 그 로그는 2-6b의
  `slack_path_failures` 필터("pipeline watchdog")에는 걸리지 않으므로 메시지를 `pipeline watchdog:`으로 시작해
  GCP 경보에도 걸리게 한다.
- `.env` 템플릿 가드(`scripts/test_deploy_runtime.py:432-509`)가 새 Settings 필드를 두 템플릿에 요구하고
  `.env.production.example` 값이 config 기본값과 같아야 한다 → 두 템플릿에 `OPS_ALERT_EMAIL=`(빈 값).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_ops_alert_email.py` (새 파일):

```python
"""운영 경보 메일 — Slack이 닿지 않을 때의 2차 경로(감사 F8)."""

import httpx
import pytest

from app.services import ops_alert_email


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ops_alert_email.settings, "OPS_ALERT_EMAIL", "ops@example.test")
    monkeypatch.setattr(ops_alert_email.settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(ops_alert_email.settings, "LEAD_MAIL_FROM", "Re:putation <noreply@example.test>")
    monkeypatch.setattr(ops_alert_email.settings, "LEAD_MAIL_REPLY_TO", "")


async def test_sends_one_mail_with_the_given_idempotency_key(configured):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="watchdog:operator:2026-10-11T00:00:00+00:00",
            title="외부 감시 경보를 Slack에 전달하지 못했습니다",
            lines=("아침 자동 발행이 멈췄습니다.", "<b>escape</b>"),
            client=client,
        )

    assert sent is True
    assert len(seen) == 1
    assert seen[0].headers["Idempotency-Key"] == "watchdog:operator:2026-10-11T00:00:00+00:00"
    body = seen[0].content.decode()
    assert "ops@example.test" in body
    assert "&lt;b&gt;escape&lt;/b&gt;" in body
    assert "<b>escape</b>" not in body


async def test_conflict_means_the_key_was_already_used(configured):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(409, text="invalid_idempotent_request"))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is True


async def test_server_error_is_reported_as_not_sent(configured, caplog):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500, text="boom"))
    ) as client:
        sent = await ops_alert_email.send_ops_alert(
            idempotency_key="k", title="t", lines=("a",), client=client
        )
    assert sent is False
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_missing_address_is_not_sent_and_logged_for_the_gcp_alert(monkeypatch, caplog):
    monkeypatch.setattr(ops_alert_email.settings, "OPS_ALERT_EMAIL", "")
    sent = await ops_alert_email.send_ops_alert(idempotency_key="k", title="t", lines=("a",))
    assert sent is False
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    # 2-6b의 GCP 경보 필터("pipeline watchdog")가 이 줄을 잡는다.
    assert errors and errors[0].startswith("pipeline watchdog: ops alert email not configured")
```

`backend/tests/test_pipeline_watchdog.py` 끝에 추가:

```python
async def test_undelivered_alerts_are_emailed_once_per_episode(monkeypatch):
    calls: list[dict] = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(pipeline_watchdog.ops_alert_email, "send_ops_alert", fake_send)
    now = datetime(2026, 10, 11, 0, 5, tzinfo=UTC)
    decisions = (
        pipeline_watchdog.AlertDecision(
            True, "ALERT", "developer", "인프라 경보", "new_condition_set",
            "https://hooks.slack.com/ops", ("beat_down",), "2026-10-11T00:00:00+00:00",
        ),
        pipeline_watchdog.AlertDecision(
            True, "ALERT", "operator", "발행 경보", "redis_unavailable",
            "https://hooks.slack.com/ops", ("publish_missing",), None,
        ),
        pipeline_watchdog.AlertDecision(
            True, "RECOVERY", "operator", "복구", "recovered", "https://hooks.slack.com/ops"
        ),
        pipeline_watchdog.AlertDecision(False, None, "operator", None, "deduped", None),
    )

    emailed = await pipeline_watchdog.email_undelivered(
        decisions, (False, False, False, False), now=now
    )

    # 전달 실패한 ALERT만 메일로 간다. RECOVERY·보내지 않은 결정은 메일이 없다.
    assert emailed == (True, True, False, False)
    assert [call["idempotency_key"] for call in calls] == [
        "watchdog:developer:2026-10-11T00:00:00+00:00",
        "watchdog:operator:2026-10-11:" + pipeline_watchdog._signature(("publish_missing",)),
    ]
    assert calls[0]["lines"] == ("인프라 경보",)


async def test_delivered_alerts_are_not_emailed(monkeypatch):
    async def unexpected(**_kwargs):
        raise AssertionError("delivered alerts must not be emailed")

    monkeypatch.setattr(pipeline_watchdog.ops_alert_email, "send_ops_alert", unexpected)
    decision = pipeline_watchdog.AlertDecision(
        True, "ALERT", "operator", "발행", "new_condition_set", "https://hooks.slack.com/ops",
        ("publish_missing",), "e1",
    )
    assert await pipeline_watchdog.email_undelivered(
        (decision,), (True,), now=datetime(2026, 10, 11, tzinfo=UTC)
    ) == (False,)
```

`backend/tests/test_admin_watchdog.py` 끝에 추가:

```python
async def test_alert_endpoint_emails_alerts_that_slack_did_not_receive(monkeypatch):
    decisions = (
        AlertDecision(
            True, "ALERT", "operator", "발행", "new_condition_set",
            "https://hooks.slack.com/ops", ("publish_missing",), "e1",
        ),
    )

    async def fake_deliver_all(values):
        return tuple(False for _ in values)

    emailed_with: list[tuple] = []

    async def fake_email(values, outcomes, *, now):
        emailed_with.append((values, outcomes, now))
        return (True,)

    monkeypatch.setattr(watchdog, "_evaluate_and_decide", lambda: (_report(), decisions))
    monkeypatch.setattr(watchdog.pipeline_watchdog, "deliver_all", fake_deliver_all)
    monkeypatch.setattr(watchdog.pipeline_watchdog, "record_deliveries", lambda *_args: None)
    monkeypatch.setattr(watchdog.pipeline_watchdog, "email_undelivered", fake_email)

    payload = await watchdog.run_pipeline_watchdog_alert()

    assert emailed_with[0][:2] == (decisions, (False,))
    assert emailed_with[0][2] == _report().observed_at
    assert payload["alerts"][0]["delivered"] is False
    assert payload["alerts"][0]["emailed"] is True
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_ops_alert_email.py tests/test_pipeline_watchdog.py::test_undelivered_alerts_are_emailed_once_per_episode tests/test_admin_watchdog.py::test_alert_endpoint_emails_alerts_that_slack_did_not_receive -v`
  Expected: FAIL with `ImportError: cannot import name 'ops_alert_email'` / `AttributeError: module 'app.services.pipeline_watchdog' has no attribute 'ops_alert_email'`

- [ ] **Step 3: 최소 구현**

`backend/app/core/config.py` — `RESEND_API_KEY: str = ""` 줄 바로 아래에 추가:

```python
    # 운영 경보 메일(2차 경로). 운영 Slack 채널이 죽었거나 외부 감시가 Slack에 전하지 못했을 때만
    # 이 주소로 Resend 메일을 보낸다(services/ops_alert_email.py). 비어 있으면 보내지 않고 ERROR만 남긴다.
    OPS_ALERT_EMAIL: str = ""
```

`.env.example`와 `.env.production.example` — 각각 `LEAD_MAIL_REPLY_TO=` 줄 바로 뒤에 추가:

```
# 운영 Slack이 닿지 않을 때 운영 경보를 받을 메일 주소(2차 경로). 비우면 메일을 보내지 않는다.
OPS_ALERT_EMAIL=
```

`backend/app/services/ops_alert_email.py` (새 파일):

```python
"""운영 경보 메일 — 운영 Slack이 닿지 않을 때의 두 번째 경로(2026-10 자율 운영 감사 F8).

운영 채널 하나로 운영하므로 그 웹훅이 죽거나 외부 감시가 Slack에 전하지 못하면 사람에게 닿을
곳이 없다. 그때만 `OPS_ALERT_EMAIL`로 Resend 메일을 보낸다. 정상 경로가 아니다.

중복 방지는 `mailer.send_email`과 같은 규칙이다 — 호출자가 사건(사고 episode, 감시 에피소드)마다
결정적인 `idempotency_key`를 주고, Resend가 같은 키의 재요청을 24시간 동안 한 번으로 묶는다. 같은
키에 다른 본문이 와서 409가 나면 그 키로 이미 보냈다는 뜻이므로 보낸 것으로 본다.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from app.core.config import settings
from app.services import mailer

logger = logging.getLogger(__name__)

_HEADER_UNSAFE = re.compile(r"[\r\n\t]+")
_CONFLICT_PREFIX = "resend_idempotency_conflict"


def ops_alert_configured() -> bool:
    return bool(settings.OPS_ALERT_EMAIL.strip()) and mailer.is_configured()


def _subject(title: str) -> str:
    return f"[Re:putation 운영 경보] {_HEADER_UNSAFE.sub(' ', title).strip()}"


def _html(title: str, lines: tuple[str, ...]) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 10px;font-size:14px;line-height:1.7;">{html.escape(line)}</p>'
        for line in lines
        if line.strip()
    )
    return (
        '<!DOCTYPE html><html lang="ko"><body style="margin:0;padding:24px;'
        "font-family:'Apple SD Gothic Neo',sans-serif;color:#1a1d21;\">"
        f'<h1 style="margin:0 0 16px;font-size:18px;">{html.escape(title)}</h1>'
        f"{paragraphs}</body></html>"
    )


async def send_ops_alert(
    *,
    idempotency_key: str,
    title: str,
    lines: tuple[str, ...],
    client: httpx.AsyncClient | None = None,
) -> bool:
    """보냈거나 같은 키로 이미 보냈으면 True. 설정이 없거나 실패하면 False와 ERROR 로그."""

    to = settings.OPS_ALERT_EMAIL.strip()
    if not to or not mailer.is_configured():
        # 2-6b의 GCP 로그 경보 필터("pipeline watchdog")가 이 줄을 잡는다.
        logger.error("pipeline watchdog: ops alert email not configured key=%s", idempotency_key)
        return False
    try:
        await mailer.send_email(
            to=to,
            subject=_subject(title),
            html=_html(title, lines),
            idempotency_key=idempotency_key,
            client=client,
        )
    except RuntimeError as exc:
        if str(exc).startswith(_CONFLICT_PREFIX):
            return True
        logger.error(
            "pipeline watchdog: ops alert email failed key=%s error=%s",
            idempotency_key,
            str(exc)[:80],
        )
        return False
    except (httpx.HTTPError, mailer.MailNotConfigured) as exc:
        logger.error(
            "pipeline watchdog: ops alert email failed key=%s error=%s",
            idempotency_key,
            exc.__class__.__name__,
        )
        return False
    return True
```

`backend/app/services/pipeline_watchdog.py` — import 블록에 `from app.services import ops_alert_email` 추가
(다른 `app.services` import 옆, 알파벳 순). 파일 끝(`deliver_all` 뒤)에 추가:

```python


_EMAIL_TITLE: Final = "외부 감시 경보를 Slack에 전달하지 못했습니다"


def _email_key(decision: AlertDecision, *, now: datetime) -> str:
    """에피소드당 하나. Redis가 없어 에피소드가 없으면 KST 날짜와 조건 집합으로 묶는다."""
    if decision.episode_id:
        return f"watchdog:{decision.audience}:{decision.episode_id}"
    kst_date = now.astimezone(KST).date().isoformat()
    return f"watchdog:{decision.audience}:{kst_date}:{_signature(decision.conditions)}"


async def email_undelivered(
    decisions: tuple[AlertDecision, ...], delivered: tuple[bool, ...], *, now: datetime
) -> tuple[bool, ...]:
    """Slack이 받지 않은 ALERT를 운영 경보 메일로 보낸다(2차 경로).

    전달되지 않은 ALERT는 15분마다 다시 판정되므로 이 함수도 다시 불린다. 키가 에피소드에
    묶여 있어 Resend가 24시간 동안 한 통으로 묶는다. RECOVERY는 메일로 보내지 않는다.
    """
    results: list[bool] = []
    for decision, ok in zip(decisions, delivered, strict=True):
        if ok or not (decision.send and decision.kind == "ALERT" and decision.text):
            results.append(False)
            continue
        results.append(
            await ops_alert_email.send_ops_alert(
                idempotency_key=_email_key(decision, now=now),
                title=_EMAIL_TITLE,
                lines=tuple(decision.text.splitlines()),
            )
        )
    return tuple(results)
```

(`Final`, `KST`, `datetime`은 이미 모듈에 있다 — `pipeline_watchdog.py:100-117`, `tests`가 `pipeline_watchdog.KST`를 쓴다.)

`backend/app/api/admin/watchdog.py` — `run_pipeline_watchdog_alert` 본문을 다음으로 바꾼다:

```python
@router.post("/pipeline/alert", dependencies=[Depends(verify_watchdog_access)])
async def run_pipeline_watchdog_alert() -> dict[str, Any]:
    """판정 후 필요한 경우에만 수신자별로 Slack 한 건씩 직접 보낸다."""
    report, decisions = await run_in_threadpool(_evaluate_and_decide)
    delivered = await pipeline_watchdog.deliver_all(decisions)
    # 전달이 확인된 ALERT만 에피소드를 '전달됨'으로 바꾼다. 실패한 것은 다음 하트비트가 다시 보낸다.
    await run_in_threadpool(pipeline_watchdog.record_deliveries, decisions, delivered)
    # Slack이 받지 않은 ALERT는 운영 경보 메일로도 보낸다(에피소드당 한 통, 2차 경로).
    emailed = await pipeline_watchdog.email_undelivered(
        decisions, delivered, now=report.observed_at
    )
    return {
        "report": _payload(report),
        "alerts": [
            {
                "audience": decision.audience,
                "kind": decision.kind,
                "sent": decision.send,
                "delivered": was_delivered,
                "emailed": was_emailed,
                "reason": decision.reason,
            }
            for decision, was_delivered, was_emailed in zip(
                decisions, delivered, emailed, strict=True
            )
        ],
    }
```

`backend/tests/test_admin_watchdog.py:126-141`의 기존 기대 dict 두 개에 `"emailed": False,`를 `"delivered": True,`
바로 뒤에 넣는다(전달된 ALERT는 메일이 없다). `test_alert_endpoint_stays_silent_when_healthy`는 `sent`/`delivered`만
보므로 그대로.

`backend/app/utils/production_readiness.py` `build_report`의 `"facts"` dict에 참고 사실 추가
(`"slack_webhook_valid": slack_webhooks,` 바로 뒤):

```python
            # 2차 경로(운영 경보 메일) 설정 — 참고 사실이며 준비 판정을 막지 않는다.
            "ops_alert_email_configured": bool(settings.OPS_ALERT_EMAIL.strip()),
```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_ops_alert_email.py tests/test_pipeline_watchdog.py tests/test_admin_watchdog.py tests/test_production_readiness.py -v`
  Expected: 전부 PASS (`test_production_readiness`의 Redis 의존 테스트는 env 필요).
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -k "env_templates or matches_config_defaults" -v`
  Expected: 3 PASS (템플릿 가드).

- [ ] **Step 5: 커밋**
  `git add backend/app/core/config.py .env.example .env.production.example backend/app/services/ops_alert_email.py backend/app/services/pipeline_watchdog.py backend/app/api/admin/watchdog.py backend/app/utils/production_readiness.py backend/tests/test_ops_alert_email.py backend/tests/test_pipeline_watchdog.py backend/tests/test_admin_watchdog.py && git commit -m "feat(watchdog): Slack에 닿지 않은 외부 감시 경보를 운영 경보 메일로 보낸다"`

**계약 영향:** CLAUDE.md "Celery 밖의 외부 감시…Slack 웹훅에 직접 보낸다" 문장 뒤에 "Slack이 받지 않은 ALERT는
`OPS_ALERT_EMAIL`로 에피소드당 한 통(Resend 멱등 키) 메일로 보낸다. RECOVERY는 메일로 보내지 않는다."를 추가.
"저장 성공과 알림 성공을 구분" 원칙은 그대로다 — 메일은 판정·에피소드 기록을 바꾸지 않는다.

**위험·롤백:** 운영 값 `OPS_ALERT_EMAIL`을 넣기 전까지는 동작 변화 없음(ERROR 로그 + 2-6b 경보). 운영 주소는
대표가 정한다(아래 질문). Resend 일 한도와 무관한 저빈도(에피소드당 24시간 1통). 롤백은 커밋 revert.

---

### Task 2-6d: 운영 채널 사고가 열렸는데 대체 채널이 없으면 운영 경보 메일(사고 episode당 한 번)

**Files:**
- Modify: `backend/app/services/notification_channel_health.py` (import + 파일 끝에 함수 추가)
- Modify: `backend/app/services/notification_delivery.py:24-30`(import), `:155-158`(호출)
- Test: `backend/tests/test_notification_outbox.py` (`_isolate_dispatch` 보강 + 새 DB 테스트)

**조사 결과:**
- 채널 사고는 `open_channel_incident`(`notification_channel_health.py:118-163`)가 열고, 열림 알림은 다른
  채널로 가려 한다. 단일 채널 모드(`SLACK_WEBHOOK_URL_DEV` 비활성 — 2026-10-08 운영 상태)에서 운영 웹훅이
  죽으면 그 알림도 보낼 곳이 없어 `HOLD`된다(기존 테스트 `test_hourly_probe_recovers_the_channel_and_requeues_held_rows`,
  `tests/test_notification_outbox.py:1164-1215`가 바로 이 상황).
- 사고는 DB에 지속되는 의도다(`Incident`, `episode_seq` — `models/operations.py:238`). 메일의 "보냈다" 사실은
  `AdminAuditLog`(target_id String(80), `models/audit.py:30`)에 `"{incident_id}:e{episode_seq}"`로 남긴다 —
  같은 모듈이 이미 감사 기록을 탐침 간격의 정본으로 쓴다(`_record_flap`, `:376-386`).
- 발송기는 매분 돈다(Beat `dispatch_notification_outbox`). 메일 실패 시 매분 재시도하지 않도록 프로세스 내
  15분 간격을 둔다(지속 상태가 아니어도 된다 — 재시도는 같은 Idempotency-Key라 중복이 없다).
- 기존 순수 테스트 헬퍼 `_isolate_dispatch`(`tests/test_notification_outbox.py:232-257`)는 DB 함수를 하나씩
  바꿔 끼운다 → 새 호출도 바꿔 끼워야 순수 테스트가 DB에 닿지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_notification_outbox.py`

import 블록의 `from app.services import notification_delivery`를
`from app.services import notification_channel_health, notification_delivery`로 바꾸고,
`_isolate_dispatch` 마지막 줄 뒤에 추가:

```python
    monkeypatch.setattr(notification_delivery, "email_dead_operator_channel", nothing)
```

파일 끝에 추가:

```python
@pytest.mark.asyncio
async def test_dead_operator_channel_without_alternate_is_emailed_once_per_episode(
    outbox_sessions, monkeypatch
) -> None:
    # Given: 단일 채널 운영에서 운영 웹훅이 죽었다 — 채널 사고의 열림 알림도 보낼 곳이 없다
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(notification_channel_health.ops_alert_email, "send_ops_alert", fake_send)
    notification_channel_health._last_ops_email_attempt.clear()
    dead = lambda _request: httpx.Response(404, text="no_service")  # noqa: E731

    # When: 사고가 열린 회차와 20분 뒤 회차(탐침 간격 안)가 돈다
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-EMAIL-1", dead)
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-EMAIL-1", dead, now=_NOW + timedelta(minutes=20))

    # Then: 사고 episode당 메일 한 통, 감사 기록이 '보냄'을 남긴다
    async with outbox_sessions() as verify:
        incident = await _channel_incident(verify, "SLACK")
        assert incident is not None
        marker = f"{incident.id}:e{incident.episode_seq}"
        audits = (
            await verify.execute(
                select(AdminAuditLog).where(
                    AdminAuditLog.action == "ops_alert_email_sent", AdminAuditLog.target_id == marker
                )
            )
        ).scalars().all()
    assert [call["idempotency_key"] for call in sent] == [f"ops-channel-down:{marker}"]
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_failed_ops_email_is_retried_after_fifteen_minutes(outbox_sessions, monkeypatch) -> None:
    attempts: list[str] = []

    async def failing_send(**kwargs):
        attempts.append(kwargs["idempotency_key"])
        return False

    monkeypatch.setattr(notification_channel_health.ops_alert_email, "send_ops_alert", failing_send)
    notification_channel_health._last_ops_email_attempt.clear()
    dead = lambda _request: httpx.Response(404, text="no_service")  # noqa: E731

    await _dispatch_once(outbox_sessions, "OPS-QA-T10-EMAIL-2", dead)
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-EMAIL-2", dead, now=_NOW + timedelta(minutes=5))
    await _dispatch_once(outbox_sessions, "OPS-QA-T10-EMAIL-2", dead, now=_NOW + timedelta(minutes=16))

    # 처음과 15분이 지난 뒤에만 시도한다. 같은 키라 Resend가 중복을 막는다.
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && NOTIFICATION_OUTBOX_DATABASE_URL=... .venv/bin/python -m pytest tests/test_notification_outbox.py -k "ops_email or emailed_once" -v`
  Expected: FAIL with `AttributeError: module 'app.services.notification_channel_health' has no attribute 'ops_alert_email'`

- [ ] **Step 3: 최소 구현**

`notification_channel_health.py` — import 블록에 추가:

```python
from app.services import ops_alert_email
```

파일 끝에 추가:

```python


_OPS_EMAIL_ACTION = "ops_alert_email_sent"
# 메일 발송이 실패했을 때 다음 시도까지의 간격(프로세스 안). 같은 Idempotency-Key라 중복은 없다.
_OPS_EMAIL_RETRY_AFTER = timedelta(minutes=15)
_last_ops_email_attempt: dict[str, datetime] = {}


def _ops_email_marker(incident: Incident) -> str:
    return f"{incident.id}:e{incident.episode_seq}"


def _ops_email_lines(incident: Incident) -> tuple[str, ...]:
    # 본문은 결정적이어야 한다(같은 키에 다른 본문이면 Resend 409). 시각·건수를 넣지 않는다.
    return (
        "운영 Slack 웹훅이 알림을 받지 않습니다. Re:putation 운영 알림이 Slack에 전달되지 않고 보류되고 있습니다.",
        f"원인 코드: {incident.safe_error_code or '확인 필요'}",
        "Slack 웹훅 주소를 확인해 주세요. 주소가 살아나면 한 시간 안에 자동으로 복구되고, 보류된 알림을 다시 보냅니다.",
        f"운영센터: {settings.ADMIN_BASE_URL.rstrip('/')}/operations",
    )


async def email_dead_operator_channel(
    sessions: async_sessionmaker[AsyncSession],
    webhooks: Mapping[str, str],
    *,
    now: datetime,
) -> bool:
    """운영 채널이 죽었고 대신 받을 살아 있는 채널도 없으면 운영 경보 메일로 알린다.

    단일 채널 운영에서 운영 웹훅이 죽으면 채널 사고의 열림 알림조차 보낼 곳이 없어 보류된다.
    그 사실을 Slack 밖으로 알리는 경로다(2026-10 자율 운영 감사 F8). 사고 episode당 한 번 보내고
    보낸 사실은 감사 기록으로 남긴다. 실패하면 15분 뒤 같은 키로 다시 시도한다.
    """

    async with sessions() as db:
        unhealthy = await load_unhealthy_channels(db)
        incident = unhealthy.get(SLACK_CHANNEL)
        if incident is None:
            return False
        developer_live = bool(webhooks.get(SLACK_DEVELOPER_CHANNEL, "").strip()) and (
            SLACK_DEVELOPER_CHANNEL not in unhealthy
        )
        if developer_live:
            return False  # 개발 채널이 대신 받는다(`[채널 대체 전송]`).
        marker = _ops_email_marker(incident)
        already = await db.scalar(
            select(AdminAuditLog.id)
            .where(AdminAuditLog.action == _OPS_EMAIL_ACTION, AdminAuditLog.target_id == marker)
            .limit(1)
        )
    if already is not None:
        return False
    key = f"ops-channel-down:{marker}"
    last = _last_ops_email_attempt.get(key)
    if last is not None and now - last < _OPS_EMAIL_RETRY_AFTER:
        return False
    _last_ops_email_attempt[key] = now
    sent = await ops_alert_email.send_ops_alert(
        idempotency_key=key,
        title="운영 Slack 채널에 알림이 전달되지 않습니다",
        lines=_ops_email_lines(incident),
    )
    if not sent:
        return False
    async with sessions() as db:
        await write_audit_log(
            db,
            action=_OPS_EMAIL_ACTION,
            actor=_ACTOR,
            target_type="incident",
            target_id=marker,
            detail={"channel": SLACK_CHANNEL, "idempotency_key": key},
        )
        await db.commit()
    return True
```

`notification_delivery.py` — import 목록(`:24-30`)에 `email_dead_operator_channel,` 추가하고,
`dispatch_notification_batch`의 `for channel in sorted(sent_channels - unhealthy):` 루프 뒤,
`return DispatchResult(...)` 바로 앞에 추가:

```python
    # 운영 채널이 죽었고 대체 채널도 없으면 Slack 밖(운영 경보 메일)으로 한 번 알린다.
    await email_dead_operator_channel(sessions, webhooks, now=dispatch_at)
```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && NOTIFICATION_OUTBOX_DATABASE_URL=... .venv/bin/python -m pytest tests/test_notification_outbox.py -v`
  Expected: 전부 PASS(새 테스트 2개 포함). 순수 테스트(`_isolate_dispatch` 사용)는 DB 없이도 PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/services/notification_channel_health.py backend/app/services/notification_delivery.py backend/tests/test_notification_outbox.py && git commit -m "feat(notifications): 운영 Slack 채널이 죽으면 운영 경보 메일로 알린다"`

**계약 영향:** CLAUDE.md "Slack 채널은 운영 채널 하나다…" 문단에 "운영 채널 사고가 열렸는데 살아 있는 대체
채널이 없으면 `OPS_ALERT_EMAIL`로 사고 episode당 한 번 메일을 보낸다(감사 기록 `ops_alert_email_sent`)."를 추가.
outbox·사고 상태는 바꾸지 않는다(알림 성공과 저장 성공 분리 유지).

**위험·롤백:** 메일 발송이 매분 회차에 DB select 한 번을 더한다(채널 사고가 있을 때만 의미 있음). 운영 채널이
깜빡이면 episode가 늘 때마다 메일 한 통 — 채널 건강 모듈의 KST 하루 재알림 억제(`_open_notice_today`)와 달리
메일은 episode 기준이다. 깜빡임이 잦으면 `_open_notice_today`와 같은 하루 기준으로 좁힌다. 롤백은 revert.

---

### Task 2-7: Beat liveness — RedBeat tick이 파일을 갱신하고 `/live`가 그 나이를 본다

**Files:**
- Modify: `backend/app/workers/worker_liveness.py:25` (상수 추가)
- Create: `backend/app/workers/beat_scheduler.py`
- Modify: `backend/app/core/celery_app.py:229`
- Modify: `backend/app/workers/health_server.py:22-25, 54-77, 113-120`
- Modify: `backend/docker-entrypoint.sh:32-35`
- Modify: `backend/app/core/config.py:577-578` 근처, `.env.example`, `.env.production.example`
- Test: `backend/tests/test_worker_health_server.py`, `backend/tests/test_beat_scheduler.py`

**조사 결과:**
- Beat 분기는 `python -m app.workers.health_server &` 뒤 `exec celery ... beat`(`docker-entrypoint.sh:32-35`).
  `/live`는 부모(=exec된 celery beat) 생존만 본다(`health_server.py:28-33, 73-74`). 감사 주장 확인.
- Celery에는 beat tick 신호가 없다(`beat_init`/`beat_embedded_init`뿐). RedBeat 0.13 계열 `RedBeatScheduler.tick`
  (venv `redbeat/schedulers.py:524-538`)이 매 루프 **락을 연장한 뒤** due 엔트리를 보낸다. 루프 간격은
  `beat_max_loop_interval=30`(`celery_app.py:233-236`) → 정상이면 30초마다 tick. 그래서 `tick`을 덮어쓴 하위
  클래스가 tick 끝에 파일을 touch하면 "스케줄 루프가 돌았다"의 인스턴스 로컬 증거가 된다.
- **감사 제안과 다르다(채택하지 않음):** "/live에서 RedBeat 락 소유·엔트리 신선도 확인"은 liveness를 Redis에
  의존시킨다 — Redis 장애 동안 Beat가 재시작을 반복한다(재시작해도 고쳐지지 않는 원인). 대신
  ① 락을 잃으면 `tick`의 `lock.extend`가 `LockNotOwnedError`를 던져 beat 프로세스가 죽고(Celery `Service.start`는
  KeyboardInterrupt/SystemExit만 잡는다) Cloud Run이 컨테이너를 다시 띄운다. ② 루프가 멈추면(블로킹) tick 파일이
  낡아 `/live`가 실패한다. 두 경우 모두 Redis를 읽지 않고 처리된다.
- 지시된 기본값(stale 900초)은 "5분 canary에 걸어 쓴다"는 가정이었다. tick은 30초마다이므로 600초(워커와 같은 값,
  락 TTL 300초의 2배)로 둔다. **기동 유예는 900초**: 강제 종료된 옛 Beat의 락은 TTL(`redbeat_lock_timeout=300`,
  `celery_app.py:237`)까지 남고, 새 Beat는 `beat_init`의 `lock.acquire()`(blocking, `schedulers.py:565-586`)에서
  최대 300초 기다리며 그동안 tick이 없다. 900초 유예가 이 대기를 덮는다.
- Beat의 Cloud Run liveness probe는 이미 `/live`(`terraform/cloudrun.tf:581-586`)이고 startup probe는 `/ready`.
  `deploy.sh`의 `gcloud run deploy`는 probe를 건드리지 않아 보존된다 → **Terraform 변경 불필요**.
- 죽여도 안전한가: SIGTERM이면 RedBeat `close()`가 락을 풀고(`schedulers.py:540-546`) 새 Beat가 바로 인수한다.
  응답 없는 Beat가 SIGKILL되면 최대 300초 스케줄 공백 뒤 첫 tick에서 밀린 엔트리를 한 번씩 보낸다(중복 아님).
  `beat_max_instances=1`(`terraform/variables.tf:205-209`)이라 동시 Beat는 롤아웃 겹침뿐이고 락이 막는다.
- 기존 `release_e2e`(`scripts/release_e2e/runtime.py:180`)는 `redbeat.RedBeatScheduler`를 명시하므로 영향 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_beat_scheduler.py` (새 파일):

```python
"""Beat liveness — RedBeat tick이 인스턴스 로컬 파일을 갱신한다(감사 F8)."""

from pathlib import Path

from redbeat.schedulers import RedBeatScheduler

from app.core.celery_app import celery_app
from app.workers import beat_scheduler, worker_liveness


def test_tick_touches_the_beat_liveness_file_and_keeps_the_interval(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)
    monkeypatch.setattr(RedBeatScheduler, "tick", lambda self, *args, **kwargs: 7.5)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)

    assert scheduler.tick() == 7.5
    assert target.exists()


def test_failed_tick_does_not_touch_the_file(monkeypatch, tmp_path: Path):
    target = tmp_path / "beat-tick"
    monkeypatch.setattr(worker_liveness, "BEAT_TICK_FILE", target)

    def lost_lock(self, *args, **kwargs):
        raise RuntimeError("lock not owned")

    monkeypatch.setattr(RedBeatScheduler, "tick", lost_lock)
    scheduler = object.__new__(beat_scheduler.LivenessRedBeatScheduler)

    try:
        scheduler.tick()
    except RuntimeError:
        pass
    # 락을 잃은 tick은 증거를 남기지 않는다 — 예외가 beat를 끝내고 Cloud Run이 다시 띄운다.
    assert not target.exists()


def test_celery_beat_uses_the_liveness_scheduler():
    assert celery_app.conf.beat_scheduler == "app.workers.beat_scheduler.LivenessRedBeatScheduler"


def test_beat_entrypoint_starts_the_health_server_with_tick_checks():
    entrypoint = (Path(__file__).resolve().parents[1] / "docker-entrypoint.sh").read_text()
    beat_branch = entrypoint.split("  beat)", 1)[1].split(";;", 1)[0]
    assert "python -m app.workers.health_server --beat-tick &" in beat_branch
```

`backend/tests/test_worker_health_server.py` 끝에 추가:

```python
def test_beat_live_fails_when_the_tick_file_is_stale(monkeypatch, tmp_path) -> None:
    import os

    tick = tmp_path / "beat-tick"
    tick.touch()
    os.utime(tick, (1_000.0, 1_000.0))
    monkeypatch.setattr(health_server.worker_liveness, "BEAT_TICK_FILE", tick)
    monkeypatch.setattr(health_server, "_CHECK_BEAT_TICK", True)
    monkeypatch.setattr(health_server, "_STARTED_AT", 0.0)
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STALE_SECONDS", 600)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STARTUP_GRACE_SECONDS", 900)
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 601)

    assert health_server.is_live() is False

    os.utime(tick, (1_500.0, 1_500.0))
    assert health_server.is_live() is True


def test_beat_live_passes_inside_startup_grace_while_waiting_for_the_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(health_server.worker_liveness, "BEAT_TICK_FILE", tmp_path / "missing")
    monkeypatch.setattr(health_server, "_CHECK_BEAT_TICK", True)
    monkeypatch.setattr(health_server, "_STARTED_AT", 1_000.0)
    monkeypatch.setattr(health_server, "_parent_process_alive", lambda: True)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STALE_SECONDS", 600)
    monkeypatch.setattr(health_server.settings, "BEAT_LIVENESS_STARTUP_GRACE_SECONDS", 900)

    # 옛 Beat의 락이 TTL(300초)까지 남아 tick이 아직 없다 — 유예 안이면 살아 있다.
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 400)
    assert health_server.is_live() is True
    monkeypatch.setattr(health_server.time, "time", lambda: 1_000.0 + 901)
    assert health_server.is_live() is False
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_beat_scheduler.py tests/test_worker_health_server.py -v`
  Expected: FAIL with `ImportError: cannot import name 'beat_scheduler'` 및 `AttributeError: ... '_CHECK_BEAT_TICK'`

  주의: `worker_liveness.consumer_alive`는 `now`를 받지 않으면 `time.time()`을 쓴다. health_server 테스트는
  `health_server.time.time`을 바꾸므로 구현에서 `now=time.time()`을 명시적으로 넘긴다(아래).

- [ ] **Step 3: 최소 구현**

`worker_liveness.py` — `HEARTBEAT_FILE = ...` 줄 뒤에 추가:

```python
# Beat 스케줄 루프(RedBeat tick)의 인스턴스 로컬 생존 증거. `beat_scheduler.LivenessRedBeatScheduler`가 쓴다.
BEAT_TICK_FILE = Path(tempfile.gettempdir()) / "reputation-beat-tick"
```

`backend/app/workers/beat_scheduler.py` (새 파일):

```python
"""RedBeat 스케줄러 + 스케줄 루프 생존 증거(2026-10 자율 운영 감사 F8).

Beat의 Cloud Run `/live`는 부모 프로세스가 살아 있는지만 봤다. 스케줄 루프가 Redis 등에 막혀
멈춰도 프로세스는 살아 있어 사람이 재기동해야 했다. RedBeat는 매 루프(최대 30초,
`beat_max_loop_interval`)마다 `tick`에서 분산 락을 연장하고 due 엔트리를 보낸다. tick이 끝날 때마다
인스턴스 로컬 파일을 갱신하면 health server가 그 나이로 멈춘 루프를 알아챈다.

락을 잃으면 `tick`의 `lock.extend`가 예외를 던져 beat 프로세스가 끝난다 — 그 tick은 파일을 갱신하지 않는다.
liveness가 Redis를 직접 읽지 않으므로 Redis 장애 동안 재시작을 반복하지 않는다.
"""

from __future__ import annotations

from typing import Any

from redbeat.schedulers import RedBeatScheduler

from app.workers import worker_liveness


class LivenessRedBeatScheduler(RedBeatScheduler):
    def tick(self, *args: Any, **kwargs: Any) -> float:
        interval = super().tick(*args, **kwargs)
        worker_liveness.touch_heartbeat(path=worker_liveness.BEAT_TICK_FILE)
        return interval
```

`celery_app.py:229` — `beat_scheduler="redbeat.RedBeatScheduler",`를 다음으로 바꾼다:

```python
    # RedBeat + tick마다 인스턴스 로컬 생존 파일 갱신(Beat /live가 그 나이를 본다).
    beat_scheduler="app.workers.beat_scheduler.LivenessRedBeatScheduler",
```

`health_server.py`:
- `_CHECK_WORKER_HEARTBEAT = False` 아래에 `_CHECK_BEAT_TICK = False` 추가(주석: "`--beat-tick`(beat 분기만)일 때 /live가 RedBeat tick 파일의 나이를 본다.").
- `_worker_consumer_alive` 뒤에 추가:

```python
def _beat_ticking() -> bool:
    if not _CHECK_BEAT_TICK:
        return True
    alive = worker_liveness.consumer_alive(
        started_at=_STARTED_AT,
        stale_seconds=settings.BEAT_LIVENESS_STALE_SECONDS,
        startup_grace_seconds=settings.BEAT_LIVENESS_STARTUP_GRACE_SECONDS,
        path=worker_liveness.BEAT_TICK_FILE,
        now=time.time(),
    )
    if not alive:
        # Cloud Run이 이 응답으로 Beat를 재시작한다 — 재시작 사유를 남긴다.
        logger.warning(
            "beat_liveness_failed reason=tick_stale age_seconds=%s stale_seconds=%s",
            worker_liveness.heartbeat_age_seconds(path=worker_liveness.BEAT_TICK_FILE),
            settings.BEAT_LIVENESS_STALE_SECONDS,
        )
    return alive
```

- `is_live`를 `return _parent_process_alive() and _worker_consumer_alive() and _beat_ticking()`로.
- `main`:

```python
def main(argv: list[str] | None = None) -> None:
    global _CHECK_WORKER_HEARTBEAT, _CHECK_BEAT_TICK
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-heartbeat", action="store_true")
    parser.add_argument("--beat-tick", action="store_true")
    args = parser.parse_args(argv)
    _CHECK_WORKER_HEARTBEAT = args.worker_heartbeat
    _CHECK_BEAT_TICK = args.beat_tick
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("worker health server listening on :%d", port)
    server.serve_forever()
```

`docker-entrypoint.sh` beat 분기:

```bash
  beat)
    # --beat-tick: /live(Cloud Run liveness probe)가 RedBeat tick 파일의 나이도 본다.
    # 스케줄 루프가 멈추면 BEAT_LIVENESS_STALE_SECONDS 뒤 재시작된다(기동 유예는 락 대기를 덮는다).
    python -m app.workers.health_server --beat-tick &
    exec celery -A app.core.celery_app beat --loglevel=info
    ;;
```

`config.py` — `WORKER_LIVENESS_STARTUP_GRACE_SECONDS: int = 300` 아래에 추가:

```python
    # Beat 스케줄 루프(RedBeat tick, 30초 주기)가 이 시간(초) 멈추면 /live 실패 → Cloud Run이 재시작한다.
    BEAT_LIVENESS_STALE_SECONDS: int = 600
    # 기동 직후 첫 tick 전까지 /live를 통과시키는 유예(초). 강제 종료된 옛 Beat의 RedBeat 락은
    # TTL(300초)까지 남고 새 Beat는 그동안 락을 기다리며 tick하지 않는다 — 그 대기를 덮는다.
    BEAT_LIVENESS_STARTUP_GRACE_SECONDS: int = 900
```

`.env.example`·`.env.production.example` — 각각 `WORKER_LIVENESS_STARTUP_GRACE_SECONDS=300` 줄 뒤에 추가:

```
# Beat 스케줄 루프(RedBeat tick)가 이 시간(초) 멈추면 /live 실패 → Cloud Run이 Beat를 재시작한다.
BEAT_LIVENESS_STALE_SECONDS=600
# 기동 직후 첫 tick 전까지 /live를 통과시키는 유예(초). 옛 Beat 락(TTL 300초) 대기를 덮는다.
BEAT_LIVENESS_STARTUP_GRACE_SECONDS=900
```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_beat_scheduler.py tests/test_worker_health_server.py tests/test_production_readiness.py::test_readiness_covers_every_declared_schedule_and_routed_task -v`
  Expected: 전부 PASS.
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -k "env_templates or matches_config_defaults" -v` → PASS.
  수동 확인(로컬 Redis): `cd backend && REDIS_URL=redis://localhost:6379/0 .venv/bin/celery -A app.core.celery_app beat --loglevel=info` 실행 후
  `ls -l --time-style=+%T /tmp/reputation-beat-tick`(macOS는 `$TMPDIR`)이 30초 안에 갱신되는지 본다.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/worker_liveness.py backend/app/workers/beat_scheduler.py backend/app/core/celery_app.py backend/app/workers/health_server.py backend/docker-entrypoint.sh backend/app/core/config.py .env.example .env.production.example backend/tests/test_beat_scheduler.py backend/tests/test_worker_health_server.py && git commit -m "fix(beat): 스케줄 루프가 멈추면 /live가 실패해 Cloud Run이 Beat를 재시작한다"`

**계약 영향:** CLAUDE.md "워커·Beat·발행자의 Redis 연결은…" 문단의 "연결을 잃은 뒤 … 워커는 liveness만이 끝낸다" 다음에
"Beat도 같다 — RedBeat `tick`마다 `/tmp`의 tick 파일을 갱신하고(`beat_scheduler.LivenessRedBeatScheduler`), Beat health
server(`--beat-tick`)는 그 파일이 `BEAT_LIVENESS_STALE_SECONDS`(600) 넘게 낡으면 `/live`를 실패시킨다(기동 유예
`BEAT_LIVENESS_STARTUP_GRACE_SECONDS` 900 — 옛 락 TTL 대기). liveness는 Redis를 읽지 않는다."를 추가.
RedBeat 스케줄 정의는 바뀌지 않으므로 `REDBEAT_SCHEDULE_VERSION` 재조정 불필요.

**위험·롤백:** 정상 상태에서 tick이 600초 넘게 막히는 경우(예: due 엔트리 대량 발송 중 브로커 지연)가 재시작을
일으킬 수 있다 — 브로커 소켓 timeout 30초·`max_retries` 기본 3이라 한 엔트리 최대 수 분이다. 롤백은 revert
(또는 Beat env에 `BEAT_LIVENESS_STALE_SECONDS=86400`). 배포는 Beat 재시작을 수반한다 — 배치 시간대 밖.

---

### Task 2-8a: 비용 가드 fail-open 첫 발생 — 프로세스·카테고리당 ERROR 1회 + 개발 사고 1건, 정상화되면 자동 복구

**Files:**
- Create: `backend/app/services/cost_guard_health.py`
- Modify: `backend/app/services/cost_guard.py:458-471`(check_and_increment), `:575-601`(reserve)
- Modify: `backend/app/services/incident_types.py:56-69`(`_DEVELOPER_INCIDENT_TYPES`)
- Modify: `backend/app/services/notification_copy.py:101` 근처(`_CATALOG`)
- Test: `backend/tests/test_cost_guard.py`

**조사 결과:**
- fail-open 두 곳: `check_and_increment`(`cost_guard.py:464-471`)와 `reserve`(`:595-601`). 둘 다 WARNING만 남긴다.
  WARNING은 어떤 GCP 경보에도 걸리지 않는다(경보는 worker/beat ERROR). 감사 주장 확인.
- 같은 파일에 "DB 사고 + outbox"를 별도 세션으로 남기는 패턴이 이미 있다(`_enqueue_durable_cost_alert`, `:841-874`,
  실패해도 가드 결정에 영향 없음). 같은 방식으로 따른다.
- 테스트 가드: 앱 코드가 연결 오류를 삼켜도 `tests/conftest.py`의 표지 호스트 가드가 실패시킨다 → fail-open을
  타는 기존 테스트(`test_redis_failure_fails_open` `:453`, `test_zero_call_kill_switch_check_remains_fail_open` `:507`)는
  DB 경로를 바꿔 끼워야 한다. 그래서 DB 함수(`_open_fail_open_incident`, `_recover_fail_open_incident`)를 분리하고
  `tests/test_cost_guard.py`에 autouse 픽스처를 둔다. 다른 테스트 파일에서 fail-open을 타는지 Step 4의 전체 실행으로 확인.
- 사고 복구: 같은 프로세스가 그 카테고리에서 다시 Redis 판정에 성공하면(허용·차단 모두 "가드가 동작함") 한 번만
  DB에 접근해 OPEN→RETRYING→RECOVERED→자동 확인한다(`recover_channel_incident`, `notification_channel_health.py:166-197`
  와 같은 순서). 열림 알림을 보낸 episode에만 복구 알림을 짝짓는다(`open_notice_exists`, `dependency_incident_helpers.py:72-86`).
- 사고 종류는 개발 대상(`_DEVELOPER_INCIDENT_TYPES`) — AE가 Redis를 고칠 수 없다. 운영자 할 일 집계에서 빠진다
  (`operator_action.is_operator_todo`는 운영 대상만 센다).
- **감사의 "달러 기준 일 상한 보조"는 지금 데이터로 불가능하다:** `provider_usage_events`에 비용(USD) 컬럼이 없다
  (`models/usage.py:74-130` — 토큰·검색·이미지 단위만). 가격표 없이 달러를 계산할 수 없다. 별도 과제(가격표 버전 +
  일 합계 뷰)로 남긴다(Task 2-8c 참조).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cost_guard.py`

import에 `from app.services import audit_log, cost_guard, cost_guard_health`(기존 줄 수정)와 `import logging` 추가.
`alerts` 픽스처 위에 추가:

```python
class FailOpenRecorder:
    def __init__(self):
        self.opened: list[tuple[str, str]] = []
        self.recovered: list[str] = []

    async def open(self, category, error_name, *, now):
        self.opened.append((category, error_name))

    async def recover(self, category, *, now):
        self.recovered.append(category)


@pytest.fixture(autouse=True)
def fail_open_incidents(monkeypatch):
    """fail-open 사고는 별도 DB 세션으로 남긴다 — 단위 테스트에서는 기록만 본다."""
    recorder = FailOpenRecorder()
    monkeypatch.setattr(cost_guard_health, "_open_fail_open_incident", recorder.open)
    monkeypatch.setattr(cost_guard_health, "_recover_fail_open_incident", recorder.recover)
    cost_guard_health._REPORTED.clear()
    yield recorder
    cost_guard_health._REPORTED.clear()
```

파일 끝에 추가:

```python
async def test_first_fail_open_per_category_logs_error_and_opens_one_incident(
    monkeypatch, alerts, fail_open_incidents, caplog
):
    _set_limits(monkeypatch, daily=10, monthly=10)
    _set_limits(monkeypatch, category="image", daily=10, monthly=10)
    redis = FakeRedis()
    redis.fail = True

    with caplog.at_level(logging.WARNING, logger="app.services.cost_guard_health"):
        for _ in range(3):
            assert (await cost_guard.check_and_increment("content", redis_client=redis)).allowed
        assert (await cost_guard.reserve("image", redis_client=redis)).allowed

    assert fail_open_incidents.opened == [("content", "RedisError"), ("image", "RedisError")]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert [r.getMessage().split(" ")[0] for r in errors] == ["cost_guard_fail_open", "cost_guard_fail_open"]
    # 두 번째 이후 같은 카테고리는 WARNING만 — 사고를 다시 열지 않는다.
    assert sum(1 for r in caplog.records if r.levelname == "WARNING") == 2


async def test_guard_recovery_closes_the_fail_open_incident_once(monkeypatch, alerts, fail_open_incidents):
    _set_limits(monkeypatch, daily=10, monthly=10)
    redis = FakeRedis()
    redis.fail = True
    await cost_guard.check_and_increment("content", redis_client=redis)

    redis.fail = False
    await cost_guard.check_and_increment("content", redis_client=redis)
    await cost_guard.check_and_increment("content", redis_client=redis)

    assert fail_open_incidents.recovered == ["content"]
    assert "content" not in cost_guard_health._REPORTED


async def test_fail_open_incident_failure_never_blocks_the_guard_decision(monkeypatch, alerts):
    _set_limits(monkeypatch, daily=10, monthly=10)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(cost_guard_health, "_open_fail_open_incident", broken)
    redis = FakeRedis()
    redis.fail = True

    decision = await cost_guard.check_and_increment("content", redis_client=redis)

    assert decision.allowed is True
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_cost_guard.py -k "fail_open or recovery_closes" -v`
  Expected: FAIL with `ImportError: cannot import name 'cost_guard_health'`

- [ ] **Step 3: 최소 구현**

`backend/app/services/cost_guard_health.py` (새 파일):

```python
"""비용 가드가 상한을 확인하지 못하고 통과시킨(fail-open) 사실을 개발 사고로 남긴다.

Redis 장애 때 비용 가드는 가용성을 위해 호출을 허용한다(fail-open). 그동안 비용 상한이 없다는 뜻인데
지금까지는 WARNING 한 줄뿐이라 아무도 몰랐다(2026-10 자율 운영 감사 F8). 이제:

- 프로세스·카테고리마다 첫 fail-open에 ERROR 한 줄(GCP worker ERROR 경보가 이메일로 승격)과 개발 대상
  사고 하나(`COST_GUARD_FAIL_OPEN`)를 남긴다. 사고 키는 카테고리 단위라 여러 프로세스가 같은 사고를 만진다.
- 같은 프로세스에서 그 카테고리의 가드가 다시 Redis로 판정하면 사고를 복구한다.
- 사고 기록 실패는 가드 결정에 영향을 주지 않는다(`cost_guard._enqueue_durable_cost_alert`와 같은 원칙).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.dependency_incident_helpers import open_notice_exists
from app.services.incident_types import (
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_type_of,
)
from app.services.incidents import (
    auto_acknowledge_incident,
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
)
from app.services.notification_store import enqueue_notification

logger = logging.getLogger(__name__)

FAIL_OPEN_INCIDENT_TYPE = "COST_GUARD_FAIL_OPEN"
_ACTOR = "cost-guard"
# 이 프로세스가 사고로 알린 카테고리. 워커 자식은 50개 작업마다 바뀌므로 자연히 다시 관측된다.
_REPORTED: set[str] = set()


def _request(category: str, error_name: str) -> IncidentOpenRequest:
    return IncidentOpenRequest(
        pipeline="cost_guard",
        object_type="fail_open",
        object_id=category,
        fingerprint=IncidentFingerprint.BROKER_UNAVAILABLE,
        incident_type=FAIL_OPEN_INCIDENT_TYPE,
        severity=IncidentSeverity.HIGH,
        customer_impact=(
            "비용 가드가 Redis를 읽지 못해 자동 AI 호출을 상한 확인 없이 허용하고 있습니다."
        ),
        source_type="COST_GUARD",
        next_action=(
            "Redis(Memorystore) 상태를 확인해 주세요. 비용 가드가 다시 판정하면 자동으로 닫힙니다."
        ),
        admin_path="/operations",
        source_id=f"{category}:fail_open",
        safe_error_code=FAIL_OPEN_INCIDENT_TYPE,
        safe_error_message=f"category={category} error={error_name}",
    )


def _projection(incident: Incident) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident_id=incident.id,
        hospital_name="시스템 공통 작업",
        severity=incident.severity,
        customer_impact=incident.customer_impact,
        next_action=incident.next_action,
        admin_path=incident.admin_path,
        owner_label="미지정",
        sla_label="확인 필요",
        version=incident.version,
        problem=incident.safe_error_message or "비용 가드가 상한을 확인하지 못했습니다.",
        episode_seq=incident.episode_seq,
        incident_type=incident_type_of(incident),
    )


async def _open_fail_open_incident(category: str, error_name: str, *, now: datetime) -> None:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await open_or_touch_incident(
            db, _request(category, error_name), actor=_ACTOR, reason="cost guard failed open", now=now
        )
        # 열림 알림의 dedupe 키는 episode 단위라 여러 프로세스가 불러도 한 번만 쌓인다.
        await enqueue_notification(
            db, build_open_incident_notification(_projection(incident), settings.ADMIN_BASE_URL), now=now
        )
        await db.commit()


async def _recover_fail_open_incident(category: str, *, now: datetime) -> None:
    key = build_incident_key("cost_guard", "fail_open", category, IncidentFingerprint.BROKER_UNAVAILABLE)
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == key,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
        if incident is None:
            return
        current: object = incident
        if incident.state == IncidentState.OPEN.value:
            current = await mark_retrying(
                db, incident.id, expected_version=incident.version, actor=_ACTOR,
                reason="cost guard judged with redis again",
            )
        if not isinstance(current, Incident) or current.state != IncidentState.RETRYING.value:
            return
        recovered = await mark_recovered(
            db, current.id, expected_version=current.version, observed_success=True,
            actor=_ACTOR, reason="cost guard judged with redis again", now=now,
        )
        if not isinstance(recovered, Incident):
            return
        if await open_notice_exists(db, recovered.id):
            await enqueue_notification(
                db,
                build_recovered_incident_notification(_projection(recovered), settings.ADMIN_BASE_URL),
                now=now,
            )
        await auto_acknowledge_incident(
            db, recovered.id, expected_version=recovered.version, actor=_ACTOR,
            reason="cost guard judged with redis again", now=now,
        )
        await db.commit()


async def report_fail_open(category: str, error_name: str) -> None:
    """fail-open 관측. 프로세스·카테고리당 첫 번째만 ERROR와 사고, 이후는 WARNING."""
    if category in _REPORTED:
        logger.warning("cost_guard fail-open (redis unavailable): category=%s error=%s", category, error_name)
        return
    _REPORTED.add(category)
    logger.error(
        "cost_guard_fail_open category=%s error=%s — 비용 상한 없이 호출을 허용하는 중", category, error_name
    )
    try:
        await _open_fail_open_incident(category, error_name, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001 — 사고 기록 실패가 가드 결정을 바꾸지 않는다.
        logger.warning("cost_guard fail-open incident not recorded: category=%s", category)


async def report_guard_healthy(category: str) -> None:
    """가드가 Redis로 판정했다. 이 프로세스가 fail-open을 알린 카테고리면 사고를 닫는다."""
    if category not in _REPORTED:
        return
    _REPORTED.discard(category)
    try:
        await _recover_fail_open_incident(category, now=datetime.now(UTC))
    except Exception:  # noqa: BLE001
        logger.warning("cost_guard fail-open recovery not recorded: category=%s", category)
```

`cost_guard.py` — import에 `from app.services import cost_guard_health` 추가.

`check_and_increment`:
- `if not allowed and blocked_scope == "monthly":` 바로 앞(= Lua 판정 직후, `new_monthly = int(reservation[3])` 다음 줄)에
  `await cost_guard_health.report_guard_healthy(category)` 추가.
- `except (OSError, RedisError, RuntimeError, TimeoutError) as exc:` 블록(`:464-471`)을 다음으로 바꾼다:

```python
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        # fail-open: Redis 장애가 콘텐츠/이미지/측정 파이프라인을 멈추게 하지 않는다(가용성 우선).
        # 첫 발생은 ERROR와 개발 사고로 남긴다(cost_guard_health).
        await cost_guard_health.report_fail_open(category, exc.__class__.__name__)
        return CostGuardDecision(True, None)
```

`reserve`:
- `status = int(result[0])` 다음 줄에 `await cost_guard_health.report_guard_healthy(category)` 추가.
- `:595-601`의 except 블록 본문을 다음으로 바꾼다:

```python
    except (OSError, RedisError, RuntimeError, TimeoutError) as exc:
        await cost_guard_health.report_fail_open(category, exc.__class__.__name__)
        return CostGuardDecision(True, None)
```

(킬스위치 확인에서 터진 Redis 오류도 이 except로 온다 — 같은 fail-open이므로 같은 보고가 맞다.)

`incident_types.py` `_DEVELOPER_INCIDENT_TYPES`에 추가(주석 포함):

```python
        # 비용 가드가 Redis를 읽지 못해 상한 확인 없이 허용 중. AE가 고칠 수 없는 인프라 사실이다.
        "COST_GUARD_FAIL_OPEN",
        # 공급자 사용량 기록 spool이 넘쳐 오래된 기록을 버렸다(provider_usage, Task 2-8b).
        "PROVIDER_USAGE_SPOOL_TRUNCATED",
```

`notification_copy.py` `_CATALOG`의 `"COST_GUARD_LIMIT_REACHED"` 항목 뒤에 추가:

```python
    "COST_GUARD_FAIL_OPEN": ActionCopy(
        "비용 가드 상한 확인 불가",
        "개발 담당자는 Redis 연결을 확인해 주세요. 그동안 자동 AI 호출은 상한 확인 없이 계속됩니다.",
        "운영센터 비용 보기",
    ),
    "PROVIDER_USAGE_SPOOL_TRUNCATED": ActionCopy(
        "AI 사용량 기록 일부 유실",
        "개발 담당자는 DB 저장 실패 원인을 확인해 주세요. 버려진 사용량 기록은 되살릴 수 없습니다.",
        "운영센터 비용 보기",
    ),
```

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_cost_guard.py tests/test_cost_guard_admin_bypass.py tests/test_notification_outbox.py -k "not redis_integration" -v`
  Expected: 새 테스트 3개 PASS, 기존 fail-open 테스트 PASS(autouse 픽스처).
  Run(전체, CI env export 후): `make test-backend-local`
  Expected: 통과. 실패하는 테스트가 "REDIS_URL/DATABASE_URL is not set … connected through" 형태로 cost_guard_health를
  가리키면, 그 파일에 위 `fail_open_incidents`와 같은 픽스처를 추가한다(대상 파일 찾기:
  `grep -rln "cost_guard" backend/tests`).

- [ ] **Step 5: 커밋**
  `git add backend/app/services/cost_guard_health.py backend/app/services/cost_guard.py backend/app/services/incident_types.py backend/app/services/notification_copy.py backend/tests/test_cost_guard.py && git commit -m "fix(cost-guard): fail-open 첫 발생을 ERROR와 개발 사고로 남기고 정상화되면 닫는다"`

**계약 영향:** CLAUDE.md "Redis 비용 가드와 single-flight는 장애 시 fail-open이므로 절대 지출 상한이라고 설명하지 않는다"
뒤에 "fail-open은 프로세스·카테고리당 첫 발생에 ERROR와 개발 사고(`COST_GUARD_FAIL_OPEN`) 하나를 남기고, 그 카테고리가
다시 Redis로 판정되면 자동 복구한다."를 추가. fail-open 동작(허용) 자체는 바뀌지 않는다.

**위험·롤백:** Redis 전체 장애 때는 외부 감시(beat_down)와 함께 Slack 개발 확인 알림이 하나 더 생긴다(카테고리마다
episode당 1건, 최대 5건). 너무 많으면 카테고리 대신 `object_id="all"`로 묶는다. 롤백은 revert(데이터 변경 없음 — 남은 사고는 Admin에서 확인 처리).

---

### Task 2-8b: 사용량 spool이 1만 건을 넘겨 잘리면 개발 사고 — DB가 돌아온 뒤 drain이 연다

**Files:**
- Modify: `backend/app/services/provider_usage.py:30-33`(상수), `:255-270`(`_defer`), 새 함수 `report_dropped_usage`
- Modify: `backend/app/workers/provider_usage_recovery.py:14-20`
- Test: `backend/tests/test_provider_usage.py`, `backend/tests/test_provider_usage_recovery.py`

**조사 결과:**
- `_defer`는 `zremrangebyrank(index, 0, -10001)`로 1만 건을 넘는 가장 오래된 인덱스를 조용히 지운다
  (`provider_usage.py:255-270`). 지워진 인덱스의 payload 키는 TTL까지 남지만 replay가 인덱스만 보므로 사실상 유실.
- **감사와 다르다(설계 조정):** spool은 "DB 저장 실패" 때만 쓰인다(`record_attempt` `:534-544`). 잘리는 그 순간은
  DB가 죽어 있으므로 사고 행을 쓸 수 없다. 그래서 ① 잘린 순간에는 ERROR 로그와 Redis 카운터
  `provider_usage:recovery:dropped`만 남기고, ② 1분마다 도는 drain(`provider_usage_recovery.drain`, Worker)이 DB가
  돌아온 뒤 카운터를 읽어 사고를 연다. 사고 기록이 실패하면 카운터를 되돌려 다음 drain이 다시 시도한다.
- `replay_deferred_attempts`의 반환 dict를 기존 테스트가 정확히 비교한다(`tests/test_provider_usage.py:338`) →
  replay는 바꾸지 않고 drain에서 별도 함수로 부른다. 기존 drain 테스트(`tests/test_provider_usage_recovery.py:6-21`)는
  새 함수를 바꿔 끼우도록 고친다.
- 사고 키는 KST 날짜 단위(하루에 한 건, 같은 날 추가 유실은 같은 사고를 touch). 유실은 되살릴 수 없으므로 자동 복구
  하지 않는다 — 개발 대상이라 운영자 할 일에 잡히지 않고 "개발 확인"으로만 보인다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_provider_usage.py` 끝에 추가:

```python
class FakeSpoolRedis:
    """_defer가 쓰는 transaction pipeline과 카운터만 흉내 낸다."""

    def __init__(self, existing: int):
        self.index_size = existing
        self.counters: dict[str, int] = {}
        self.values: dict[str, object] = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def incrby(self, key, amount):
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops: list[str] = []

    def set(self, key, value, ex=None):
        self.ops.append("set")
        return self

    def zadd(self, _key, mapping):
        self.ops.append("zadd")
        self.redis.index_size += len(mapping)
        return self

    def zremrangebyrank(self, _key, _start, stop):
        self.ops.append("zrem")
        return self

    def get(self, key):
        self.ops.append(("get", key))
        return self

    def delete(self, key):
        self.ops.append(("delete", key))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op == "zrem":
                keep = provider_usage._RECOVERY_MAX_ITEMS
                removed = max(0, self.redis.index_size - keep)
                self.redis.index_size -= removed
                results.append(removed)
            elif isinstance(op, tuple) and op[0] == "get":
                results.append(self.redis.counters.get(op[1]))
            elif isinstance(op, tuple) and op[0] == "delete":
                results.append(1 if self.redis.counters.pop(op[1], None) is not None else 0)
            else:
                results.append(True)
        return results


async def test_spool_truncation_is_counted_and_logged(monkeypatch, caplog):
    db = FakeDB()
    await provider_usage.record_attempt(
        provider="openai", model="m", workflow="SOV_ANSWER", cost_category="sov",
        hospital_id=uuid.uuid4(), logical_call_id="c", http_attempt=1, usage=None,
        idempotency_key="k-trunc", db=db,
    )
    event = next(value for value in db.added if isinstance(value, ProviderUsageEvent))
    redis = FakeSpoolRedis(existing=provider_usage._RECOVERY_MAX_ITEMS)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_client(_injected=None):
        yield redis

    monkeypatch.setattr(provider_usage, "_recovery_redis_client", fake_client)

    assert await provider_usage._defer(event) is True
    assert redis.counters[provider_usage._RECOVERY_DROPPED_KEY] == 1
    assert any(
        r.levelname == "ERROR" and r.getMessage().startswith("provider_usage_spool_truncated")
        for r in caplog.records
    )


async def test_dropped_usage_opens_one_developer_incident_and_resets_the_counter(monkeypatch):
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 7
    opened: list[int] = []

    async def fake_open(dropped, *, now):
        opened.append(dropped)

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", fake_open)

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 7
    assert opened == [7]
    assert provider_usage._RECOVERY_DROPPED_KEY not in redis.counters
    # 다음 drain에는 할 일이 없다.
    assert await provider_usage.report_dropped_usage(redis_client=redis) == 0
    assert opened == [7]


async def test_incident_failure_puts_the_dropped_count_back(monkeypatch):
    redis = FakeSpoolRedis(existing=0)
    redis.counters[provider_usage._RECOVERY_DROPPED_KEY] = 3

    async def broken(_dropped, *, now):
        raise RuntimeError("db down")

    monkeypatch.setattr(provider_usage, "_open_spool_truncation_incident", broken)

    assert await provider_usage.report_dropped_usage(redis_client=redis) == 0
    assert redis.counters[provider_usage._RECOVERY_DROPPED_KEY] == 3
```

`tests/test_provider_usage_recovery.py`의 기존 테스트를 다음으로 바꾼다:

```python
def test_drain_requires_signed_dispatch_and_replays_bounded_batch(monkeypatch):
    calls = []

    def require_dispatch(_task, purpose):
        calls.append(("dispatch", purpose))

    async def replay(*, limit):
        calls.append(("replay", limit))
        return {"recovered": 2, "missing": 1, "failed": 0}

    async def report_dropped():
        calls.append(("report_dropped",))
        return 0

    monkeypatch.setattr(provider_usage_recovery, "require_dispatch", require_dispatch)
    monkeypatch.setattr(provider_usage_recovery, "replay_deferred_attempts", replay)
    monkeypatch.setattr(provider_usage_recovery, "report_dropped_usage", report_dropped)

    result = provider_usage_recovery.drain.run()

    assert result == {"recovered": 2, "missing": 1, "failed": 0}
    # 복구 뒤 유실 카운터를 사고로 바꾼다(DB가 돌아온 뒤에만 의미가 있다).
    assert calls == [
        ("dispatch", "drain-provider-usage-spool"),
        ("replay", 100),
        ("report_dropped",),
    ]
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_provider_usage.py -k "truncat or dropped" tests/test_provider_usage_recovery.py -v`
  Expected: FAIL with `AttributeError: module 'app.services.provider_usage' has no attribute '_RECOVERY_DROPPED_KEY'`

- [ ] **Step 3: 최소 구현**

`provider_usage.py` — `_RECOVERY_RETRY_DELAY_SECONDS = 60` 아래에 추가:

```python
# spool이 상한을 넘겨 버린 기록 수. 잘리는 순간은 DB가 죽어 있으므로 사고는 drain이 나중에 연다.
_RECOVERY_DROPPED_KEY = "provider_usage:recovery:dropped"
```

`_defer`를 다음으로 바꾼다:

```python
async def _defer(event: ProviderUsageEvent) -> bool:
    key = f"provider_usage:recovery:event:{event.id}"
    async with _recovery_redis_client() as client:
        try:
            pipe = client.pipeline(transaction=True)
            pipe.set(key, json.dumps(_event_payload(event)), ex=_RECOVERY_TTL_SECONDS)
            pipe.zadd(_RECOVERY_INDEX_KEY, {key: event.created_at.timestamp()})
            pipe.zremrangebyrank(_RECOVERY_INDEX_KEY, 0, -_RECOVERY_MAX_ITEMS - 1)
            results = await pipe.execute()
            dropped = int(results[2] or 0)
            if dropped:
                # 상한(1만 건)을 넘겨 가장 오래된 기록을 버렸다 — 되살릴 수 없다. DB가 돌아오면 drain이
                # 이 카운터를 개발 사고로 바꾼다(report_dropped_usage).
                await client.incrby(_RECOVERY_DROPPED_KEY, dropped)
                logger.error(
                    "provider_usage_spool_truncated dropped=%s max_items=%s",
                    dropped,
                    _RECOVERY_MAX_ITEMS,
                )
            return True
        except (OSError, RedisError, RuntimeError, TimeoutError, TypeError, ValueError):
            logger.warning(
                "provider usage recovery spool unavailable: idempotency_key=%s",
                event.idempotency_key,
            )
            return False
```

`replay_deferred_attempts` 뒤에 추가:

```python
async def _open_spool_truncation_incident(dropped: int, *, now: datetime) -> None:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from app.models.operations import IncidentSeverity  # noqa: PLC0415
    from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest  # noqa: PLC0415
    from app.services.incidents import open_or_touch_incident  # noqa: PLC0415

    kst_date = now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as db:
        await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="provider_usage",
                object_type="recovery_spool",
                object_id=kst_date,
                fingerprint=IncidentFingerprint.CONFIGURATION_ERROR,
                incident_type="PROVIDER_USAGE_SPOOL_TRUNCATED",
                severity=IncidentSeverity.MEDIUM,
                customer_impact="DB 저장 실패가 길어져 AI 사용량 기록 일부를 버렸습니다. 비용 집계가 실제보다 적습니다.",
                source_type="PROVIDER_USAGE",
                next_action="DB 저장이 실패한 원인을 확인해 주세요. 버려진 기록은 되살릴 수 없습니다.",
                admin_path="/operations",
                source_id=kst_date,
                safe_error_code="PROVIDER_USAGE_SPOOL_TRUNCATED",
                safe_error_message=f"dropped={dropped} max_items={_RECOVERY_MAX_ITEMS}",
            ),
            actor="provider-usage-recovery",
            reason="provider usage spool truncated",
            now=now,
        )
        await db.commit()


async def report_dropped_usage(*, redis_client: redis_async.Redis | None = None) -> int:
    """spool이 버린 기록 수를 개발 사고로 바꾸고 카운터를 비운다. 실패하면 카운터를 되돌린다."""
    async with _recovery_redis_client(redis_client) as client:
        try:
            pipe = client.pipeline(transaction=True)
            pipe.get(_RECOVERY_DROPPED_KEY)
            pipe.delete(_RECOVERY_DROPPED_KEY)
            raw, _deleted = await pipe.execute()
        except (OSError, RedisError, RuntimeError, TimeoutError):
            logger.warning("provider usage dropped counter unavailable: redis unavailable")
            return 0
        dropped = int(raw or 0)
        if not dropped:
            return 0
        try:
            await _open_spool_truncation_incident(dropped, now=datetime.now(UTC))
        except Exception:  # noqa: BLE001 — 다음 drain이 다시 시도한다.
            logger.warning("provider usage spool truncation incident not recorded: dropped=%s", dropped)
            try:
                await client.incrby(_RECOVERY_DROPPED_KEY, dropped)
            except (OSError, RedisError, RuntimeError, TimeoutError):
                logger.error("provider_usage_spool_truncated_count_lost dropped=%s", dropped)
            return 0
        return dropped
```

`provider_usage.py` 상단 import에 `UTC`가 없으면 `from datetime import UTC, datetime`으로 고친다(현재 모듈은
`datetime.now().astimezone()`을 쓴다 — 구현 전 `grep -n "^from datetime" backend/app/services/provider_usage.py`로 확인).
사고는 열림 알림을 enqueue하지 않는다 — 개발 대상 사고 목록·일일 요약의 "개발 확인 M건"으로 보인다. Slack까지
원하면 2-8a의 `_projection` + `build_open_incident_notification` 패턴을 그대로 붙인다.

`provider_usage_recovery.py`:

```python
from app.services.provider_usage import replay_deferred_attempts, report_dropped_usage


async def _drain_once() -> dict[str, int]:
    result = await replay_deferred_attempts(limit=100)
    # DB가 돌아온 뒤에만 의미가 있다 — 버린 기록 수를 개발 사고로 바꾼다(반환값에는 넣지 않는다).
    await report_dropped_usage()
    return result


@celery_app.task(
    name="app.workers.provider_usage_recovery.drain",
    soft_time_limit=45,
    time_limit=55,
)
def drain() -> dict[str, int]:
    require_dispatch(current_task, "drain-provider-usage-spool")
    return asyncio.run(_drain_once())
```

(`report_dropped_usage`는 테스트가 `monkeypatch.setattr(provider_usage_recovery, "report_dropped_usage", ...)`로 바꾸므로
`_drain_once`가 모듈 전역 이름으로 불러야 한다 — 위 코드가 그렇다.)

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_provider_usage.py tests/test_provider_usage_recovery.py -v`
  Expected: 전부 PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/services/provider_usage.py backend/app/workers/provider_usage_recovery.py backend/tests/test_provider_usage.py backend/tests/test_provider_usage_recovery.py && git commit -m "fix(usage): 사용량 spool이 넘쳐 기록을 버리면 DB 복구 뒤 개발 사고로 남긴다"`

**계약 영향:** CLAUDE.md "usage 누락은 0으로 바꾸지 않고 DB 실패 시 bounded Redis spool로 복구한다" 뒤에 "spool이 상한
(1만 건)을 넘겨 버린 기록은 ERROR와 카운터로 남기고, DB가 돌아온 뒤 drain이 KST 하루 한 건의 개발 사고
(`PROVIDER_USAGE_SPOOL_TRUNCATED`)로 연다."를 추가. "정상 제출·재시도 성공·usage spool 복구를 Slack 알림으로 만들지
않는다"는 그대로 — 유실만 사고다.

**위험·롤백:** drain이 매분 Redis 왕복 1회를 더한다. 롤백은 revert(카운터 키는 TTL 없음 — 남으면 `DEL provider_usage:recovery:dropped`).

---

### Task 2-8c: 달러 기준 일 상한 — 이번 범위에서 하지 않는다(근거)

**조사 결과:** `provider_usage_events`(`models/usage.py:74-130`, 마이그레이션 `0065_provider_usage`)에는 토큰·검색·이미지
단위만 있고 비용(USD) 컬럼이 없다. 비용 가드 카운터(`cost_guard.py`)도 호출 수 단위다. OpenRouter 응답의 `usage.cost`를
저장하지 않으므로 달러 상한을 계산할 근거가 없다. 구현하려면 ① `provider_usage_events.cost_usd NUMERIC NULL` 추가
마이그레이션, ② OpenRouter `usage.cost`(또는 `usage: {include: true}` 응답) 정규화, ③ KST 일 합계를 Redis에 누적하는
예약 단계가 필요하다 — M 규모의 별도 과제다. 대표 결정 후 Phase 3 후보로 등록한다. **계약 영향:** 없음.

---

### Task 2-9-0: `deploy.sh` 옵션 파싱(`--accept-env-diff`, `--override-reason=`, `--unpin`)

**Files:**
- Modify: `scripts/deploy.sh:39-44`
- Test: `scripts/test_deploy_runtime.py`

**조사 결과:** `deploy.sh`는 첫 인자(`TARGET`)만 읽고 나머지는 무시한다(`:40-44`). 2-9a·b·e가 옵션 3개를 쓴다.
`fail`/`info`는 `:35-37`에 먼저 정의돼 있다. 알 수 없는 옵션은 조용히 무시하지 않고 멈춘다(오타가 안전 장치를 끄지 않게).

- [ ] **Step 1: 실패하는 테스트 작성** — `scripts/test_deploy_runtime.py` 끝에 추가

```python
def test_unknown_option_stops_before_anything_runs(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api", "--acept-env-diff"],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "알 수 없는 옵션: --acept-env-diff" in result.stderr
    assert not command_log.exists() or "gcloud run deploy" not in command_log.read_text()
```

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py::test_unknown_option_stops_before_anything_runs -v`
  Expected: FAIL (`assert 0 != 0` — 지금은 옵션을 무시하고 배포가 끝까지 돈다)

- [ ] **Step 3: 최소 구현** — `scripts/deploy.sh:40-44`를 다음으로 바꾼다

```bash
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: bash scripts/deploy.sh [backend|api|worker|beat|site|admin|all|migrate|rollback] [--accept-env-diff] [--unpin] [--override-reason=사유]"
  exit 1
fi
shift

# 옵션 — 모르는 옵션은 무시하지 않는다(오타가 안전 장치를 끄지 않게).
#   --accept-env-diff        로컬 .env.production이 운영 env와 달라도 로컬 값으로 바꾼다(2-9a)
#   --override-reason=사유   배치 시간대·하루 한 번 규칙을 이번만 넘긴다. 사유는 기록된다(2-9b)
#   --unpin                  특정 리비전에 고정된 트래픽을 이번 배포의 새 리비전으로 옮긴다(2-9e)
ACCEPT_ENV_DIFF=0
UNPIN_TRAFFIC=0
OVERRIDE_REASON=""
for arg in "$@"; do
  case "$arg" in
    --accept-env-diff) ACCEPT_ENV_DIFF=1 ;;
    --unpin) UNPIN_TRAFFIC=1 ;;
    --override-reason=*) OVERRIDE_REASON="${arg#--override-reason=}" ;;
    *) fail "알 수 없는 옵션: ${arg}" ;;
  esac
done
# 빈 사유(--override-reason= 또는 공백뿐)는 규칙을 넘기는 근거가 아니다.
if [[ " $* " == *" --override-reason="* && -z "${OVERRIDE_REASON//[[:space:]]/}" ]]; then
  fail "--override-reason=에는 사유를 적어야 합니다."
fi
```

테스트 하나 더(같은 Step 1에 추가):

```python
def test_empty_override_reason_is_refused(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api", "--override-reason=  "],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1"),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode != 0
    assert "사유를 적어야" in result.stderr
```

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -v`
  Expected: 전부 PASS(기존 테스트는 옵션 없이 부른다).

- [ ] **Step 5: 커밋**
  `git add scripts/deploy.sh scripts/test_deploy_runtime.py && git commit -m "feat(deploy): 배포 옵션을 파싱하고 모르는 옵션은 멈춘다"`

**계약 영향:** 없음(옵션만 추가). **위험·롤백:** 없음. revert.

---

### Task 2-9a: env 정본을 운영 서비스 env로 — 로컬 `.env.production`과 다르면 멈춘다(`--accept-env-diff`)

**Files:**
- Create: `scripts/deploy_env.py`
- Modify: `scripts/deploy.sh` — `DEPLOY_INJECTED_ENV_KEYS` 배열(`:1093-1099`)을 `ALL_MANAGED_SECRET_NAMES`(`:189-200`) 바로 뒤로 이동, `:236-238`(env 파일 확인)을 env 정본 조립으로 교체, `require_no_dropped_terraform_env`(`:1114-1144`) 앞머리
- Test: `scripts/test_deploy_env.py`(새 파일), `scripts/test_deploy_runtime.py`(`_FAKE_GCLOUD`, `_make_project`, 새 테스트)

**조사 결과:**
- 모든 backend 서비스 env는 `--env-vars-file="$SERVICE_ENV_FILE"`(`deploy_api :661-693`, `deploy_worker :695-724`,
  `deploy_beat :726-755`, migrate/redbeat/readiness Job `:1156-1270`)로 **통째로 교체**되고, 그 파일은 로컬
  `.env.production`(`ENV_FILE`, `:48`)에서 만든다(`prepare_non_secret_env_file :348-403`, `make_service_env_file :405-419`).
  9/23·10/1 두 번 오래된 파일이 발견됐다(CLAUDE.md 배포 기록) — 감사 주장 확인.
- 같은 파일에서 최상위 코드가 값을 읽는다(`read_env_file_value` 호출 `:252-275`: Cloud SQL 연결명, DB 사용자, 버킷,
  기능 플래그, 측정 모델). 그래서 정본 조립은 **그 읽기보다 먼저**(= `:236` 위치) 해야 하고, 조립 결과를 다시
  `ENV_FILE`로 두면 이후 코드는 그대로 동작한다.
- 운영 env의 비밀 아닌 값은 `gcloud run services describe reputation-api --format=json`의
  `spec.template.spec.containers[0].env`에 `{name, value}`로, 시크릿은 `{name, valueFrom.secretKeyRef}`로 있다.
  deploy.sh는 같은 파일을 API·Worker·Beat에 모두 쓰므로 API env가 정본으로 충분하다.
- deploy.sh는 빈 값을 주입하지 않는다(`:381-386`) → 운영 env에는 빈 값 키가 없다. 그래서 운영 env를 정본으로 쓰면
  `require_no_dropped_terraform_env`(`:1114-1144`, "Terraform 선언 키가 파일에 `KEY=` 줄로라도 있어야 한다")가 빈 값 키를
  잘못 잡는다. 이 검사의 목적은 "파일이 키를 잃어 운영에서 사라지는 것"인데, 운영 env 자신은 운영 env 대비 키를 잃을 수
  없다 → 운영 정본일 때는 이 검사를 건너뛴다.
- `REPUTATION_RELEASE_REVISION`이 렌더된 파일에 남으면 `prepare_non_secret_env_file`이 그대로 YAML에 넣고
  `make_service_env_file`이 또 넣어 **YAML 중복 키**가 된다 → 렌더에서 `SERVICE`·`APP_ENV`·`REPUTATION_RELEASE_REVISION`을 뺀다.
  `OPENAI_CHATGPT_USE_WEB_SEARCH`·`CERTIFICATE_MANAGER_AUTO_PROVISION`은 남긴다(`:268`, `:276`에서 값으로 읽고
  `require_production_feature_flags :979-988`가 검사한다).
- 신규 환경(서비스가 아직 없음)은 기존처럼 `.env.production`으로 시작한다. "없다"와 "못 읽었다"를 구분한다
  (시크릿 조회 `probe_secret_exists :432-448`와 같은 원칙) — 못 읽었으면 멈춘다.
- 기존 테스트의 가짜 gcloud(`_FAKE_GCLOUD`, `test_deploy_runtime.py:42-96`)는 `run services describe`에 리비전 문자열을
  돌려준다 → JSON 요청에는 기본으로 "서비스 없음"(신규 환경)을 돌려주게 해서 기존 테스트 54개가 지금과 같은 로컬 파일
  경로를 타게 한다.
- 새 Settings 키(2-6c `OPS_ALERT_EMAIL`, 2-7 `BEAT_LIVENESS_*`)는 운영 env에 없다 — 기본값이 쓰이므로 문제없고, 운영 값을
  넣을 때만 로컬 파일에 적어 `--accept-env-diff`로 배포한다(의도된 변경만 diff로 드러난다).

- [ ] **Step 1: 실패하는 테스트 작성**

`scripts/test_deploy_env.py` (새 파일):

```python
import json

import pytest

from scripts.deploy_env import diff, main, plain_env, read_dotenv, render

_DROP = frozenset({"SERVICE", "APP_ENV", "REPUTATION_RELEASE_REVISION"})


def _service(*env: dict) -> dict:
    return {"spec": {"template": {"spec": {"containers": [{"env": list(env)}]}}}}


def test_plain_env_keeps_values_and_drops_secrets_and_per_release_keys() -> None:
    service = _service(
        {"name": "CLAUDE_MODEL", "value": "anthropic/claude-sonnet-5.5"},
        {"name": "SENTRY_DSN"},
        {"name": "SLACK_WEBHOOK_URL", "valueFrom": {"secretKeyRef": {"name": "SLACK_WEBHOOK_URL", "key": "latest"}}},
        {"name": "SERVICE", "value": "api"},
        {"name": "REPUTATION_RELEASE_REVISION", "value": "old-sha"},
    )
    assert plain_env(service, _DROP) == {"CLAUDE_MODEL": "anthropic/claude-sonnet-5.5", "SENTRY_DSN": ""}


def test_plain_env_rejects_a_service_without_containers() -> None:
    with pytest.raises(ValueError):
        plain_env({"spec": {"template": {"spec": {}}}}, _DROP)


def test_render_round_trips_through_read_dotenv() -> None:
    env = {"B": "Re:putation <noreply@x.test>", "A": "1"}
    assert render(env) == "A=1\nB=Re:putation <noreply@x.test>\n"
    assert read_dotenv(render(env)) == env


def test_render_refuses_multiline_values() -> None:
    with pytest.raises(ValueError):
        render({"A": "line1\nline2"})


def test_read_dotenv_matches_deploy_sh_parsing() -> None:
    text = '# comment\n\nexport A="quoted"\nB=\'single\'\nC=\nD=a=b\n'
    assert read_dotenv(text) == {"A": "quoted", "B": "single", "C": "", "D": "a=b"}


def test_diff_reports_names_only_and_ignores_empty_and_listed_keys() -> None:
    local = {"A": "1", "B": "new", "C": "", "SECRET": "x", "LOCAL_ONLY": "v"}
    live = {"A": "1", "B": "old", "LIVE_ONLY": "v", "SECRET": "y"}
    assert diff(local, live, frozenset({"SECRET"})) == [
        "added: LOCAL_ONLY",
        "removed: LIVE_ONLY",
        "changed: B",
    ]
    assert diff({"A": "1", "C": ""}, {"A": "1"}, frozenset()) == []


def test_cli_render_then_diff(tmp_path, capsys) -> None:
    service_json = tmp_path / "service.json"
    service_json.write_text(json.dumps(_service({"name": "A", "value": "1"}, {"name": "SERVICE", "value": "api"})))
    live = tmp_path / "live.env"
    assert main(["render", "--service-json", str(service_json), "--out", str(live),
                 "--drop", "SERVICE", "APP_ENV", "REPUTATION_RELEASE_REVISION"]) == 0
    assert live.read_text() == "A=1\n"

    local = tmp_path / "local.env"
    local.write_text("A=2\n")
    assert main(["diff", "--local", str(local), "--live", str(live)]) == 1
    assert "changed: A" in capsys.readouterr().out
    local.write_text("A=1\n")
    assert main(["diff", "--local", str(local), "--live", str(live)]) == 0
```

`scripts/test_deploy_runtime.py`:

1) `_FAKE_GCLOUD`의 `'case "$*" in',` 바로 다음 줄에 다음 항목을 **맨 앞에** 넣는다:

```python
        '  "run services describe "*"--format=json"*)',
        '    if [[ -n "${FAKE_SERVICE_ENV_JSON:-}" ]]; then',
        '      printf \'%s\\n\' "$FAKE_SERVICE_ENV_JSON"',
        "      exit 0",
        "    fi",
        '    echo "ERROR: (gcloud.run.services.describe) Cannot find service [reputation-api]" >&2',
        "    exit 1",
        "    ;;",
```

2) `_make_project`의 `shutil.copy2(PROJECT_ROOT / "scripts" / "deploy.sh", ...)` 다음 줄에 추가:

```python
    shutil.copy2(PROJECT_ROOT / "scripts" / "deploy_env.py", project / "scripts" / "deploy_env.py")
```

3) 파일 끝에 추가:

```python
def _live_service_json(**overrides: str) -> str:
    """`.env.production.example`의 비어 있지 않은 값을 그대로 가진 운영 API 서비스(JSON)."""
    pairs = [
        (key, value)
        for key, value in _dotenv_pairs((PROJECT_ROOT / ".env.production.example").read_text())
        if value
    ]
    env = [{"name": key, "value": overrides.get(key, value)} for key, value in pairs]
    env.append({"name": "SERVICE", "value": "api"})
    env.append({"name": "REPUTATION_RELEASE_REVISION", "value": "previous-release"})
    env.append({"name": "SLACK_WEBHOOK_URL", "valueFrom": {"secretKeyRef": {"name": "SLACK_WEBHOOK_URL", "key": "latest"}}})
    return json.dumps({"spec": {"template": {"spec": {"containers": [{"env": env}]}}}})


def _run_api_with_live_env(tmp_path: Path, *args: str, local: bool = True, **extra: str):
    project, fake_bin, command_log = _make_project(tmp_path)
    if local:
        shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api", *args],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1", **extra),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result, command_log.read_text() if command_log.exists() else ""


def test_live_service_env_is_the_source_when_the_local_file_matches(tmp_path: Path) -> None:
    result, commands = _run_api_with_live_env(tmp_path, FAKE_SERVICE_ENV_JSON=_live_service_json())
    assert result.returncode == 0, result.stderr
    assert "운영 서비스 env를 정본으로" in result.stderr
    # 옛 릴리스 값은 렌더에서 빠지고 이번 릴리스 값만 한 번 들어간다(YAML 중복 키 없음).
    release_lines = [l for l in commands.splitlines() if l.startswith("env REPUTATION_RELEASE_REVISION:")]
    assert release_lines and set(release_lines) == {'env REPUTATION_RELEASE_REVISION: "test-source-revision"'}


def test_a_stale_local_file_stops_before_mutation_and_names_the_keys(tmp_path: Path) -> None:
    result, commands = _run_api_with_live_env(
        tmp_path, FAKE_SERVICE_ENV_JSON=_live_service_json(CLAUDE_MODEL="anthropic/claude-live-only")
    )
    assert result.returncode != 0
    assert "changed: CLAUDE_MODEL" in result.stderr
    assert "--accept-env-diff" in result.stderr
    # 값은 출력하지 않는다.
    assert "claude-live-only" not in result.stderr
    assert "gcloud run deploy" not in commands
    assert "docker build" not in commands


def test_accept_env_diff_deploys_the_local_values(tmp_path: Path) -> None:
    result, commands = _run_api_with_live_env(
        tmp_path,
        "--accept-env-diff",
        FAKE_SERVICE_ENV_JSON=_live_service_json(CLAUDE_MODEL="anthropic/claude-live-only"),
    )
    assert result.returncode == 0, result.stderr
    assert "claude-live-only" not in commands
    assert "gcloud run deploy reputation-api" in commands


def test_no_local_file_uses_the_live_env(tmp_path: Path) -> None:
    result, commands = _run_api_with_live_env(
        tmp_path, local=False, FAKE_SERVICE_ENV_JSON=_live_service_json()
    )
    assert result.returncode == 0, result.stderr
    assert "gcloud run deploy reputation-api" in commands


def test_unreadable_live_env_fails_closed(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    # 권한 오류는 '서비스 없음'이 아니다 — 로컬 파일로 넘어가지 않는다.
    _write_executable(
        fake_bin / "gcloud",
        _FAKE_GCLOUD.replace(
            "Cannot find service [reputation-api]", "PERMISSION_DENIED: caller lacks run.services.get"
        ),
    )
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api"],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1"),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode != 0
    assert "운영 서비스 env를 읽지 못했습니다" in result.stderr
    assert "gcloud run deploy" not in command_log.read_text()
```

(`json`을 파일 상단 import에 추가한다.)

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_env.py scripts/test_deploy_runtime.py -k "deploy_env or live_env or stale_local or accept_env or no_local_file or unreadable" -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.deploy_env'`, `FileNotFoundError: .../scripts/deploy_env.py`

- [ ] **Step 3: 최소 구현**

`scripts/deploy_env.py` (새 파일):

```python
#!/usr/bin/env python3
"""배포 env 정본 — 운영 Cloud Run 서비스의 현재 env를 배포 입력(dotenv)으로 옮기고 로컬 파일과 비교한다.

`scripts/deploy.sh`가 부른다. `--env-vars-file` 배포는 서비스 env를 통째로 바꾸므로, 작업 PC의 오래된
`.env.production`이 운영 값을 덮지 않도록 운영 env를 정본으로 쓴다. 차이는 **키 이름과 종류만** 출력한다.

  render  --service-json FILE|- --out FILE --drop KEY...   운영 서비스 JSON → dotenv
  diff    --local FILE --live FILE --ignore KEY...           0 = 같다, 1 = 다르다(목록 출력)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def plain_env(service: dict, drop: frozenset[str]) -> dict[str, str]:
    """비밀이 아닌 env(`value`)만. 시크릿 참조(`valueFrom`)와 배포가 매번 넣는 키는 뺀다."""
    template = ((service.get("spec") or {}).get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    if not containers:
        raise ValueError("service has no containers")
    env: dict[str, str] = {}
    for item in containers[0].get("env") or []:
        name = str(item.get("name") or "")
        if not name or name in drop or "valueFrom" in item:
            continue
        env[name] = str(item.get("value") or "")
    return env


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_dotenv(text: str) -> dict[str, str]:
    """deploy.sh(`prepare_non_secret_env_file`, `read_env_file_value`)와 같은 규칙으로 읽는다."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw[len("export "):] if raw.startswith("export ") else raw
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = _unquote(value)
    return values


def render(env: dict[str, str]) -> str:
    for key, value in env.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"multiline env value is not supported: {key}")
    return "".join(f"{key}={value}\n" for key, value in sorted(env.items()))


def diff(local: dict[str, str], live: dict[str, str], ignore: frozenset[str]) -> list[str]:
    """배포하면 바뀌는 키. deploy.sh는 빈 값을 주입하지 않으므로 빈 값은 '없음'과 같다."""

    def effective(values: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in values.items() if key not in ignore and value != ""}

    a, b = effective(local), effective(live)
    lines = [f"added: {key}" for key in sorted(a.keys() - b.keys())]
    lines += [f"removed: {key}" for key in sorted(b.keys() - a.keys())]
    lines += [f"changed: {key}" for key in sorted(k for k in a.keys() & b.keys() if a[k] != b[k])]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    render_cmd = sub.add_parser("render")
    render_cmd.add_argument("--service-json", required=True)
    render_cmd.add_argument("--out", required=True)
    render_cmd.add_argument("--drop", nargs="*", default=[])
    diff_cmd = sub.add_parser("diff")
    diff_cmd.add_argument("--local", required=True)
    diff_cmd.add_argument("--live", required=True)
    diff_cmd.add_argument("--ignore", nargs="*", default=[])
    args = parser.parse_args(argv)

    if args.command == "render":
        raw = sys.stdin.read() if args.service_json == "-" else Path(args.service_json).read_text()
        try:
            env = plain_env(json.loads(raw), frozenset(args.drop))
            text = render(env)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"deploy_env: {exc}", file=sys.stderr)
            return 2
        Path(args.out).write_text(text, encoding="utf-8")
        return 0

    changes = diff(
        read_dotenv(Path(args.local).read_text(encoding="utf-8")),
        read_dotenv(Path(args.live).read_text(encoding="utf-8")),
        frozenset(args.ignore),
    )
    for line in changes:
        print(line)
    return 1 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/deploy.sh`:

(1) `DEPLOY_INJECTED_ENV_KEYS=( ... )` 블록(현재 `:1093-1099`, 주석 `# make_service_env_file이 서비스별로 직접 써 넣는 키…` 포함)을
잘라 `ALL_MANAGED_SECRET_NAMES=( ... )` 블록 닫는 `)` 바로 뒤로 옮긴다(내용 변경 없음 — `test_deploy_runtime.py`의
`_deploy_sh_name_arrays` 파서는 위치와 무관).

(2) `:236-238`의

```bash
if [[ "$TARGET" != "rollback" && ! -f "$ENV_FILE" ]]; then
  fail ".env.production 파일이 없습니다. .env.production.example을 복사해서 작성해 주세요."
fi
```

를 다음으로 바꾼다:

```bash
# ─── env 정본: 운영 서비스의 현재 env ─────────────────────────────
# `--env-vars-file`은 서비스 env를 통째로 바꾼다. 작업 PC의 .env.production이 정본이던 동안 오래된 파일이
# 두 번(9/23·10/1) 발견됐다. backend 대상의 정본은 이제 운영 reputation-api의 현재 env(비밀 아닌 값)이고,
# 로컬 파일은 '바꾸려는 값'을 담은 제안이다. 둘이 다르면 키 이름만 보여 주고 멈춘다. 로컬 값으로 바꾸는 것이
# 맞으면 --accept-env-diff로 다시 실행한다. 서비스가 아직 없으면(신규 환경) 로컬 파일로 시작한다.
LOCAL_ENV_FILE="$ENV_FILE"
ENV_SOURCE="local"
# 배포가 서비스·릴리스마다 직접 넣는 키 — 렌더에서 빼야 YAML 중복 키가 생기지 않는다.
LIVE_ENV_DROP_KEYS=("SERVICE" "APP_ENV" "REPUTATION_RELEASE_REVISION")

resolve_backend_env_source() {
  local err live_json live_file status=0 diff_out
  err="$(mktemp)"; TEMP_ENV_FILES+=("$err")
  live_json="$(gcloud run services describe reputation-api --region="$REGION" --project="$PROJECT_ID" \
      --format=json 2>"$err")" || status=$?
  if (( status != 0 )); then
    if grep -qiE "could not be found|cannot find service|NOT_FOUND" "$err"; then
      [[ -f "$LOCAL_ENV_FILE" ]] \
        || fail "운영 서비스가 아직 없고 .env.production도 없습니다. 신규 환경은 .env.production.example을 복사해 작성하세요."
      info "운영 서비스가 아직 없어 .env.production으로 시작합니다(신규 환경)."
      return
    fi
    cat "$err" >&2
    fail "운영 서비스 env를 읽지 못했습니다(reputation-api). gcloud 인증·권한을 확인하세요. 로컬 파일로 대신 배포하지 않습니다."
  fi

  live_file="$(mktemp)"; TEMP_ENV_FILES+=("$live_file")
  printf '%s' "$live_json" | python3 "${SCRIPT_DIR}/deploy_env.py" render \
      --service-json - --out "$live_file" --drop "${LIVE_ENV_DROP_KEYS[@]}" \
    || fail "운영 서비스 env를 배포 입력으로 옮기지 못했습니다(여러 줄 값 등). 위 오류를 확인하세요."

  if [[ -f "$LOCAL_ENV_FILE" ]]; then
    status=0
    diff_out="$(python3 "${SCRIPT_DIR}/deploy_env.py" diff --local "$LOCAL_ENV_FILE" --live "$live_file" \
        --ignore "${LIVE_ENV_DROP_KEYS[@]}" "${ALL_MANAGED_SECRET_NAMES[@]}")" || status=$?
    if (( status == 1 )); then
      echo "$diff_out" >&2
      if [[ "$ACCEPT_ENV_DIFF" == "1" ]]; then
        info "--accept-env-diff: 위 키를 로컬 .env.production 값으로 바꿉니다."
        return
      fi
      fail "로컬 .env.production이 운영 env와 다릅니다(위 키 목록, 값은 출력하지 않음). 운영 값이 맞으면 로컬 파일을 지우거나 고친 뒤 다시 실행하고, 로컬 값으로 바꾸려면 --accept-env-diff를 붙이세요."
    elif (( status != 0 )); then
      fail "env 비교에 실패했습니다(deploy_env.py diff exit=${status})."
    fi
  fi
  ENV_FILE="$live_file"
  ENV_SOURCE="live"
  ok "운영 서비스 env를 정본으로 씁니다(reputation-api 현재 env)."
}

case "$TARGET" in
  backend|api|worker|beat|all|migrate)
    resolve_backend_env_source
    ;;
esac

if [[ "$TARGET" != "rollback" && ! -f "$ENV_FILE" ]]; then
  fail ".env.production 파일이 없습니다. .env.production.example을 복사해서 작성해 주세요."
fi
```

(3) `require_no_dropped_terraform_env` 함수 첫 줄(`if [[ "${SKIP_TERRAFORM_ENV_PREFLIGHT:-0}" == "1" ]]; then`) 앞에 추가:

```bash
  if [[ "$ENV_SOURCE" == "live" ]]; then
    # 운영 env 자신은 운영 env보다 키를 잃을 수 없다 — 이 검사의 대상(오래된 로컬 파일)이 아니다.
    info "운영 서비스 env를 정본으로 쓰므로 Terraform env 드롭 preflight를 건너뜁니다."
    return
  fi
```

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_env.py scripts/test_deploy_runtime.py -v`
  Expected: 전부 PASS(기존 54개는 신규 환경 경로로 동작이 같다).
  운영 dry check(읽기 전용): `gcloud run services describe reputation-api --region=asia-northeast3 --format=json | python3 scripts/deploy_env.py render --service-json - --out /tmp/live.env --drop SERVICE APP_ENV REPUTATION_RELEASE_REVISION && python3 scripts/deploy_env.py diff --local .env.production --live /tmp/live.env`
  Expected: 첫 배포 전 이 목록이 비어 있거나, 남은 차이가 무엇인지 사람이 확인한다.

- [ ] **Step 5: 커밋**
  `git add scripts/deploy_env.py scripts/deploy.sh scripts/test_deploy_env.py scripts/test_deploy_runtime.py && git commit -m "feat(deploy): 운영 서비스 env를 배포 정본으로 쓰고 로컬 파일과 다르면 멈춘다"`

**계약 영향:** CLAUDE.md 최상단 배포 절의 "배포 전 `.env.production`을 운영 env와 대조한다 — `deploy.sh`가 서비스 env를 파일로
통째로 바꾸며"(09-23 절)와 10-01 절의 같은 서술은 "backend env의 정본은 운영 reputation-api의 현재 env다. 로컬
`.env.production`이 다르면 deploy.sh가 멈추고, 의도한 변경이면 `--accept-env-diff`로 배포한다."로 바꾼다.
`docs/ops/deployment-runbook.md` 사전 준비 절도 같이 고친다.

**위험·롤백:** 운영 env 자체가 잘못돼 있으면(예: 누군가 콘솔에서 바꿈) 그 값이 계속 정본이 된다 — 차이를 보여 주는 것까지가
이 단계의 몫이다. site/admin 대상은 이번에 바꾸지 않는다(빌드 인자 경로가 다르다). 롤백: revert(로컬 파일 경로로 돌아간다).

---

### Task 2-9b: 배치 시간대 차단 + 하루 한 번 잠금(GCS) + `--override-reason`

**Files:**
- Modify: `scripts/deploy.sh` — 새 함수 블록(롤백 좌표 절 `:1272` 앞), 메인 `case`의 backend 계열 분기(`:1490-1534`)
- Test: `scripts/test_deploy_runtime.py`(`_FAKE_GCLOUD`, `_clean_env`, 새 테스트)

**조사 결과:**
- 규칙은 문서에만 있다(`docs/ops/deployment-runbook.md:198-200`, CLAUDE.md 2026-10-08 절: "배포는 하루 한 번, 배치 시간대
  (23:00·01/04/07/12/18/22시 KST) 밖에서"). 코드 강제 없음 — 감사 주장 확인(10/9 4회 배포).
- 잠금 저장소: Terraform이 만든 버킷은 `reputation-images-<project>`, `reputation-reports-<project>`(`terraform/storage.tf:5-75`)
  뿐이다. 전용 버킷을 새로 만들지 않고 리포트 버킷의 `ops/deploy-lock/` 접두사를 쓴다(버전 관리·`force_destroy=false`).
  `DEPLOY_LOCK_BUCKET`으로 바꿀 수 있다. Terraform state 버킷(`mso-platform-terraform-state`, `.terraform/terraform.tfstate`)은 쓰지 않는다.
- 원자적 "없을 때만 생성": `gcloud storage cp --if-generation-match=0` — 이미 있으면 412(precondition)로 실패한다.
  stdin 파이프 대신 임시 파일을 쓴다(가짜 gcloud가 stdin을 안 읽으면 `pipefail`에서 SIGPIPE).
- 적용 대상: Worker·Beat를 재시작하는 대상(`backend|api|worker|beat|all`)만. 10/6~10/8 V0 유실 원인은 워커 재시작이었다.
  `site|admin|migrate|rollback`은 막지 않는다(롤백은 장애 복구 경로).
- 같은 날 같은 릴리스의 재시도(중간 실패 후 다시 실행)는 허용한다 — 잠금 내용의 `revision`이 같으면 통과.
- 기존 테스트가 실제 시각에 돌면 CI가 배치 시간대에 깨진다 → `DEPLOY_CLOCK_KST`(YYYY-MM-DDTHH:MM) 주입을 두고
  `_clean_env` 기본값을 안전한 14:30으로 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**

`_FAKE_GCLOUD`의 `'case "$*" in',` 다음(2-9a에서 넣은 JSON 항목 뒤)에 추가:

```python
        '  "storage cp "*)',
        '    if [[ "${FAKE_LOCK_HELD:-0}" == "1" && "$*" == *"--if-generation-match=0"* ]]; then',
        '      echo "ERROR: HTTPError 412: At least one of the pre-conditions you specified did not hold." >&2',
        "      exit 1",
        "    fi",
        "    exit 0",
        "    ;;",
        '  "storage cat "*)',
        '    printf \'%s\\n\' "${FAKE_LOCK_CONTENT:-}"',
        "    exit 0",
        "    ;;",
```

`_clean_env`의 기본 dict에 `"DEPLOY_CLOCK_KST": "2026-10-11T14:30",` 추가.

파일 끝에 추가:

```python
def _run_target(tmp_path: Path, target: str, *args: str, **extra: str):
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", target, *args],
        cwd=project,
        env=_clean_env(
            fake_bin, command_log,
            SKIP_ASSET_BUCKET_PREFLIGHT="1",
            PUBLIC_DOMAIN="reputation.example.test",
            ADMIN_DOMAIN="admin.reputation.example.test",
            **extra,
        ),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result, command_log.read_text() if command_log.exists() else ""


@pytest.mark.parametrize("clock", ["2026-10-11T22:30", "2026-10-11T00:20", "2026-10-11T12:45", "2026-10-11T17:15"])
def test_backend_deploy_is_blocked_inside_a_batch_window(tmp_path: Path, clock: str) -> None:
    result, commands = _run_target(tmp_path, "api", DEPLOY_CLOCK_KST=clock)
    assert result.returncode != 0
    assert "콘텐츠 배치" in result.stderr
    assert "--override-reason" in result.stderr
    assert "docker build" not in commands
    assert "storage cp" not in commands


@pytest.mark.parametrize("clock", ["2026-10-11T12:46", "2026-10-11T14:30", "2026-10-11T02:46"])
def test_backend_deploy_runs_outside_batch_windows(tmp_path: Path, clock: str) -> None:
    result, commands = _run_target(tmp_path, "api", DEPLOY_CLOCK_KST=clock)
    assert result.returncode == 0, result.stderr
    assert "ops/deploy-lock/2026-10-11.json" in commands
    assert "--if-generation-match=0" in commands
    # 잠금은 첫 mutation(이미지 빌드) 전에 잡는다.
    assert commands.index("storage cp") < commands.index("docker build")


def test_second_release_on_the_same_day_is_refused(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", FAKE_LOCK_HELD="1",
        FAKE_LOCK_CONTENT='{"day": "2026-10-11", "revision": "another-release"}',
    )
    assert result.returncode != 0
    assert "하루 한 번" in result.stderr
    assert "docker build" not in commands


def test_retrying_the_same_release_on_the_same_day_is_allowed(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", FAKE_LOCK_HELD="1",
        FAKE_LOCK_CONTENT='{"day": "2026-10-11", "revision": "test-source-revision"}',
    )
    assert result.returncode == 0, result.stderr
    assert "같은 릴리스" in result.stderr


def test_override_reason_passes_the_rules_and_is_recorded(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", "--override-reason=발행 장애 긴급 수정",
        DEPLOY_CLOCK_KST="2026-10-11T22:50", FAKE_LOCK_HELD="1",
    )
    assert result.returncode == 0, result.stderr
    assert "배포 규칙 예외: 발행 장애 긴급 수정" in result.stderr
    assert "ops/deploy-lock/2026-10-11-override-2250" in commands


def test_site_deploy_is_not_bound_by_the_backend_rules(tmp_path: Path) -> None:
    result, commands = _run_target(tmp_path, "site", DEPLOY_CLOCK_KST="2026-10-11T22:50")
    assert result.returncode == 0, result.stderr
    assert "storage cp" not in commands
```

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -k "batch_window or same_day or same_release or override_reason or not_bound" -v`
  Expected: FAIL — 시간대 테스트가 `returncode == 0`, 잠금 테스트가 `"ops/deploy-lock/2026-10-11.json" in commands` 실패

- [ ] **Step 3: 최소 구현** — `scripts/deploy.sh`

`# ─── 롤백 좌표 ───` 절 바로 앞에 추가:

```bash
# ─── 배포 시간대·하루 한 번 ─────────────────────────────────────────
# 배포는 하루 한 번, 콘텐츠 배치(23:00과 01·04·07·12·18·22시 KST) 앞뒤 45분 밖에서 한다(2026-10-08).
# Worker 교체가 실행 중 메시지를 끊어 10/6~10/8 V0 보고서 2건이 유실됐다. Worker·Beat를 바꾸는 대상만 적용한다.
BATCH_WINDOW_HOURS_KST=(23 1 4 7 12 18 22)
BATCH_WINDOW_MARGIN_MINUTES="${BATCH_WINDOW_MARGIN_MINUTES:-45}"
DEPLOY_LOCK_BUCKET="${DEPLOY_LOCK_BUCKET:-reputation-reports-${PROJECT_ID}}"
DEPLOY_LOCK_PREFIX="ops/deploy-lock"

deploy_clock_kst() {
  # 테스트·재현은 DEPLOY_CLOCK_KST(YYYY-MM-DDTHH:MM)로 시각을 고정한다.
  if [[ -n "${DEPLOY_CLOCK_KST:-}" ]]; then
    printf '%s' "$DEPLOY_CLOCK_KST"
  else
    TZ=Asia/Seoul date '+%Y-%m-%dT%H:%M'
  fi
}

# HH:MM이 어느 배치의 앞뒤 여유 안이면 그 배치 시각을 출력하고 0을 돌려준다.
batch_window_conflict() {
  local hhmm="$1" now center distance
  now=$(( 10#${hhmm%%:*} * 60 + 10#${hhmm##*:} ))
  for center in "${BATCH_WINDOW_HOURS_KST[@]}"; do
    distance=$(( (now - center * 60 + 1440) % 1440 ))
    (( distance > 720 )) && distance=$(( 1440 - distance ))
    if (( distance <= BATCH_WINDOW_MARGIN_MINUTES )); then
      printf '%02d:00' "$center"
      return 0
    fi
  done
  return 1
}

write_deploy_record() {
  # $1=객체 이름, $2=precondition(빈 값 또는 --if-generation-match=0), 나머지=JSON 키/값 쌍
  local object="$1" precondition="$2" record
  shift 2
  record="$(mktemp)"; TEMP_ENV_FILES+=("$record")
  python3 -c 'import json, sys; a = sys.argv[1:]; print(json.dumps(dict(zip(a[0::2], a[1::2])), ensure_ascii=False))' \
    "$@" > "$record"
  gcloud storage cp "$record" "gs://${DEPLOY_LOCK_BUCKET}/${DEPLOY_LOCK_PREFIX}/${object}.json" \
    --project="$PROJECT_ID" ${precondition:+"$precondition"}
}

require_deploy_slot() {
  local clock day hhmm batch err existing operator
  clock="$(deploy_clock_kst)"
  day="${clock%%T*}"
  hhmm="${clock##*T}"
  operator="$(gcloud config get-value account 2>/dev/null || true)"

  if [[ -n "$OVERRIDE_REASON" ]]; then
    info "배포 규칙 예외: ${OVERRIDE_REASON}"
    write_deploy_record "${day}-override-${hhmm/:/}" "" \
      day "$day" clock_kst "$hhmm" target "$TARGET" revision "$RELEASE_REVISION" \
      operator "${operator:-unknown}" override_reason "$OVERRIDE_REASON" \
      || fail "배포 예외 기록을 남기지 못했습니다(gs://${DEPLOY_LOCK_BUCKET}). 기록 없이 규칙을 넘지 않습니다."
    # 오늘의 잠금도 잡아 둔다(이미 있으면 그대로). 같은 날 다른 릴리스는 다시 사유가 필요하다.
    write_deploy_record "$day" "--if-generation-match=0" \
      day "$day" clock_kst "$hhmm" target "$TARGET" revision "$RELEASE_REVISION" \
      operator "${operator:-unknown}" >/dev/null 2>&1 || true
    return
  fi

  if batch="$(batch_window_conflict "$hhmm")"; then
    fail "지금(KST ${hhmm})은 콘텐츠 배치(${batch} KST) 앞뒤 ${BATCH_WINDOW_MARGIN_MINUTES}분 안입니다. 배치 시간대 밖에서 다시 배포하거나, 꼭 필요하면 --override-reason=\"사유\"를 붙이세요."
  fi

  err="$(mktemp)"; TEMP_ENV_FILES+=("$err")
  if write_deploy_record "$day" "--if-generation-match=0" \
      day "$day" clock_kst "$hhmm" target "$TARGET" revision "$RELEASE_REVISION" \
      operator "${operator:-unknown}" 2>"$err"; then
    ok "오늘(${day}) 배포 잠금: gs://${DEPLOY_LOCK_BUCKET}/${DEPLOY_LOCK_PREFIX}/${day}.json"
    return
  fi
  if grep -qiE "412|precondition" "$err"; then
    existing="$(gcloud storage cat "gs://${DEPLOY_LOCK_BUCKET}/${DEPLOY_LOCK_PREFIX}/${day}.json" \
        --project="$PROJECT_ID" 2>/dev/null || true)"
    if [[ "$existing" == *"\"revision\": \"${RELEASE_REVISION}\""* ]]; then
      info "같은 릴리스(${RELEASE_REVISION})의 오늘 재시도입니다 — 계속합니다."
      return
    fi
    fail "오늘(${day}) 이미 다른 릴리스를 배포했습니다(하루 한 번 규칙). 내일 배포하거나, 꼭 필요하면 --override-reason=\"사유\"를 붙이세요."
  fi
  cat "$err" >&2
  fail "배포 잠금을 확인하지 못했습니다(gs://${DEPLOY_LOCK_BUCKET}). 버킷과 권한을 확인하세요."
}
```

메인 `case`:
- `backend)` 분기: `require_legacy_publish_retirement_clear` 다음, `capture_rollback_point ...` 앞에 `require_deploy_slot` 추가.
- `api|worker|beat)` 분기: 같은 위치(`require_legacy_publish_retirement_clear` 다음, `if [[ "$TARGET" == "api" ]]; then capture_rollback_point…` 앞)에 `require_deploy_slot`.
- `all)` 분기: `require_legacy_publish_retirement_clear` 다음, `capture_rollback_point \` 앞에 `require_deploy_slot`.

`json.dumps`의 기본 구분자는 `", "`와 `": "`이므로 잠금 내용 비교는 `"revision": "<값>"`(콜론 뒤 공백)이다 — 위 코드와 테스트의
`FAKE_LOCK_CONTENT`가 이 형식이다.

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -v`
  Expected: 전부 PASS.
  운영 권한 확인(읽기·쓰기 1회, 배포 날 아침): `echo '{}' > /tmp/probe.json && gcloud storage cp /tmp/probe.json gs://reputation-reports-$(gcloud config get-value project)/ops/deploy-lock/probe.json && gcloud storage rm gs://reputation-reports-$(gcloud config get-value project)/ops/deploy-lock/probe.json`
  Expected: 두 명령 모두 성공.

- [ ] **Step 5: 커밋**
  `git add scripts/deploy.sh scripts/test_deploy_runtime.py && git commit -m "feat(deploy): 배치 시간대와 하루 두 번째 backend 배포를 막고 예외 사유를 기록한다"`

**계약 영향:** CLAUDE.md "배포는 하루 한 번, 배치 시간대(23:00·01/04/07/12/18/22시 KST) 밖에서 한다" 문장 뒤에 "`deploy.sh`가
backend 대상에 이를 강제한다(앞뒤 45분, `gs://reputation-reports-<project>/ops/deploy-lock/<KST 날짜>.json`). 예외는
`--override-reason=`으로만, 사유가 같은 경로에 기록된다."를 추가. 런북 같은 절도 갱신.

**위험·롤백:** 잠금 객체는 리포트 버킷 버전 관리에 남는다(용량 무시 가능). 버킷 권한 없는 계정은 배포가 멈춘다(의도).
긴급 시 `--override-reason`. 롤백: revert.

---

### Task 2-9c: readiness 실패 시 자동 롤백

**Files:**
- Modify: `scripts/deploy.sh:1266-1269`(`run_production_readiness_gate`의 실패 처리) + 새 함수 `rollback_after_failed_gate`
- Test: `scripts/test_deploy_runtime.py`

**조사 결과:**
- readiness Job 실패는 `fail "…트래픽 승격을 중단하고 롤백 좌표를 확인하세요."`(`deploy.sh:1266-1268`)로 끝난다. 이때 Worker·Beat는
  이미 새 리비전이다(`api|worker|beat` 분기 `:1519-1529`, `all` `:1562-1569`) — 감사 주장 확인. 롤백은 사람이
  `bash scripts/deploy.sh rollback`을 쳐야 한다.
- `run_rollback`(`:1363-1393`)은 `.deploy-rollback`의 서비스마다 `update-traffic --to-revisions=<rev>=100`을 하고,
  비활성 선택 시크릿을 참조하는 리비전이면 멈춘다(`require_rollback_secrets_enabled :1347-1361`). 실패 시 `fail`(exit)을
  쓰므로 서브셸로 감싸야 결과를 받아 메시지를 남길 수 있다.
- 롤백은 트래픽만이다 — 이미 적용된 마이그레이션은 그대로(추가형 전제, `run_migration` 주석). RedBeat 재조정도 새
  이미지 기준으로 이미 적용됐지만, 옛 Beat는 시작 시 자기 정적 스케줄을 RedBeat에 다시 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`_FAKE_GCLOUD`에 (2-9b 항목 뒤) 추가:

```python
        '  "run jobs execute reputation-production-readiness"*)',
        '    exit "${FAKE_READINESS_EXIT:-0}"',
        "    ;;",
```

파일 끝에 추가:

```python
def test_readiness_failure_rolls_traffic_back_automatically(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", FAKE_READY_REVISION="reputation-api-00240-old", FAKE_READINESS_EXIT="1"
    )
    assert result.returncode != 0
    assert "자동 롤백" in result.stderr
    traffic = [l for l in commands.splitlines() if l.startswith("gcloud run services update-traffic")]
    # worker·beat·api 모두 배포 전 리비전으로 되돌린다(api는 아직 바뀌지 않았어도 같은 좌표로 고정).
    assert {l.split()[4] for l in traffic} == {"reputation-worker", "reputation-beat", "reputation-api"}
    assert all("--to-revisions=reputation-api-00240-old=100" in l for l in traffic)
    # API는 readiness 뒤에 배포하므로 배포되지 않는다.
    assert "gcloud run deploy reputation-api" not in commands
```

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py::test_readiness_failure_rolls_traffic_back_automatically -v`
  Expected: FAIL — `assert "자동 롤백" in result.stderr`(지금은 수동 안내만), update-traffic 없음

- [ ] **Step 3: 최소 구현** — `scripts/deploy.sh`

`run_rollback` 함수 정의 바로 뒤에 추가:

```bash
# 배포 뒤 검사(readiness·공개 표면)가 실패하면 사람을 기다리지 않고 배포 시작 전 리비전으로 트래픽을 되돌린다.
# run_rollback은 실패 시 fail(exit)하므로 서브셸로 부르고 결과에 따라 메시지를 남긴다.
rollback_after_failed_gate() {
  local reason="$1"
  info "${reason} — 배포 시작 전 리비전으로 자동 롤백합니다."
  if ( run_rollback ); then
    fail "${reason}. 배포 시작 전 리비전으로 트래픽을 자동으로 되돌렸습니다(DB 마이그레이션은 그대로입니다)."
  fi
  fail "${reason}. 자동 롤백도 실패했습니다 — bash scripts/deploy.sh rollback 으로 다시 시도하고 서비스 상태를 확인하세요."
}
```

`run_production_readiness_gate`의

```bash
  gcloud run jobs execute reputation-production-readiness --region="$REGION" --wait \
    || fail "새 배포의 자동 작업 준비 확인에 실패했습니다. 트래픽 승격을 중단하고 롤백 좌표를 확인하세요."
```

를 다음으로 바꾼다:

```bash
  gcloud run jobs execute reputation-production-readiness --region="$REGION" --wait \
    || rollback_after_failed_gate "새 배포의 자동 작업 준비 확인(readiness)에 실패했습니다"
```

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -v` → 전부 PASS.

- [ ] **Step 5: 커밋**
  `git add scripts/deploy.sh scripts/test_deploy_runtime.py && git commit -m "feat(deploy): readiness 실패 시 배포 전 리비전으로 자동 롤백한다"`

**계약 영향:** CLAUDE.md "검증과 배포" 절에 "readiness 또는 배포 뒤 공개 표면 확인이 실패하면 `deploy.sh`가 배포 시작 전 리비전으로
트래픽을 자동 롤백한다(마이그레이션은 되돌리지 않는다)."를 추가. 0084 이후 일반 롤백 금지 안내(`run_rollback`의 info, `:1391`)와
충돌하는 릴리스에서는 자동 롤백도 같은 위험을 가진다 — 그 릴리스의 배포 기록에 "자동 롤백 금지 → `AUTO_ROLLBACK=0`" 같은 스위치가
필요한지 대표·개발 판단(아래 질문).

**위험·롤백:** readiness가 일시 장애로 실패해도 롤백한다(보수적). 같은 릴리스 재시도는 2-9b가 허용한다. revert로 이전 동작.

---

### Task 2-9d: API 배포 뒤 공개 표면 smoke — API release와 병원 헬스, 실패 시 자동 롤백

**Files:**
- Modify: `backend/app/main.py:217-220`(`/api/v1/health/live`)
- Test: `backend/tests/test_health.py`
- Create: `scripts/deploy_smoke.py`, `scripts/test_deploy_smoke.py`
- Modify: `scripts/deploy.sh`(새 함수 + 메인 분기), `scripts/test_deploy_runtime.py`(`_make_project` 스텁 + 새 테스트)

**조사 결과:**
- **감사와 다르다:** 런북의 병원 헬스(`docs/ops/deployment-runbook.md:282`) `release`는 **Site**의 `K_REVISION`이다
  (`site/lib/reputation-health.ts` `publicRelease()`), API 배포 SHA가 아니다. 그래서 "release를 배포 SHA와 비교"는 그대로는
  불가능하다. 대신 ① API가 LB 경로 `/api/v1/health/live`에 `REPUTATION_RELEASE_REVISION`을 돌려주게 하고(현재 `{"status":"ok"}`뿐,
  `main.py:217-220`) 그 값이 이번 배포 SHA인지, ② 병원 헬스(`https://{host}/.well-known/reputation-health`)가 200이고
  `slug`·`canonical_host`가 맞고 `release`가 지금 Site가 서비스하는 리비전인지 본다. 병원 헬스는 Site가 API
  `/site/hospitals/health/by-domain/{host}`(`api/public/site.py:137-176`)를 부르므로 새 API를 실제로 지난다.
- 병원 목록: `GET https://{PUBLIC_DOMAIN}/api/v1/public/site/hospitals`(`api/public/site.py:179-186`, 공개 병원만)가 `slug`·`aeo_domain`을
  준다. 공개 호스트는 자기 도메인이 있으면 그것, 없으면 `{slug}.{PUBLIC_DOMAIN}`(`_platform_subdomain_slug`, `:80-97`).
- 오탐 롤백 방지: 이미 깨져 있던 병원(예: 자기 도메인 인증서 문제)으로 롤백하지 않도록 **배포 전 기준선**에서 정상이던 호스트만
  배포 뒤 검사한다.
- PUBLIC_DOMAIN은 `api` 대상에서 필수가 아니다 → `SITE_BASE_URL`(운영 `https://reputation.motionlabs.kr`, `.env.production.example:86`)에서
  호스트를 뽑는다. 배포 전에 알 수 없으면 mutation 전에 멈춘다.
- 테스트 프로젝트는 네트워크가 없다 → `_make_project`가 `scripts/deploy_smoke.py` 자리에 기록만 하는 스텁을 쓴다. 실제 스크립트는
  `scripts/test_deploy_smoke.py`에서 가짜 fetch로 검사한다.
- 공개 응답에 커밋 SHA가 노출된다(비공개 저장소). 원하지 않으면 SHA의 sha256 앞 12자로 바꿀 수 있다(아래 질문).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_health.py` 끝에 추가:

```python
def test_lb_liveness_reports_the_release_revision(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REPUTATION_RELEASE_REVISION", "abc1234def5678")
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    # deploy_smoke.py가 LB 너머 API가 이번 배포 소스인지 이 값으로 확인한다.
    assert response.json() == {"status": "ok", "release": "abc1234def5678"}
```

`scripts/test_deploy_smoke.py` (새 파일):

```python
import json

from scripts.deploy_smoke import baseline_hosts, public_hosts, verify


def _hospitals() -> bytes:
    return json.dumps([
        {"slug": "jangpyeon", "aeo_domain": None},
        {"slug": "custom", "aeo_domain": "Clinic.Example.TEST"},
        {"slug": "", "aeo_domain": None},
    ]).encode()


def _health(slug: str, host: str, release: str = "site-00143-mvd") -> bytes:
    return json.dumps({"hospital_id": "h", "slug": slug, "canonical_host": host, "release": release}).encode()


def _fetcher(responses: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    def fetch(url: str) -> tuple[int, bytes]:
        calls.append(url)
        return responses.get(url, (0, b""))

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_public_hosts_prefer_the_custom_domain() -> None:
    assert public_hosts(json.loads(_hospitals()), "reputation.example.test") == [
        ("jangpyeon.reputation.example.test", "jangpyeon"),
        ("clinic.example.test", "custom"),
    ]


def test_baseline_keeps_only_hosts_that_are_healthy_now() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/public/site/hospitals": (200, _hospitals()),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test")),
        "https://clinic.example.test/.well-known/reputation-health": (503, b""),
    })
    assert baseline_hosts("reputation.example.test", fetch, attempts=1, delay=0) == [
        ("jangpyeon.reputation.example.test", "jangpyeon")
    ]


def test_verify_passes_when_api_release_and_hospitals_match() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"status":"ok","release":"sha-new"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test")),
    })
    problems = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="site-00143-mvd", fetch=fetch, attempts=1, delay=0,
    )
    assert problems == []


def test_verify_reports_an_old_api_release_and_a_broken_hospital() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"status":"ok","release":"sha-old"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("other", "jangpyeon.reputation.example.test")),
    })
    problems = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="", fetch=fetch, attempts=2, delay=0,
    )
    assert problems == [
        "API release가 sha-new가 아닙니다(받은 값: sha-old)",
        "jangpyeon.reputation.example.test: 다른 병원(other)이 응답했습니다",
    ]
    # API 확인은 재시도한다(LB가 새 리비전으로 옮겨 가는 동안).
    assert fetch.calls.count("https://reputation.example.test/api/v1/health/live") == 2


def test_verify_checks_the_site_release_when_given() -> None:
    fetch = _fetcher({
        "https://reputation.example.test/api/v1/health/live": (200, b'{"release":"sha-new"}'),
        "https://jangpyeon.reputation.example.test/.well-known/reputation-health": (
            200, _health("jangpyeon", "jangpyeon.reputation.example.test", release="site-00142-old")),
    })
    problems = verify(
        "reputation.example.test", [("jangpyeon.reputation.example.test", "jangpyeon")],
        api_release="sha-new", site_release="site-00143-mvd", fetch=fetch, attempts=1, delay=0,
    )
    assert problems == ["jangpyeon.reputation.example.test: Site 리비전이 site-00143-mvd가 아닙니다(site-00142-old)"]
```

`scripts/test_deploy_runtime.py`:

`_make_project`의 `deploy_env.py` 복사 줄 뒤에 추가(스텁 — 실제 네트워크를 쓰지 않는다):

```python
    _write_executable(
        project / "scripts" / "deploy_smoke.py",
        "\n".join([
            "#!/usr/bin/env python3",
            "import os, sys",
            "with open(os.environ['FAKE_COMMAND_LOG'], 'a') as log:",
            "    log.write('SMOKE ' + ' '.join(sys.argv[1:]) + '\\n')",
            "if sys.argv[1] == 'baseline':",
            "    out = sys.argv[sys.argv.index('--out') + 1]",
            "    open(out, 'w').write('jangpyeon.example.test jangpyeon\\n')",
            "    raise SystemExit(0)",
            "raise SystemExit(int(os.environ.get('FAKE_SMOKE_VERIFY_EXIT', '0')))",
            "",
        ]),
    )
```

파일 끝에 추가:

```python
def test_api_deploy_runs_a_baseline_then_verifies_the_new_release(tmp_path: Path) -> None:
    result, commands = _run_target(tmp_path, "api", FAKE_READY_REVISION="reputation-site-00143-mvd")
    assert result.returncode == 0, result.stderr
    smoke = [l for l in commands.splitlines() if l.startswith("SMOKE ")]
    assert smoke[0].startswith("SMOKE baseline --public-domain reputation.example.test")
    assert smoke[1].startswith("SMOKE verify --public-domain reputation.example.test")
    assert "--api-release test-source-revision" in smoke[1]
    assert "--site-release reputation-site-00143-mvd" in smoke[1]
    # 기준선은 첫 mutation 전, 확인은 API 배포 뒤.
    assert commands.index("SMOKE baseline") < commands.index("docker build")
    assert commands.index("gcloud run deploy reputation-api") < commands.index("SMOKE verify")


def test_failed_smoke_after_api_deploy_rolls_back(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", FAKE_READY_REVISION="reputation-api-00240-old", FAKE_SMOKE_VERIFY_EXIT="1"
    )
    assert result.returncode != 0
    assert "공개 표면 확인" in result.stderr
    assert "자동 롤백" in result.stderr
    assert commands.index("SMOKE verify") < commands.index("gcloud run services update-traffic")


def test_smoke_domain_comes_from_site_base_url_without_public_domain(tmp_path: Path) -> None:
    project, fake_bin, command_log = _make_project(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.production.example", project / ".env.production")
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", "api"],
        cwd=project,
        env=_clean_env(fake_bin, command_log, SKIP_ASSET_BUCKET_PREFLIGHT="1"),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SMOKE baseline --public-domain reputation.motionlabs.kr" in command_log.read_text()
```

- [ ] **Step 2: 실패 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_health.py::test_lb_liveness_reports_the_release_revision -v`
  Expected: FAIL (`{'status': 'ok'}` != 기대값)
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_smoke.py scripts/test_deploy_runtime.py -k "smoke or deploy_smoke" -v`
  Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.deploy_smoke'`, `"SMOKE " not in commands`

- [ ] **Step 3: 최소 구현**

`backend/app/main.py:217-220`:

```python
@app.get("/api/v1/health/live")
async def liveness_via_lb():
    # 배포 확인(scripts/deploy_smoke.py)이 LB 너머 API가 이번 배포 소스인지 이 값으로 본다.
    return {"status": "ok", "release": settings.REPUTATION_RELEASE_REVISION.strip() or None}
```

`scripts/deploy_smoke.py` (새 파일):

```python
#!/usr/bin/env python3
"""배포 뒤 공개 표면 확인 — API release와 병원 헬스(`/.well-known/reputation-health`).

  baseline --public-domain D --out FILE
      배포 전 지금 정상인 병원 공개 호스트를 FILE에 적는다(이미 깨진 병원으로 롤백하지 않기 위해).
  verify --public-domain D --hosts FILE --api-release SHA [--site-release REV]
      API `/api/v1/health/live`의 release가 SHA인지, FILE의 병원마다 헬스가 200이고 slug·canonical_host가 맞고
      (주어지면) Site 리비전이 REV인지 본다. 문제가 있으면 한 줄씩 출력하고 1로 끝난다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

Fetch = Callable[[str], tuple[int, bytes]]


def http_get(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"Cache-Control": "no-cache", "User-Agent": "reputation-deploy-smoke"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — https 고정
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def _json(body: bytes) -> object:
    try:
        return json.loads(body or b"null")
    except ValueError:
        return None


def public_hosts(hospitals: object, public_domain: str) -> list[tuple[str, str]]:
    """병원마다 공개 호스트 하나 — 자기 도메인이 있으면 그것, 없으면 `{slug}.{public_domain}`."""
    hosts: list[tuple[str, str]] = []
    for item in hospitals if isinstance(hospitals, list) else []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        custom = str(item.get("aeo_domain") or "").strip().lower()
        hosts.append((custom or f"{slug}.{public_domain}", slug))
    return hosts


def _health_problem(host: str, slug: str, status: int, body: bytes, site_release: str) -> str | None:
    if status != 200:
        return f"{host}: 헬스가 HTTP {status or '연결 실패'}입니다"
    payload = _json(body)
    if not isinstance(payload, dict):
        return f"{host}: 헬스 응답을 읽지 못했습니다"
    if payload.get("slug") != slug:
        return f"{host}: 다른 병원({payload.get('slug')})이 응답했습니다"
    if payload.get("canonical_host") != host:
        return f"{host}: canonical_host가 {payload.get('canonical_host')}입니다"
    if site_release and payload.get("release") != site_release:
        return f"{host}: Site 리비전이 {site_release}가 아닙니다({payload.get('release')})"
    return None


def _with_retries(check: Callable[[], str | None], attempts: int, delay: float) -> str | None:
    problem: str | None = None
    for attempt in range(attempts):
        problem = check()
        if problem is None:
            return None
        if attempt < attempts - 1:
            time.sleep(delay)
    return problem


def baseline_hosts(public_domain: str, fetch: Fetch, *, attempts: int = 3, delay: float = 5.0) -> list[tuple[str, str]]:
    status, body = fetch(f"https://{public_domain}/api/v1/public/site/hospitals")
    if status != 200:
        return []
    healthy: list[tuple[str, str]] = []
    for host, slug in public_hosts(_json(body), public_domain):
        problem = _with_retries(
            lambda h=host, s=slug: _health_problem(h, s, *fetch(f"https://{h}/.well-known/reputation-health"), ""),
            attempts,
            delay,
        )
        if problem is None:
            healthy.append((host, slug))
    return healthy


def verify(
    public_domain: str,
    hosts: list[tuple[str, str]],
    *,
    api_release: str,
    site_release: str,
    fetch: Fetch,
    attempts: int = 6,
    delay: float = 5.0,
) -> list[str]:
    problems: list[str] = []

    def api_problem() -> str | None:
        status, body = fetch(f"https://{public_domain}/api/v1/health/live")
        payload = _json(body)
        release = payload.get("release") if isinstance(payload, dict) else None
        if status == 200 and release == api_release:
            return None
        return f"API release가 {api_release}가 아닙니다(받은 값: {release})"

    api = _with_retries(api_problem, attempts, delay)
    if api:
        problems.append(api)
    for host, slug in hosts:
        problem = _with_retries(
            lambda h=host, s=slug: _health_problem(
                h, s, *fetch(f"https://{h}/.well-known/reputation-health"), site_release
            ),
            min(attempts, 3),
            delay,
        )
        if problem:
            problems.append(problem)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline")
    base.add_argument("--public-domain", required=True)
    base.add_argument("--out", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--public-domain", required=True)
    check.add_argument("--hosts", required=True)
    check.add_argument("--api-release", required=True)
    check.add_argument("--site-release", default="")
    args = parser.parse_args(argv)

    if args.command == "baseline":
        healthy = baseline_hosts(args.public_domain, http_get)
        Path(args.out).write_text("".join(f"{host} {slug}\n" for host, slug in healthy), encoding="utf-8")
        print(f"배포 전 정상 병원 {len(healthy)}곳", file=sys.stderr)
        return 0

    hosts = [
        tuple(line.split(" ", 1))
        for line in Path(args.hosts).read_text(encoding="utf-8").splitlines()
        if " " in line
    ]
    problems = verify(
        args.public_domain,
        hosts,  # type: ignore[arg-type]
        api_release=args.api_release,
        site_release=args.site_release,
        fetch=http_get,
    )
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/deploy.sh` — `rollback_after_failed_gate` 뒤에 추가:

```bash
# ─── 배포 뒤 공개 표면 확인 ─────────────────────────────────────────
# API 배포 뒤 LB 너머 API가 이번 릴리스인지, 배포 전 정상이던 병원 헬스가 그대로인지 본다. 실패하면 자동 롤백.
SMOKE_HOSTS_FILE=""

smoke_public_domain() {
  if [[ -n "$PUBLIC_DOMAIN" ]]; then
    printf '%s' "$PUBLIC_DOMAIN"
    return
  fi
  read_env_file_value SITE_BASE_URL | sed -E 's#^https?://##; s#/.*$##'
}

capture_smoke_baseline() {
  if [[ "${SKIP_POST_DEPLOY_SMOKE:-0}" == "1" ]]; then
    info "SKIP_POST_DEPLOY_SMOKE=1 — 배포 뒤 공개 표면 확인을 건너뜁니다."
    return
  fi
  local domain
  domain="$(smoke_public_domain)"
  [[ -n "$domain" ]] \
    || fail "배포 뒤 확인할 공개 도메인을 알 수 없습니다. PUBLIC_DOMAIN이나 SITE_BASE_URL을 설정하세요."
  SMOKE_HOSTS_FILE="$(mktemp)"; TEMP_ENV_FILES+=("$SMOKE_HOSTS_FILE")
  python3 "${SCRIPT_DIR}/deploy_smoke.py" baseline --public-domain "$domain" --out "$SMOKE_HOSTS_FILE" \
    || fail "배포 전 공개 표면 기준선을 만들지 못했습니다."
}

run_post_deploy_smoke() {
  [[ -n "$SMOKE_HOSTS_FILE" ]] || return 0
  local domain site_release
  domain="$(smoke_public_domain)"
  site_release="$(gcloud run services describe reputation-site --region="$REGION" --project="$PROJECT_ID" \
      --format='value(status.traffic[0].revisionName)' 2>/dev/null || true)"
  info "배포 뒤 공개 표면 확인 중(${domain})..."
  python3 "${SCRIPT_DIR}/deploy_smoke.py" verify --public-domain "$domain" --hosts "$SMOKE_HOSTS_FILE" \
      --api-release "$RELEASE_REVISION" --site-release "$site_release" \
    || rollback_after_failed_gate "배포 뒤 공개 표면 확인(API release·병원 헬스)에 실패했습니다"
  ok "배포 뒤 공개 표면 확인 완료"
}
```

메인 `case`:
- `backend)`: `require_deploy_slot` 다음에 `capture_smoke_baseline`; 마지막 `deploy_api "$IMAGE_URL"` 다음에 `run_post_deploy_smoke`.
- `api|worker|beat)`: `require_deploy_slot` 다음에 `if [[ "$TARGET" == "api" ]]; then capture_smoke_baseline; fi`;
  `api` 블록의 `deploy_api "$IMAGE_URL"` 다음에 `run_post_deploy_smoke`.
- `all)`: `require_deploy_slot` 다음에 `capture_smoke_baseline`; `deploy_admin "$ADMIN_IMAGE_URL"` 다음에 `run_post_deploy_smoke`.

- [ ] **Step 4: 통과 확인**
  Run: `cd backend && .venv/bin/python -m pytest tests/test_health.py -v` → PASS.
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_smoke.py scripts/test_deploy_runtime.py -v` → 전부 PASS.
  운영 읽기 전용 확인(배포 전 아무 때나): `python3 scripts/deploy_smoke.py baseline --public-domain reputation.motionlabs.kr --out /tmp/hosts.txt && cat /tmp/hosts.txt`
  Expected: 공개 병원 9곳의 호스트가 한 줄씩(현재 운영 9곳).

- [ ] **Step 5: 커밋**
  `git add backend/app/main.py backend/tests/test_health.py scripts/deploy_smoke.py scripts/test_deploy_smoke.py scripts/deploy.sh scripts/test_deploy_runtime.py && git commit -m "feat(deploy): API 배포 뒤 release·병원 헬스를 확인하고 실패하면 자동 롤백한다"`

**계약 영향:** CLAUDE.md "검증과 배포"의 "병원별 헬스는 HTTP 200만 보지 않고 hospital ID·canonical host·현재 리비전까지 확인한다"
뒤에 "`deploy.sh`가 API 배포 뒤 이를 자동으로 확인한다 — `/api/v1/health/live`의 `release`가 배포 SHA인지, 배포 전 정상이던
병원 헬스의 slug·canonical_host·Site 리비전이 맞는지. 실패하면 자동 롤백."을 추가. 런북 "검증할 증거" 표의 API 행 갱신.
첫 배포는 새 API가 `release`를 처음 내보내므로 옛 API는 `release` 키가 없다 — 배포 뒤 확인만 하므로 문제없다.

**위험·롤백:** 공개 표면 일시 장애(LB·인증서)로 오탐 롤백 가능 — 기준선·재시도(API 6회×5초, 병원 3회)로 줄인다.
`SKIP_POST_DEPLOY_SMOKE=1`로 끌 수 있고 로그에 남는다. revert로 이전 동작.

---

### Task 2-9e: `--unpin` — 고정된 트래픽을 이번 배포의 새 리비전으로 옮긴다

**Files:**
- Modify: `scripts/deploy.sh:1278-1345`(`current_ready_revision`, `require_serving_latest`, `capture_rollback_point`)
- Test: `scripts/test_deploy_runtime.py`

**조사 결과:**
- PR #238이 고정 트래픽을 배포 전에 **막는다**(`capture_rollback_point :1312-1322`, 테스트 `:1893-1918`). 해결은 사람이
  `update-traffic --to-latest`를 먼저 치는 것이다. 그런데 배포 **전에** `--to-latest`를 하면 지금의 최신 Ready 리비전(고정을
  건 이유일 수 있는 리비전)으로 트래픽이 먼저 옮겨 간다 — 안전하지 않다.
- 안전한 순서: 롤백 좌표는 **지금 트래픽을 받는 리비전**(`status.traffic[0].revisionName`)으로 기록하고, 새 리비전 배포 직후
  `update-traffic --to-latest`로 옮긴 뒤 `require_serving_latest`(`:1288-1301`)가 확인한다. 배포 함수 5개가 모두 끝에서
  `require_serving_latest`를 부르므로 그 안에서 한 번 처리하면 된다.
- 롤백(`run_rollback`)은 `--to-revisions=<rev>=100`으로 다시 고정한다 — 롤백은 원래 그래야 한다.

- [ ] **Step 1: 실패하는 테스트 작성** — 파일 끝에 추가

```python
def test_unpin_moves_pinned_traffic_to_the_new_revision_after_deploy(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", "--unpin",
        FAKE_READY_REVISION="reputation-api-00239-mqs", FAKE_TRAFFIC_PINNED="1",
    )
    assert result.returncode == 0, result.stderr
    assert "고정 트래픽을 이번 배포에서 풉니다" in result.stderr
    lines = commands.splitlines()
    deploy_at = next(i for i, l in enumerate(lines) if l.startswith("gcloud run deploy reputation-api"))
    unpin_at = next(
        i for i, l in enumerate(lines)
        if l.startswith("gcloud run services update-traffic reputation-api") and "--to-latest" in l
    )
    # 새 리비전이 생긴 뒤에만 옮긴다 — 배포 전에 옮기면 고정의 이유였을 리비전으로 트래픽이 간다.
    assert deploy_at < unpin_at
    state = (tmp_path / "project" / ".deploy-rollback").read_text()
    assert "reputation-api=reputation-api-00239-mqs" in state


def test_pinned_traffic_without_unpin_still_stops(tmp_path: Path) -> None:
    result, commands = _run_target(
        tmp_path, "api", FAKE_READY_REVISION="reputation-api-00239-mqs", FAKE_TRAFFIC_PINNED="1"
    )
    assert result.returncode != 0
    assert "--unpin" in result.stderr
    assert "gcloud run deploy" not in commands
```

- [ ] **Step 2: 실패 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -k "unpin" -v`
  Expected: 첫 테스트 FAIL(고정이면 멈춘다), 둘째 테스트 FAIL(안내에 `--unpin` 없음)

- [ ] **Step 3: 최소 구현** — `scripts/deploy.sh`

`current_ready_revision` 뒤에 추가:

```bash
# 지금 트래픽을 받는 리비전. 고정을 풀 때의 롤백 좌표다(최신 Ready가 아니다).
current_serving_revision() {
  local service="$1"
  gcloud run services describe "$service" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format='value(status.traffic[0].revisionName)' 2>/dev/null || true
}

UNPIN_SERVICES=()

service_is_unpinning() {
  local service="$1" name
  for name in ${UNPIN_SERVICES[@]+"${UNPIN_SERVICES[@]}"}; do
    [[ "$name" == "$service" ]] && return 0
  done
  return 1
}
```

`require_serving_latest` 본문 첫 줄(`local service="$1" ready serving`) 다음에 추가:

```bash
  if service_is_unpinning "$service"; then
    # --unpin: 새 리비전이 Ready가 된 지금 고정 트래픽을 옮긴다(배포 전에는 옮기지 않는다).
    gcloud run services update-traffic "$service" --region="$REGION" --project="$PROJECT_ID" --to-latest \
      || fail "${service}: 고정 트래픽을 새 리비전으로 옮기지 못했습니다. gcloud run services describe ${service}로 확인하세요."
  fi
```

`capture_rollback_point`의 첫 루프를 다음으로 바꾼다:

```bash
  for service in "$@"; do
    revision="$(current_ready_revision "$service")"
    if [[ -n "$revision" ]] && ! service_traffic_follows_latest "$service"; then
      if [[ "$UNPIN_TRAFFIC" == "1" ]]; then
        UNPIN_SERVICES+=("$service")
        info "${service}: 고정 트래픽을 이번 배포에서 풉니다(--unpin). 롤백 좌표는 지금 트래픽을 받는 리비전입니다."
        continue
      fi
      fail "${service}의 트래픽이 최신 리비전이 아닌 특정 리비전에 고정돼 있습니다(현재 ${revision}). 이대로 배포하면 새 리비전이
   만들어지자마자 은퇴하고 옛 코드가 계속 서비스됩니다. 고정이 의도한 것이 아니면 --unpin을 붙여 다시 배포하세요
   (새 리비전이 Ready가 된 뒤 트래픽을 옮기고, 롤백 좌표는 지금 서비스 중인 리비전으로 남깁니다)."
    fi
  done
```

둘째(기록) 루프의 `revision="$(current_ready_revision "$service")"`를 다음으로 바꾼다:

```bash
    if service_is_unpinning "$service"; then
      revision="$(current_serving_revision "$service")"
    else
      revision="$(current_ready_revision "$service")"
    fi
```

기존 테스트 `test_deploy_stops_before_mutation_when_traffic_is_pinned`(`:1893-1918`)의 `assert "--to-latest" in result.stderr`를
`assert "--unpin" in result.stderr`로 바꾼다(안내가 바뀌었다).

- [ ] **Step 4: 통과 확인**
  Run: `backend/.venv/bin/python -m pytest scripts/test_deploy_runtime.py -v` → 전부 PASS.

- [ ] **Step 5: 커밋**
  `git add scripts/deploy.sh scripts/test_deploy_runtime.py && git commit -m "feat(deploy): --unpin으로 고정 트래픽을 새 리비전 배포 뒤에 옮긴다"`

**계약 영향:** 없음(CLAUDE.md는 고정 트래픽을 다루지 않는다). 런북의 PR #238 안내("먼저 `--to-latest`")를 "`--unpin`"으로 갱신.

**위험·롤백:** `--unpin`은 사람이 명시해야만 동작한다. revert.

---

### Task 2-10 (개요만): GitHub Actions `deploy.yml` — Workload Identity Federation

단계별 코드는 쓰지 않는다. 2-9가 끝난 뒤의 설계 개요다.

**필요한 GCP 자원(Terraform 새 파일 `terraform/github_deploy.tf` 권장):**
- `google_iam_workload_identity_pool.github`(`github`) + `google_iam_workload_identity_pool_provider.github`(OIDC, issuer
  `https://token.actions.githubusercontent.com`, attribute mapping `google.subject=assertion.sub`, `attribute.repository`,
  `attribute.ref`, `attribute.workflow_ref`; **attribute_condition** `assertion.repository=="wjlee930501/reputation" &&
  assertion.ref.startsWith("refs/tags/release-")`).
- 배포 전용 SA `reputation-deployer@<project>`(키 파일 없음). 권한:
  - `roles/run.admin`(서비스 배포·update-traffic·Job create/update/execute) — 최소화하려면 `run.developer` + 아래 actAs.
  - `roles/iam.serviceAccountUser` on `reputation-sa`, `reputation-site-sa`, `reputation-admin-sa`(배포 시 런타임 SA 지정).
  - `roles/artifactregistry.writer` on 저장소 `reputation`.
  - `roles/secretmanager.viewer`(versions describe — `probe_secret_*`) + `roles/secretmanager.secretAccessor` **on
    `SLACK_WEBHOOK_URL`, `SLACK_WEBHOOK_URL_DEV`만**(`verify_slack_webhooks`가 값을 읽어 탐침).
  - `roles/storage.objectAdmin` on `reputation-reports-<project>` — 조건 `resource.name.startsWith(".../objects/ops/")`
    (2-9b 잠금), `roles/storage.legacyBucketReader` on images 버킷(`require_asset_bucket`의 buckets describe).
  - `roles/cloudsql.viewer`(`require_cloudsql_app_user`의 `sql users list`), `roles/logging.viewer`(VPC preflight 로그 읽기).
  - `roles/iam.workloadIdentityUser` 바인딩: `principalSet://iam.googleapis.com/<pool>/attribute.repository/wjlee930501/reputation`.
- GitHub 쪽: Repository variables `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER`(provider 전체 이름), `DEPLOY_SA`,
  `PUBLIC_DOMAIN`, `ADMIN_DOMAIN`. **시크릿 없음**(JSON 키 금지). Environment `production` + required reviewer(대표 또는 지정자) —
  배포 승인 클릭 하나만 사람 몫으로 남긴다.

**게이트 단계(모두 `scripts/deploy.sh` 함수 재사용):**
1. 트리거: `push: tags: ['release-*']`(main에서 만든 태그만 — 첫 job에서 `git merge-base --is-ancestor $GITHUB_SHA origin/main` 검사) +
   `workflow_dispatch`(input `target`: api|all|site|admin).
2. CI 녹색 확인: 같은 SHA의 `CI` 워크플로 결론이 success인지 `gh api repos/:owner/:repo/commits/$SHA/check-runs`로 확인. 아니면 중단.
3. `concurrency: { group: deploy-production, cancel-in-progress: false }` — 동시 배포 금지(GCS 잠금과 이중 방어).
4. 추가형 마이그레이션 검사(새 스크립트 `scripts/check_additive_migrations.py`): 운영 DB head(readiness Job 로그 또는
   `inspect-schema` Job)부터 새 head까지의 `alembic/versions/*.py` `upgrade()`에 `op.drop_table`, `op.drop_column`,
   `op.rename_table`, `op.alter_column(..., type_=...)`, `nullable=False`이면서 `server_default` 없는 `add_column`/`alter_column`,
   `op.execute("DROP ...")`가 있으면 실패. 예외는 파일 머리 주석 `# deploy: non-additive-approved <사유>`로만.
5. 인증: `google-github-actions/auth@v2`(workload_identity_provider, service_account) → `google-github-actions/setup-gcloud@v2` →
   `gcloud auth configure-docker ${REGION}-docker.pkg.dev`.
6. 배포: `bash scripts/deploy.sh ${{ inputs.target || 'api' }}` — 2-9의 env 정본(운영 env, 로컬 파일 없음), 시간대·하루 잠금,
   readiness 자동 롤백, smoke 자동 롤백이 그대로 적용된다. env 변경은 저장소의 리뷰된 파일(예: `deploy/env-changes.env`)을
   `.env.production`으로 복사하고 `--accept-env-diff`를 붙이는 별도 input으로만.
7. 사후: `.deploy-rollback`과 `.omo/evidence/deploy-preflight/*`를 `actions/upload-artifact`로 올리고, 롤백 좌표를
   `gs://reputation-reports-<project>/ops/deploy-rollback/<SHA>.txt`에도 복사(러너가 사라져도 `DEPLOY_ROLLBACK_STATE_FILE`로 롤백 가능).
   별도 `rollback.yml`(workflow_dispatch)이 그 파일을 받아 `bash scripts/deploy.sh rollback`.

**`deploy.sh`가 작업 PC를 가정하는 지점(개선 목록):**
- Docker 데몬: `command -v docker` 사전 검증(`:228-230`), `build_and_push`(`:642-658`), `build_and_push_site`(`:807-828`),
  `build_and_push_admin`(`:830-846`)이 `docker build --platform linux/amd64` + `docker push`. GitHub ubuntu 러너는 Docker가
  있고 amd64라 그대로 동작하지만 `gcloud auth configure-docker`가 필요하다. 대안은 `gcloud builds submit`(Cloud Build, 러너에
  Docker 불필요 — 이때 `cloudbuild.builds.editor` 추가).
- 로컬 파일: `.env.production`(2-9a 뒤 backend는 불필요, site/admin의 `read_env_file_value` 몇 개는 여전히 파일을 읽는다 —
  `GOOGLE_SITE_VERIFICATION`, `GA_MEASUREMENT_ID`, `GCP_STORAGE_BUCKET`: 2-10에서 Actions 변수로 넘기거나 같은 방식으로 Site
  서비스 env에서 읽게 한다), `.deploy-rollback`(`:1276`), 증거 디렉터리 `.omo/evidence/deploy-preflight`(`:1449`).
- `git status --porcelain` 깨끗함 검사(`resolve_release_revision :60-73`) — checkout 직후라 통과. `REPUTATION_RELEASE_REVISION`을
  `$GITHUB_SHA`로 넘기는 편이 명확하다.
- `gcloud config get-value project`(`:53`) — `GCP_PROJECT_ID`를 넘기면 쓰지 않는다. `uv`는 `DATABASE_URL`이 있을 때만(러너에는
  없음 → VPC Job 경로).
- 사용자 계정 가정: 오류 안내 문구의 `gcloud auth login`(`:1186-1190` 근처 `require_asset_bucket`) — WIF에서는 의미가 없으므로 문구만 조건부로.

**계약 영향:** CLAUDE.md "배포는 [현재 배포 안내]를 따른다" 절에 "운영 배포는 `release-*` 태그 → GitHub Actions(WIF) → `deploy.sh`.
작업 PC 배포는 비상 경로"를 2-10 완료 후 추가.

---

## 요약 표

| Task | 핵심 변경 | 테스트 |
|---|---|---|
| 2-6a | readiness `pipeline_watchdog_token_configured` | 단위 1 |
| 2-6b | GCP 로그 경보 2개(Scheduler 실패, Slack 경로 실패) | 정적 1 + validate |
| 2-6c | `OPS_ALERT_EMAIL`, `ops_alert_email.py`, 감시 미전달 ALERT 메일 | 단위 7 |
| 2-6d | 운영 채널 사고 + 대체 없음 → 메일(episode당 1) | DB 2 |
| 2-7 | `LivenessRedBeatScheduler` + `/live --beat-tick` | 단위 6 |
| 2-8a | 비용 가드 fail-open ERROR 1회 + 개발 사고 + 자동 복구 | 단위 3 |
| 2-8b | spool 잘림 카운터 → drain이 개발 사고 | 단위 4 |
| 2-8c | USD 상한 — 데이터 없음, 보류 | — |
| 2-9-0~e | 옵션, env 정본, 시간대·잠금, 자동 롤백, smoke, `--unpin` | 셸 통합 ~20 + 순수 ~12 |
| 2-10 | WIF deploy.yml 개요 | — |

## 대표 확인이 필요한 질문

1. `OPS_ALERT_EMAIL` 수신 주소(그리고 Terraform `alert_email`이 실제로 누구에게 가는지 — 저장소에는 예시 `ops@motionlabs.kr`만 있다).
2. GCP 경보(2-6b)만으로 2차 경로가 충분한가, 앱 메일(2-6c/d)까지 둘 것인가. 2-6b는 코드 변경 없이 같은 효과의 대부분을 낸다.
3. 하루 한 번 잠금을 `site`/`admin` 배포에도 걸 것인가(이 계획은 Worker·Beat를 바꾸는 대상에만 건다).
4. 공개 `/api/v1/health/live`에 커밋 SHA를 그대로 노출해도 되는가(대안: sha256 앞 12자).
5. readiness·smoke 실패 시 자동 롤백을 0084 같은 비호환 릴리스에서도 켜 둘 것인가(그런 릴리스는 `AUTO_ROLLBACK=0` 스위치 필요).
6. 2-10의 배포 승인을 사람 클릭(Environment reviewer)으로 남길 것인가.
