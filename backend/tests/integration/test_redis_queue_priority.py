"""Verify Kombu and Redis select queued control work before background recovery."""

from __future__ import annotations

import json
import os
import uuid

import pytest
import redis
from celery.beat import ScheduleEntry, Scheduler
from kombu import Connection

from app.core.celery_app import celery_app

# Register the task object Beat resolves before exercising Scheduler.apply_async.
from app.workers import autonomous_recovery as _autonomous_recovery  # noqa: F401
from app.workers import provider_usage_recovery as _provider_usage_recovery  # noqa: F401
from app.workers.dispatch_auth import build_dispatch_headers

CONTROL_TASK = "app.workers.autonomous_recovery.reconcile"
BACKGROUND_TASK = "app.workers.provider_usage_recovery.drain"


def test_actual_redis_consumer_key_order_prioritizes_control() -> None:
    redis_url = os.getenv("INTEGRATION_REDIS_URL")
    if not redis_url:
        pytest.skip("INTEGRATION_REDIS_URL is required for the real Redis priority check")

    suffix = uuid.uuid4().hex
    control_queue = f"itest-control-{suffix}"
    background_queue = f"itest-background-{suffix}"
    connection = Connection(
        redis_url,
        transport_options={"queue_order_strategy": "priority"},
    )
    channel = connection.channel()
    client = redis.Redis.from_url(redis_url)
    keys = [
        channel._q_for_pri(queue, priority)
        for priority in channel.priority_steps
        for queue in (control_queue, background_queue)
    ]
    try:
        channel._put(
            background_queue,
            {"body": "background", "headers": {}, "properties": {"priority": 9}},
        )
        channel._put(
            control_queue,
            {"body": "control", "headers": {}, "properties": {"priority": 0}},
        )

        selected = client.brpop(keys, timeout=1)

        assert selected is not None
        assert selected[0].decode() == control_queue
    finally:
        client.delete(*keys)
        channel.close()
        connection.close()


def test_actual_redis_publishers_put_control_on_priority_zero_key() -> None:
    """Beat's registered Task path and direct send_task must agree on wire priority."""
    redis_url = os.getenv("INTEGRATION_REDIS_URL")
    if not redis_url:
        pytest.skip("INTEGRATION_REDIS_URL is required for the real Redis priority check")

    suffix = uuid.uuid4().hex
    beat_queue = f"itest-beat-control-{suffix}"
    task_queue = f"itest-task-control-{suffix}"
    direct_queue = f"itest-direct-control-{suffix}"
    background_queue = f"itest-task-background-{suffix}"
    connection = Connection(
        redis_url,
        transport_options={"queue_order_strategy": "priority"},
    )
    channel = connection.channel()
    client = redis.Redis.from_url(redis_url)
    queues = (beat_queue, task_queue, direct_queue, background_queue)
    keys = [
        channel._q_for_pri(queue, priority)
        for queue in queues
        for priority in channel.priority_steps
    ]
    binding_keys = [f"_kombu.binding.{queue}" for queue in queues]
    try:
        producer = connection.Producer()
        scheduler = Scheduler(app=celery_app, schedule={}, lazy=True)
        scheduler.apply_async(
            ScheduleEntry(
                name="itest-autonomous-recovery",
                task=CONTROL_TASK,
                schedule=60,
                options={
                    "queue": beat_queue,
                    "headers": build_dispatch_headers("reconcile-autonomous-workflows"),
                },
                app=celery_app,
            ),
            producer=producer,
            advance=False,
        )
        celery_app.tasks[CONTROL_TASK].apply_async(
            queue=task_queue,
            producer=producer,
            headers=build_dispatch_headers("reconcile-autonomous-workflows"),
        )
        celery_app.send_task(
            CONTROL_TASK,
            queue=direct_queue,
            producer=producer,
            headers=build_dispatch_headers("reconcile-autonomous-workflows"),
        )
        celery_app.tasks[BACKGROUND_TASK].apply_async(
            queue=background_queue,
            producer=producer,
            headers=build_dispatch_headers("drain-provider-usage-spool"),
        )

        for queue in (beat_queue, task_queue, direct_queue):
            raw = client.lindex(queue, -1)
            assert raw is not None
            message = json.loads(raw)
            assert message["headers"]["task"] == CONTROL_TASK
            assert message["properties"]["priority"] == 0
            assert client.llen(channel._q_for_pri(queue, 3)) == 0

        background_key = channel._q_for_pri(background_queue, 9)
        raw_background = client.lindex(background_key, -1)
        assert raw_background is not None
        background_message = json.loads(raw_background)
        assert background_message["headers"]["task"] == BACKGROUND_TASK
        assert background_message["properties"]["priority"] == 9
        assert client.llen(background_queue) == 0
    finally:
        client.delete(*keys, *binding_keys)
        channel.close()
        connection.close()
