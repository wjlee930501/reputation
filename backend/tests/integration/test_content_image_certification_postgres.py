import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import content_image_certification as backfill
from app.services.image_engine import CertifiedImageArtifact, image_subject_hash
from app.services.image_policy import ImagePolicyAssessment, ImagePolicyRejectedError
from app.services.sync_async_bridge import SyncAsyncBridge


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


def _seed_legacy_public_image(conn) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    content_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status, site_live) "
            "VALUES (:id, '이미지검수병원', :slug, 'ACTIVE', true)"
        ),
        {"id": hospital_id, "slug": f"image-cert-{uuid.uuid4().hex[:8]}"},
    )
    conn.execute(
        text(
            "INSERT INTO content_schedules (id, hospital_id, plan, publish_days, active_from) "
            "VALUES (:id, :hid, 'PLAN_12', '[1, 3]', :active_from)"
        ),
        {"id": schedule_id, "hid": hospital_id, "active_from": date(2026, 7, 1)},
    )
    conn.execute(
        text(
            "INSERT INTO content_items "
            "(id, hospital_id, schedule_id, content_type, sequence_no, total_count, "
            "scheduled_date, status, title, body, image_url, published_at) "
            "VALUES (:id, :hid, :sid, 'DISEASE', 1, 8, :d, 'PUBLISHED', "
            "'검증할 제목', '검증할 본문', 'gs://legacy-bucket/original.png', :published_at)"
        ),
        {
            "id": content_id,
            "hid": hospital_id,
            "sid": schedule_id,
            "d": date(2026, 7, 15),
            "published_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
        },
    )
    return content_id


def test_dry_run_is_zero_mutation_and_zero_provider_calls(pg_conn, pg_session, monkeypatch):
    content_id = _seed_legacy_public_image(pg_conn)
    monkeypatch.setattr(
        backfill,
        "certify_existing_image_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider called")),
    )

    result = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=True
    )

    assert result.candidates == 1
    row = pg_conn.execute(
        text(
            "SELECT content_revision, generation_claim_token, image_content_hash "
            "FROM content_items WHERE id = :id"
        ),
        {"id": content_id},
    ).one()
    assert row == (1, None, None)


def test_allowlist_dry_run_classifies_every_id_without_false_current(
    pg_conn, pg_session
):
    candidate_id = _seed_legacy_public_image(pg_conn)
    current_id = _seed_legacy_public_image(pg_conn)
    no_image_id = _seed_legacy_public_image(pg_conn)
    inactive_id = _seed_legacy_public_image(pg_conn)
    unpublished_id = _seed_legacy_public_image(pg_conn)
    missing_id = uuid.uuid4()
    current_hash = "d" * 64
    pg_conn.execute(
        text(
            "UPDATE content_items SET image_url = :url, image_content_hash = :hash, "
            "image_subject_hash = :subject, image_policy_version = 'image-policy-v2', "
            "image_policy_verified_at = now() WHERE id = :id"
        ),
        {
            "id": current_id,
            "url": f"gs://bucket/{current_hash}-copy.png",
            "hash": current_hash,
            "subject": image_subject_hash("DISEASE", "검증할 제목"),
        },
    )
    pg_conn.execute(
        text("UPDATE content_items SET image_url = NULL WHERE id = :id"),
        {"id": no_image_id},
    )
    pg_conn.execute(
        text(
            "UPDATE hospitals SET site_live = false WHERE id = "
            "(SELECT hospital_id FROM content_items WHERE id = :id)"
        ),
        {"id": inactive_id},
    )
    pg_conn.execute(
        text("UPDATE content_items SET status = 'REJECTED' WHERE id = :id"),
        {"id": unpublished_id},
    )

    result = backfill.run_legacy_image_certification_backfill(
        pg_session,
        content_ids={
            candidate_id,
            current_id,
            no_image_id,
            inactive_id,
            unpublished_id,
            missing_id,
        },
        dry_run=True,
    )

    assert result.allowlisted == 6
    assert result.candidates == 1
    assert result.already_current == 1
    assert result.no_image == 1
    assert result.inactive_site == 1
    assert result.no_longer_public == 1
    assert result.missing_db_ids == 1
    assert result.remaining == 5


