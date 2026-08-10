# Landing audit blocker — 2026-08-09

Target requested: `https://repuatation.motionlabs.kr/`

- In-app Browser navigation failed with `net::ERR_NAME_NOT_RESOLVED`.
- `curl -I -L --max-time 20 https://repuatation.motionlabs.kr/` failed with `curl: (6) Could not resolve host: repuatation.motionlabs.kr`.
- The corrected spelling `https://reputation.motionlabs.kr/` returned HTTP 200 during a diagnostic-only check, but it was not audited because the request explicitly specified the misspelled hostname.

No landing-page screenshot is accepted as evidence. Desktop/mobile visual capture is blocked until the requested hostname resolves or the audit scope is explicitly changed to the corrected host.
