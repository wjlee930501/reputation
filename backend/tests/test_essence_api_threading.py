import inspect

from app.api.admin import essence


def test_philosophy_draft_synthesis_is_offloaded_from_the_event_loop():
    source = inspect.getsource(essence.create_philosophy_draft)

    assert "await asyncio.to_thread(" in source
    assert "synthesize_philosophy," in source
