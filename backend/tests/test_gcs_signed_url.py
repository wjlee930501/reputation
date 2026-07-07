"""R9 — signed URL 생성: ADC credentials 모듈 캐시 + 경로별 TTL 메모."""
import time

import pytest

from app.services import gcs_utils


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(gcs_utils, "_signed_url_cache", {})
    monkeypatch.setattr(gcs_utils, "_adc_credentials", None)
    monkeypatch.setattr(gcs_utils, "_gcs_client", None)
    # _sign_blob_url은 tenacity 재시도(최대 3회, 지수 백오프)가 걸려 있다 — 실패 테스트가
    # 실제로 수 초씩 대기하지 않도록 sleep을 no-op으로 대체한다.
    monkeypatch.setattr(gcs_utils._sign_blob_url.retry, "sleep", lambda _delay: None)


class _FakeBlob:
    """mode='local' — 즉시 서명. mode='keyless' — Cloud Run처럼 로컬 서명 불가."""

    def __init__(self, mode="local"):
        self.mode = mode
        self.calls = []

    def generate_signed_url(self, expiration=None, **kwargs):
        self.calls.append(kwargs)
        if self.mode == "keyless" and not kwargs:
            raise RuntimeError("need a private key to sign credentials")
        return f"https://signed.example/url-{len(self.calls)}"


class _FakeClient:
    def __init__(self, blob):
        self._blob = blob

    def bucket(self, _name):
        return self

    def blob(self, _name):
        return self._blob


class _FakeCredentials:
    def __init__(self, valid=True):
        self.valid = valid
        self.refresh_calls = 0
        self.service_account_email = "sa@example.iam.gserviceaccount.com"
        self.token = "fake-access-token"

    def refresh(self, _request):
        self.refresh_calls += 1
        self.valid = True


def _install_blob(monkeypatch, blob):
    monkeypatch.setattr(gcs_utils, "_get_gcs_client", lambda: _FakeClient(blob))


def test_non_gcs_paths_pass_through(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)

    assert gcs_utils.get_signed_url("") == ""
    assert gcs_utils.get_signed_url(None) == ""
    assert gcs_utils.get_signed_url("https://legacy.example/x.png") == "https://legacy.example/x.png"
    assert blob.calls == []


def test_signed_url_is_memoized_per_path(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)

    first = gcs_utils.get_signed_url("gs://bucket/a.png")
    second = gcs_utils.get_signed_url("gs://bucket/a.png")

    assert first == second == "https://signed.example/url-1"
    assert len(blob.calls) == 1  # 두 번째 호출은 캐시 히트 — 네트워크 왕복 없음

    # 다른 경로는 별도 서명
    other = gcs_utils.get_signed_url("gs://bucket/b.png")
    assert other == "https://signed.example/url-2"


def test_signed_url_cache_key_includes_expiration_hours(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)

    gcs_utils.get_signed_url("gs://bucket/a.png", expiration_hours=24)
    gcs_utils.get_signed_url("gs://bucket/a.png", expiration_hours=1)

    assert len(blob.calls) == 2


def test_expired_cache_entry_is_resigned(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)

    gcs_utils.get_signed_url("gs://bucket/a.png")
    # 캐시 만료를 강제로 과거로 돌린다
    key = ("gs://bucket/a.png", 24)
    expires_at, url = gcs_utils._signed_url_cache[key]
    gcs_utils._signed_url_cache[key] = (time.monotonic() - 1, url)

    refreshed = gcs_utils.get_signed_url("gs://bucket/a.png")
    assert refreshed == "https://signed.example/url-2"
    assert len(blob.calls) == 2


def test_short_expiration_caps_cache_ttl_below_half_lifetime(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)

    gcs_utils.get_signed_url("gs://bucket/a.png", expiration_hours=1)

    expires_at, _url = gcs_utils._signed_url_cache[("gs://bucket/a.png", 1)]
    remaining = expires_at - time.monotonic()
    # 1시간짜리 서명은 최대 30분만 캐시 — 만료 임박 URL을 캐시에서 서빙하지 않는다.
    assert remaining <= 1800 + 1


