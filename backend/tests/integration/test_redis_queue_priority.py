"""Verify Kombu and Redis select queued control work before background recovery."""

from __future__ import annotations

import os
import uuid

import pytest
import redis
from kombu import Connection


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
