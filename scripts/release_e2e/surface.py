"""Assert real public API, rendered pages, crawler surfaces and exact image bytes."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path("/evidence")
assert os.environ["APP_ENV"] == "test"
assert os.environ["DATABASE_URL"].endswith("@qa-db:5432/reputation_release_e2e")
fixture = json.loads((ROOT / "ui-fixtures.json").read_text())
hospital = fixture["hospitals"][0]
api = "http://qa-api:8000/api/v1/public/hospitals/" + hospital["slug"]
site = "https://e2e-clinic-0.site.example.test"
results = []
with httpx.Client(timeout=20, follow_redirects=False) as client:
    health = client.get(site + "/.well-known/reputation-health")
    assert health.status_code == 200
    assert health.json()["hospital_id"] == hospital["id"]
    assert health.json()["canonical_host"] == "e2e-clinic-0.site.example.test"
    results.append({"name": "exact tenant health identity", "status": "PASS"})
    response = client.get(api + "/contents")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 12 and len({r["id"] for r in rows}) == 12
    for row in rows:
        item = client.get(api + "/contents/" + row["id"])
        assert item.status_code == 200 and item.json()["title"] == row["title"]
        image = client.get("http://qa-api:8000" + row["image_url"])
        expected = parse_qs(urlparse(row["image_url"]).query)["v"][0]
        assert (
            image.status_code == 302
        )  # real API intentionally issues a fresh signed URL
        location = image.headers["location"]
        assert urlparse(location).hostname == "qa-capture"
        image = client.get(location)
        assert image.status_code == 200
        assert hashlib.sha256(image.content).hexdigest() == expected
        html = client.get(site + "/contents/" + row["id"])
        assert html.status_code == 200 and row["title"] in html.text
        assert (
            'rel="canonical"' in html.text
            and f"{site}/contents/{row['id']}" in html.text
        )
    results.append(
        {
            "name": "12 public articles and certified image bytes",
            "status": "PASS",
            "count": 12,
        }
    )
    for route in ["/", "/contents", "/sitemap.xml", "/robots.txt", "/llms.txt"]:
        result = client.get(site + route)
        assert result.status_code == 200, (route, result.status_code)
        if route == "/sitemap.xml":
            assert all(row["id"] in result.text for row in rows)
        (ROOT / ("surface-" + route.replace("/", "_") + ".txt")).write_text(result.text)
        results.append({"name": route, "status": "PASS"})
    other = fixture["hospitals"][1]["slug"]
    leaked = client.get(
        "http://qa-api:8000/api/v1/public/hospitals/"
        + other
        + "/contents/"
        + rows[0]["id"]
    )
    assert leaked.status_code == 404
    hidden = client.get(
        "http://qa-api:8000/api/v1/public/hospitals/" + other + "/contents"
    )
    assert hidden.status_code == 200 and hidden.json() == []
    results.append(
        {"name": "negative and cross-tenant public isolation", "status": "PASS"}
    )
(ROOT / "surface-results.json").write_text(
    json.dumps({"results": results, "actual_image_hashes_verified": 12}, indent=2)
)
print(json.dumps({"passed": len(results), "actual_image_hashes_verified": 12}))
