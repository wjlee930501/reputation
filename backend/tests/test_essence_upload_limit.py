"""업로드 크기 상한은 '읽은 뒤'가 아니라 '읽는 중'에 걸려야 한다.

`await file.read()`로 전부 읽고 나서 len()을 검사하면, 상한 검사가 실행되는 시점에는 이미
파일 전체가 메모리에 올라가 있다 — 상한보다 훨씬 큰 업로드 몇 건으로 워커를 OOM으로 죽일 수
있다. 아래 테스트는 청크 단위로 읽으며 초과 즉시 중단하는지를 고정한다.
"""
import pytest

from app.api.admin import essence


class _FakeUpload:
    """size 인자를 받는 청크 read만 허용하는 UploadFile 스텁."""

    def __init__(self, total_bytes: int):
        self.remaining = total_bytes
        self.served = 0
        self.read_calls: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise AssertionError("전체를 한 번에 적재하는 read() 호출은 금지된다")
        self.read_calls.append(size)
        chunk_size = min(size, self.remaining)
        self.remaining -= chunk_size
        self.served += chunk_size
        return b"\0" * chunk_size


@pytest.fixture
def small_limits(monkeypatch):
    """실제 12MB를 할당하지 않고 동일한 경계 동작을 검증하기 위해 상한을 축소한다."""
    monkeypatch.setattr(essence, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(essence, "UPLOAD_CHUNK_BYTES", 4)


async def test_oversized_upload_is_rejected_before_full_read(small_limits):
    upload = _FakeUpload(total_bytes=10_000)

    with pytest.raises(essence.HTTPException) as exc:
        await essence._read_upload_within_limit(upload)

    assert exc.value.status_code == 413
    # 핵심: 상한을 넘긴 순간 멈춘다 — 10_000바이트 전체를 메모리에 올리지 않는다.
    assert upload.served <= essence.MAX_UPLOAD_BYTES + essence.UPLOAD_CHUNK_BYTES
    assert upload.remaining > 0
    assert all(size == essence.UPLOAD_CHUNK_BYTES for size in upload.read_calls)


async def test_upload_at_limit_is_accepted(small_limits):
    upload = _FakeUpload(total_bytes=essence.MAX_UPLOAD_BYTES)

    data = await essence._read_upload_within_limit(upload)

    assert len(data) == essence.MAX_UPLOAD_BYTES


async def test_upload_below_limit_returns_all_bytes(small_limits):
    upload = _FakeUpload(total_bytes=7)

    data = await essence._read_upload_within_limit(upload)

    assert data == b"\0" * 7


async def test_empty_upload_returns_empty_bytes(small_limits):
    data = await essence._read_upload_within_limit(_FakeUpload(total_bytes=0))

    assert data == b""
