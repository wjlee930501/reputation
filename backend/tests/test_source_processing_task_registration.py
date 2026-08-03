from app.core.celery_app import celery_app
from app.workers.tasks import process_source_asset_task


def test_bulk_source_processing_task_is_registered() -> None:
    task_name = "app.workers.tasks.process_source_asset_task"

    assert process_source_asset_task.name == task_name
    assert celery_app.tasks[task_name].name == task_name