def test_safe_review_copies_exact_bytes_and_cas_binds_certificate(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    image_bytes = b"reviewed-exact-image"
    reviewed = 0

    async def certify(*_args, **_kwargs):
        nonlocal reviewed
        reviewed += 1
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=__import__("hashlib").sha256(image_bytes).hexdigest(),
            subject_hash=image_subject_hash("DISEASE", "검증할 제목"),
        )

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", certify)
    monkeypatch.setattr(
        backfill,
        "store_certified_image_bytes",
        lambda value, _slug: (
            "gs://new-bucket/" + __import__("hashlib").sha256(value).hexdigest() + "-copy.png"
        ),
    )
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    result = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )

    assert result.certified == 1
    assert reviewed == 1
    row = pg_conn.execute(
        text(
            "SELECT content_revision, generation_claim_token, image_content_hash, "
            "image_subject_hash, image_policy_version, image_policy_verified_at, image_url "
            "FROM content_items WHERE id = :id"
        ),
        {"id": content_id},
    ).one()
    assert row.content_revision == 2
    assert row.generation_claim_token is None
    assert row.image_content_hash == __import__("hashlib").sha256(image_bytes).hexdigest()
    assert row.image_subject_hash == image_subject_hash("DISEASE", "검증할 제목")
    assert row.image_policy_version == "image-policy-v2"
    assert row.image_policy_verified_at is not None
    assert row.image_url.startswith("gs://new-bucket/")


def test_upload_retry_reuses_safe_checkpoint_without_second_review(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    image_bytes = b"durable-reviewed-image"
    reviewed = 0
    uploads = 0

    async def certify(*_args, **_kwargs):
        nonlocal reviewed
        reviewed += 1
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=__import__("hashlib").sha256(image_bytes).hexdigest(),
            subject_hash=image_subject_hash("DISEASE", "검증할 제목"),
        )

    def upload(_value, _slug):
        nonlocal uploads
        uploads += 1
        if uploads == 1:
            raise TimeoutError("storage unavailable")
        return "gs://new-bucket/hash-copy.png"

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", certify)
    monkeypatch.setattr(backfill, "store_certified_image_bytes", upload)
    monkeypatch.setattr(
        "app.services.image_engine._download_stored_image", lambda _url: image_bytes
    )
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    first = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )
    second = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )

    assert first.safe_pending_upload == 1
    assert second.certified == 1
    assert reviewed == 1
    assert uploads == 2


