# Landing visual QA — final independent pass B

Read-only review against `AGENTS.md` and `DESIGN.md`. The five `*-final.png` captures were opened at original resolution; file signatures and dimensions were checked before visual review.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-FB-DSK-01 | desktop responsive surface | `/` landing hero + quota, 1280×900 | `file artifacts/landing-quota-2026-08-10/live-desktop-final.png`; `view_image(path=.../live-desktop-final.png, detail=original)` | PASS | A1 |
| L-FB-TAB-01 | tablet responsive surface | `/` landing hero + quota + evidence band, 768×900 | `file artifacts/landing-quota-2026-08-10/live-tablet-final.png`; `view_image(path=.../live-tablet-final.png, detail=original)` | PASS | A2 |
| L-FB-MOB-01 | mobile hero/quota and CJK wrapping | `/` landing top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-mobile-final.png`; `view_image(path=.../live-mobile-final.png, detail=original)` | PASS | A3 |
| L-FB-FORM-01 | diagnosis instructions and mobile form surface | `/ai-diagnosis` top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-form-mobile-final.png`; `view_image(path=.../live-form-mobile-final.png, detail=original)`; source audit `site/app/ai-diagnosis/page.tsx:35-40`, `site/app/globals.css:8447-8459` | PASS | A4, S1, S2 |
| L-FB-CTA-01 | final CTA mobile surface | `/` landing final CTA, 375×812 | `file artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png`; `view_image(path=.../live-cta-mobile-final.png, detail=original)` | PASS | A5 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-FB-BOUNDS-01 | responsive bounds | clipping / horizontal overflow | Every requested frame keeps header, hero, quota, evidence columns, and CTA inside the image bounds; below-fold content may continue vertically. | PASS | A1, A2, A3, A4, A5 |
| L-FB-CJK-01 | natural Korean wrapping | orphaned particles / broken semantic phrases | Korean headings and body copy wrap at phrase boundaries without detached particles, tofu, or one-syllable orphan lines. | PASS | A2, A3, A4, A5 |
| L-FB-GEMINI-01 | diagnosis method copy | source/API identifier wrapping | `Gemini API` remains an unbroken token in the rendered mobile instructions; source uses `.dg-no-break { white-space: nowrap; }`. | PASS | A4, S1, S2 |
| L-FB-QUOTA-01 | live quota truthfulness | unavailable / stale data state | Valid live payloads render used/total and remaining; null or failed fetch renders only `접수 현황 · 실시간 확인 중` and never a guessed number. | PASS | A1, A3, A4, S3, S4 |
| L-FB-TOUCH-01 | mobile interaction sizing | undersized major controls | Landing CTA/toggle and diagnosis text inputs/inline correction control expose at least 44px CSS hit heights. | PASS | A3, A4, A5, S2, S5 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | screenshot | Final desktop capture; RGB PNG, 1280×900; SHA-256 `2222d40086cc782b1b190d07b9482b96a102e9fce3185bfccba543eb6db2dff1` | `artifacts/landing-quota-2026-08-10/live-desktop-final.png` |
| A2 | screenshot | Final tablet capture; RGB PNG, 768×900; SHA-256 `125c732ad997c0b06c149eb618c01041f66469049361744527fa163e51d73dc1` | `artifacts/landing-quota-2026-08-10/live-tablet-final.png` |
| A3 | screenshot | Final landing mobile capture; RGB PNG, 375×812; SHA-256 `e6ea770409469ac85b048bb63fd6e6e8549bb648bdd5409d4709f7b7d39bcd5e` | `artifacts/landing-quota-2026-08-10/live-mobile-final.png` |
| A4 | screenshot | Final diagnosis-form mobile capture; RGB PNG, 375×812; SHA-256 `d2fb1eca60df7cc19ea6697629263b5090103cbb3ee56210582e734be17fd832` | `artifacts/landing-quota-2026-08-10/live-form-mobile-final.png` |
| A5 | screenshot | Final CTA mobile capture; RGB PNG, 375×812; SHA-256 `afbca2fc58cfa0cca887df36b29e0a228b71dd90054900f8369a2b9bd0670db6` | `artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png` |
| S1 | source | API identifiers are wrapped in `.dg-no-break` spans | `site/app/ai-diagnosis/page.tsx:35-40` |
| S2 | source | Diagnosis inputs and inline email-correction button have `min-height: 44px`; no-break token is defined | `site/app/globals.css:8447-8459` |
| S3 | source | Live quota renderer distinguishes valid slots, sold-out, and unavailable (`실시간 확인 중`) states | `site/app/_components/DiagnosisQuota.tsx:34-49` |
| S4 | source | Slot proxy is `force-dynamic`, `no-store`, and returns 502 without fabricated counts on upstream failure | `site/app/api/diagnosis/slots/route.ts:5-20`; `site/lib/diagnosis-slots.ts:12-32` |
| S5 | source | Mobile sticky CTA is 46px minimum; header motion toggle is 44px minimum | `site/app/globals.css:2324-2329`, `site/app/globals.css:2339-2377` |

## verdict

**PASS.** All five final captures match their requested dimensions and show no visible horizontal clipping or overflow. Korean text wraps naturally, `Gemini API` stays intact, and both the source-level unavailable quota state and visible live-state copy avoid invented availability. Major visible mobile controls meet the 44px sizing criterion.
