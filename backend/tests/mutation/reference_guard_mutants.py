"""Guard-removal mutation table for the reference-source guard PR (fix/reference-source-guard).

For every guard: apply one minimal edit that removes (or disables) the guard via an exact
string replacement (the target must occur exactly once), run the pytest node ids that must
catch it, record CAUGHT (tests failed) / SURVIVED (tests still passed), then restore the file
from a saved in-memory copy (never `git checkout` — nothing is committed).

Before any mutation, the same node ids are run against the unmodified tree; a guard whose
tests do not pass on the clean tree is reported as BASELINE-FAIL (the mutation result would
be meaningless).

Not a pytest module (the file name does not match `test_*.py`); run it directly:
    python3 backend/tests/mutation/reference_guard_mutants.py [--out PATH] [--check-targets]
Paths are derived from this file (BACKEND = backend/). It uses
`backend/.venv/bin/python -m pytest -p no:cacheprovider -q -x <node ids>` with the caller's
environment unchanged. DB-backed node ids (PG) FAIL when their DB env vars are unset (no skip),
so run it inside an isolated harness that exports every DB/Redis URL of the backend job env in
.github/workflows/ci.yml, pointed at throwaway `_test` databases (never a shared or production
database); outside it a PG row reports BASELINE-FAIL, not a mutation result. See README.md.
A row whose every catching test was skipped is reported as SKIPPED-ONLY.
No mutation enables real network access: the offline default fetcher is never mutated.

The script edits files in the working tree and restores each one from an in-memory copy
(never `git checkout`); do not run two copies against the same tree at once.

`--check-targets` only checks that every target string occurs exactly once (no tests run).
`--out PATH` also writes the Markdown table to PATH (default: stdout only).

Exit status: 0 when every mutant is CAUGHT, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PYTHON = BACKEND / ".venv" / "bin" / "python"

RV = "app/services/reference_verification.py"
RP = "app/services/reference_publication.py"
CE = "app/services/content_engine.py"
AS = "app/utils/authority_sources.py"
TASKS = "app/workers/tasks.py"
ADMIN = "app/api/admin/content.py"
RUN_CONTROL = "app/workers/generation_run_control.py"
INCIDENT = "app/workers/generation_incident_control.py"
ROW = "app/services/content_row_state.py"
SWAP = "app/workers/topic_swap_fallback.py"
PLANNER = "app/services/content_target_planner.py"
GAP = "app/services/gap_driven_slots.py"
SPEC = "app/services/specialty_compatibility.py"
NOTIF_COPY = "app/services/notification_copy.py"

T_RV = "tests/test_reference_verification.py"
T_GATE = "tests/test_reference_publication_gate.py"
T_CE = "tests/test_content_engine.py"
T_AS = "tests/test_authority_sources.py"
T_SPEC = "tests/test_specialty_compatibility.py"
T_SLOTS_PG = "tests/test_monthly_slots.py"
T_SWAP_PG = "tests/integration/test_topic_swap_fallback_postgres.py"
T_FAQ = "tests/test_content_compliance_faq.py"
T_WITHHOLD_PG = "tests/integration/test_content_withhold_postgres.py"
T_REQ = "tests/test_reference_requirement.py"
T_EXCL = "tests/test_reference_exclusions.py"
T_PUB = "tests/test_content_publication.py"
REQ = "app/services/reference_requirement.py"
CP = "app/services/content_publication.py"
RETRY = "app/workers/generation_retry_policy.py"
T_OPD = "tests/test_reference_operator_decides.py"
T_ALIAS = "tests/test_reference_curated_alias.py"
T_COPY = "tests/test_reference_operator_copy.py"
T_CLAIM_PG = "tests/integration/test_reference_claim_publisher_postgres.py"
T_LAST_PG = "tests/integration/test_reference_claim_last_run_postgres.py"


@dataclass(frozen=True)
class Mutant:
    guard: str
    location: str  # file:function
    path: str
    target: str
    replacement: str
    tests: tuple[str, ...]
    note: str = ""


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "목록 밖 URL은 실제 GET 통과분만 (GET 못 하면 제거)",
        f"{RV}:judge_fetched_page",
        RV,
        "        reason = REASON_NOT_VERIFIED if fetched.error == FETCH_ERROR_OFFLINE else REASON_UNDETERMINABLE\n"
        "        return judgement(VERDICT_FAIL, reason)",
        "        reason = REASON_NOT_VERIFIED if fetched.error == FETCH_ERROR_OFFLINE else REASON_UNDETERMINABLE\n"
        "        return judgement(VERDICT_PASS, reason)",
        (
            f"{T_RV}::test_outside_list_url_needs_a_real_passing_get",
            f"{T_GATE}::test_admin_patch_offline_rejects_an_unverifiable_outside_url",
        ),
    ),
    Mutant(
        "모델 라벨 제외 (라벨이 글 주제와 맞으면 통과시키던 단락 복원)",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            judged = judge_fetched_page(url, fetched, topic_terms, curated=curated)",
        "            judged = (\n"
        "                PageJudgement(VERDICT_PASS, REASON_PAGE_VERIFIED)\n"
        "                if page_topic_related(str(reference.get('title') or ''), topic_terms)\n"
        "                else judge_fetched_page(url, fetched, topic_terms, curated=curated)\n"
        "            )",
        (
            f"{T_RV}::test_label_that_matches_the_article_cannot_rescue_a_different_real_page",
            f"{T_RV}::test_audit_label_mismatch_rows_are_removed_by_the_real_page",
        ),
    ),
    Mutant(
        "KDCA·아산 빈 템플릿(200 + 빈 문서명)",
        f"{RV}:_is_empty_template",
        RV,
        '        if not re.search(r"[0-9A-Za-z가-힣]", segment):\n            return True',
        '        if not re.search(r"[0-9A-Za-z가-힣]", segment):\n            pass',
        (
            f"{T_RV}::test_kdca_empty_template_served_as_200_without_redirect_is_rejected",
            f"{T_RV}::test_amc_empty_content_id_template_is_rejected",
        ),
    ),
    Mutant(
        "국가암정보센터 통계 화면",
        f"{RV}:_is_stats_or_menu",
        RV,
        "        marker in crumb for crumb in crumbs for marker in _STATS_BREADCRUMBS\n    ):",
        "        marker in crumb for crumb in crumbs for marker in ()\n    ):",
        (f"{T_RV}::test_cancer_statistics_breadcrumb_is_rejected_on_any_path",),
    ),
    Mutant(
        "국가암정보센터 추측 메뉴 코드(목록 밖)",
        f"{RV}:_is_stats_or_menu",
        RV,
        "        if _host_matches(_host(candidate), _CANCER_DOMAIN) and _CANCER_MENU_PATH.match(path):\n            return True",
        "        if _host_matches(_host(candidate), _CANCER_DOMAIN) and _CANCER_MENU_PATH.match(path):\n            pass",
        (f"{T_RV}::test_guessed_cancer_menu_code_outside_the_list_is_rejected_even_when_on_topic",),
    ),
    Mutant(
        "같은 도메인 홈·오류 페이지로의 soft-404",
        f"{RV}:judge_fetched_page",
        RV,
        "    if _is_soft_404(url, final_url):",
        "    if False and _is_soft_404(url, final_url):",
        (f"{T_RV}::test_same_domain_redirect_to_home_is_a_soft_404",),
    ),
    Mutant(
        "죽은 링크 404/410 (수기 목록 포함)",
        f"{RV}:judge_fetched_page",
        RV,
        "    if status in {404, 410}:",
        "    if False:",
        (f"{T_RV}::test_dead_links_are_removed_even_from_the_curated_list",),
    ),
    Mutant(
        "판단 불가(403/429/203) + 목록 밖 → 제거",
        f"{RV}:judge_fetched_page",
        RV,
        "        if status >= 400 and status not in {401, 403, 429}:\n"
        "            return judgement(VERDICT_FAIL, REASON_HTTP_ERROR)\n"
        "        return judgement(VERDICT_FAIL, REASON_UNDETERMINABLE)",
        "        if status >= 400 and status not in {401, 403, 429}:\n"
        "            return judgement(VERDICT_FAIL, REASON_HTTP_ERROR)\n"
        "        return judgement(VERDICT_PASS, REASON_UNDETERMINABLE)",
        (f"{T_RV}::test_undeterminable_outside_list_is_removed_and_curated_is_kept",),
    ),
    Mutant(
        "판단 불가(영문 전용·기관명뿐인 제목) + 목록 밖 → 제거",
        f"{RV}:judge_fetched_page",
        RV,
        "    if not topic or not _HANGUL.search(topic):\n"
        "        # 영문 전용·기관명뿐인 제목은 주제를 판단할 수 없다 → 목록 밖이면 제거.\n"
        "        return judgement(VERDICT_FAIL, REASON_UNDETERMINABLE, topic)",
        "    if not topic or not _HANGUL.search(topic):\n"
        "        # 영문 전용·기관명뿐인 제목은 주제를 판단할 수 없다 → 목록 밖이면 제거.\n"
        "        return judgement(VERDICT_PASS, REASON_UNDETERMINABLE, topic)",
        (f"{T_RV}::test_undeterminable_outside_list_is_removed_and_curated_is_kept",),
    ),
    Mutant(
        "실제 문서 제목이 글 주제와 다르면 제거",
        f"{RV}:judge_fetched_page",
        RV,
        "    if not related:\n        return judgement(VERDICT_FAIL, REASON_UNRELATED, topic)",
        "    if not related:\n        pass",
        (
            f"{T_RV}::test_label_that_matches_the_article_cannot_rescue_a_different_real_page",
            f"{T_RV}::test_audit_label_mismatch_rows_are_removed_by_the_real_page",
        ),
    ),
    Mutant(
        "수기 목록도 카탈로그 주제 대조(무조건 통과 아님)",
        f"{RV}:judge_fetched_page",
        RV,
        "        if curated_topic_relevant(catalog_url, topic_terms):\n"
        "            return judgement(VERDICT_PASS, REASON_CURATED_VERIFIED)\n"
        "        return judgement(VERDICT_FAIL, REASON_UNRELATED)",
        "        return judgement(VERDICT_PASS, REASON_CURATED_VERIFIED)",
        (f"{T_RV}::test_audit_curated_screening_page_on_an_unrelated_article_is_removed",),
    ),
    Mutant(
        "저장된 판정의 신선도(24시간)",
        f"{RV}:check_is_fresh_pass",
        RV,
        '    return _age_within(check.get("checked_at"), now=now, limit=REFERENCE_CHECK_MAX_AGE)',
        "    return True",
        (
            f"{T_RV}::test_gate_requires_a_fresh_pass_for_the_same_url",
            f"{T_GATE}::test_stale_check_is_reverified_before_publishing",
        ),
    ),
    Mutant(
        "같은 URL(지문)의 판정만 인정",
        f"{RV}:reference_gate_status",
        RV,
        "        check = indexed.get(reference_url_fingerprint(url))",
        "        check = next(iter(indexed.values()), None)",
        (
            f"{T_RV}::test_gate_requires_a_fresh_pass_for_the_same_url",
            f"{T_GATE}::test_check_for_a_different_url_does_not_count",
        ),
    ),
    Mutant(
        "08:00 발행기가 재검증 뒤 게이트를 확인",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "        if _has_generated_text(item) and not publication_references_current(item):",
        "        if False:",
        (f"{T_GATE}::test_publisher_never_publishes_when_the_gate_is_not_current",),
    ),
    Mutant(
        "08:00 발행기의 오래된 판정 재검증",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "    reference_refresh = _prefetch_publication_references(\n"
        "        content_id, reference_verifier or ReferenceVerifier(), now_kst=now_kst\n"
        "    )",
        "    reference_refresh = None",
        (
            f"{T_GATE}::test_stale_check_is_reverified_before_publishing",
            f"{T_GATE}::test_still_nothing_holds_publication_with_the_real_cause",
        ),
    ),
    Mutant(
        "07:45 게이트의 재검증(08:00 GET 몰림 방지)",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "        if _has_generated_text(item) and not publication_references_settled(item):\n"
        "            # GET은 잠금·열린 트랜잭션 밖에서 한다",
        "        if False:\n"
        "            # GET은 잠금·열린 트랜잭션 밖에서 한다",
        (
            f"{T_GATE}::test_seven_forty_five_reverifies_stale_references_so_eight_does_not_burst",
            f"{T_GATE}::test_seven_forty_five_holds_and_pages_the_real_cause_when_nothing_survives",
        ),
    ),
    Mutant(
        "관리자 PATCH 참고자료 검증",
        f"{ADMIN}:update_content",
        ADMIN,
        "        patched_reference_checks = await _verify_patched_references(\n"
        "            unlocked_item, body, normalized_refs\n"
        "        )",
        "        patched_reference_checks = []",
        (
            f"{T_GATE}::test_admin_patch_rejects_a_reference_that_fails_verification",
            f"{T_GATE}::test_admin_patch_stores_the_checks_of_accepted_references",
        ),
    ),
    Mutant(
        "관리자 수동 발행의 참고자료 재검증",
        f"{ADMIN}:publish_content",
        ADMIN,
        "    if _has_generated_text(unlocked_item) and not publication_references_settled(unlocked_item):\n"
        "        reference_refresh = await refresh_publication_references(",
        "    if False:\n"
        "        reference_refresh = await refresh_publication_references(",
        (f"{T_GATE}::test_admin_manual_publish_runs_the_same_reference_gate",),
    ),
    Mutant(
        "관리자 수동 발행의 참고자료 게이트(미룸·비교 실패·current 아님이면 409)",
        f"{ADMIN}:publish_content",
        ADMIN,
        "    if (\n"
        "        not reference_applied\n"
        "        or (reference_refresh is not None and reference_refresh.deferred)\n"
        "        or (_has_generated_text(item) and not publication_references_current(item))\n"
        "    ):",
        "    if False:",
        (f"{T_GATE}::test_manual_publish_outage_is_a_retry_later_not_a_missing_reference",),
    ),
    Mutant(
        "치유는 글 주제로 채점한 수기 목록만 (키워드 대조)",
        f"{AS}:select_curated_authority_sources",
        AS,
        "        if not any(str(keyword).lower() in compact for keyword in keywords):\n            continue",
        "        if not any(str(keyword).lower() in compact for keyword in keywords):\n            pass",
        (
            f"{T_GATE}::test_heal_never_borrows_a_curated_document_from_another_topic",
            f"{T_CE}::test_hospital_wide_messaging_never_picks_evidence_for_an_unnamed_slot",
            f"{T_AS}::test_select_curated_authority_sources_supports_dehydration_content",
            f"{T_OPD}::test_a_provider_or_routing_keyword_alone_never_passes_any_curated_document",
        ),
        "r7: CE 테스트의 병원 선택 브리프는 이제 프롬프트 힌트 가드(7a)가 먼저 비운다 — 선택 결과를 "
        "그대로 보는 테스트를 더했다",
    ),
    Mutant(
        "발행 직전 0개면 수기 목록으로 치유",
        f"{RP}:refresh_publication_references",
        RP,
        "    if not kept and required and not operator_decides:",
        "    if False:",
        (f"{T_GATE}::test_nothing_left_is_healed_from_the_topic_matched_curated_list",),
    ),
    Mutant(
        "그래도 없으면 발행 보류(MISSING_REFERENCES) — 실패한 URL을 남기지 않는다",
        f"{RP}:refresh_publication_references",
        RP,
        "        references=kept,\n        checks=checks,\n        references_changed=bool(malformed) or kept != references,",
        "        references=kept or references,\n        checks=checks,\n"
        "        references_changed=bool(malformed) or (kept or references) != references,",
        (
            f"{T_GATE}::test_still_nothing_holds_publication_with_the_real_cause",
            f"{T_GATE}::test_seven_forty_five_holds_and_pages_the_real_cause_when_nothing_survives",
        ),
    ),
    Mutant(
        "보류 알림 문구 = 실제 원인(MISSING_REFERENCES)",
        f"{INCIDENT}:_generation_safe_cause",
        INCIDENT,
        '            "실제 문서 확인을 통과한 공신력 있는 참고 자료를 확보하지 못해 발행을 보류했습니다."',
        '            "의료 콘텐츠에 필요한 참고 자료가 준비되지 않았습니다."',
        (f"{T_GATE}::test_still_nothing_holds_publication_with_the_real_cause",),
    ),
    Mutant(
        "생성 거절 문구 = 참고자료 확보 실패(가격·지역 게이트 문구 아님)",
        f"{RUN_CONTROL}:_safe_rejection_message",
        RUN_CONTROL,
        '    if "references is empty" in detail:\n        return _REJECTION_MESSAGES["references"]\n',
        "",
        (f"{T_GATE}::test_generation_reference_rejection_is_labelled_as_a_reference_failure",),
    ),
    Mutant(
        "인시던트 조치 문구 = 참고자료 확보 실패",
        f"{INCIDENT}:_generation_operator_copy",
        INCIDENT,
        '    if code == "GENERATION_REJECTED" and message == GENERATION_REFERENCE_REJECTION_MESSAGE:',
        "    if False:",
        (f"{T_GATE}::test_generation_reference_rejection_is_labelled_as_a_reference_failure",),
    ),
    Mutant(
        "Admin 행 상태 = 참고자료 재시도 대기(‘초안 생성 중’ 아님)",
        f"{ROW}:content_row_state",
        ROW,
        "        if _last_rejection_was_references(item):",
        "        if False:",
        (f"{T_GATE}::test_admin_row_shows_the_reference_retry_instead_of_nightly_generation",),
    ),
    Mutant(
        "기관 사이트 장애 시 직전 통과 재사용",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if transient and _reusable_previous_pass(\n"
        "                prior, now=observed, topic_fingerprint=fingerprint\n"
        "            ):",
        "            if False:",
        (
            f"{T_RV}::test_domain_outage_reuses_the_previous_pass_within_the_window",
            f"{T_GATE}::test_domain_outage_reuses_the_previous_pass_instead_of_holding",
        ),
    ),
    Mutant(
        "장애 폴백 재사용 기간(7일, 마지막 실제 확인 기준)",
        f"{RV}:_reusable_previous_pass",
        RV,
        '    return _age_within(check.get("verified_at"), now=now, limit=REFERENCE_CHECK_REUSE_WINDOW)',
        "    return True",
        (f"{T_RV}::test_outage_reuse_is_bounded_by_the_last_real_verification",),
    ),
    Mutant(
        "GET 요청당 시간 제한",
        f"{RV}:ReferenceVerifier.fetch",
        RV,
        "                    result = await asyncio.wait_for(self._fetcher(url), timeout=self.timeout)",
        "                    result = await self._fetcher(url)",
        (f"{T_RV}::test_each_get_is_bounded_by_the_timeout",),
    ),
    Mutant(
        "GET 전체 동시성 상한",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "        semaphore = asyncio.Semaphore(self.max_concurrency)",
        "        semaphore = asyncio.Semaphore(10_000)",
        (f"{T_RV}::test_total_concurrency_is_capped",),
    ),
    Mutant(
        "도메인별 동시성 1",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            lambda: asyncio.Semaphore(self.per_domain_concurrency)",
        "            lambda: asyncio.Semaphore(10_000)",
        (f"{T_RV}::test_one_domain_gets_one_request_at_a_time_with_spacing",),
    ),
    Mutant(
        "도메인별 요청 간격",
        f"{RV}:ReferenceVerifier.fetch",
        RV,
        "                    if wait > 0:\n                        await self._sleep(wait)",
        "                    if False:\n                        await self._sleep(wait)",
        (f"{T_RV}::test_one_domain_gets_one_request_at_a_time_with_spacing",),
    ),
    Mutant(
        "실행당 GET 상한(넘으면 미룸)",
        f"{RV}:ReferenceVerifier.fetch",
        RV,
        "        if self.fetch_count >= self.max_fetches:",
        "        if False:",
        (
            f"{T_RV}::test_gets_beyond_the_run_budget_are_deferred_not_failed",
            f"{T_GATE}::test_run_budget_exhaustion_defers_without_blocking",
        ),
    ),
    Mutant(
        "httpx 예외 전부 포착(운영 fetcher)",
        f"{RV}:HttpxReferenceFetcher.__call__",
        RV,
        "        except Exception as exc:  # noqa: BLE001 — URL 하나가 생성·발행을 멈추면 안 된다.",
        "        except (httpx.TimeoutException, httpx.NetworkError) as exc:",
        (f"{T_RV}::test_httpx_fetcher_turns_every_exception_into_an_observation",),
    ),
    Mutant(
        "주입 fetcher 예외 포착(검증기)",
        f"{RV}:ReferenceVerifier.fetch",
        RV,
        "                except Exception as exc:  # noqa: BLE001 — 주입된 fetcher의 예외도 여기서 멈춘다.",
        "                except asyncio.TimeoutError as exc:",
        (f"{T_RV}::test_a_raising_injected_fetcher_cannot_crash_verification",),
    ),
    Mutant(
        "생성 시 검증 연결(생성 결과가 실제 문서 검증을 거침)",
        f"{CE}:_generate_content_attempt",
        CE,
        "    reference_drops += await _verify_generated_references(\n"
        "        result,\n"
        "        content_brief,\n"
        "        required=references_required_for(content_type, content_brief=content_brief),\n"
        "    )",
        "    reference_drops += []",
        (f"{T_CE}::test_unrelated_reference_is_dropped_without_rejecting_the_article",),
    ),
    Mutant(
        "지어내기 압력 문구 제거(정적 화이트리스트 블록)",
        f"{AS}:render_source_hint_block",
        AS,
        '        "- [현재 주제와 일치하는 검증된 문서]가 함께 주어지면 **그 URL을 그대로** 쓰세요. "',
        '        "- references에는 아래 도메인의 **실제 문서 URL을 최소 1개** 반드시 넣으세요. '
        '빈 references는 저장되지 않습니다. "\n'
        '        "- [현재 주제와 일치하는 검증된 문서]가 함께 주어지면 **그 URL을 그대로** 쓰세요. "',
        (
            f"{T_CE}::test_no_prompt_surface_pressures_the_writer_to_invent_a_url",
            f"{T_AS}::test_source_hint_block_prefers_verified_documents_and_forbids_guessing",
        ),
    ),
    Mutant(
        "지어내기 압력 제거(스키마 minItems:1)",
        f"{CE}:_with_verified_reference_guidance",
        CE,
        '                **references,\n                "description": (\n'
        '                    "이 글의 주제를 다루는 화이트리스트 도메인의 실제 문서. 주어진 검증 문서 "',
        '                **references,\n                "minItems": 1,\n                "description": (\n'
        '                    "이 글의 주제를 다루는 화이트리스트 도메인의 실제 문서. 주어진 검증 문서 "',
        (
            f"{T_CE}::test_schema_never_forces_a_minimum_number_of_references",
            f"{T_CE}::test_no_prompt_surface_pressures_the_writer_to_invent_a_url",
        ),
    ),
    Mutant(
        "지어내기 압력 제거(재작성 지적 '다른 문서를 쓰세요')",
        f"{CE}:_empty_reference_cause",
        CE,
        '    guidance = "검증 문서가 없으면 추측한 URL 대신 비워 두세요."',
        '    guidance = "같은 사유를 피해 다른 문서를 쓰세요."',
        (f"{T_CE}::test_no_prompt_surface_pressures_the_writer_to_invent_a_url",),
    ),
    Mutant(
        "영상의학과 필터 — 규칙 표(내과 ⟂ 영상의학과)",
        f"{SPEC}:INCOMPATIBLE_TOPIC_DEPARTMENTS",
        SPEC,
        '    "내과": frozenset({"영상의학과"}),',
        '    "내과": frozenset(),',
        (f"{T_SPEC}::test_internal_medicine_hospital_rejects_both_radiology_seeds_despite_listing_radiology",),
    ),
    Mutant(
        "영상의학과 필터 — 주제 교체 후보 사후 가드",
        f"{SWAP}:_swap_one",
        SWAP,
        "    if target_conflicts_with_hospital(target, hospital):\n        return None, \"incompatible_specialty\"",
        "    if False:\n        return None, \"incompatible_specialty\"",
        (f"{T_SPEC}::test_topic_swap_never_moves_an_internal_medicine_slot_to_radiology",),
    ),
    Mutant(
        "영상의학과 필터 — 주제 교체가 병원을 넘겨 후보를 거름",
        f"{SWAP}:_swap_one",
        SWAP,
        "        exclude_target_ids={item.query_target_id},\n        hospital=hospital,\n    )",
        "        exclude_target_ids={item.query_target_id},\n    )",
        (
            f"{T_SPEC}::test_topic_swap_never_moves_an_internal_medicine_slot_to_radiology",
            f"{T_SWAP_PG}::test_internal_medicine_slot_is_swapped_to_a_compatible_topic_not_radiology",
        ),
        note="PG 행은 INTEGRATION_DATABASE_URL 필요",
    ),
    Mutant(
        "영상의학과 필터 — 달력 슬롯 배정(격차 타깃)",
        f"{GAP}:build_gap_targets",
        GAP,
        "        if target_conflicts_with_hospital(target, hospital):\n            continue",
        "        if target_conflicts_with_hospital(target, hospital):\n            pass",
        (
            f"{T_SPEC}::test_calendar_gap_targets_exclude_radiology_for_an_internal_medicine_hospital",
            f"{T_SLOTS_PG}::test_internal_medicine_calendar_never_assigns_a_radiology_gap_target",
        ),
        note="PG 행은 INTEGRATION_DATABASE_URL 필요",
    ),
    Mutant(
        "영상의학과 필터 — 생성 시점 슬롯 배정(_choose_target)",
        f"{PLANNER}:_choose_target",
        PLANNER,
        "        if str(target.id) not in excluded and target_fits_hospital(target, hospital)",
        "        if str(target.id) not in excluded",
        (
            f"{T_SPEC}::test_generation_time_assignment_skips_radiology_even_when_it_ranks_first",
            f"{T_SWAP_PG}::test_internal_medicine_slot_is_swapped_to_a_compatible_topic_not_radiology",
        ),
    ),
    Mutant(
        "영상의학과 필터 — 미리 배정된 영상의학과 타깃 재선택",
        f"{PLANNER}:prepare_automatic_content_brief_sync",
        PLANNER,
        "    if target is not None and target_conflicts_with_hospital(target, hospital):",
        "    if False:",
        (f"{T_SPEC}::test_brief_preparation_replaces_a_precommitted_radiology_target",),
    ),
    # ── Pass 2 (codex 교차 검수 반영) ─────────────────────────────────────────
    Mutant(
        "P2 restore 참고자료 게이트 — restore가 게이트를 부른다",
        f"{ADMIN}:restore_content",
        ADMIN,
        "    await _require_restorable_references(db, item, reference_verification)\n",
        "",
        (
            f"{T_WITHHOLD_PG}::test_restore_is_refused_when_a_legacy_reference_fails_a_real_get",
            f"{T_WITHHOLD_PG}::test_restore_is_refused_while_the_institution_site_is_down",
        ),
        note="PG 전용 — INTEGRATION_DATABASE_URL 필요",
    ),
    Mutant(
        "P2 restore 참고자료 게이트 — 통과하지 못한 항목이 있으면 409",
        f"{ADMIN}:_require_restorable_references",
        ADMIN,
        "    failures = unverified_reference_details(item)\n    if not failures:\n        return\n",
        "    failures = []\n    if not failures:\n        return\n",
        (
            f"{T_GATE}::test_restore_gate_refuses_without_touching_the_published_references",
            f"{T_GATE}::test_restore_gate_refuses_malformed_entries_and_site_outages",
        ),
    ),
    Mutant(
        "P2 restore는 검증만(공개 글 참고자료를 빼거나 채우지 않음)",
        f"{ADMIN}:restore_content",
        ADMIN,
        "        reference_verification = await verify_publication_references(",
        "        reference_verification = await refresh_publication_references(",
        (f"{T_WITHHOLD_PG}::test_restore_is_refused_when_a_legacy_reference_fails_a_real_get",),
        note="PG 전용",
    ),
    Mutant(
        "P2 통과 판정을 글 주제 지문에 묶음(신선도)",
        f"{RV}:check_is_fresh_pass",
        RV,
        "    if not _same_topic(check, topic_fingerprint):\n"
        "        return False\n"
        '    return _age_within(check.get("checked_at")',
        '    return _age_within(check.get("checked_at")',
        (
            f"{T_RV}::test_gate_binds_each_pass_to_the_article_topic",
            f"{T_GATE}::test_title_only_patch_makes_the_stored_passes_stale_without_a_get",
            f"{T_GATE}::test_brief_change_makes_the_stored_passes_stale",
        ),
    ),
    Mutant(
        "P2 장애 재사용도 같은 글 주제일 때만",
        f"{RV}:_reusable_previous_pass",
        RV,
        "    if not _same_topic(check, topic_fingerprint):\n"
        "        return False\n"
        '    return _age_within(check.get("verified_at")',
        '    return _age_within(check.get("verified_at")',
        (f"{T_RV}::test_outage_reuse_never_crosses_article_topics",),
    ),
    Mutant(
        "P2 발행 직전 일시 장애는 미룸(제거 아님) — 검증기",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if transient and defer_transient and not curated:",
        "            if False:",
        (
            f"{T_RV}::test_publish_time_transient_failure_defers_instead_of_removing",
            f"{T_GATE}::test_publish_time_outage_without_a_prior_pass_defers_instead_of_holding",
        ),
    ),
    Mutant(
        "P2 발행 직전 미룸이면 아무것도 빼지·채우지 않음 — 재검증",
        f"{RP}:refresh_publication_references",
        RP,
        "    if outcome.deferred:\n        # 한 항목이라도",
        "    if False:\n        # 한 항목이라도",
        (
            f"{T_GATE}::test_publish_time_outage_without_a_prior_pass_defers_instead_of_holding",
            f"{T_GATE}::test_run_budget_exhaustion_defers_without_blocking",
        ),
    ),
    Mutant(
        "P2 일시 장애 분류 — 408/429",
        f"{RV}:is_transient_fetch",
        RV,
        "_TRANSIENT_HTTP_STATUSES = frozenset({408, 429})",
        "_TRANSIENT_HTTP_STATUSES = frozenset()",
        (f"{T_RV}::test_publish_time_transient_failure_defers_instead_of_removing",),
    ),
    Mutant(
        "P2 확정 판정(404/410·그 밖의 4xx)은 미루지 않고 제거",
        f"{RV}:is_transient_fetch",
        RV,
        "    return status is not None and (status >= 500 or status in _TRANSIENT_HTTP_STATUSES)",
        "    return status is not None and (status >= 400 or status in _TRANSIENT_HTTP_STATUSES)",
        (
            f"{T_RV}::test_publish_time_definitive_results_still_remove",
            f"{T_GATE}::test_publish_time_definitive_failure_still_removes_and_heals",
        ),
    ),
    Mutant(
        "P2 생성 단계는 미루지 않음(수용 기준 2 — 기본값)",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "        defer_transient: bool = False,",
        "        defer_transient: bool = True,",
        (f"{T_RV}::test_generation_time_transient_failure_still_removes_outside_urls",),
    ),
    Mutant(
        "P2 GET 한도가 다해도 수기 목록 URL은 카탈로그로 판정",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if fetched.error == FETCH_ERROR_BUDGET and not curated:",
        "            if fetched.error == FETCH_ERROR_BUDGET:",
        (f"{T_RV}::test_curated_reference_is_judged_by_catalog_when_the_run_budget_is_spent",),
    ),
    Mutant(
        "P2 미룸이 예정일 마지막 발행기(23시)부터 이어지면 요약에 알림",
        f"{TASKS}:morning_content_auto_publish",
        TASKS,
        '                    if reference_outage_alert_due(outcome.get("scheduled_date"), observed_kst):',
        "                    if False:",
        (f"{T_GATE}::test_outage_deferral_is_reported_from_the_last_publisher_run_with_its_real_cause",),
    ),
    Mutant(
        "P2 미룸 알림은 마지막 발행기 전에는 보내지 않음",
        f"{RP}:reference_outage_alert_due",
        RP,
        "        scheduled_date == today and now_kst.hour >= REFERENCE_OUTAGE_LAST_PUBLISHER_HOUR",
        "        scheduled_date == today and now_kst.hour >= 0",
        (f"{T_GATE}::test_outage_deferral_is_reported_from_the_last_publisher_run_with_its_real_cause",),
    ),
    Mutant(
        "P2 미룸 알림 문구 = 기관 사이트 접속 불가(‘참고자료 확보 실패’ 아님)",
        f"{NOTIF_COPY}:blocker_copy",
        NOTIF_COPY,
        '    if value == "REFERENCE_SITE_UNREACHABLE":',
        "    if False:",
        (f"{T_GATE}::test_outage_deferral_is_reported_from_the_last_publisher_run_with_its_real_cause",),
    ),
    Mutant(
        "P2 잠금 뒤 비교 후 적용(재검증 중 바뀐 행을 덮어쓰지 않음)",
        f"{RP}:apply_publication_reference_refresh",
        RP,
        "    if not reference_snapshot_matches(item, refresh.snapshot):\n        return False\n",
        "",
        (
            f"{T_GATE}::test_apply_refuses_a_refresh_whose_row_changed",
            f"{T_GATE}::test_publisher_fetches_before_locking_and_never_overwrites_a_concurrent_edit",
            f"{T_GATE}::test_seven_forty_five_fetches_before_locking_and_skips_a_changed_row",
        ),
    ),
    Mutant(
        "P2 관리자 PATCH — 검증 중 바뀐 행이면 409",
        f"{ADMIN}:update_content",
        ADMIN,
        "    if pre_patch_snapshot is not None and not reference_snapshot_matches(item, pre_patch_snapshot):",
        "    if False:",
        (f"{T_GATE}::test_patch_verifies_before_locking_and_refuses_when_the_row_changed",),
    ),
    Mutant(
        "P2 07:45 — 재검증 결과가 적용되지 않았으면 이어서 쓰지 않음",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "            if not applied or refresh.deferred:",
        "            if refresh.deferred:",
        (f"{T_GATE}::test_seven_forty_five_fetches_before_locking_and_skips_a_changed_row",),
    ),
    Mutant(
        "P2 미래 시각 기록 거절(시계 차이 허용 폭까지만)",
        f"{RV}:_age_within",
        RV,
        "    return -REFERENCE_CHECK_CLOCK_SKEW <= age <= limit",
        "    return age <= limit",
        (
            f"{T_RV}::test_gate_rejects_future_dated_checks_beyond_the_clock_skew",
            f"{T_RV}::test_outage_reuse_rejects_a_future_dated_verification",
        ),
    ),
    Mutant(
        "P2 형식이 깨진 참고자료 항목은 게이트 실패",
        f"{RV}:reference_gate_status",
        RV,
        "        current=not missing and not malformed,",
        "        current=not missing,",
        (
            f"{T_RV}::test_gate_fails_every_malformed_reference_entry",
            f"{T_GATE}::test_restore_gate_refuses_malformed_entries_and_site_outages",
        ),
    ),
    Mutant(
        "P2 영상의학과 필터 — 구조화된 진료과 우선",
        f"{SPEC}:_target_texts",
        SPEC,
        "    if structured:\n        return (structured,)",
        "    if False:\n        return (structured,)",
        (f"{T_SPEC}::test_structured_specialty_wins_over_a_question_that_only_mentions_radiology",),
    ),
    Mutant(
        "P2 넓은 카탈로그 키워드 축소 — '내시경'",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        '        "keywords": ("대장내시경", "장정결"),',
        '        "keywords": ("대장내시경", "내시경", "장정결"),',
        (f"{T_AS}::test_generic_words_do_not_pull_an_unrelated_curated_document",),
    ),
    Mutant(
        "P2 넓은 카탈로그 키워드 축소 — '통증'·'관절'(요통)",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        '            "요통",\n            "허리통증",\n            "도수치료",',
        '            "요통",\n            "허리통증",\n            "관절",\n            "통증",\n'
        '            "도수치료",',
        (f"{T_AS}::test_generic_words_do_not_pull_an_unrelated_curated_document",),
    ),
    Mutant(
        "P2 생성 단계 장애는 수기 목록 치유가 먼저(거절 전)",
        f"{CE}:_verify_generated_references",
        CE,
        "    if not result[\"references\"] and required:",
        "    if False:",
        (f"{T_GATE}::test_generation_outage_heals_from_the_curated_list_instead_of_rejecting",),
    ),

    # ── Pass 3: 2af00d02 — 참고자료 필수 판정 한 규칙(의료 주제 NOTICE) ─────────────────
    Mutant(
        "P3 참고자료 0개 발행 차단 — 필수 글은 인용 가능한 참고자료 1개 이상(발행 게이트)",
        f"{CP}:has_required_references",
        CP,
        "    if not references_required(item):\n"
        "        return True\n"
        "    return count_citable_references(item) > 0\n",
        "    return True\n",
        (
            f"{T_REQ}::test_zero_reference_publish_block_for_medical_notice_2af00d02",
            f"{T_PUB}::test_notice_does_not_require_references_but_other_types_do",
        ),
    ),
    Mutant(
        "P3 의료 주제 NOTICE는 참고자료 필수(판정)",
        f"{REQ}:references_required_for",
        REQ,
        "        return _filled(query_target_id) or brief_carries_medical_topic(content_brief)\n",
        "        return False\n",
        (
            f"{T_REQ}::test_medical_notice_2af00d02_shape_requires_references",
            f"{T_REQ}::test_zero_reference_publish_block_for_medical_notice_2af00d02",
        ),
    ),
    Mutant(
        "P3 (음성) 순수 운영 공지 면제 유지 — 모든 NOTICE를 필수로 만들면 실패해야 한다(5821409e)",
        f"{REQ}:references_required_for",
        REQ,
        "        return _filled(query_target_id) or brief_carries_medical_topic(content_brief)\n",
        "        return True\n",
        (
            f"{T_REQ}::test_pure_operational_notice_stays_exempt",
            f"{T_REQ}::test_pure_operational_notice_still_publishes_without_references",
            f"{T_PUB}::test_notice_does_not_require_references_but_other_types_do",
        ),
    ),
    Mutant(
        "P3 (음성) 계획 모드·유도 필드만으로는 필수가 아님 — 질문 연결이 있어야 한다",
        f"{REQ}:brief_carries_medical_topic",
        REQ,
        "    if not isinstance(content_brief, Mapping):\n        return False\n",
        "    if isinstance(content_brief, Mapping) and content_brief.get(\"target_keyword\"):\n"
        "        return True\n"
        "    if not isinstance(content_brief, Mapping):\n        return False\n",
        (f"{T_REQ}::test_pure_operational_notice_stays_exempt",),
    ),
    Mutant(
        "P3 필수 글이 비어 있으면 재검증·치유 대상(settled) — 치유가 보류보다 먼저",
        f"{RP}:publication_references_settled",
        RP,
        "    if not publication_references_current(item, now=now) or publication_references_missing(item):\n",
        "    if not publication_references_current(item, now=now):\n",
        (
            f"{T_REQ}::test_medical_notice_with_no_references_is_healed_from_the_curated_list_first",
            f"{T_REQ}::test_seven_forty_five_heals_an_empty_medical_notice_before_paging",
        ),
    ),
    Mutant(
        "P3 재검증이 빈 필수 글을 '이미 current'로 건너뛰지 않음",
        f"{RP}:refresh_publication_references",
        RP,
        "        and not (required and not references)\n",
        "",
        (f"{T_REQ}::test_medical_notice_with_no_references_is_healed_from_the_curated_list_first",),
    ),
    Mutant(
        "P3 08:00·catch-up 잠금 전 재검증이 settled를 본다",
        f"{TASKS}:_prefetch_publication_references",
        TASKS,
        "            or publication_references_settled(item)\n",
        "            or publication_references_current(item)\n",
        (f"{T_REQ}::test_medical_notice_with_no_references_is_healed_from_the_curated_list_first",),
    ),
    Mutant(
        "P3 잠금 전 재검증의 행 보기에 질문 연결(query_target_id)",
        f"{TASKS}:_REFERENCE_VIEW_FIELDS",
        TASKS,
        '    "query_target_id",\n    # 스냅샷 비교',
        "    # 스냅샷 비교",
        (
            f"{T_REQ}::test_medical_notice_with_no_references_is_healed_from_the_curated_list_first[row_link_only]",
        ),
    ),
    Mutant(
        "P3 restore — 필수 글이 비어 있으면 409(채우지 않음)",
        f"{ADMIN}:_require_restorable_references",
        ADMIN,
        "    if publication_references_missing(item):\n",
        "    if False:\n",
        (f"{T_REQ}::test_restore_refuses_a_medical_notice_without_references_and_never_fills_them",),
    ),
    Mutant(
        "P3 생성 — 의료 주제 NOTICE 프롬프트에 참고자료 규칙",
        f"{CE}:_fill_type_prompt",
        CE,
        "    if content_type not in REFERENCES_REQUIRED_TYPES and references_required_for(\n",
        "    if False and references_required_for(\n",
        (f"{T_REQ}::test_medical_notice_prompt_carries_the_reference_rule_and_the_pure_notice_does_not",),
    ),
    Mutant(
        "P3 생성 — 의료 주제 NOTICE 스키마",
        f"{CE}:_article_tool_schema",
        CE,
        "    if references_required_for(content_type, content_brief=content_brief):\n"
        "        return _REFERENCE_REQUIRED_ARTICLE_INPUT_SCHEMA\n",
        "    if content_type in REFERENCES_REQUIRED_TYPES:\n"
        "        return _REFERENCE_REQUIRED_ARTICLE_INPUT_SCHEMA\n",
        (f"{T_REQ}::test_medical_notice_gets_the_reference_schema",),
    ),
    Mutant(
        "P3 생성 — 의료 주제 NOTICE가 전부 빠지면 거절(GEO hard-fail이 브리프로 판정)",
        f"{CE}:_validate_generated_result",
        CE,
        "        references_required=references_required_for(content_type, content_brief=content_brief),\n",
        "",
        (f"{T_REQ}::test_medical_notice_whose_references_all_drop_is_rejected_with_the_reference_copy",),
    ),
    Mutant(
        "P3 생성 — 의료 주제 NOTICE가 전부 빠지면 검증된 수기 목록으로 치유",
        f"{CE}:_generate_content_attempt",
        CE,
        "        required=references_required_for(content_type, content_brief=content_brief),\n    )",
        "        required=content_type in REFERENCES_REQUIRED_TYPES,\n    )",
        (f"{T_REQ}::test_medical_notice_whose_references_all_drop_is_healed_from_the_curated_list",),
    ),
    # ── Pass 3: 김실장 제외 목록·정규화 비교·수기 목록 ─────────────────────────────
    Mutant(
        "P3 제외 목록 끄기(전체)",
        f"{AS}:reference_exclusion_reason",
        AS,
        "    reasons = _matching_documents(url, _EXCLUDED_DOCUMENTS)\n"
        "    return str(reasons[0]) if reasons else None",
        "    return None",
        (
            f"{T_EXCL}::test_exclusion_list_row_is_blocked",
            f"{T_EXCL}::test_excluded_url_is_blocked_even_when_the_site_serves_a_real_document",
        ),
    ),
    Mutant(
        "P3 제외 목록 — KDCA cntnts_sn=6263(소화불량)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        "gnrlzHealthInfoView.do?cntnts_sn=6263\",",
        "gnrlzHealthInfoView.do?cntnts_sn=0\",",
        (f"{T_EXCL}::test_exclusion_list_row_is_blocked[cntnts_sn=6263]",),
    ),
    Mutant(
        "P3 제외 목록 — KDCA cntnts_sn=2351(당뇨병 합병증)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        "gnrlzHealthInfoView.do?cntnts_sn=2351\",",
        "gnrlzHealthInfoView.do?cntnts_sn=0\",",
        (f"{T_EXCL}::test_exclusion_list_row_is_blocked[cntnts_sn=2351]",),
    ),
    Mutant(
        "P3 제외 목록 — 국가암정보센터 S1T211C213(위암 허브)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        "\"url\": \"https://www.cancer.go.kr/lay1/S1T211C213/contents.do\",",
        "\"url\": \"https://www.cancer.go.kr/lay1/S0T0C0/contents.do\",",
        (f"{T_EXCL}::test_exclusion_list_row_is_blocked[S1T211C213]",),
    ),
    Mutant(
        "P3 제외 목록 — 국가암정보센터 S1T211C214(대장암 허브)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        "\"url\": \"https://www.cancer.go.kr/lay1/S1T211C214/contents.do\",",
        "\"url\": \"https://www.cancer.go.kr/lay1/S0T0C0/contents.do\",",
        (f"{T_EXCL}::test_exclusion_list_row_is_blocked[S1T211C214]",),
    ),
    Mutant(
        "P3 제외 목록 — 국가암정보센터 S1T274C286(수술 허브)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        "\"url\": \"https://www.cancer.go.kr/lay1/S1T274C286/contents.do\",",
        "\"url\": \"https://www.cancer.go.kr/lay1/S0T0C0/contents.do\",",
        (f"{T_EXCL}::test_exclusion_list_row_is_blocked[S1T274C286]",),
    ),
    Mutant(
        "P3 제외 목록 — 검증기가 GET·재사용 전에 떨어뜨림",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if reference_exclusion_reason(url) is not None:\n"
        "                # 사람이 확인해 제외한 주소",
        "            if False:\n"
        "                # 사람이 확인해 제외한 주소",
        (
            f"{T_EXCL}::test_excluded_url_is_blocked_even_when_the_site_serves_a_real_document",
            f"{T_EXCL}::test_exclusion_list_row_is_blocked",
        ),
    ),
    Mutant(
        "P3 제외 목록 — 저장된 통과도 게이트에서 인정하지 않음",
        f"{RV}:reference_gate_status",
        RV,
        "        if reference_exclusion_reason(url) is not None or not check_is_fresh_pass(\n",
        "        if not check_is_fresh_pass(\n",
        (f"{T_EXCL}::test_a_stored_pass_for_an_excluded_url_is_not_reused_or_accepted_by_the_gate",),
    ),
    Mutant(
        "P3 제외 목록 — 수기 목록 후보에서 제외",
        f"{AS}:select_curated_authority_sources",
        AS,
        "        if url in seen_urls or reference_exclusion_reason(url) is not None:\n",
        "        if url in seen_urls:\n",
        (f"{T_EXCL}::test_catalog_selection_skips_an_excluded_entry",),
    ),
    Mutant(
        "P3 문서 판정 — www 무시(제외 목록)",
        f"{AS}:_document_location",
        AS,
        '    host = (parsed.hostname or "").lower().rstrip(".")\n    if host.startswith("www."):\n        host = host[4:]\n',
        '    host = (parsed.hostname or "").lower().rstrip(".")\n',
        (f"{T_EXCL}::test_exclusion_matches_by_normalized_url",),
    ),
    Mutant(
        "4차 제외 목록이 목록 문서와 같은 판정을 쓴다(옛 정규화 비교로 되돌리기)",
        f"{AS}:reference_exclusion_reason",
        AS,
        "    reasons = _matching_documents(url, _EXCLUDED_DOCUMENTS)\n",
        "    def _norm(value):\n"
        "        parsed = urlparse(str(value or '').strip())\n"
        "        host = (parsed.hostname or '').lower().removeprefix('www.')\n"
        "        return (host, parsed.path.rstrip('/'), sorted(parse_qsl(parsed.query)))\n"
        "    reasons = [e['reason'] for e in REFERENCE_URL_EXCLUSIONS if _norm(e['url']) == _norm(url)]\n",
        (
            f"{T_ALIAS}::test_the_exclusion_list_uses_the_same_document_matcher",
            f"{T_EXCL}::test_normalization_ignores_query_order_scheme_www_and_trailing_slash",
        ),
    ),
    Mutant(
        "P3·4차 r05 문서 판정 — 끝 슬래시 무시(목록 URL에 있거나 없거나, 리다이렉트 없이)",
        f"{AS}:_document_location",
        AS,
        '    path = re.sub(r"/{2,}", "/", unquote(parsed.path)).rstrip("/")\n',
        '    path = re.sub(r"/{2,}", "/", unquote(parsed.path))\n',
        (
            f"{T_EXCL}::test_exclusion_matches_by_normalized_url",
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
        ),
    ),
    Mutant(
        "P3 수기 목록에 제외 주소가 없음(카탈로그 검사)",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        "gnrlzHealthInfoView.do?cntnts_sn=5305\",",
        "gnrlzHealthInfoView.do?cntnts_sn=6263\",",
        (f"{T_EXCL}::test_no_catalog_entry_is_excluded",),
    ),
    # ── 리뷰 2차(PR #177 BLOCK) — B1·B2·P1·S1~S4와 리뷰어 m21/m26/m28/m32/m46 ──
    Mutant(
        "B1 07:45 잠근 행이 발행 전 상태가 아니면 아무것도 쓰지 않음",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "            if locked.status not in AUTO_PUBLISHABLE_STATUSES or generation_claim_is_active(\n"
        "                locked, now=observed.datetime\n"
        "            ):\n"
        "                # GET 사이에 수동 발행·취소됐다 — 공개된 글의 참고자료를 자동으로 바꾸지 않는다.\n"
        "                # 또는 GET 사이에 생성 워커가 이 슬롯을 잡았다 — 위와 같이 워커의 결과를 둔다.\n"
        "                db.commit()\n"
        "                continue\n",
        "",
        (
            f"{T_GATE}::test_seven_forty_five_writes_nothing_to_a_row_that_is_not_publishable_at_lock",
        ),
    ),
    Mutant(
        "B1 재검증 스냅샷에 상태(status) — GET 사이 상태가 바뀌면 적용 거절",
        f"{RP}:reference_snapshot",
        RP,
        "        status=_status_value(item),\n",
        "",
        (f"{T_GATE}::test_apply_refuses_a_refresh_once_the_row_left_the_publishable_states",),
    ),
    Mutant(
        "B1 적용은 발행 전(DRAFT·READY) 행의 참고자료만 바꿈",
        f"{RP}:apply_publication_reference_refresh",
        RP,
        "        and status not in _REFERENCE_WRITABLE_STATUSES\n",
        "        and False\n",
        (
            f"{T_GATE}::test_apply_refuses_a_refresh_once_the_row_left_the_publishable_states",
            f"{T_GATE}::test_published_references_never_change_on_apply_restore_or_manual_publish",
        ),
    ),
    Mutant(
        "B1 잠금 전 재검증의 행 보기에 상태(status)",
        f"{TASKS}:_REFERENCE_VIEW_FIELDS",
        TASKS,
        "    # 스냅샷 비교 — GET 사이에 공개된 글에는 결과를 쓰지 않는다.\n    \"status\",\n",
        "",
        (f"{T_GATE}::test_seven_forty_five_reverifies_stale_references_so_eight_does_not_burst",),
        "상태 없는 보기는 잠근 행과 영원히 달라 07:45 재검증이 한 번도 적용되지 않는다",
    ),
    Mutant(
        "B2 08:00 잠금 전 재검증은 생성된 글만",
        f"{TASKS}:_prefetch_publication_references",
        TASKS,
        "            or not _has_generated_text(item)\n",
        "",
        (f"{T_GATE}::test_publisher_leaves_an_ungenerated_slot_untouched",),
    ),
    Mutant(
        "B2 08:00 게이트 확인은 생성된 글만(생성 전은 CONTENT_NOT_GENERATED)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "        if _has_generated_text(item) and not publication_references_current(item):",
        "        if not publication_references_current(item):",
        (f"{T_GATE}::test_publisher_leaves_an_ungenerated_slot_untouched",),
    ),
    Mutant(
        "B2 수동 발행 재검증은 생성된 글만",
        f"{ADMIN}:publish_content",
        ADMIN,
        "    if _has_generated_text(unlocked_item) and not publication_references_settled(unlocked_item):",
        "    if not publication_references_settled(unlocked_item):",
        (f"{T_GATE}::test_manual_publish_leaves_an_ungenerated_slot_untouched",),
    ),
    Mutant(
        "B2 수동 발행 게이트 확인은 생성된 글만(생성 전은 400 그대로)",
        f"{ADMIN}:publish_content",
        ADMIN,
        "        or (_has_generated_text(item) and not publication_references_current(item))\n",
        "        or not publication_references_current(item)\n",
        (f"{T_GATE}::test_manual_publish_leaves_an_ungenerated_slot_untouched",),
    ),
    Mutant(
        "B2 재검증 함수 자체도 본문 없는 행을 치유하지 않음",
        f"{RP}:refresh_publication_references",
        RP,
        "    if not _has_generated_text(item):\n"
        "        # 아직 생성되지 않은 슬롯",
        "    if False:\n"
        "        # 아직 생성되지 않은 슬롯",
        (f"{T_GATE}::test_refresh_never_heals_a_row_without_generated_text",),
    ),
    Mutant(
        "P1 깨진 포트는 화이트리스트 밖(인용 불가)",
        f"{AS}:_extract_hostname",
        AS,
        "        _ = parsed.port\n",
        "",
        (f"{T_RV}::test_malformed_port_url_is_rejected_without_crashing_or_fetching",),
    ),
    Mutant(
        "P1 문서 판정이 깨진 포트로 크래시하지 않음(07:45 루프 계속)",
        f"{AS}:_document_location",
        AS,
        "    try:\n"
        "        parsed = urlparse(text)\n"
        "        port = parsed.port\n"
        "    except ValueError:\n"
        "        return None  # 깨진 포트 — 화이트리스트 밖이라 인용되지 않는다\n",
        "    parsed = urlparse(text)\n"
        "    port = parsed.port\n",
        (
            f"{T_RV}::test_malformed_port_url_is_rejected_without_crashing_or_fetching",
            f"{T_GATE}::test_seven_forty_five_rejects_a_malformed_port_url_and_keeps_going",
        ),
    ),
    Mutant(
        "S1 수동 발행 — 잠금 전 통과였어도 잠근 행의 게이트 재확인 (리뷰어 m28)",
        f"{ADMIN}:publish_content",
        ADMIN,
        "        or (_has_generated_text(item) and not publication_references_current(item))\n",
        "",
        (f"{T_GATE}::test_manual_publish_rechecks_the_gate_on_the_locked_row",),
    ),
    Mutant(
        "S2 제목만 맞고 본문이 다른 문서는 통과 아님 (리뷰어 m21)",
        f"{RV}:judge_fetched_page",
        RV,
        "    if not any(token in normalized_body for token in topic_tokens):\n"
        "        return judgement(VERDICT_FAIL, REASON_UNRELATED, topic)",
        "    if not any(token in normalized_body for token in topic_tokens):\n"
        "        pass",
        (f"{T_RV}::test_matching_title_over_a_different_document_body_is_not_a_pass",),
    ),
    Mutant(
        "S3 공개 글 PATCH가 필수 참고자료를 비우지 못함 (리뷰어 m32)",
        f"{ADMIN}:update_content",
        ADMIN,
        "        if was_published and not has_required_references(item):",
        "        if False:",
        (f"{T_GATE}::test_patch_cannot_empty_the_references_of_a_published_post",),
    ),
    Mutant(
        "S4 첫 생성(배치)이 검증 기록을 저장 (리뷰어 m46)",
        f"{TASKS}:_run_generation_item",
        TASKS,
        "                \"reference_checks\": content_data.get(\"reference_checks\") or [],\n",
        "",
        (f"{T_GATE}::test_first_generation_stores_the_reference_checks_for_the_publication_gates",),
    ),
    Mutant(
        "S4 재생성(스윕·단건)이 검증 기록을 저장 (리뷰어 m46)",
        f"{TASKS}:_generate_single_content_item",
        TASKS,
        "\n            \"reference_checks\": content_data.get(\"reference_checks\") or [],\n",
        "\n",
        (f"{T_GATE}::test_regeneration_stores_the_reference_checks_for_the_publication_gates",),
    ),
    Mutant(
        "08:00 발행기 — 스냅샷 불일치면 적용·미룸 보고 없이 물러남 (리뷰어 m26)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "if not apply_publication_reference_refresh(item, reference_refresh):",
        "apply_publication_reference_refresh(item, reference_refresh)\n            if False:",
        (f"{T_GATE}::test_publisher_does_not_report_an_outage_for_a_row_changed_during_the_get",),
        "리뷰어는 equivalent로 봤다 — 미룸 결과면 이미 없는 주소를 '기관 사이트 접속 불가'로 알린다",
    ),
    # ── r3 (2026-09-29 김실장 결정): 요통·디스크 좁은 키워드, 3797 제외, 진료비·병원 선택 글 ──
    Mutant(
        "1a 요통(3796) 좁은 키워드 — 단독 '허리'·'도수'는 허리둘레·빈도수·알코올 도수에 걸린다",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        '            "허리통증",\n            "도수치료",\n            "허리디스크",\n            "허리다리",\n',
        '            "허리통증",\n            "도수",\n            "허리",\n',
        (f"{T_AS}::test_short_waist_or_degree_words_do_not_pull_low_back_or_disc_documents",),
    ),
    Mutant(
        "1a 디스크(3348) 좁은 키워드 — 단독 '허리'·'도수' 금지",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        '            "디스크",\n            "도수치료",\n            "허리디스크",\n            "허리다리",\n',
        '            "디스크",\n            "도수",\n            "허리",\n',
        (f"{T_AS}::test_short_waist_or_degree_words_do_not_pull_low_back_or_disc_documents",),
    ),
    Mutant(
        "1a 요통(3796) 도수치료·허리디스크·허리다리 복원 (도수치료 글이 요통 문서를 받는다)",
        f"{AS}:CURATED_MEDICAL_SOURCE_PAGES",
        AS,
        '            "허리통증",\n            "도수치료",\n            "허리디스크",\n            "허리다리",\n',
        '            "허리통증",\n',
        (f"{T_AS}::test_manual_therapy_and_lumbar_disc_titles_select_low_back_and_disc_documents",),
    ),
    Mutant(
        "2 cancer_seq=3797 제외 (www·비www 모두 GET 0회 excluded_source)",
        f"{AS}:REFERENCE_URL_EXCLUSIONS",
        AS,
        '        "url": "https://cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797",\n'
        '        "topic": "대장암",',
        '        "url": "https://cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797x",\n'
        '        "topic": "대장암",',
        (
            f"{T_EXCL}::test_colon_cancer_3797_is_excluded_without_a_fetch",
            f"{T_EXCL}::test_exclusion_list_is_the_reviewed_six_with_a_reason_each",
        ),
    ),
    Mutant(
        "3 분류 — 제목·FAQ 질문의 비용 말(진료비·비용·가격·비급여·본인부담)",
        f"{REQ}:topic_without_authoritative_source",
        REQ,
        "    if any(term in text for term in _COST_TOPIC_TERMS):",
        "    if False:",
        (f"{T_OPD}::test_title_classification_is_deterministic_and_conservative",),
    ),
    Mutant(
        "3 분류 — 떨어진 고르기 말은 대상(병원·전문의·진료과)이 같은 제목에 있을 때만",
        f"{REQ}:topic_without_authoritative_source",
        REQ,
        "            and any(noun in text for noun in _PROVIDER_NOUNS)\n",
        "",
        (f"{T_OPD}::test_title_classification_is_deterministic_and_conservative",),
    ),
    Mutant(
        "3 분류 — 필수가 아닌 글(순수 공지)은 사람 결정 대상이 아니다",
        f"{REQ}:references_left_to_operator",
        REQ,
        "    return references_required(item) and (\n        topic_without_authoritative_source(",
        "    return (\n        topic_without_authoritative_source(",
        (f"{T_OPD}::test_a_post_whose_references_are_not_required_is_never_left_to_the_operator",),
    ),
    Mutant(
        "3 발행 치유 금지 — 진료비·병원 선택 글을 수기 목록으로 채우지 않는다",
        f"{RP}:refresh_publication_references",
        RP,
        "    operator_decides = not kept and references_left_to_operator(item)",
        "    operator_decides = False",
        (
            f"{T_OPD}::test_scheduled_post_is_held_for_the_operator_without_a_curated_fill",
            f"{T_OPD}::test_published_post_is_never_changed_and_restore_reports_it",
        ),
    ),
    Mutant(
        "3 생성 치유 금지 — 검증 뒤 채우기·GEO 거절 뒤 채우기",
        f"{CE}:_topic_aligned_curated_sources",
        CE,
        "    if result and topic_without_authoritative_source(",
        "    if False and topic_without_authoritative_source(",
        (f"{T_OPD}::test_generation_never_fills_the_post_from_the_curated_list",),
    ),
    Mutant(
        "3 OPERATOR_REQUIRED 라우팅 — 시도 기록에 사람 결정 표시",
        f"{TASKS}:_remember_generation_attempt",
        TASKS,
        "    if operator_decides or operator_decides_references(reason, item):",
        "    if operator_decides:",
        (f"{T_OPD}::test_scheduled_post_is_held_for_the_operator_without_a_curated_fill",),
    ),
    Mutant(
        "3 OPERATOR_REQUIRED 라우팅 — 표시가 수리 세션 예산의 소유를 끊는다(다음 시도 없음)",
        f"{RETRY}:_earliest_eligible_date",
        RETRY,
        "        and not attempt.get(OPERATOR_DECIDES_KEY)\n",
        "",
        (f"{T_OPD}::test_scheduled_post_is_held_for_the_operator_without_a_curated_fill",),
    ),
    Mutant(
        "3 OPERATOR_REQUIRED 라우팅 — 배포 전 기록도 게이트가 다시 쓴다",
        f"{TASKS}:_record_gate_blocker_decision",
        TASKS,
        "    if stored_reason == code and bool(stored.get(OPERATOR_DECIDES_KEY)) == (\n"
        "        operator_decides_references(code, item)\n    ):",
        "    if stored_reason == code:",
        (f"{T_OPD}::test_a_stored_record_from_before_the_rule_is_rewritten_as_operator_work",),
    ),
    Mutant(
        "3 자동 본문 수리(LLM) 금지 — 복구 스윕이 작가를 부르지 않는다",
        f"{TASKS}:_generate_single_content_item",
        TASKS,
        "            and not operator_decides_references(stored_assessment.code, item)\n",
        "",
        (f"{T_OPD}::test_recovery_sweep_never_buys_a_body_repair_for_the_hold",),
    ),
    Mutant(
        "3 인시던트 — 스윕이 소유하지 않는다(RETRYING 아님)",
        f"{INCIDENT}:scheduled_recovery_owns_blocker",
        INCIDENT,
        "    if operator_decides_references(code, item):\n        return False\n",
        "",
        (f"{T_OPD}::test_the_hold_is_an_open_operator_incident_with_its_own_copy",),
    ),
    Mutant(
        "3 인시던트 — 종착(기한 없는 OPEN)",
        f"{INCIDENT}:generation_block_is_terminal",
        INCIDENT,
        "    if operator_decides_references(code, item):\n        return True\n",
        "",
        (f"{T_OPD}::test_the_hold_is_an_open_operator_incident_with_its_own_copy",),
    ),
    Mutant(
        "3 인시던트 문구 — '자동 복구가 다시 씁니다'가 아니라 사람의 결정",
        f"{INCIDENT}:open_generation_incident",
        INCIDENT,
        "                if operator_decides_references(code, swapped_item)\n",
        "                if False\n",
        (f"{T_OPD}::test_the_hold_is_an_open_operator_incident_with_its_own_copy",),
    ),
    Mutant(
        "3 공개 글 불변 — 공개 글에는 재검증 결과를 적용하지 않는다",
        f"{RP}:apply_publication_reference_refresh",
        RP,
        "    if not reference_snapshot_matches(item, refresh.snapshot):\n        return False\n"
        "    status = _status_value(item)\n    if (",
        "    if not reference_snapshot_matches(item, refresh.snapshot):\n        return False\n"
        "    status = _status_value(item)\n    if False and (",
        (f"{T_OPD}::test_published_post_is_never_changed_and_restore_reports_it",),
    ),
    Mutant(
        "4a 병원 선택 — 의료 주제(수기 목록 질환·시술 키워드)가 있는 고르기 글은 의료 글",
        f"{REQ}:topic_without_authoritative_source",
        REQ,
        "    ) and not title_names_medical_subject(text):",
        "    ):",
        (f"{T_OPD}::test_provider_choice_is_only_a_choice_post_without_a_medical_subject",),
    ),
    Mutant(
        "4a 의료 주제 — 진료과 이름 키워드(정형외과·심장내과…)는 주제가 아니다",
        f"{AS}:keyword_names_provider",
        AS,
        "    return text in _PROVIDER_ROUTING_TEXTS or any(noun in text for noun in PROVIDER_NOUNS)",
        "    return text in _PROVIDER_ROUTING_TEXTS",
        (
            f"{T_OPD}::test_provider_choice_is_only_a_choice_post_without_a_medical_subject",
            f"{T_OPD}::test_provider_keywords_are_exactly_the_specialty_and_routing_keywords",
            f"{T_AS}::test_select_curated_authority_sources_does_not_route_on_a_specialty_name",
        ),
        "r7: 술어가 authority_sources로 옮겨 치유 선택·수기 문서 대조도 같이 본다",
    ),
    Mutant(
        "4a 의료 주제 — 병원 고르기 FAQ 경로 키워드(병원선택·통증종류)는 주제가 아니다",
        f"{AS}:keyword_names_provider",
        AS,
        "    return text in _PROVIDER_ROUTING_TEXTS or any(",
        "    return any(",
        (
            f"{T_OPD}::test_provider_choice_is_only_a_choice_post_without_a_medical_subject",
            f"{T_OPD}::test_provider_keywords_are_exactly_the_specialty_and_routing_keywords",
            f"{T_OPD}::test_a_routing_only_post_gets_no_heal_at_publication",
        ),
        "r7: 술어가 authority_sources로 옮겨 치유 선택·수기 문서 대조도 같이 본다",
    ),
    Mutant(
        "4a 의료 주제 — 제외 목록 문서의 키워드는 세지 않는다(붙일 수 있는 문서만)",
        f"{REQ}:title_names_medical_subject",
        REQ,
        "        if reference_exclusion_reason(source[\"url\"]) is not None:\n            continue\n",
        "",
        (f"{T_OPD}::test_an_excluded_document_does_not_make_a_medical_subject",),
    ),
    Mutant(
        "4b 생성 라우팅 — 참고자료 0개로 끝난 진료비·병원 선택 슬롯은 곧바로 사람의 결정",
        f"{TASKS}:_run_generation_item",
        TASKS,
        "        operator_decides = unwritten and _generation_left_references_to_operator(e, item)\n",
        "        operator_decides = False\n",
        (
            f"{T_OPD}::test_unwritten_slot_without_references_goes_straight_to_the_operator",
            f"{T_OPD}::test_the_generation_hold_is_an_open_incident_without_a_deadline",
        ),
    ),
    Mutant(
        "4b 생성 라우팅 — 판정은 작가가 만든 제목(행에는 제목이 없다)",
        f"{TASKS}:_generation_left_references_to_operator",
        TASKS,
        "    return bool(title) and references_left_to_operator(item, title=title)\n",
        "    return bool(title) and references_left_to_operator(item)\n",
        (f"{T_OPD}::test_unwritten_slot_without_references_goes_straight_to_the_operator",),
    ),
    Mutant(
        "4b 주제 교체 제외 — 사유가 표본 코드(GENERATION_REJECTED)를 떠난다",
        f"{TASKS}:_run_generation_item",
        TASKS,
        "            code, message = \"MISSING_REFERENCES\", REFERENCES_OPERATOR_DECIDES_CAUSE\n",
        "            pass\n",
        (f"{T_OPD}::test_unwritten_slot_without_references_goes_straight_to_the_operator",),
    ),
    Mutant(
        "4b 재생성·수리 금지 — 표시가 기한 계산 전에 남는다(다음 시도 없음)",
        f"{TASKS}:_remember_generation_attempt",
        TASKS,
        "                db, item, philosophy, code, message=message, operator_decides=operator_decides\n",
        "                db, item, philosophy, code, message=message\n",
        (
            f"{T_OPD}::test_unwritten_slot_without_references_goes_straight_to_the_operator",
            f"{T_OPD}::test_the_generation_hold_is_an_open_incident_without_a_deadline",
        ),
    ),
    Mutant(
        "4b 인시던트 — 쓰이지 않은 슬롯은 저장된 표시로 종착(기한 없는 OPEN)",
        f"{INCIDENT}:operator_decides_references",
        INCIDENT,
        "    return (\n        not getattr(item, \"body\", None)\n        and attempt.get(\"reason\") == code\n",
        "    return False and (\n        not getattr(item, \"body\", None)\n        and attempt.get(\"reason\") == code\n",
        (
            f"{T_OPD}::test_the_generation_hold_is_an_open_incident_without_a_deadline",
            f"{T_OPD}::test_the_morning_gate_keeps_the_generation_hold_as_operator_work",
        ),
    ),
    Mutant(
        "4b 재생성 금지 — 07:45·08:00의 증상(CONTENT_NOT_GENERATED)이 기록을 덮어쓰지 않는다",
        f"{TASKS}:_publication_block_details",
        TASKS,
        "    if operator_decides_references(stored_code, item):\n",
        "    if False:\n",
        (f"{T_OPD}::test_the_morning_gate_keeps_the_generation_hold_as_operator_work",),
    ),
    Mutant(
        "4b 생성 치유 금지 — GEO 거절 뒤 수기 목록 채우기(_heal_from_curated_catalog)",
        f"{CE}:_topic_aligned_curated_sources",
        CE,
        "    if result and topic_without_authoritative_source(",
        "    if False and topic_without_authoritative_source(",
        (f"{T_OPD}::test_generation_heal_never_fills_a_cost_or_choice_slot",),
    ),
    Mutant(
        "5 발행 재검증 — 발행 전 진료비·병원 선택 글의 수기 목록 문서는 통과해도 뺀다",
        f"{RP}:refresh_publication_references",
        RP,
        "    strip_curated = strips_curated and any(",
        "    strip_curated = False and any(",
        (
            f"{T_OPD}::test_scheduled_post_drops_a_cited_curated_document_even_when_it_passes",
            f"{T_OPD}::test_publication_refresh_drops_the_cited_curated_document_without_a_heal",
        ),
    ),
    Mutant(
        "5 settled — 신선한 통과 기록이 있어도 수기 목록 문서가 남은 예정 글은 재검증 대상",
        f"{RP}:publication_references_settled",
        RP,
        "        return not any(names_curated_document(entry, checks) for entry in entries)",
        "        return True",
        (f"{T_OPD}::test_scheduled_post_drops_a_cited_curated_document_even_when_it_passes",),
    ),
    Mutant(
        "5 재검증의 '이미 current' 단락이 수기 목록 문서 빼기를 건너뛰지 않음",
        f"{RP}:refresh_publication_references",
        RP,
        "        and not (required and not references)\n        and not strip_curated\n",
        "        and not (required and not references)\n",
        (f"{T_OPD}::test_scheduled_post_drops_a_cited_curated_document_even_when_it_passes",),
    ),
    Mutant(
        "5 공개된 글은 규칙 밖 — DRAFT·READY 밖이면 수기 목록 문서를 빼지 않는다",
        f"{RP}:_strips_curated_references",
        RP,
        "    if status is not None and status not in _REFERENCE_WRITABLE_STATUSES:\n"
        "        return False\n"
        "    return references_left_to_operator(item, title=title)",
        "    return references_left_to_operator(item, title=title)",
        (f"{T_OPD}::test_published_post_with_a_curated_document_stays_byte_identical",),
    ),
    Mutant(
        "5 수기 목록 판정은 정규화한 주소(scheme·www 표기 차이도 같은 문서)",
        f"{AS}:is_curated_source_url",
        AS,
        "    return bool(_matching_documents(url, _CURATED_DOCUMENTS))",
        "    return str(url or \"\").strip() in CURATED_SOURCE_URLS",
        (
            f"{T_OPD}::test_a_curated_document_is_dropped_in_any_spelling",
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
        ),
    ),
    Mutant(
        "5 생성 — 진료비·병원 선택 제목이면 작가가 인용한 수기 목록 문서를 받지 않는다",
        f"{CE}:_verify_generated_references",
        CE,
        "    if result[\"references\"] and no_source_topic:",
        "    if False and no_source_topic:",
        (
            f"{T_OPD}::test_generation_does_not_accept_a_cited_curated_document",
            f"{T_OPD}::test_unwritten_slot_whose_writer_cited_a_curated_document_goes_to_the_operator",
        ),
    ),
    Mutant(
        "5 생성 — 뺀 수기 목록 문서의 사유가 재작성 지적에 실린다",
        f"{CE}:_verify_generated_references",
        CE,
        "        notes = _reference_drop_notes(\n"
        "            cited, result[\"references\"], _REFERENCE_DROP_NO_SOURCE_TOPIC\n"
        "        )",
        "        notes = []",
        (f"{T_OPD}::test_generation_does_not_accept_a_cited_curated_document",),
    ),
    # ── r7: 2차 리뷰(4dc64119 BLOCK) — 가짜 출처 세 경로·p07·관리자 PATCH 422 ──
    Mutant(
        "7a 생성 프롬프트 — 진료비·병원 선택 브리프에는 검증된 문서 힌트를 주지 않는다",
        f"{CE}:_topic_aligned_curated_sources",
        CE,
        "    if result is None and _brief_names_no_source_topic(content_brief):",
        "    if False and _brief_names_no_source_topic(content_brief):",
        (f"{T_OPD}::test_prompt_offers_no_curated_document_for_a_cost_or_choice_brief",),
    ),
    Mutant(
        "7a 생성 프롬프트 — 브리프의 핵심 키워드(target_keyword)도 본다",
        f"{CE}:_brief_names_no_source_topic",
        CE,
        "        content_brief.get(\"target_keyword\"),\n        content_brief.get(\"target_question\"),",
        "        content_brief.get(\"target_question\"),",
        (f"{T_OPD}::test_prompt_offers_no_curated_document_for_a_cost_or_choice_brief",),
    ),
    Mutant(
        "7a 생성 프롬프트 — 브리프의 측정 질문 이름(query_target.name)도 본다",
        f"{CE}:_brief_names_no_source_topic",
        CE,
        "        query_target.get(\"name\") if isinstance(query_target, dict) else None,\n    )\n"
        "    return any(",
        "        None,\n    )\n    return any(",
        (f"{T_OPD}::test_prompt_offers_no_curated_document_for_a_cost_or_choice_brief",),
    ),
    Mutant(
        "7b 치유 선택 — 진료과 이름·병원 고르기 경로 키워드만 겹친 글에 수기 목록 문서를 고르지 않는다",
        f"{AS}:select_curated_authority_sources",
        AS,
        "        keywords = [\n"
        "            keyword for keyword in source[\"keywords\"] if not keyword_names_provider(keyword)\n"
        "        ]",
        "        keywords = list(source[\"keywords\"])",
        (
            f"{T_OPD}::test_a_provider_or_routing_keyword_alone_never_passes_any_curated_document",
            f"{T_OPD}::test_a_routing_only_post_gets_no_heal_at_publication",
            f"{T_AS}::test_select_curated_authority_sources_does_not_route_a_clinic_choice_faq",
        ),
    ),
    Mutant(
        "7b 수기 문서 대조 — 진료과 이름·경로 키워드만 겹쳐서는 curated_verified가 아니다",
        f"{RV}:curated_topic_relevant",
        RV,
        "            if keyword and not keyword_names_provider(keyword)\n",
        "            if keyword\n",
        (
            f"{T_OPD}::test_a_provider_choice_post_citing_the_spine_documents_is_not_curated_verified",
            f"{T_OPD}::test_a_provider_or_routing_keyword_alone_never_passes_any_curated_document",
        ),
    ),
    Mutant(
        "7c 병원 선택 — '병원 고를 때'(고를)도 고르기 말이다",
        f"{REQ}:_PROVIDER_CHOICE_TERMS",
        REQ,
        "        \"고를\",\n        \"을고를\",\n        \"를고를\",\n",
        "",
        (f"{T_OPD}::test_choosing_a_clinic_with_goreul_is_a_provider_choice_post",),
    ),
    Mutant(
        "7d 저장된 사람 결정 표시는 본문 없는(쓰이지 않은) 슬롯에서만 판정이 된다 (p07)",
        f"{INCIDENT}:operator_decides_references",
        INCIDENT,
        "        not getattr(item, \"body\", None)\n        and attempt.get(\"reason\") == code\n",
        "        attempt.get(\"reason\") == code\n",
        (f"{T_OPD}::test_a_leftover_operator_flag_does_not_decide_a_written_medical_post",),
    ),
    Mutant(
        "7e 관리자 PATCH — 발행 전 진료비·병원 선택 글의 수기 목록 문서는 GET 전에 422",
        f"{ADMIN}:update_content",
        ADMIN,
        "        _reject_curated_references_for_no_source_topic(unlocked_item, body, normalized_refs)\n",
        "",
        (f"{T_OPD}::test_patch_rejects_a_curated_document_on_a_scheduled_cost_or_choice_post",),
    ),
    Mutant(
        "7e 관리자 PATCH — 잠근 행으로 다시 판정(필수 여부는 스냅샷 밖)",
        f"{ADMIN}:update_content",
        ADMIN,
        "        _reject_curated_references_for_no_source_topic(\n"
        "            item, body, normalized_refs, checks=patched_reference_checks\n"
        "        )\n",
        "",
        (f"{T_OPD}::test_patch_rechecks_on_the_locked_row_when_the_post_becomes_reference_required",),
    ),
    Mutant(
        "7e 관리자 PATCH — 거절 자체(가드 제거)",
        f"{ADMIN}:_reject_curated_references_for_no_source_topic",
        ADMIN,
        "    if not urls:\n        return\n    raise HTTPException(\n        status_code=422,",
        "    if True:\n        return\n    raise HTTPException(\n        status_code=422,",
        (
            f"{T_OPD}::test_patch_rejects_a_curated_document_on_a_scheduled_cost_or_choice_post",
            f"{T_OPD}::test_patch_judges_the_title_it_saves",
        ),
    ),
    Mutant(
        "7e 관리자 PATCH — 저장될 제목(바꾸면 새 제목)으로 판정",
        f"{ADMIN}:_reject_curated_references_for_no_source_topic",
        ADMIN,
        "    urls = disallowed_curated_references(item, references, title=body.title, checks=checks)",
        "    urls = disallowed_curated_references(item, references, checks=checks)",
        (f"{T_OPD}::test_patch_judges_the_title_it_saves",),
    ),
    Mutant(
        "7e 관리자 PATCH — 공개·보존된 글에는 적용하지 않는다",
        f"{RP}:disallowed_curated_references",
        RP,
        "    if not _strips_curated_references(item, title=title):\n        return []",
        "    if not references_left_to_operator(item, title=title):\n        return []",
        (f"{T_OPD}::test_patch_on_a_published_cost_post_is_unaffected",),
    ),
    Mutant(
        "7e 관리자 PATCH — 진료비·병원 선택이 아닌 글(의료 제목)에는 적용하지 않는다",
        f"{RP}:disallowed_curated_references",
        RP,
        "    if not _strips_curated_references(item, title=title):\n        return []",
        "    if _status_value(item) not in _REFERENCE_WRITABLE_STATUSES:\n        return []",
        (
            f"{T_OPD}::test_patch_accepts_a_curated_document_on_a_medical_post",
            f"{T_OPD}::test_patch_judges_the_title_it_saves",
        ),
    ),
    # ── 리뷰 3차 F1: 수기 목록 문서의 별칭·리다이렉트, F5: 운영자 문구, F6: q04 ──────────
    Mutant(
        "8a 문서 id는 정수로 비교 — 앞 0이 아무리 많아도(정수 변환 한도 전에 정리)",
        f"{AS}:_document_id_value",
        AS,
        '    digits = found.lstrip("0")\n',
        "    digits = found\n",
        (f"{T_ALIAS}::test_default_ports_and_decorations_are_the_same_document",),
    ),
    Mutant(
        "8a 문서 판정 — 기본 포트(80·443)는 같은 문서",
        f"{AS}:_document_location",
        AS,
        "    if port and port not in _DEFAULT_PORTS:",
        "    if port:",
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_default_ports_and_decorations_are_the_same_document",
        ),
    ),
    Mutant(
        "4차 (b)-(d) 항목이 쓰지 않는 id 이름(contentId·SEQ·thtimt_cntnts_sn)은 보지 않는다",
        f"{AS}:_matching_documents",
        AS,
        "        if all(number in values.get(name, ()) for name, number in ids)\n",
        "        if all(number in values.get(name, ()) for name, number in ids)\n"
        "        and set(values) <= {name for name, _number in ids}\n",
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "8a 반복 id는 모든 값을 본다 — 마지막 값만 보기",
        f"{AS}:_matching_documents",
        AS,
        "                values.setdefault(name, set()).add(number)\n",
        "                values[name] = {number}\n",
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_a_repeated_id_naming_two_catalog_documents_is_curated_but_served_as_the_first",
        ),
    ),
    Mutant(
        "5차 인정해 주는 판정(목록 항목·카탈로그 대조)은 반복 id의 첫 값만 — 모든 값 보기로 되돌리기",
        f"{AS}:curated_source_entries",
        AS,
        "_matching_documents(url, _CURATED_DOCUMENTS, first_value_only=True, exact_ids=True)",
        "_matching_documents(url, _CURATED_DOCUMENTS, exact_ids=True)",
        (
            f"{T_ALIAS}::test_a_repeated_id_naming_two_catalog_documents_is_curated_but_served_as_the_first",
            f"{T_ALIAS}::test_a_medical_post_citing_a_two_document_url_matches_only_the_first_value",
            f"{T_ALIAS}::test_a_medical_post_citing_a_catalog_id_after_another_id_is_not_that_document",
            f"{T_ALIAS}::test_a_repeated_id_is_kept_on_outage_only_when_the_catalog_id_comes_first",
        ),
    ),
    Mutant(
        "8b 검증기의 수기 판정이 동일성 키를 쓴다(원문 정확 비교로 되돌리기)",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            curated = _serves_curated_document(url)\n",
        '            curated = url in {str(s["url"]) for s in CURATED_MEDICAL_SOURCE_PAGES}\n',
        (
            f"{T_ALIAS}::test_a_medical_post_citing_an_alias_passes_as_the_curated_document",
            f"{T_ALIAS}::test_an_alias_is_judged_by_the_catalog_when_its_site_is_down",
        ),
        note=(
            "GET이 최종 주소를 주면 최종 주소 판정(8c)이 별칭을 다시 목록 문서로 본다 — "
            "접속 불가(최종 주소 없음)에서만 드러난다."
        ),
    ),
    Mutant(
        "8b 카탈로그 대조의 목록 항목 찾기도 동일성 키(원문 정확 비교로 되돌리기)",
        f"{RV}:_curated_entries",
        RV,
        "    return list(curated_source_entries(url))",
        '    return [entry for entry in CURATED_MEDICAL_SOURCE_PAGES if str(entry["url"]) == url]',
        (f"{T_ALIAS}::test_a_medical_post_citing_an_alias_passes_as_the_curated_document",),
    ),
    Mutant(
        "8c 검증기 — GET의 최종 주소가 목록 문서면 목록 문서(리다이렉트)",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if not curated and _serves_curated_document(fetched.final_url):\n",
        "            if False:\n",
        (
            f"{T_ALIAS}::test_generation_drops_an_outside_url_that_redirects_to_a_curated_document",
            f"{T_ALIAS}::test_generation_keeps_a_redirect_to_a_curated_document_on_a_medical_title",
        ),
    ),
    Mutant(
        "8c 리다이렉트된 주소는 최종 주소의 목록 항목으로 카탈로그 대조",
        f"{RV}:judge_fetched_page",
        RV,
        "    catalog_url = url if _serves_curated_document(url) else (fetched.final_url or url)",
        "    catalog_url = url",
        (
            f"{T_ALIAS}::test_generation_keeps_a_redirect_to_a_curated_document_on_a_medical_title",
            f"{T_ALIAS}::test_patch_accepts_a_redirect_to_a_curated_document_on_a_medical_post",
            f"{T_ALIAS}::test_the_catalog_is_the_served_document_of_the_url_or_else_the_final_url",
        ),
    ),
    Mutant(
        "8c 생성 — GET에서 목록 문서로 리다이렉트된 주소도 진료비·병원 선택 글에서 뺀다",
        f"{CE}:_verify_generated_references",
        CE,
        "                if not names_curated_document(reference, outcome.checks)\n",
        "                if True\n",
        (f"{T_ALIAS}::test_generation_drops_an_outside_url_that_redirects_to_a_curated_document",),
    ),
    Mutant(
        "8c 발행 재검증 — 이번 GET에서 목록 문서로 리다이렉트된 주소도 뺀다",
        f"{RP}:refresh_publication_references",
        RP,
        "        kept = [entry for entry in kept if not names_curated_document(entry, outcome.checks)]",
        "        pass",
        (f"{T_ALIAS}::test_publication_refresh_drops_a_redirect_to_a_curated_document",),
    ),
    Mutant(
        "8c 저장된 기록의 최종 주소가 목록 문서면 settled가 아니고 GET 없이 뺀다",
        f"{RV}:names_curated_document",
        RV,
        '    return check is not None and is_curated_source_url(check.get("final_url"))',
        "    return False",
        (
            f"{T_ALIAS}::test_a_stored_pass_whose_final_url_is_curated_is_not_settled",
            f"{T_ALIAS}::test_patch_rejects_a_url_that_redirects_to_a_curated_document",
        ),
    ),
    Mutant(
        "8d 관리자 PATCH — GET이 목록 문서로 리다이렉트되면 같은 422",
        f"{ADMIN}:update_content",
        ADMIN,
        "            item, body, normalized_refs, checks=patched_reference_checks\n",
        "            item, body, normalized_refs\n",
        (f"{T_ALIAS}::test_patch_rejects_a_url_that_redirects_to_a_curated_document",),
    ),
    Mutant(
        "8e 운영자 문구 — 없는 조작('항목 종료')을 말하지 않는다(보류 조치)",
        f"{INCIDENT}:REFERENCES_OPERATOR_DECIDES_ACTION",
        INCIDENT,
        '    "제목·본문을 질환·검사 안내 글로 고쳐 저장합니다 — 다음 발행 확인이 검증된 문서로 참고 "\n'
        '    "자료를 채울 수 있습니다. 병원 누리집과 검증된 문서 목록의 질환 문서는 이 글의 참고 자료가 "',
        '    "제목·본문을 질환·검사 안내 글로 고쳐 저장하거나 해당 항목을 종료합니다 — 다음 발행 확인이 검증된 문서로 참고 "\n'
        '    "자료를 채울 수 있습니다. 병원 누리집과 검증된 문서 목록의 질환 문서는 이 글의 참고 자료가 "',
        (f"{T_COPY}::test_the_copy_names_only_the_real_ways_out",),
    ),
    Mutant(
        "8e 운영자 문구 — 없는 조작('항목 종료')을 말하지 않는다(PATCH 422)",
        f"{ADMIN}:CURATED_REFERENCE_NOT_ALLOWED_MESSAGE",
        ADMIN,
        '    "저장하거나, 그런 문서가 없으면 제목·본문을 질환·검사 안내 글로 고쳐 저장해 주세요."',
        '    "저장하거나, 그런 문서가 없으면 제목·본문을 질환·검사 안내 글로 고쳐 저장해 주세요. 없으면 해당 항목을 종료하세요."',
        (f"{T_COPY}::test_the_copy_names_only_the_real_ways_out",),
    ),
    Mutant(
        "8e 운영자 문구 — 따옴표로 이름 붙인 버튼이 콘텐츠 화면에 있다",
        f"{INCIDENT}:REFERENCES_OPERATOR_DECIDES_ACTION",
        INCIDENT,
        'REFERENCES_OPERATOR_DECIDES_ACTION = (\n'
        '    "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 둘 중 하나를 하세요. 글의 주장을 직접 "',
        'REFERENCES_OPERATOR_DECIDES_ACTION = (\n'
        '    "콘텐츠 탭에서 이 글의 “항목 정리”를 눌러 둘 중 하나를 하세요. 글의 주장을 직접 "',
        (f"{T_COPY}::test_every_quoted_control_in_the_copy_exists_on_the_content_screen",),
    ),
    Mutant(
        "7a q04 — 브리프의 target_question 칸도 따로 본다",
        f"{CE}:_brief_names_no_source_topic",
        CE,
        '        content_brief.get("target_question"),\n',
        "",
        (f"{T_OPD}::test_prompt_offers_no_curated_document_for_a_cost_or_choice_brief",),
    ),
    # ── 리뷰 4차: 남은 별칭 5종(a)-(e), 리뷰어 생존 뮤턴트 r05·r06·r20·r22, 문구, CI 가드 ──────
    Mutant(
        "4차 (a) id 값은 앞 정수로 읽는다(3796abc·3796%2B) — 값 전체가 정수일 때만 보기",
        f"{AS}:_document_id_value",
        AS,
        "        match = _LEADING_INTEGER.match(value)\n",
        "        match = re.fullmatch(r\"\\s*\\+?(\\d+)\\s*\", value)\n",
        (
            f"{T_ALIAS}::test_only_cntnts_sn_reads_every_digit",
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "4차 (e) 반복 id 상한 없음 — 정렬해 16개로 자르기",
        f"{AS}:_matching_documents",
        AS,
        "    seen: set[str] = set()\n    for name, value in pairs:\n",
        "    seen: set[str] = set()\n    for name, value in sorted(pairs)[:16]:\n",
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "4차 (e) 반복 id — 첫 값만 보기",
        f"{AS}:_matching_documents",
        AS,
        "                values.setdefault(name, set()).add(number)\n",
        "                values.setdefault(name, {number})\n",
        (
            f"{T_ALIAS}::test_a_repeated_id_naming_two_catalog_documents_is_curated_but_served_as_the_first",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "4차 r20 반복 id — 대표 값(최솟값) 하나만 비교",
        f"{AS}:_matching_documents",
        AS,
        "                values.setdefault(name, set()).add(number)\n",
        "                values[name] = {min(values.get(name, {number}) | {number})}\n",
        (f"{T_ALIAS}::test_an_alias_is_the_curated_document",),
    ),
    Mutant(
        "4차 r06 경로의 퍼센트 인코딩은 같은 문서(unquote 끄기)",
        f"{AS}:_document_location",
        AS,
        '    path = re.sub(r"/{2,}", "/", unquote(parsed.path)).rstrip("/")\n',
        '    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")\n',
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
        ),
    ),
    Mutant(
        "4차 r22 겹친 슬래시는 같은 문서(축약 끄기)",
        f"{AS}:_document_location",
        AS,
        '    path = re.sub(r"/{2,}", "/", unquote(parsed.path)).rstrip("/")\n',
        '    path = unquote(parsed.path).rstrip("/")\n',
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_the_exclusion_list_uses_the_same_document_matcher",
        ),
    ),
    Mutant(
        "4차 F5 운영자 문구 — 없는 조작('항목 종료')을 말하지 않는다(생성 거절 조치)",
        f"{INCIDENT}:REFERENCE_REJECTION_OPERATOR_ACTION",
        INCIDENT,
        '    "저장하세요. 저장한 글은 일반 글과 같이 발행 전 검사를 거칩니다. 참고 자료 없이 저장하면 "',
        '    "저장하세요. 주제가 맞지 않으면 해당 항목을 종료하세요. 참고 자료 없이 저장하면 "',
        (f"{T_COPY}::test_the_copy_names_only_the_real_ways_out",),
    ),
    Mutant(
        "4차 F5 생성 거절 조치 — 자동 재시도를 약속하지 않는다(사람이 볼 때는 재시도가 끝났다)",
        f"{INCIDENT}:REFERENCE_REJECTION_OPERATOR_ACTION",
        INCIDENT,
        '    "저장하세요. 저장한 글은 일반 글과 같이 발행 전 검사를 거칩니다. 참고 자료 없이 저장하면 "',
        '    "저장하세요. 넣지 않아도 다음 자동 재시도가 검증된 문서를 다시 찾습니다. 참고 자료 없이 저장하면 "',
        (f"{T_COPY}::test_the_copy_names_only_the_real_ways_out",),
    ),
    Mutant(
        "4차 F5 생성 거절 조치 — 문서 없이 저장하면 본문이 다시 쓰일 수 있다고 알린다",
        f"{INCIDENT}:REFERENCE_REJECTION_OPERATOR_ACTION",
        INCIDENT,
        '    "자동 복구가 참고 자료를 찾으며 본문을 다시 쓸 수 있습니다. 병원 누리집은 참고 자료가 될 수 "',
        '    "다음 발행 확인이 참고 자료를 채울 수 있습니다. 병원 누리집은 참고 자료가 될 수 "',
        (f"{T_COPY}::test_the_copy_names_only_the_real_ways_out",),
    ),
    Mutant(
        "4차 CI에서 Admin 소스가 없으면 건너뛰지 않고 실패",
        "tests/test_reference_operator_copy.py:require_admin_source",
        T_COPY,
        '    if environ.get("CI"):\n',
        "    if False:\n",
        (f"{T_COPY}::test_admin_source_guard_fails_under_ci_and_skips_locally",),
    ),
    # ── 리뷰 5차 후속: cntnts_sn 숫자만 읽기, 반복 id 첫 값(인정)·모든 값(뺌), 제외 문서 리다이렉트,
    #    F2(제목만 PATCH), F4(07:45 claim 먼저) ─────────────────────────────────────────────────
    Mutant(
        "5차 cntnts_sn은 값의 숫자만 모아 읽는다(a3796·-3796·37a96) — 앞 정수로 되돌리기",
        f"{AS}:_document_id_value",
        AS,
        "    if name in _DIGITS_ONLY_ID_PARAMS:\n",
        "    if False:\n",
        (
            f"{T_ALIAS}::test_digits_forming_a_catalog_id_name_that_document",
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "5차 숫자만 읽기 — 후보 주소의 값도 id 이름으로 읽는다(이름 전달 빼기)",
        f"{AS}:_matching_documents",
        AS,
        "            number = _document_id_value(value, name=name, exact=exact_ids)\n            if number is not None:\n",
        "            number = _document_id_value(value, exact=exact_ids)\n            if number is not None:\n",
        (
            f"{T_ALIAS}::test_an_alias_is_the_curated_document",
            f"{T_ALIAS}::test_a_different_document_is_not_curated",
        ),
    ),
    Mutant(
        "5차 검증기의 curated(카탈로그 대조·장애 시 유지)는 반복 id의 첫 값만 — 모든 값으로 되돌리기",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            curated = _serves_curated_document(url)\n",
        "            curated = is_curated_source_url(url)\n",
        (
            f"{T_ALIAS}::test_a_medical_post_citing_a_catalog_id_after_another_id_is_not_that_document",
            f"{T_ALIAS}::test_a_repeated_id_is_kept_on_outage_only_when_the_catalog_id_comes_first",
        ),
    ),
    Mutant(
        "5차 빼는 판정(진료비·병원 선택·PATCH 422)은 반복 id의 어느 값이든 — 첫 값만으로 좁히기",
        f"{AS}:is_curated_source_url",
        AS,
        "    return bool(_matching_documents(url, _CURATED_DOCUMENTS))\n",
        "    return bool(_matching_documents(url, _CURATED_DOCUMENTS, first_value_only=True))\n",
        (
            f"{T_ALIAS}::test_a_repeated_id_naming_two_catalog_documents_is_curated_but_served_as_the_first",
            f"{T_ALIAS}::test_generation_drops_a_curated_alias_cited_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_drops_a_curated_alias_on_a_cost_post",
            f"{T_ALIAS}::test_patch_rejects_a_curated_alias_before_any_get",
        ),
    ),
    Mutant(
        "5차 제외 목록은 반복 id의 어느 값이든 — 첫 값만으로 좁히기",
        f"{AS}:reference_exclusion_reason",
        AS,
        "    reasons = _matching_documents(url, _EXCLUDED_DOCUMENTS)\n",
        "    reasons = _matching_documents(url, _EXCLUDED_DOCUMENTS, first_value_only=True)\n",
        (f"{T_ALIAS}::test_the_exclusion_list_uses_the_same_document_matcher",),
    ),
    Mutant(
        "5차 제외 문서로 리다이렉트되는 주소는 제외 주소와 같다(GET 최종 주소 판정 끄기)",
        f"{RV}:ReferenceVerifier.verify",
        RV,
        "            if reference_exclusion_reason(fetched.final_url) is not None:\n",
        "            if False:\n",
        (
            f"{T_ALIAS}::test_a_redirect_to_an_excluded_document_is_judged_like_the_excluded_url",
            f"{T_ALIAS}::test_generation_drops_a_redirect_to_an_excluded_document",
            f"{T_ALIAS}::test_publication_refresh_drops_a_redirect_to_an_excluded_document",
            f"{T_ALIAS}::test_patch_rejects_a_redirect_to_an_excluded_document_like_the_excluded_url",
        ),
        note="검증기 하나가 생성·발행 전 재검증·PATCH 세 경로를 모두 판정한다 — 세 경로 테스트가 각각 잡는다.",
    ),
    Mutant(
        "5차 저장된 통과 기록의 최종 주소가 제외 문서면 통과가 아니다(수정 전 기록 재사용 막기)",
        f"{RV}:_final_url_excluded",
        RV,
        "    return reference_exclusion_reason(check.get(\"final_url\")) is not None\n",
        "    return False\n",
        (f"{T_ALIAS}::test_publication_refresh_drops_a_redirect_to_an_excluded_document",),
    ),
    Mutant(
        "5차 F2 제목만 바꾸는 PATCH도 저장된 목록 문서로 422",
        f"{ADMIN}:update_content",
        ADMIN,
        "    elif body.title is not None:\n",
        "    elif False:\n",
        (f"{T_OPD}::test_a_title_only_patch_that_makes_stored_curated_references_disallowed_is_422",),
    ),
    Mutant(
        "5차 F4 07:45 — 살아 있는 claim이 있는 슬롯은 참고자료 재검증부터 건너뛴다",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "        if generation_claim_is_active(item, now=observed.datetime):\n"
        "            # 생성 워커가 지금 이 슬롯을 쓰고 있다",
        "        if False:\n"
        "            # 생성 워커가 지금 이 슬롯을 쓰고 있다",
        (f"{T_GATE}::test_seven_forty_five_does_not_refresh_a_slot_a_live_worker_is_writing",),
    ),
    Mutant(
        "5차 F4 07:45 — GET 사이에 워커가 잡은 슬롯에는 재검증을 적용하지 않는다",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "            if locked.status not in AUTO_PUBLISHABLE_STATUSES or generation_claim_is_active(\n"
        "                locked, now=observed.datetime\n"
        "            ):\n",
        "            if locked.status not in AUTO_PUBLISHABLE_STATUSES:\n",
        (f"{T_GATE}::test_seven_forty_five_does_not_apply_a_refresh_when_a_worker_claims_during_the_get",),
    ),
    # ── PR #183 후속: ASCII 숫자만, 08:00 claim, 미고정 줄(3a·3b·3d) ─────────────────────
    Mutant(
        "#183 후속 1 `cntnts_sn`의 숫자 모으기는 ASCII 0-9만(전각 ３７９６은 숫자가 아니다)",
        f"{AS}:_NON_DIGITS",
        AS,
        '_NON_DIGITS = re.compile(r"[^0-9]+")\n',
        '_NON_DIGITS = re.compile(r"\\D+")\n',
        (
            f"{T_ALIAS}::test_a_fullwidth_id_is_not_a_catalog_document",
            f"{T_ALIAS}::test_generation_judges_a_fullwidth_id_on_a_cost_title_by_its_page",
            f"{T_ALIAS}::test_publication_refresh_judges_a_fullwidth_id_on_a_cost_post_by_its_page",
            f"{T_ALIAS}::test_patch_judges_a_fullwidth_id_on_a_cost_post_by_its_page",
        ),
        note="`int()`가 유니코드 숫자를 읽으므로 옛 전각 매핑 없이도 전각 id가 3796이 된다.",
    ),
    Mutant(
        "#183 후속 1 앞 정수 규칙도 ASCII 0-9만(AMC `contentId=３１７７３`)",
        f"{AS}:_LEADING_INTEGER",
        AS,
        '_LEADING_INTEGER = re.compile(r"\\s*\\+?([0-9]+)")\n',
        '_LEADING_INTEGER = re.compile(r"\\s*\\+?(\\d+)")\n',
        (
            f"{T_ALIAS}::test_a_fullwidth_id_is_not_a_catalog_document",
            f"{T_ALIAS}::test_generation_judges_a_fullwidth_id_on_a_cost_title_by_its_page",
            f"{T_ALIAS}::test_publication_refresh_judges_a_fullwidth_id_on_a_cost_post_by_its_page",
            f"{T_ALIAS}::test_patch_judges_a_fullwidth_id_on_a_cost_post_by_its_page",
        ),
    ),
    Mutant(
        "#183 후속 1 두 규칙 모두 유니코드 숫자(수정 전 동작 복원 — 전각 → ASCII)",
        f"{AS}:_LEADING_INTEGER/_NON_DIGITS",
        AS,
        '_LEADING_INTEGER = re.compile(r"\\s*\\+?([0-9]+)")\n_NON_DIGITS = re.compile(r"[^0-9]+")\n',
        '_LEADING_INTEGER = re.compile(r"\\s*\\+?(\\d+)")\n_NON_DIGITS = re.compile(r"\\D+")\n',
        (
            f"{T_ALIAS}::test_a_fullwidth_id_never_equals_a_catalog_or_excluded_document",
            f"{T_ALIAS}::test_a_medical_post_citing_a_fullwidth_id_is_judged_by_its_page",
        ),
        note="전각 매핑만 되살리는 편집은 등가 변이다 — ASCII 정규식이 전각 문자를 숫자 문자열에 넣지 않는다.",
    ),
    Mutant(
        "#183 후속 2 08:00 잠금 전 읽기 — 살아 있는 claim이면 GET하지 않는다",
        f"{TASKS}:_prefetch_publication_references",
        TASKS,
        "        if generation_claim_is_active(\n"
        "            item, now=datetime.now(timezone.utc)\n"
        "        ) and not reference_outage_alert_due(\n",
        "        if False and not reference_outage_alert_due(\n",
        (
            f"{T_GATE}::test_eight_does_not_refresh_a_slot_a_live_worker_is_writing",
            f"{T_CLAIM_PG}::test_eight_does_not_refresh_a_row_a_live_worker_is_writing",
        ),
        note="PG",
    ),
    Mutant(
        "#183 후속 2 08:00 잠근 뒤 — GET 사이에 워커가 잡은 행에는 재검증을 적용하지 않는다",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "        if generation_claim_is_active(item, now=datetime.now(timezone.utc)):\n",
        "        if False:\n",
        (
            f"{T_GATE}::test_eight_does_not_apply_a_refresh_when_a_worker_claims_during_the_get",
            f"{T_CLAIM_PG}::test_eight_does_not_apply_a_refresh_when_a_worker_claims_during_the_get",
        ),
        note="PG",
    ),
    Mutant(
        "#183 후속 3a 장애 폴백 — 최종 주소가 제외 문서인 옛 통과는 재사용하지 않는다",
        f"{RV}:_reusable_previous_pass",
        RV,
        '    if not check or check.get("verdict") != VERDICT_PASS or _final_url_excluded(check):\n'
        "        return False\n"
        "    if not _same_topic(check, topic_fingerprint):\n"
        "        return False\n"
        '    return _age_within(check.get("verified_at")',
        '    if not check or check.get("verdict") != VERDICT_PASS:\n'
        "        return False\n"
        "    if not _same_topic(check, topic_fingerprint):\n"
        "        return False\n"
        '    return _age_within(check.get("verified_at")',
        (f"{T_ALIAS}::test_an_old_pass_whose_final_url_is_excluded_is_not_reused_on_outage",),
    ),
    Mutant(
        "#183 후속 3b 카탈로그 대조 주소 — 주소가 목록 문서면(첫 값) 주소, 아니면 최종 주소(항상 최종 주소)",
        f"{RV}:judge_fetched_page",
        RV,
        "    catalog_url = url if _serves_curated_document(url) else (fetched.final_url or url)",
        "    catalog_url = fetched.final_url or url",
        (f"{T_ALIAS}::test_the_catalog_is_the_served_document_of_the_url_or_else_the_final_url",),
    ),
    Mutant(
        "#183 후속 3b 카탈로그 대조 주소의 판정은 첫 값 규칙(어느 값이든 판정으로 바꾸기)",
        f"{RV}:judge_fetched_page",
        RV,
        "    catalog_url = url if _serves_curated_document(url) else (fetched.final_url or url)",
        "    catalog_url = url if is_curated_source_url(url) else (fetched.final_url or url)",
        (f"{T_ALIAS}::test_the_catalog_is_the_served_document_of_the_url_or_else_the_final_url",),
    ),
    Mutant(
        "#183 후속 3d 07:45 — 재검증 적용 뒤·판정 전에 워커가 잡은 슬롯은 보류·인시던트 없음",
        f"{TASKS}:_page_morning_stored_publication_gates",
        TASKS,
        "        if generation_claim_is_active(item, now=observed.datetime):\n"
        "            # 재검증을 적용한 뒤에",
        "        if False:\n"
        "            # 재검증을 적용한 뒤에",
        (f"{T_GATE}::test_seven_forty_five_does_not_page_a_slot_a_worker_claims_after_the_refresh",),
    ),
    # ── PR #185 1차 리뷰: 마지막 발행기의 claim 행 읽기 전용 판정(S2), O7, 앞 정수 인정 규칙 ────────
    Mutant(
        "#185 O7 마지막 발행기가 아닌 시각 — claim 행(본문·미확정 참고자료)은 발행·보류 없이 건너뛴다",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        '                _log_auto_publish_skip("generation_claim_active", content_id, item=item)\n'
        "                return None\n",
        "                pass\n",
        (
            f"{T_GATE}::test_a_claimed_row_is_neither_published_nor_held_at_a_non_last_hour",
            f"{T_LAST_PG}::test_a_claimed_cost_post_at_noon_is_skipped_with_nothing_reported",
            f"{T_LAST_PG}::test_a_claimed_cost_post_with_a_dead_outside_url_at_noon_is_not_fetched",
        ),
        note="PG",
    ),
    Mutant(
        "#185 S2 마지막 발행기의 claim 행 사본 판정 끄기(늘 건너뛰기)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "            elif not reference_outage_alert_due(item.scheduled_date, now_kst):\n",
        "            elif True:\n",
        (
            f"{T_GATE}::test_last_run_reports_a_claimed_cost_post_without_touching_it",
            f"{T_LAST_PG}::test_s2_a_claimed_cost_post_with_a_dead_outside_url_is_reported_once",
        ),
        note="PG",
    ),
    Mutant(
        "#185 S2 사본 판정은 마지막 발행기만(매시 켜기)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "            elif not reference_outage_alert_due(item.scheduled_date, now_kst):\n",
        "            elif False:\n",
        (
            f"{T_GATE}::test_a_claimed_row_is_neither_published_nor_held_at_a_non_last_hour",
            f"{T_LAST_PG}::test_a_claimed_cost_post_at_noon_is_skipped_with_nothing_reported",
            f"{T_LAST_PG}::test_a_claimed_cost_post_with_a_dead_outside_url_at_noon_is_not_fetched",
        ),
        note="PG",
    ),
    Mutant(
        "#185 S2 마지막 발행기의 claim 행은 분리된 사본에서 판정(행에 재검증 적용·판 올림 허용)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "                item = _detached_publication_view(item)\n",
        "",
        (
            f"{T_GATE}::test_last_run_reports_a_claimed_cost_post_without_touching_it",
            f"{T_LAST_PG}::test_s2_a_claimed_cost_post_with_a_dead_outside_url_is_reported_once",
        ),
        note="PG",
    ),
    Mutant(
        "#185 S2 사본 판정이 보류가 아니면 공개하지 않는다(사본 공개 허용)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "        if read_only_row is not None:\n"
        "            # 저장된 상태로는 보류가 아니다",
        "        if False:\n"
        "            # 저장된 상태로는 보류가 아니다",
        (f"{T_GATE}::test_last_run_does_not_report_or_publish_a_claimed_medical_post_the_catalog_heals",),
    ),
    Mutant(
        "#185 S2 잠금 전 재검증이 없을 때의 저장된 상태 판정은 GET하지 않는다(GET 한도 0 제거)",
        f"{TASKS}:_stored_state_reference_verifier",
        TASKS,
        "    return ReferenceVerifier(_never_fetch, max_fetches=0)\n",
        "    return ReferenceVerifier(_never_fetch)\n",
        (f"{T_GATE}::test_last_run_falls_back_to_the_stored_state_without_a_prefetched_refresh",),
    ),
    Mutant(
        "#185 S2 잠금 전 재검증이 없으면 저장된 상태로 판정(대체 경로 끄기)",
        f"{TASKS}:_auto_publish_one",
        TASKS,
        "                if reference_refresh is None:\n"
        "                    # 잠금 전 읽기와 잠금 사이의 경합",
        "                if False:\n"
        "                    # 잠금 전 읽기와 잠금 사이의 경합",
        (f"{T_GATE}::test_last_run_falls_back_to_the_stored_state_without_a_prefetched_refresh",),
    ),
    Mutant(
        "#185 S2 잠금 전 재검증 — 마지막 발행기에는 claim 행도 종전처럼 GET(예외 끄기)",
        f"{TASKS}:_prefetch_publication_references",
        TASKS,
        "        if generation_claim_is_active(\n"
        "            item, now=datetime.now(timezone.utc)\n"
        "        ) and not reference_outage_alert_due(\n"
        '            item.scheduled_date, now_kst or arrow.now("Asia/Seoul")\n'
        "        ):\n",
        "        if generation_claim_is_active(item, now=datetime.now(timezone.utc)):\n",
        (
            f"{T_GATE}::test_prefetch_gets_a_claimed_row_only_in_the_last_window",
            f"{T_GATE}::test_last_run_reports_a_claimed_cost_post_without_touching_it",
            f"{T_LAST_PG}::test_s2_a_claimed_cost_post_with_a_dead_outside_url_is_reported_once",
        ),
        note="PG",
    ),
    Mutant(
        "#185 S2 잠금 전 재검증 — 마지막 발행기가 아니면 claim 행은 GET 없이(예외를 매시에)",
        f"{TASKS}:_prefetch_publication_references",
        TASKS,
        "        if generation_claim_is_active(\n"
        "            item, now=datetime.now(timezone.utc)\n"
        "        ) and not reference_outage_alert_due(\n"
        '            item.scheduled_date, now_kst or arrow.now("Asia/Seoul")\n'
        "        ):\n",
        "        if False:\n",
        (
            f"{T_GATE}::test_prefetch_gets_a_claimed_row_only_in_the_last_window",
            f"{T_GATE}::test_a_claimed_row_is_neither_published_nor_held_at_a_non_last_hour",
            f"{T_LAST_PG}::test_a_claimed_cost_post_with_a_dead_outside_url_at_noon_is_not_fetched",
        ),
        note="PG",
    ),
    Mutant(
        "#185 후속 3 인정해 주는 판정은 앞 정수 이름의 값이 ASCII 숫자만(curated_source_entries에서 끄기)",
        f"{AS}:curated_source_entries",
        AS,
        "first_value_only=True, exact_ids=True)",
        "first_value_only=True)",
        (
            f"{T_ALIAS}::test_an_inexact_leading_integer_id_is_not_recognised_but_still_removed",
            f"{T_ALIAS}::test_a_medical_post_citing_an_inexact_leading_integer_id_is_judged_by_its_page",
            f"{T_ALIAS}::test_only_ascii_digits_are_read",
        ),
    ),
    Mutant(
        "#185 후속 3 값 규칙 — exact면 값 전체가 숫자여야 한다(규칙 끄기)",
        f"{AS}:_document_id_value",
        AS,
        "    elif exact:\n",
        "    elif False:\n",
        (
            f"{T_ALIAS}::test_an_inexact_leading_integer_id_is_not_recognised_but_still_removed",
            f"{T_ALIAS}::test_a_medical_post_citing_an_inexact_leading_integer_id_is_judged_by_its_page",
        ),
    ),
    Mutant(
        "#185 후속 3 빼는 판정은 관대한 앞 정수 해석 그대로(엄격 규칙을 빼는 쪽에도 쓰기)",
        f"{AS}:_matching_documents",
        AS,
        "            number = _document_id_value(value, name=name, exact=exact_ids)\n",
        "            number = _document_id_value(value, name=name, exact=True)\n",
        (
            f"{T_ALIAS}::test_generation_still_drops_an_inexact_leading_integer_id_on_a_cost_title",
            f"{T_ALIAS}::test_publication_refresh_still_drops_an_inexact_leading_integer_id_on_a_cost_post",
            f"{T_ALIAS}::test_patch_still_rejects_an_inexact_leading_integer_id_on_a_cost_post",
        ),
    ),
    Mutant(
        "#185 후속 3 인정 값은 부호·공백 없이(`+7`·` 7` 받기)",
        f"{AS}:_EXACT_ID_VALUE",
        AS,
        '_EXACT_ID_VALUE = re.compile(r"[0-9]+")\n',
        '_EXACT_ID_VALUE = re.compile(r"\\s*\\+?[0-9]+")\n',
        (f"{T_ALIAS}::test_an_inexact_leading_integer_id_is_not_recognised_but_still_removed",),
    ),
    Mutant(
        "#185 후속 3 인정 값은 ASCII 숫자(`\\d`로 전각 `7３` 받기)",
        f"{AS}:_EXACT_ID_VALUE",
        AS,
        '_EXACT_ID_VALUE = re.compile(r"[0-9]+")\n',
        '_EXACT_ID_VALUE = re.compile(r"\\d+")\n',
        (
            f"{T_ALIAS}::test_an_inexact_leading_integer_id_is_not_recognised_but_still_removed",
            f"{T_ALIAS}::test_a_fullwidth_id_is_not_a_catalog_document",
        ),
        note="`7３`은 `int()`가 73으로 읽어 어차피 7이 아니다 — 전각만의 `３１７７３`이 잡는다.",
    ),
    Mutant(
        "#185 후속 3 `cntnts_sn`은 인정할 때도 숫자만 모은다(엄격 규칙을 cntnts_sn에도 쓰기)",
        f"{AS}:_document_id_value",
        AS,
        "    if name in _DIGITS_ONLY_ID_PARAMS:\n        found = _NON_DIGITS.sub",
        "    if name in _DIGITS_ONLY_ID_PARAMS and not exact:\n        found = _NON_DIGITS.sub",
        (f"{T_ALIAS}::test_a_medical_post_citing_an_alias_passes_as_the_curated_document",),
    ),
    Mutant(
        "#185 리뷰 A2 인시던트는 살아 있는 claim 행의 저장 기한 키를 지우지 않는다(가드 제거)",
        f"{INCIDENT}:open_generation_incident",
        INCIDENT,
        "            if item is not None and not generation_claim_is_active(item, now=observed_at):\n",
        "            if item is not None:\n",
        (
            f"{T_OPD}::test_the_incident_leaves_the_attempt_of_a_live_claimed_row_alone",
            f"{T_LAST_PG}::test_the_real_incident_does_not_write_a_live_claimed_row_at_the_last_run",
        ),
        note="PG",
    ),
)


def run_pytest(tests: tuple[str, ...]) -> tuple[int, str]:
    command = [
        str(PYTHON),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-x",
        "-rs",
        *tests,
    ]
    completed = subprocess.run(
        command, cwd=BACKEND, env=os.environ.copy(), capture_output=True, text=True
    )
    return completed.returncode, completed.stdout[-3000:] + completed.stderr[-2000:]


def _summary(output: str) -> str:
    for line in reversed(output.strip().splitlines()):
        if re.search(r"\d+ (passed|failed|skipped|error)", line):
            return line.strip().strip("=").strip()
    return output.strip().splitlines()[-1] if output.strip() else ""


def check_targets() -> int:
    """Dry run: every row's target string must occur exactly once. Runs no tests."""

    bad = 0
    for index, mutant in enumerate(MUTANTS, 1):
        count = (BACKEND / mutant.path).read_text(encoding="utf-8").count(mutant.target)
        if count != 1:
            bad += 1
            print(f"#{index} x{count} {mutant.path}: {mutant.guard}")
    print(f"{len(MUTANTS)} rows, {bad} with a target that does not occur exactly once")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-targets", action="store_true", help="only check target strings")
    parser.add_argument("--out", type=Path, help="also write the Markdown table here")
    args = parser.parse_args(argv)
    if args.check_targets:
        return check_targets()
    rows = []
    all_caught = True
    for mutant in MUTANTS:
        path = BACKEND / mutant.path
        original = path.read_text(encoding="utf-8")
        occurrences = original.count(mutant.target)
        if occurrences != 1:
            rows.append((mutant, f"TARGET x{occurrences} (not applied)", ""))
            all_caught = False
            continue
        baseline_code, baseline_out = run_pytest(mutant.tests)
        if baseline_code != 0:
            rows.append((mutant, "BASELINE-FAIL", _summary(baseline_out)))
            all_caught = False
            continue
        if re.search(r"\d+ skipped", _summary(baseline_out)) and not re.search(
            r"\d+ passed", _summary(baseline_out)
        ):
            rows.append((mutant, "SKIPPED-ONLY", _summary(baseline_out)))
            all_caught = False
            continue
        try:
            path.write_text(original.replace(mutant.target, mutant.replacement, 1), encoding="utf-8")
            code, output = run_pytest(mutant.tests)
        finally:
            path.write_text(original, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == original, f"restore failed: {path}"
        result = "CAUGHT (FAIL)" if code != 0 else "SURVIVED"
        if code == 0:
            all_caught = False
        rows.append((mutant, result, _summary(output)))
        print(f"[{result}] {mutant.guard}", file=sys.stderr, flush=True)

    lines = [
        "# 가드 하나씩 제거 시 FAIL 표 (reference_guard_mutants.py)",
        "",
        "각 행: 가드를 한 줄 수정으로 제거 → 지정 테스트 실행 → 원본 복원(저장된 사본). "
        "CAUGHT = 테스트가 실패해 가드 제거를 잡음.",
        "",
        "| # | 가드 | 파일:함수 | 변이(제거 편집) | 잡는 테스트 | 결과 | pytest 요약 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, (mutant, result, summary) in enumerate(rows, 1):
        mutation = " ".join(mutant.target.split())[:70] + " → " + (
            " ".join(mutant.replacement.split())[:50] or "(삭제)"
        )
        tests = "<br>".join(f"`{test}`" for test in mutant.tests)
        note = f" ({mutant.note})" if mutant.note else ""
        lines.append(
            f"| {index} | {mutant.guard}{note} | `{mutant.location}` | `{mutation.replace('|', '¦')}` | "
            f"{tests} | **{result}** | {summary.replace('|', '¦')} |"
        )
    caught = sum(1 for _m, result, _s in rows if result.startswith("CAUGHT"))
    lines += ["", f"**요약: {len(rows)}개 가드 중 {caught}개 CAUGHT.**"]
    text = "\n".join(lines) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if all_caught else 1


if __name__ == "__main__":
    raise SystemExit(main())