def test_provider_failures_are_durably_bounded_before_each_call(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    calls = 0

    async def unavailable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("review provider unavailable")

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", unavailable)

    outcomes = []
    outcomes.append(
        backfill.run_legacy_image_certification_backfill(
            pg_session, content_ids={content_id}, dry_run=False
        )
    )
    # Essence-only writes advance the row revision. They must preserve the paid
    # attempt budget while the actual image subject is unchanged.
    pg_conn.execute(
        text(
            "UPDATE content_items SET content_revision = content_revision + 1 "
            "WHERE id = :id"
        ),
        {"id": content_id},
    )
    outcomes.extend(
        backfill.run_legacy_image_certification_backfill(
            pg_session, content_ids={content_id}, dry_run=False
        )
        for _ in range(3)
    )

    assert calls == 3
    assert outcomes[2].review_exhausted == 1
    assert outcomes[3].review_exhausted == 1


def test_crash_after_provider_start_does_not_refund_durable_attempt(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    calls = 0

    async def crash_then_fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit("simulated worker loss")
        raise TimeoutError("review provider unavailable")

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", crash_then_fail)

    with pytest.raises(SystemExit, match="simulated worker loss"):
        backfill.run_legacy_image_certification_backfill(
            pg_session, content_ids={content_id}, dry_run=False
        )
    pg_conn.execute(
        text(
            "UPDATE content_items SET generation_claimed_at = now() - interval '3 hours' "
            "WHERE id = :id"
        ),
        {"id": content_id},
    )
    outcomes = [
        backfill.run_legacy_image_certification_backfill(
            pg_session, content_ids={content_id}, dry_run=False
        )
        for _ in range(3)
    ]

    assert calls == 3
    assert outcomes[-1].review_exhausted == 1


def test_editor_revision_change_blocks_provider_checkpoint_write(pg_engine, monkeypatch):
    with pg_engine.begin() as seed_conn:
        content_id = _seed_legacy_public_image(seed_conn)
    image_bytes = b"late-reviewed-image"
    uploads = 0

    async def certify(*_args, **_kwargs):
        with pg_engine.begin() as editor_conn:
            editor_conn.execute(
                text(
                    "UPDATE content_items SET title = '운영자 최신 제목', "
                    "content_revision = content_revision + 1 WHERE id = :id"
                ),
                {"id": content_id},
            )
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=__import__("hashlib").sha256(image_bytes).hexdigest(),
            subject_hash=image_subject_hash("DISEASE", "검증할 제목"),
        )

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", certify)

    def upload(*_args):
        nonlocal uploads
        uploads += 1
        return "gs://new-bucket/" + "a" * 64 + "-copy.png"

    monkeypatch.setattr(backfill, "store_certified_image_bytes", upload)
    with Session(bind=pg_engine, expire_on_commit=False) as session:
        result = backfill.run_legacy_image_certification_backfill(
            session, content_ids={content_id}, dry_run=False
        )

    assert result.cas_stale == 1
    assert uploads == 0
    with pg_engine.begin() as verify_conn:
        row = verify_conn.execute(
            text(
                "SELECT title, content_revision, image_content_hash, generation_claim_token, "
                "essence_check_summary FROM content_items WHERE id = :id"
            ),
            {"id": content_id},
        ).one()
        assert row.title == "운영자 최신 제목"
        assert row.content_revision == 2
        assert row.image_content_hash is None
        assert row.generation_claim_token is None
        assert "SAFE_REVIEWED" not in str(row.essence_check_summary)
        verify_conn.execute(text("DELETE FROM content_items WHERE id = :id"), {"id": content_id})


def test_safe_finalization_merges_concurrent_hard_review_without_lost_update(
    pg_engine, monkeypatch
):
    with pg_engine.begin() as seed_conn:
        content_id = _seed_legacy_public_image(seed_conn)
    image_bytes = b"reviewed-while-hard-finding-arrived"
    content_hash = __import__("hashlib").sha256(image_bytes).hexdigest()

    async def certify(*_args, **_kwargs):
        with pg_engine.begin() as reviewer_conn:
            reviewer_conn.execute(
                text(
                    "UPDATE content_items SET essence_check_summary = "
                    "coalesce(essence_check_summary, '{}'::jsonb) || "
                    "CAST(:review AS jsonb) "
                    "WHERE id = :id"
                ),
                {
                    "id": content_id,
                    "review": (
                        '{"ai_review":{"status":"REVISE","blocking":true}}'
                    ),
                },
            )
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=content_hash,
            subject_hash=image_subject_hash("DISEASE", "검증할 제목"),
        )

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", certify)
    monkeypatch.setattr(
        backfill,
        "store_certified_image_bytes",
        lambda *_args: f"gs://new-bucket/{content_hash}-copy.png",
    )
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    with Session(bind=pg_engine, expire_on_commit=False) as session:
        result = backfill.run_legacy_image_certification_backfill(
            session, content_ids={content_id}, dry_run=False
        )

    assert result.certified == 1
    with pg_engine.begin() as verify_conn:
        row = verify_conn.execute(
            text(
                "SELECT image_content_hash, essence_check_summary "
                "FROM content_items WHERE id = :id"
            ),
            {"id": content_id},
        ).one()
        assert row.image_content_hash == content_hash
        assert row.essence_check_summary["ai_review"] == {
            "status": "REVISE",
            "blocking": True,
        }
        assert "legacy_image_certification" not in row.essence_check_summary
        verify_conn.execute(text("DELETE FROM content_items WHERE id = :id"), {"id": content_id})


