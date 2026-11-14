import json

from scripts.warm_site_images import (
    ACCEPT,
    image_urls,
    parse_srcset,
    pick_candidate,
    slot_width,
    warm,
)

SIZES = "(max-width: 720px) calc(100vw - 40px), (max-width: 1296px) 52vw, 640px"


def test_slot_width_follows_the_first_matching_media_condition():
    assert slot_width(SIZES, 390) == 350
    assert slot_width(SIZES, 1000) == 520
    assert slot_width(SIZES, 1440) == 640
    assert slot_width("", 390) == 390


def test_pick_candidate_takes_the_smallest_width_that_covers_the_slot():
    candidates = parse_srcset("/_next/image?a&w=1080 1080w, /_next/image?a&w=640 640w, /_next/image?a&w=2048 2048w")
    assert [w for w, _ in candidates] == [640, 1080, 2048]
    assert pick_candidate(candidates, 1050) == "/_next/image?a&w=1080"
    assert pick_candidate(candidates, 5000) == "/_next/image?a&w=2048"
    assert pick_candidate([], 100) is None


def test_image_urls_reads_escaped_srcset_and_skips_unoptimized_images():
    page = (
        '<img alt="logo" src="https://x/logo"/>'
        '<img sizes="' + SIZES + '" srcSet="/_next/image?url=a&amp;w=640&amp;q=75 640w, '
        '/_next/image?url=a&amp;w=1080&amp;q=75 1080w, /_next/image?url=a&amp;w=1600&amp;q=75 1600w"/>'
    )
    # 데스크톱 640px×2 = 1280 → 1600, 모바일 350px×3 = 1050 → 1080
    assert image_urls(page) == ["/_next/image?url=a&w=1600&q=75", "/_next/image?url=a&w=1080&q=75"]


def test_warm_requests_each_candidate_once_with_avif_accept_and_never_fails_the_deploy():
    hospitals = json.dumps([{"slug": "clinic", "aeo_domain": "clinic.example"}]).encode()
    page = ('<img sizes="640px" srcSet="/_next/image?u=1&amp;w=640 640w, /_next/image?u=1&amp;w=1600 1600w"/>').encode()
    calls: list[tuple[str, dict[str, str]]] = []

    def fetch(url, headers):
        calls.append((url, headers))
        if url.endswith("/hospitals"):
            return 200, hospitals, {}
        if "/_next/image" in url:
            return (200, b"", {"x-nextjs-cache": "HIT"}) if "w=640" in url else (500, b"", {})
        return (200, page, {}) if url == "https://clinic.example" else (404, b"", {})

    result = warm("reputation.example", fetch, budget_seconds=60)

    image_calls = [c for c in calls if "/_next/image" in c[0]]
    assert [u for u, _ in image_calls] == ["https://clinic.example/_next/image?u=1&w=1600"]
    assert image_calls[0][1] == {"Accept": ACCEPT}
    assert result.pages == 1 and result.images == 1 and result.failed and not result.hits


def test_warm_stops_at_the_time_budget():
    hospitals = json.dumps([{"slug": "clinic", "aeo_domain": "clinic.example"}]).encode()
    page = ('<img sizes="640px" srcSet="/_next/image?u=1&amp;w=1600 1600w"/>'
            '<img sizes="640px" srcSet="/_next/image?u=2&amp;w=1600 1600w"/>').encode()
    ticks = iter([0, 0, 100, 100])

    def fetch(url, headers):
        if url.endswith("/hospitals"):
            return 200, hospitals, {}
        if "/_next/image" in url:
            return 200, b"", {}
        return 200, page, {}

    result = warm("reputation.example", fetch, budget_seconds=10, clock=lambda: next(ticks))
    assert result.stopped_by_budget and result.filled == 1
