import pytest

from app.core.celery_app import celery_app


@pytest.mark.parametrize(
    "task,queue",
    [
        ("app.worker.tasks.process_line_event", "line-events"),
        ("app.worker.tasks.process_capture", "celery"),
        ("app.worker.tasks.process_voice_note", "celery"),
    ],
)
def test_real_celery_router_separates_line_events_from_capture_work(task, queue):
    route = celery_app.amqp.router.route({}, task, args=("test-record-id",), kwargs={})
    assert route["queue"].name == queue
    assert route["queue"].routing_key == queue
    if task.endswith("process_line_event"):
        assert route["routing_key"] == "line-events"
    # 未使用 Compose 的單一 worker 也會宣告並消費兩佇列。
    assert set(celery_app.amqp.queues.consume_from) == {"celery", "line-events"}
