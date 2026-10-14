"""읽기 전용 운영 조회: 자율 운영 지표(Q1~Q6), 배포 전 확인(Q7), 롤백 점검(Q8), 콘텐츠 게이트 지표(Q9~Q10),
Phase 4 사전 조회(Q12~Q14)를 JSON 한 줄씩 출력한다.

새 세션의 트랜잭션에서 첫 문장으로 `SET TRANSACTION READ ONLY`를 실행해 DB 수준에서 쓰기를 막고,
끝나면 롤백한다. 받은 세션에 이미 열린 트랜잭션이 있으면 먼저 롤백해 새로 시작한다::

    python -m app.utils.ops_sql_report                          # 전부
    python -m app.utils.ops_sql_report --only=q1,q3             # 고른 것만(쉼표 목록)
    python -m app.utils.ops_sql_report --only=q1 --only=q3      # 반복도 된다

운영에서는 VPC 안의 migrate Job으로 돌린다. gcloud `--args`는 쉼표로 인자를 나누므로 쿼리마다
`--only`를 따로 넘긴다::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.ops_sql_report,--only=q1,--only=q3

출력: 쿼리마다 `{"q": "q1", "rows": [...]}` 한 줄. 보조 쿼리는 `q1b`·`q3b`로 같은 이름 아래 나온다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from sqlalchemy import text

# 이름 → 순서대로 실행할 SELECT. 두 번째 이후 문장은 이름 뒤에 b, c…를 붙여 출력한다.
QUERIES: dict[str, tuple[str, ...]] = {
    # 열린 사고 유형 × 상태, 나이. 보조: 기한 없는 RETRYING 목록.
    "q1": (
        """
        SELECT incident_type, state, count(*) AS n,
               min(now()-first_seen_at) AS min_age, avg(now()-first_seen_at) AS avg_age,
               max(now()-first_seen_at) AS max_age,
               count(*) FILTER (WHERE state='RETRYING' AND sla_due_at IS NULL) AS retrying_no_sla
        FROM incidents
        WHERE state IN ('OPEN','RETRYING')
        GROUP BY incident_type, state
        ORDER BY n DESC, incident_type
        """,
        """
        SELECT id, incident_type, hospital_id, first_seen_at, last_seen_at, safe_error_code
        FROM incidents
        WHERE state='RETRYING' AND sla_due_at IS NULL
        ORDER BY first_seen_at
        """,
    ),
    # 최근 14일 알림 종류 × 상태. 라벨은 `notification_labels.EVENT_LABELS`로 후처리한다.
    "q2": (
        """
        SELECT notification_type, channel, state, count(*) AS n,
               min(created_at) AS first_at, max(created_at) AS last_at,
               count(*) FILTER (WHERE state IN ('HOLD','FAILED')) AS stuck,
               max(attempt_count) AS max_attempts,
               array_agg(DISTINCT safe_error_code)
                 FILTER (WHERE safe_error_code IS NOT NULL) AS error_codes
        FROM notification_outbox
        WHERE created_at >= now() - interval '14 days'
        GROUP BY notification_type, channel, state
        ORDER BY notification_type, state
        """,
    ),
    # 작업 실행 유형 × 상태, 나이. 보조: 24시간 넘은 RUNNING 목록.
    "q3": (
        """
        SELECT operation_type, state, count(*) AS n,
               min(now()-requested_at) AS min_age, avg(now()-requested_at) AS avg_age,
               max(now()-requested_at) AS max_age
        FROM operation_runs
        GROUP BY operation_type, state
        ORDER BY operation_type, state
        """,
        """
        SELECT id, operation_type, hospital_id, requested_at, started_at, heartbeat_at,
               lease_expires_at, attempt_count, task_id
        FROM operation_runs
        WHERE state='RUNNING' AND coalesce(started_at, requested_at) < now() - interval '24 hours'
        ORDER BY coalesce(started_at, requested_at)
        """,
    ),
    # 최신 생성 시도가 OPERATOR_REQUIRED / INPUT_CHANGE_REQUIRED인 글, 원인 코드별.
    "q4": (
        """
        SELECT essence_check_summary->'generation_attempt'->>'retry_class' AS retry_class,
               essence_check_summary->'generation_attempt'->>'reason' AS reason,
               status, count(*) AS n, count(DISTINCT hospital_id) AS hospitals,
               min(essence_check_summary->'generation_attempt'->>'first_observed_at')
                 AS oldest_first_observed,
               max(essence_check_summary->'generation_attempt'->>'observed_at')
                 AS latest_observed
        FROM content_items
        WHERE essence_check_summary->'generation_attempt'->>'retry_class'
                IN ('OPERATOR_REQUIRED','INPUT_CHANGE_REQUIRED')
          AND status NOT IN ('PUBLISHED','CANCELLED','WITHHELD')
        GROUP BY 1, 2, 3
        ORDER BY n DESC
        """,
    ),
    # ACTIVE 병원 중 일정 미설정이거나 발행 0건.
    "q5": (
        """
        SELECT h.id, h.name, h.status, h.profile_complete, h.site_built, h.schedule_set,
               count(c.id) FILTER (WHERE c.first_published_at IS NOT NULL OR c.status='PUBLISHED')
                 AS published_total,
               count(c.id) FILTER (WHERE c.status IN ('DRAFT','READY')) AS pending_slots
        FROM hospitals h
        LEFT JOIN content_items c ON c.hospital_id = h.id
        WHERE h.status='ACTIVE'
        GROUP BY h.id
        HAVING h.schedule_set = false
            OR count(c.id) FILTER (
                 WHERE c.first_published_at IS NOT NULL OR c.status='PUBLISHED'
               ) = 0
        ORDER BY h.name
        """,
    ),
    # 주제 교체된 슬롯과 최신 시도(교체 뒤인지). 시도 기록은 글당 한 조각이라 '교체 직후 첫
    # 시도'는 복원할 수 없다 — 최신 시도가 교체 이후인지까지만 안다.
    "q6": (
        """
        SELECT c.id AS content_id, c.hospital_id, c.status, c.scheduled_date, e.ord AS swap_no,
               e.entry->>'swapped_at' AS swapped_at, e.entry->>'reason_code' AS swap_reason,
               e.entry->>'from_title' AS from_title,
               c.essence_check_summary->'generation_attempt'->>'reason' AS latest_attempt_reason,
               c.essence_check_summary->'generation_attempt'->>'retry_class'
                 AS latest_retry_class,
               c.essence_check_summary->'generation_attempt'->>'observed_at'
                 AS latest_observed_at,
               (c.essence_check_summary->'generation_attempt'->>'observed_at')::timestamptz
                 > (e.entry->>'swapped_at')::timestamptz AS attempt_is_after_swap
        FROM content_items c
        CROSS JOIN LATERAL jsonb_array_elements(c.topic_swap_history)
          WITH ORDINALITY AS e(entry, ord)
        WHERE jsonb_typeof(c.topic_swap_history)='array'
          AND jsonb_array_length(c.topic_swap_history) > 0
        ORDER BY (e.entry->>'swapped_at') DESC
        """,
    ),
    # 배포 전 확인(Phase 1, 0088): notification_outbox 행 수. 0088은 부분 인덱스를 CONCURRENTLY 없이
    # 만든다 — 약 100만 행을 넘으면 Alembic 밖에서 CONCURRENTLY로 먼저 만든다(0088 docstring).
    "q7": (
        """
        SELECT count(*) AS outbox_rows,
               count(*) FILTER (WHERE notification_type = 'INCIDENT_OPEN') AS open_notice_rows
        FROM notification_outbox
        """,
    ),
    # 롤백 점검(Phase 2 첫 달 안분): 시스템이 첫 일정을 만들며 첫 계약 월을 안분한 병원과 그 달 슬롯 수.
    # Phase 2 이전 Worker는 약속 편수를 요금제 편수로 보므로, 그 리비전으로 롤백하면 21:30 당월 조정이
    # 안분된 달을 요금제 편수까지 채운다(0편 달은 남은 하루에 한 달 편수를 몰아 넣는다). 옛 코드가 채운
    # 슬롯은 `total_count`가 기록된 안분 편수보다 크다 — `topped_up_slots`가 0이 아니면 초과 배정이다.
    # 보조(q8b): 초과 배정 중 아직 생성·공개·사람 편집이 없는 취소 후보 슬롯.
    "q8": (
        """
        SELECT f.hospital_id, h.name, f.schedule_id, f.first_month_start, f.plan,
               f.first_month_target,
               count(ci.id) AS month_slots,
               count(ci.id) FILTER (WHERE ci.status <> 'CANCELLED') AS live_slots,
               count(ci.id) FILTER (
                 WHERE ci.status <> 'CANCELLED' AND ci.total_count > f.first_month_target
               ) AS topped_up_slots
        FROM (
          SELECT DISTINCT ON (a.hospital_id)
                 a.hospital_id, a.target_id AS schedule_id,
                 CAST(a.detail->>'active_from' AS date) AS first_month_start,
                 CAST(a.detail->>'first_month_target' AS integer) AS first_month_target,
                 a.detail->>'plan' AS plan
          FROM admin_audit_logs a
          WHERE a.action = 'set_schedule' AND a.actor = 'system:essence-auto-approval'
            AND a.target_type = 'content_schedule' AND a.detail ? 'first_month_target'
          ORDER BY a.hospital_id, a.created_at
        ) f
        JOIN hospitals h ON h.id = f.hospital_id
        LEFT JOIN content_items ci
          ON ci.hospital_id = f.hospital_id
         AND coalesce(ci.carried_over_from, ci.scheduled_date)
             >= CAST(date_trunc('month', f.first_month_start) AS date)
         AND coalesce(ci.carried_over_from, ci.scheduled_date)
             < CAST(date_trunc('month', f.first_month_start) + interval '1 month' AS date)
        WHERE f.first_month_target
              < CASE f.plan WHEN 'PLAN_12' THEN 12 WHEN 'PLAN_16' THEN 16 WHEN 'PLAN_20' THEN 20 END
        GROUP BY f.hospital_id, h.name, f.schedule_id, f.first_month_start, f.plan,
                 f.first_month_target
        ORDER BY f.first_month_start, h.name
        """,
        """
        SELECT ci.hospital_id, ci.id AS content_id, ci.scheduled_date, ci.total_count,
               f.first_month_target, ci.status, ci.created_at
        FROM (
          SELECT DISTINCT ON (a.hospital_id)
                 a.hospital_id,
                 CAST(a.detail->>'active_from' AS date) AS first_month_start,
                 CAST(a.detail->>'first_month_target' AS integer) AS first_month_target
          FROM admin_audit_logs a
          WHERE a.action = 'set_schedule' AND a.actor = 'system:essence-auto-approval'
            AND a.target_type = 'content_schedule' AND a.detail ? 'first_month_target'
          ORDER BY a.hospital_id, a.created_at
        ) f
        JOIN content_items ci
          ON ci.hospital_id = f.hospital_id
         AND coalesce(ci.carried_over_from, ci.scheduled_date)
             >= CAST(date_trunc('month', f.first_month_start) AS date)
         AND coalesce(ci.carried_over_from, ci.scheduled_date)
             < CAST(date_trunc('month', f.first_month_start) + interval '1 month' AS date)
        WHERE ci.total_count > f.first_month_target
          AND ci.status = 'DRAFT' AND ci.body IS NULL AND ci.generation_claim_token IS NULL
          AND ci.first_published_at IS NULL AND ci.human_edited_at IS NULL
        ORDER BY ci.hospital_id, ci.scheduled_date, ci.sequence_no
        """,
    ),
    # (Phase 3) 사후 검수 FLAGGED 공개 글의 지적 분포 — 유형 × 심각도 × 종류. 보조: 교정 결과 사유 분포.
    "q9": (
        """
        SELECT c.content_type,
               f.finding->>'severity' AS severity,
               f.finding->>'kind' AS kind,
               count(*) AS findings,
               count(DISTINCT c.id) AS items,
               count(*) FILTER (WHERE coalesce(f.finding->>'quote', '') <> '') AS quoted
        FROM content_items c
        CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(c.essence_check_summary->'post_publish_ai_review'
                                 ->'structured_findings') = 'array'
               THEN c.essence_check_summary->'post_publish_ai_review'->'structured_findings'
               ELSE '[]'::jsonb END
        ) AS f(finding)
        WHERE c.status = 'PUBLISHED'
          AND c.essence_check_summary->'post_publish_ai_review'->>'status' = 'FLAGGED'
        GROUP BY 1, 2, 3
        ORDER BY findings DESC, 1, 2, 3
        """,
        """
        SELECT c.content_type,
               c.essence_check_summary->'post_publish_ai_review'->'correction'->>'reason'
                 AS correction_reason,
               count(*) AS items
        FROM content_items c
        WHERE c.status = 'PUBLISHED'
          AND c.essence_check_summary->'post_publish_ai_review'->>'status' = 'FLAGGED'
        GROUP BY 1, 2
        ORDER BY items DESC, 1, 2
        """,
    ),
    # (Phase 3) 생성 거절의 사유 분포(E5). 첫째: 최신 시도 기록이 GENERATION_REJECTED인 글의 사유 라벨.
    # 둘째: 최근 30일 예정 글의 생성 세션이 겪은 결정적 거절·치유 횟수(사유별 합).
    "q10": (
        """
        SELECT coalesce(essence_check_summary->'generation_attempt'->>'rejection_kind', 'unlabelled')
                 AS rejection_kind,
               essence_check_summary->'generation_attempt'->>'retry_class' AS retry_class,
               count(*) AS n
        FROM content_items
        WHERE essence_check_summary->'generation_attempt'->>'reason' = 'GENERATION_REJECTED'
        GROUP BY 1, 2
        ORDER BY n DESC, 1, 2
        """,
        """
        SELECT b.bucket, k.kind, sum(k.n::int) AS total, count(DISTINCT c.id) AS items
        FROM content_items c
        CROSS JOIN LATERAL jsonb_each(
          CASE WHEN jsonb_typeof(c.essence_check_summary->'generation_rejections') = 'object'
               THEN c.essence_check_summary->'generation_rejections' ELSE '{}'::jsonb END
        ) AS b(bucket, counts)
        CROSS JOIN LATERAL jsonb_each_text(
          CASE WHEN jsonb_typeof(b.counts) = 'object' THEN b.counts ELSE '{}'::jsonb END
        ) AS k(kind, n)
        WHERE c.scheduled_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - 30
        GROUP BY 1, 2
        ORDER BY 1, total DESC, 2
        """,
    ),
    # (0090) 범용 서술 각도(`treatment_narrative.source='fallback'`) 글 — 공개된 적 없거나 최근 3일 안에 처음
    # 공개된 것. 검수 기록·hash 유무·승인 판·최신 생성 시도. hash가 현재 본문과 같은지는 SQL로 다시 계산하지
    # 않는다(발행 게이트가 본다) — hash가 없으면 게이트가 STALE로 막고 복구 스윕이 재검수한다.
    "q11": (
        """
        SELECT left(id::text, 8) AS content_id8,
               status,
               scheduled_date,
               essence_check_summary->'ai_review'->>'status' AS ai_review_status,
               coalesce(essence_check_summary->'ai_review'->>'candidate_sha256', '') <> ''
                 AS review_hash_present,
               active_revision_id IS NOT NULL AS has_revision,
               published_at,
               essence_check_summary->'generation_attempt'->>'reason' AS attempt_reason,
               generation_claimed_at IS NOT NULL AS claimed
        FROM content_items
        WHERE content_brief->'treatment_narrative'->>'source' = 'fallback'
          AND (first_published_at IS NULL
               OR first_published_at >= now() - interval '3 days')
          AND status <> 'CANCELLED'
        ORDER BY scheduled_date, id
        """,
    ),
    # (Phase 4, D31) 병원별 완료 술어 — 옛 술어(약력·철학·네이버+Google 필수)와 새 술어(철학·Google 선택)의
    # 누락 키, 인수 수락 여부. `would_promote`는 배포 뒤 15분 틱이 **완료로 승격**할 병원이고, 실제로 공개가
    # 새로 열리는지는 `would_publish_now`가 말한다 — 승격(또는 이미 완료) 뒤 사이트 준비가 기본 주소로 자동
    # 활성화하는 병원(자동 전환 상태·자기 도메인 없음·최소 공개 사실 있음, `hospital_activation.
    # evaluate_auto_activation`·`profile_publication_verdict`의 SQL 쌍둥이). 못 여는 이유는
    # `activation_blockers`(PAUSED·ALREADY_LIVE·ACTIVE_NOT_LIVE·CUSTOM_DOMAIN_PENDING·PROFILE_INCOMPLETE·
    # HANDOFF_NOT_ACCEPTED·SITE_NOT_BUILT·MINIMUM_PUBLIC_FACTS_MISSING). 술어는 `hospital_lifecycle.
    # profile_requirements`의 SQL 쌍둥이다(tests/integration/test_ops_sql_report_p4_postgres.py가 대조한다).
    "q12": (
        """
        SELECT r.id, r.name, r.status, r.aeo_domain, r.profile_complete, r.site_built, r.site_live,
               r.handoff_accepted, r.missing_old, r.missing_new, r.would_promote,
               array_remove(ARRAY[
                 CASE WHEN r.status = 'PAUSED' THEN 'PAUSED' END,
                 CASE WHEN r.status = 'ACTIVE' AND r.site_live THEN 'ALREADY_LIVE' END,
                 CASE WHEN r.status = 'ACTIVE' AND NOT r.site_live THEN 'ACTIVE_NOT_LIVE' END,
                 CASE WHEN r.custom_domain THEN 'CUSTOM_DOMAIN_PENDING' END,
                 CASE WHEN NOT r.profile_complete AND cardinality(r.missing_new) > 0
                      THEN 'PROFILE_INCOMPLETE' END,
                 CASE WHEN NOT r.profile_complete AND NOT r.handoff_accepted
                      THEN 'HANDOFF_NOT_ACCEPTED' END,
                 -- 이미 완료인데 준비가 안 된 병원은 이번 승격이 사이트 준비를 보내지 않는다.
                 CASE WHEN r.profile_complete AND NOT r.site_built AND r.status <> 'ACTIVE'
                      THEN 'SITE_NOT_BUILT' END,
                 CASE WHEN NOT r.public_facts_ok THEN 'MINIMUM_PUBLIC_FACTS_MISSING' END
               ], NULL) AS activation_blockers,
               (r.status IN ('ONBOARDING', 'ANALYZING', 'BUILDING', 'PENDING_DOMAIN')
                AND NOT r.custom_domain
                AND r.public_facts_ok
                AND (r.would_promote OR (r.profile_complete AND r.site_built)))
                 AS would_publish_now
        FROM (
          SELECT p.*,
                 (p.text_facts_ok AND p.treatments_ok) AS public_facts_ok,
                 array_remove(ARRAY[
                   CASE WHEN NOT (p.has_director_name AND p.has_career) THEN 'director_basic' END,
                   CASE WHEN NOT p.has_philosophy THEN 'director_philosophy' END,
                   CASE WHEN NOT p.contact_ok THEN 'contact' END,
                   CASE WHEN NOT p.web_ok THEN 'web_channels' END,
                   CASE WHEN NOT (p.has_naver AND p.has_google) THEN 'ai_channels' END,
                   CASE WHEN NOT p.geo_ok THEN 'geo' END,
                   CASE WHEN NOT p.targeting_ok THEN 'targeting' END,
                   CASE WHEN NOT p.treatments_ok THEN 'treatments' END
                 ], NULL) AS missing_old,
                 array_remove(ARRAY[
                   CASE WHEN NOT (p.has_director_name AND p.has_career) THEN 'director_basic' END,
                   CASE WHEN NOT p.contact_ok THEN 'contact' END,
                   CASE WHEN NOT p.web_ok THEN 'web_channels' END,
                   CASE WHEN NOT p.has_naver THEN 'ai_channels' END,
                   CASE WHEN NOT p.geo_ok THEN 'geo' END,
                   CASE WHEN NOT p.targeting_ok THEN 'targeting' END,
                   CASE WHEN NOT p.treatments_ok THEN 'treatments' END
                 ], NULL) AS missing_new,
                 (NOT p.profile_complete AND p.status <> 'PAUSED' AND p.handoff_accepted
                  AND p.has_director_name AND p.has_career AND p.contact_ok AND p.web_ok
                  AND p.has_naver AND p.geo_ok AND p.targeting_ok AND p.treatments_ok)
                   AS would_promote
          FROM (
            SELECT h.id, h.name, h.status, h.aeo_domain,
                   coalesce(h.aeo_domain, '') ~ '[^[:space:].]' AS custom_domain,
                   coalesce(h.profile_complete, false) AS profile_complete,
                   coalesce(h.site_built, false) AS site_built,
                   coalesce(h.site_live, false) AS site_live,
                   EXISTS (
                     SELECT 1 FROM hospital_handoffs ho
                     WHERE ho.hospital_id = h.id AND ho.state = 'HANDOFF_ACCEPTED'
                   ) AS handoff_accepted,
                   coalesce(h.director_name, '') ~ '[^[:space:]]' AS has_director_name,
                   coalesce(h.director_career, '') ~ '[^[:space:]]' AS has_career,
                   coalesce(h.director_philosophy, '') ~ '[^[:space:]]' AS has_philosophy,
                   (coalesce(h.address, '') ~ '[^[:space:]]'
                    AND coalesce(h.phone, '') ~ '[^[:space:]]'
                    AND EXISTS (
                      SELECT 1
                      FROM jsonb_each(
                        CASE WHEN jsonb_typeof(CAST(h.business_hours AS jsonb)) = 'object'
                             THEN CAST(h.business_hours AS jsonb) ELSE '{}'::jsonb END
                      ) AS bh(day, hours)
                      WHERE jsonb_typeof(bh.hours) = 'string'
                        AND (bh.hours #>> '{}') ~ '[^[:space:]]'
                    )) AS contact_ok,
                   (coalesce(h.website_url, '') ~ '[^[:space:]]'
                    OR coalesce(h.blog_url, '') ~ '[^[:space:]]') AS web_ok,
                   coalesce(h.naver_place_url, '') ~ '[^[:space:]]' AS has_naver,
                   (coalesce(h.google_maps_url, '') ~ '[^[:space:]]'
                    OR coalesce(h.google_business_profile_url, '') ~ '[^[:space:]]') AS has_google,
                   (coalesce(h.latitude BETWEEN -90 AND 90, false)
                    AND coalesce(h.longitude BETWEEN -180 AND 180, false)
                    AND jsonb_typeof(CAST(h.region AS jsonb)) = 'array'
                    AND jsonb_array_length(CAST(h.region AS jsonb)) > 0) AS geo_ok,
                   (jsonb_typeof(CAST(h.specialties AS jsonb)) = 'array'
                    AND jsonb_array_length(CAST(h.specialties AS jsonb)) > 0
                    AND jsonb_typeof(CAST(h.keywords AS jsonb)) = 'array'
                    AND jsonb_array_length(CAST(h.keywords AS jsonb)) > 0) AS targeting_ok,
                   EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(CAST(h.treatments AS jsonb)) = 'array'
                            THEN CAST(h.treatments AS jsonb) ELSE '[]'::jsonb END
                     ) AS t(item)
                     WHERE (jsonb_typeof(t.item) = 'string'
                            AND (t.item #>> '{}') ~ '[^[:space:]]')
                        OR (jsonb_typeof(t.item) = 'object'
                            AND jsonb_typeof(t.item->'name') = 'string'
                            AND (t.item->>'name') ~ '[^[:space:]]')
                   ) AS treatments_ok,
                   -- 공개 표면의 최소 사실(`minimum_public_fact_blockers`): 이름·slug·주소·전화·진료 항목.
                   (coalesce(h.name, '') ~ '[^[:space:]]'
                    AND coalesce(h.slug, '') ~ '[^[:space:]]'
                    AND coalesce(h.address, '') ~ '[^[:space:]]'
                    AND coalesce(h.phone, '') ~ '[^[:space:]]') AS text_facts_ok
            FROM hospitals h
          ) p
        ) r
        ORDER BY would_publish_now DESC, r.would_promote DESC, r.name
        """,
    ),
    # (Phase 4, D30·D33) 병원별 Essence 판·자료·자동 입력 상태. APPROVED 판 유무와 is_base, 보류 DRAFT 수,
    # 자료 종류×상태 집계(INTERVIEW 포함), PROFILE_AUTOFILL 실행 유무와 마지막 초안의 키 수.
    "q13": (
        """
        SELECT h.id, h.name, h.status, h.profile_complete,
               (SELECT max(p.version) FROM hospital_content_philosophies p
                 WHERE p.hospital_id = h.id AND p.status = 'APPROVED') AS approved_version,
               EXISTS (SELECT 1 FROM hospital_content_philosophies p
                        WHERE p.hospital_id = h.id AND p.status = 'APPROVED' AND p.is_base)
                 AS has_approved_base,
               (SELECT count(*) FROM hospital_content_philosophies p
                 WHERE p.hospital_id = h.id AND p.status = 'DRAFT') AS draft_philosophies,
               (SELECT coalesce(jsonb_object_agg(k.kind, k.n), '{}'::jsonb)
                  FROM (SELECT CAST(a.source_type AS text) || ':' || CAST(a.status AS text) AS kind,
                               count(*) AS n
                          FROM hospital_source_assets a
                         WHERE a.hospital_id = h.id
                         GROUP BY 1) k) AS sources_by_type_status,
               (SELECT count(*) FROM hospital_source_assets a
                 WHERE a.hospital_id = h.id AND a.source_type = 'INTERVIEW') AS interview_sources,
               (SELECT count(*) FROM operation_runs r
                 WHERE r.hospital_id = h.id AND r.operation_type = 'PROFILE_AUTOFILL') AS autofill_runs,
               (SELECT max(r.created_at) FROM operation_runs r
                 WHERE r.hospital_id = h.id AND r.operation_type = 'PROFILE_AUTOFILL')
                 AS last_autofill_at,
               (SELECT (SELECT count(*) FROM jsonb_object_keys(
                          CASE WHEN jsonb_typeof(r.result_summary->'draft') = 'object'
                               THEN r.result_summary->'draft' ELSE '{}'::jsonb END))
                  FROM operation_runs r
                 WHERE r.hospital_id = h.id AND r.operation_type = 'PROFILE_AUTOFILL'
                 ORDER BY r.created_at DESC LIMIT 1) AS last_draft_keys
        FROM hospitals h
        ORDER BY has_approved_base, h.name
        """,
    ),
    # (Phase 4, D33) Essence 보류 사다리의 남은 사고·알림. 열린(OPEN·RETRYING) ESSENCE_AUTO_REVIEW_*·
    # ESSENCE_MUST_USE_EXCLUDED·MISSING_APPROVED_ESSENCE 사고(유형×원인×상태), 보조: 아직 끝나지 않은
    # MISSING_APPROVED_ESSENCE_DIGEST 알림 행.
    "q14": (
        """
        SELECT incident_type, safe_error_code, state, count(*) AS n,
               count(DISTINCT hospital_id) AS hospitals,
               min(first_seen_at) AS oldest_first_seen, max(last_seen_at) AS latest_seen
        FROM incidents
        WHERE state IN ('OPEN','RETRYING')
          AND (incident_type LIKE 'ESSENCE_AUTO_REVIEW_%'
               OR incident_type = 'ESSENCE_MUST_USE_EXCLUDED'
               OR incident_type = 'MISSING_APPROVED_ESSENCE'
               OR safe_error_code = 'MISSING_APPROVED_ESSENCE')
        GROUP BY incident_type, safe_error_code, state
        ORDER BY n DESC, incident_type
        """,
        """
        SELECT state, count(*) AS n, min(created_at) AS oldest_at, max(created_at) AS latest_at
        FROM notification_outbox
        WHERE notification_type = 'MISSING_APPROVED_ESSENCE_DIGEST'
          AND state IN ('PENDING','SENDING','RETRYING','HOLD')
        GROUP BY state
        ORDER BY state
        """,
    ),
}


def _labels(name: str, count: int) -> list[str]:
    return [name if index == 0 else f"{name}{chr(ord('a') + index)}" for index in range(count)]


def run_report(db: Any, names: Sequence[str], *, out: TextIO = sys.stdout) -> None:
    """고른 쿼리를 읽기 전용 트랜잭션 하나에서 실행하고 언제나 롤백한다."""

    # SET TRANSACTION은 트랜잭션의 첫 문장이어야 한다. 세션이 이미 무언가를 실행했다면 그
    # 트랜잭션을 버리고 새로 시작한다.
    db.rollback()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        for name in names:
            statements = QUERIES[name]
            for label, sql in zip(_labels(name, len(statements)), statements, strict=True):
                rows = db.execute(text(sql)).mappings().all()
                print(
                    json.dumps(
                        {"q": label, "rows": [dict(row) for row in rows]},
                        default=str,
                        ensure_ascii=False,
                    ),
                    file=out,
                    flush=True,
                )
    finally:
        db.rollback()


def _query_names(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in QUERIES]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"알 수 없는 쿼리: {', '.join(unknown) or raw!r} (가능: {', '.join(QUERIES)})"
        )
    return names


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.utils.ops_sql_report",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        type=_query_names,
        action="append",
        default=None,
        help="쿼리 이름. 반복하거나 쉼표로 묶는다(--only=q1 --only=q3, --only=q1,q3). 생략하면 전부.",
    )
    args = parser.parse_args(argv)
    if args.only is None:
        args.only = list(QUERIES)
    else:
        # 반복과 쉼표 목록을 한 줄로 펴고, 같은 쿼리는 처음 나온 자리에서 한 번만 돈다.
        args.only = list(dict.fromkeys(name for names in args.only for name in names))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        run_report(db, args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