def test_unsafe_review_replaces_once_and_binds_generated_hash(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    rejected = ImagePolicyAssessment(
        has_text=True,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )

    async def unsafe(*_args, **_kwargs):
        raise ImagePolicyRejectedError(rejected)

    replacement_hash = "b" * 64

    async def generate(*_args, **_kwargs):
        return f"gs://new-bucket/{replacement_hash}-replacement.png", "safe prompt"

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", unsafe)
    monkeypatch.setattr(backfill, "generate_image", generate)
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    result = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )

    assert result.replaced == 1
    row = pg_conn.execute(
        text(
            "SELECT content_revision, image_url, image_content_hash, image_subject_hash, "
            "image_policy_version, image_policy_verified_at, generation_claim_token "
            "FROM content_items WHERE id = :id"
        ),
        {"id": content_id},
    ).one()
    assert row.content_revision == 3
    assert row.image_url.endswith("-replacement.png")
    assert row.image_content_hash == replacement_hash
    assert row.image_subject_hash == image_subject_hash("DISEASE", "검증할 제목")
    assert row.image_policy_version == "image-policy-v2"
    assert row.image_policy_verified_at is not None
    assert row.generation_claim_token is None


def test_replacement_failure_retries_without_second_policy_review(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    reviews = 0
    generations = 0
    repair_modes = []
    prior_rejections = []
    rejected = ImagePolicyAssessment(
        has_text=True,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )

    async def unsafe(*_args, **_kwargs):
        nonlocal reviews
        reviews += 1
        raise ImagePolicyRejectedError(rejected)

    async def generate(*_args, **kwargs):
        nonlocal generations
        generations += 1
        repair_modes.append(kwargs["policy_repair"])
        prior_rejections.append(kwargs["prior_policy_rejection"])
        if generations == 1:
            kwargs["diagnostics"].update(
                {
                    "reason": "POLICY_REJECTED",
                    "policy_rejection": {
                        "reason": "POLICY_REJECTED",
                        "stage": "GOOGLE_PRIMARY",
                        "prompt_version": "google-primary-v1",
                        "has_text": False,
                        "has_logo": False,
                        "has_recognizable_people": False,
                        "impersonates_real_clinic": False,
                        "topic_relevant": False,
                    },
                }
            )
            return "", ""
        return "gs://new-bucket/" + "c" * 64 + "-replacement.png", "safe prompt"

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", unsafe)
    monkeypatch.setattr(backfill, "generate_image", generate)
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    first = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )
    second = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )

    assert first.replacement_required == 1
    assert second.replaced == 1
    assert reviews == 1
    assert generations == 2
    assert repair_modes == [False, True]
    assert prior_rejections[0] is None
    assert prior_rejections[1]["topic_relevant"] is False


def test_replacement_diagnostic_is_durable_and_two_attempt_cap_never_resets(
    pg_conn, pg_session, monkeypatch
):
    content_id = _seed_legacy_public_image(pg_conn)
    reviews = 0
    generations = 0
    repair_modes = []
    rejected = ImagePolicyAssessment(
        has_text=False,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=False,
    )

    async def unsafe(*_args, **_kwargs):
        nonlocal reviews
        reviews += 1
        raise ImagePolicyRejectedError(rejected)

    async def generate(*_args, **kwargs):
        nonlocal generations
        generations += 1
        repair_modes.append(kwargs["policy_repair"])
        kwargs["diagnostics"].update(
            {
                "reason": "POLICY_REJECTED",
                "policy_rejection": {
                    "reason": "POLICY_REJECTED",
                    "stage": "GOOGLE_REPAIR" if kwargs["policy_repair"] else "GOOGLE_PRIMARY",
                    "prompt_version": (
                        "topical-no-text-repair-v3"
                        if kwargs["policy_repair"]
                        else "google-primary-v1"
                    ),
                    "has_text": False,
                    "has_logo": False,
                    "has_recognizable_people": False,
                    "impersonates_real_clinic": False,
                    "topic_relevant": False,
                },
            }
        )
        return "", ""

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", unsafe)
    monkeypatch.setattr(backfill, "generate_image", generate)
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    first = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )
    second = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )
    replay = backfill.run_legacy_image_certification_backfill(
        pg_session, content_ids={content_id}, dry_run=False
    )

    assert first.replacement_required == 1
    assert second.replacement_exhausted == 1
    assert replay.replacement_exhausted == 1
    assert reviews == 1
    assert generations == 2
    assert repair_modes == [False, True]
    state = pg_conn.execute(
        text("SELECT essence_check_summary FROM content_items WHERE id = :id"),
        {"id": content_id},
    ).scalar_one()["legacy_image_certification"]
    assert state["replacement_attempts"] == 2
    assert state["replacement_prompt_version"] == "topical-no-text-repair-v3"
    assert state["replacement_diagnostic"] == {
        "reason": "POLICY_REJECTED",
        "stage": "GOOGLE_REPAIR",
        "prompt_version": "topical-no-text-repair-v3",
        "has_text": False,
        "has_logo": False,
        "has_recognizable_people": False,
        "impersonates_real_clinic": False,
        "topic_relevant": False,
    }
    assert state["source_diagnostic"]["stage"] == "EXISTING_IMAGE_REVIEW"