def test_keyless_fallback_uses_cached_adc_credentials(monkeypatch):
    blob = _FakeBlob(mode="keyless")
    _install_blob(monkeypatch, blob)
    creds = _FakeCredentials(valid=True)
    cred_lookups = []

    def fake_get_creds():
        cred_lookups.append(1)
        return creds

    monkeypatch.setattr(gcs_utils, "_get_iam_signing_credentials", fake_get_creds)

    url = gcs_utils.get_signed_url("gs://bucket/a.png")
    assert url == "https://signed.example/url-2"  # 1차 로컬 서명 실패 → 2차 IAM 서명
    assert blob.calls[1]["service_account_email"] == creds.service_account_email
    assert blob.calls[1]["access_token"] == creds.token

    # 캐시 히트 — credentials 조회/서명 모두 재발생하지 않는다
    assert gcs_utils.get_signed_url("gs://bucket/a.png") == url
    assert len(cred_lookups) == 1
    assert len(blob.calls) == 2


def test_get_iam_signing_credentials_skips_refresh_when_valid(monkeypatch):
    creds = _FakeCredentials(valid=True)
    monkeypatch.setattr(gcs_utils, "_adc_credentials", creds)

    assert gcs_utils._get_iam_signing_credentials() is creds
    assert creds.refresh_calls == 0


def test_get_iam_signing_credentials_refreshes_when_expired(monkeypatch):
    creds = _FakeCredentials(valid=False)
    monkeypatch.setattr(gcs_utils, "_adc_credentials", creds)

    assert gcs_utils._get_iam_signing_credentials() is creds
    assert creds.refresh_calls == 1
    assert creds.valid is True


def test_failure_suppresses_unsigned_gcs_path_and_is_not_cached(monkeypatch):
    class _BoomBlob:
        def generate_signed_url(self, expiration=None, **kwargs):
            raise RuntimeError("permanently broken")

    _install_blob(monkeypatch, _BoomBlob())
    monkeypatch.setattr(
        gcs_utils, "_get_iam_signing_credentials", lambda: (_ for _ in ()).throw(RuntimeError("no adc"))
    )

    assert gcs_utils.get_signed_url("gs://bucket/a.png") == ""
    assert gcs_utils._signed_url_cache == {}


def test_sign_blob_url_retries_transient_failure_then_succeeds(monkeypatch):
    # CLAUDE.md 규칙 4 — 외부 API(signBlob) 호출은 최대 3회 재시도해야 한다.
    class _FlakyBlob:
        def __init__(self):
            self.attempts = 0

        def generate_signed_url(self, expiration=None, **kwargs):
            self.attempts += 1
            if self.attempts < 3:
                raise TimeoutError("transient network error")
            return "https://signed.example/recovered"

    blob = _FlakyBlob()
    result = gcs_utils._sign_blob_url(blob, gcs_utils.timedelta(hours=1))

    assert result == "https://signed.example/recovered"
    assert blob.attempts == 3


def test_sign_blob_url_reraises_after_exhausting_retries(monkeypatch):
    class _AlwaysFailsBlob:
        def generate_signed_url(self, expiration=None, **kwargs):
            raise TimeoutError("network unreachable")

    monkeypatch.setattr(
        gcs_utils, "_get_iam_signing_credentials", lambda: (_ for _ in ()).throw(TimeoutError("no adc"))
    )

    with pytest.raises(Exception):
        gcs_utils._sign_blob_url(_AlwaysFailsBlob(), gcs_utils.timedelta(hours=1))


def test_cache_size_cap_evicts_oldest_entries(monkeypatch):
    blob = _FakeBlob()
    _install_blob(monkeypatch, blob)
    monkeypatch.setattr(gcs_utils, "SIGNED_URL_CACHE_MAX_ENTRIES", 4)

    for i in range(5):
        gcs_utils.get_signed_url(f"gs://bucket/{i}.png")

    assert len(gcs_utils._signed_url_cache) <= 4
    # 가장 오래된 엔트리부터 비워졌다
    assert ("gs://bucket/0.png", 24) not in gcs_utils._signed_url_cache
    assert ("gs://bucket/4.png", 24) in gcs_utils._signed_url_cache
