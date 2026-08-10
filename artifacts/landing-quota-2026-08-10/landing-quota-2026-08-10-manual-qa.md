# Landing quota visual QA — Pass B

Reviewed against `DESIGN.md` (responsive, clinical/calm visual language, mobile readability and 44px mobile CTA intent). Five fresh captures were opened at original resolution with `view_image`; each is newer than the inspected source files.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-DSK-01 | responsive desktop / hero + quota | `/` landing, 1280×900, top viewport | `view_image(path=.../live-desktop.png, detail=original)`; dimensions/signature checked with `file` | PASS | A1 |
| L-TAB-01 | responsive tablet / no horizontal overflow | `/` landing, 768×900, top viewport | `view_image(path=.../live-tablet.png, detail=original)`; dimensions/signature checked with `file` | PASS | A2 |
| L-MOB-01 | mobile hero readability + quota | `/` landing, 375×812, top viewport | `view_image(path=.../live-mobile.png, detail=original)`; dimensions/signature checked with `file` | PASS | A3 |
| L-FORM-01 | mobile diagnosis form readability + touch targets | `/ai-diagnosis`, 375×812, top viewport | `view_image(path=.../live-form-mobile.png, detail=original)`; source touch-target audit at `globals.css:8446-8455` | REVISE | A4, S1, S2 |
| L-CTA-01 | final CTA/mobile copy + quota | `/` landing, 375×812, scrolled to final CTA | `view_image(path=.../live-cta-mobile.png, detail=original)`; dimensions/signature checked with `file` | PASS | A5 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-DSK-01-A | 1280px desktop | bounds / clipping | Header, hero, quota and metric columns remain inside the 1280px frame; below-fold content may continue past viewport without being clipped by a container. | PASS | A1 |
| L-TAB-01-A | 768px tablet | responsive re-composition | Header and hero remain readable, quota row fits, and no horizontal crop is visible at either edge. | PASS | A2 |
| L-MOB-01-A | 375px mobile | CJK line breaking | Korean clauses remain readable; no orphaned particle, tofu glyph, or detached one-syllable line. | PASS | A3 |
| L-MOB-01-B | live quota contract | data/copy fidelity | The visible live state is `2 / 20개소` and `오늘 신청 · 18개소 남음`, with reset note present. | PASS | A3, A4, A5, S3 |
| L-FORM-01-A | diagnosis method copy | CJK + English source phrase wrapping | `ChatGPT(OpenAI API)와 Gemini API` should keep the API name intact across a line break. | REVISE | A4, S1 |
| L-FORM-01-B | mobile minimum touch target | hit-area sizing | Text inputs and inline correction controls should provide at least a 44px mobile hit area. | REVISE | S2 |
| L-CTA-01-A | final CTA | CJK line breaking / contrast | Heading and notes are complete Korean phrases, body is legible on deep blue, CTA is fully visible and at least 44px high. | PASS | A5, S4 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | screenshot | Fresh desktop landing capture, 1280×900, RGB PNG, 183042 bytes, SHA-256 `8cf8d781249c97d864a08b1b30295ff80c494e9799202eb1f02effc7edfcdcf0` | `artifacts/landing-quota-2026-08-10/live-desktop.png` |
| A2 | screenshot | Fresh tablet landing capture, 768×900, RGB PNG, 116895 bytes, SHA-256 `17e53662bcfe0df6d5e0cf59429b651b691c274b1390869ead8d550943e2bc5f` | `artifacts/landing-quota-2026-08-10/live-tablet.png` |
| A3 | screenshot | Fresh mobile landing capture, 375×812, RGB PNG, 91468 bytes, SHA-256 `1881a2e6a8f15956bb14102fcf5c011d56ac3f171fe77f39cf350db82f405901` | `artifacts/landing-quota-2026-08-10/live-mobile.png` |
| A4 | screenshot | Fresh mobile diagnosis-form capture, 375×812, RGB PNG, 67171 bytes, SHA-256 `f2a98d809cec9d5ce316a0514ac0f14ae68a6db1b78115d3724fff71a46755e8` | `artifacts/landing-quota-2026-08-10/live-form-mobile.png` |
| A5 | screenshot | Fresh mobile final-CTA capture, 375×812, RGB PNG, 67153 bytes, SHA-256 `96915a605296cbbd84e63ad3b50ab84d27975c3f81054d932102b8f0395fcd2a` | `artifacts/landing-quota-2026-08-10/live-cta-mobile.png` |
| S1 | source | Diagnosis method copy containing the split-prone API phrase | `site/app/ai-diagnosis/page.tsx:28-39` |
| S2 | source | Form input and hint-button sizing; inputs have 11px vertical padding and hint button has zero padding | `site/app/globals.css:8444-8455` |
| S3 | source | Shared live quota renderer and reset/live-state copy | `site/app/_components/DiagnosisQuota.tsx:34-49` |
| S4 | source | Final CTA heading/body/notes used by the mobile capture | `site/lib/landing-copy.ts:821-835` |

## verdict

**REVISE.** The desktop, tablet, landing-mobile, quota copy, and final CTA captures pass visual inspection. The diagnosis-form mobile surface needs a no-break treatment for the `Gemini API` phrase and 44px hit-area treatment for text inputs/inline correction controls before a full Pass B approval.