def test_two_items_share_real_redis_loop_and_settle_both_reservations(
    pg_conn, pg_session, monkeypatch
):
    redis_url = os.getenv("INTEGRATION_REDIS_URL")
    if not redis_url:
        pytest.skip("INTEGRATION_REDIS_URL is required for the real Redis loop check")
    import redis

    from app.services import cost_guard

    first_id = _seed_legacy_public_image(pg_conn)
    second_id = _seed_legacy_public_image(pg_conn)
    sync_client = redis.Redis.from_url(redis_url)
    now = cost_guard._now().astimezone(cost_guard._KST)
    daily_key = cost_guard._daily_key("content", cost_guard._daily_period(now))
    monthly_key = cost_guard._monthly_key("content", cost_guard._monthly_period(now))
    before = (int(sync_client.get(daily_key) or 0), int(sync_client.get(monthly_key) or 0))
    receipts = []

    monkeypatch.setattr(cost_guard.settings, "REDIS_URL", redis_url)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_ENABLED", True)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_DAILY_CONTENT_CALLS", 1_000_000)
    monkeypatch.setattr(cost_guard.settings, "COST_GUARD_MONTHLY_CONTENT_CALLS", 1_000_000)
    cost_guard._redis_client = None

    async def certify(image_url, *, content_type, topic, **_kwargs):
        decision = await cost_guard.reserve(
            "content", reservation_id=f"image-cert-loop:{uuid.uuid4()}"
        )
        assert decision.allowed
        assert decision.receipt is not None
        receipts.append(decision.receipt)
        await cost_guard.settle_reservation(decision.receipt, consumed_units=0)
        image_bytes = f"bytes:{image_url}".encode()
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=__import__("hashlib").sha256(image_bytes).hexdigest(),
            subject_hash=image_subject_hash(content_type, topic),
        )

    monkeypatch.setattr(backfill, "certify_existing_image_artifact", certify)
    monkeypatch.setattr(
        backfill,
        "store_certified_image_bytes",
        lambda value, _slug: (
            "gs://new-bucket/" + __import__("hashlib").sha256(value).hexdigest() + "-copy.png"
        ),
    )
    monkeypatch.setattr(backfill.indexnow, "enqueue_content_published_sync", lambda *_a, **_k: None)

    try:
        result = backfill.run_legacy_image_certification_backfill(
            pg_session, content_ids={first_id, second_id}, dry_run=False
        )

        assert result.certified == 2
        assert len(receipts) == 2
        assert len({receipt.id for receipt in receipts}) == 2
        assert (int(sync_client.get(daily_key) or 0), int(sync_client.get(monthly_key) or 0)) == before
        for receipt in receipts:
            stored = sync_client.hgetall(cost_guard._reservation_key(receipt.id))
            assert stored[b"consumed_units"] == b"0"
            assert stored[b"released_units"] == b"1"
    finally:
        if cost_guard._redis_client is not None:
            with SyncAsyncBridge() as bridge:
                bridge.run(cost_guard._redis_client.aclose())
            cost_guard._redis_client = None
        if receipts:
            sync_client.delete(
                *(cost_guard._reservation_key(receipt.id) for receipt in receipts)
            )
        sync_client.close()
