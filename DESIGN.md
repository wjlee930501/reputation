# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-09
- Primary product surfaces: public hospital second-homepage, medical content list, medical content detail, treatment/doctor/visit pages.
- Evidence reviewed: `site/app/[slug]/page.tsx`, `site/app/[slug]/_components/*`, `site/app/[slug]/contents/[contentId]/page.tsx`, `site/app/globals.css`, Playwright screenshots under `/private/tmp/motionlabs-*`, reference screenshots `/private/tmp/ref-goys.png`, `/private/tmp/ref-newstandard.png`, `/private/tmp/ref-newstandard-mobile.png`.

## Brand
- Personality: clinical, expert, calm, direct, locally trustworthy.
- Trust signals: physician portrait and credentials, diagnosis-first care principle, treatment areas, care flow, official phone/address/hours, published medical content count, references and update dates on articles.
- Avoid: SaaS dashboard feeling, generic card grids, internal SEO/AI phrasing, decorative gradients, vague “정보 허브” positioning as the main headline.

## Product goals
- Goals: make any onboarded hospital look like a credible second homepage and medical blog; help patients understand care scope before calling; support sales demos with realistic depth.
- Non-goals: replace the hospital’s primary homepage, guarantee medical outcomes, present generated images as real patient results.
- Success signals: first viewport communicates specialty, physician, treatments, and contact path; article pages show source/update/medical caveat; mobile has immediate phone and visit actions.

## Personas and jobs
- Primary personas: hospital directors evaluating the product; prospective patients checking symptoms, doctor, hours, and location; internal operators preparing demos.
- User jobs: assess credibility, find the right treatment area, call or navigate, read a medical article with enough depth to trust it.
- Key contexts of use: mobile search traffic, sales demo on desktop, patient sharing of article URLs.

## Information architecture
- Primary navigation: medical content, doctor, treatment areas, visit, official homepage.
- Core routes/screens: hospital home, content list, content detail, doctor, treatments, visit.
- Content hierarchy: center-style hero -> care principle -> treatment scope -> care flow -> physician -> featured articles -> gallery -> visit/contact.

## Design Principles
- Lead with care reality: specialty, doctor, diagnosis process, and contact path must be visible before decorative content.
- Make content accountable: article pages must show authoring basis, update date, references, and medical caveat near the title.
- Prefer editorial density over card sprawl: use sections, tables, flows, and profile panels before repeated generic cards.

## Visual Language
- Color: white paper on a faintly blue-grey neutral (`--clinic-paper` #fafbfd, hairlines `--clinic-hair` #e7ebf1). A single unified clinical deep blue (`--color-revisit-primary-40` scoped to `#0b53b8`, hover `#083f8c`) carries every accent — links, CTAs, category chips, active states, the today-cell in the weekly calendar, section eyebrows, step numbers, and brand illustration strokes. No secondary navy CTA, no Tailwind bright blue, no purple. Point colour is restrained.
- Typography (Pretendard only; weight contrast 400/600/800): editorial hierarchy is built from scale + weight, not new fonts. Hero display `.clinic-hero-title` 44px desktop / 30px mobile with the specialty declared in accent blue (`.clinic-hero-title-sub`; guards against "협진 협진" when a specialty already contains 협진). Section titles `.clinic-section-title` 30px / 24px. Article title 40px / 27px. Article body 17px / line-height 1.8, measure ~680px. Body/notes ≥16px, mobile included.
- Visual anchors (v4) — every section owns exactly one dominant anchor so the eye has a place to rest and no section reads as a grey text wall: hero abstract line-art (`HeroLineArt`) + thin-line care-scope chips; basics = a 7-day weekly calendar with the KST-today cell in `#0b53b8` (`.clinic-week`) + three key-fact cards; FAQ = a left guidance rail ("처음 오시나요?") + a blue-left-rule question index, top-3 expanded then compact question-only rows (`.clinic-answers-layout`); featured = large lead with a type cover (`ContentCover`) + compact rows; principles = a blue-grey band (`.clinic-principles-band`) with three thin-line mini-icons (검사/설명/사후관리); treatments = 4 large lead cards + a 2-column compact index (`.clinic-tx-cards` / `.clinic-tx-index`, data-independent); care flow = a 4-step connected timeline with per-step aux markers; doctor = a large profile with the real PHOTO_DOCTOR illustration over a monogram; visit = three action buttons + a location frame + three checklists.
- Rhythm — editorial density over card sprawl: each section changes layout so nothing is a repeated card grid, and white ↔ `#f6f9fc`/cool-paper bands alternate. Structure is expressed with hairline dividers + whitespace; boxes are used only where they earn hierarchy (calendar, cards, band).
- Section headers are lightweight: title (and an optional one-line note) only. Mechanical eyebrow+title+description triplets are removed — the title carries the message.
- Brand illustration system (`_components/brand.tsx`, hand-authored inline SVG, no icon package): a 2-tone deep-blue + blue-grey abstract language — concentric arcs, a pulse curve, and a dot grid for the hero (`HeroLineArt`); three thin-line principle icons; and seven abstract content-type cover motifs (FAQ speech bubbles / DISEASE concentric rings / TREATMENT steps / COLUMN pen nib / HEALTH waves / LOCAL pin-on-grid / NOTICE bell). All strokes use `currentColor` + `--brand-ink-2`, `aria-hidden`, clean `viewBox`, no emoji, no gradients, no organ/body depiction.
- Shape/radius/elevation: 8–22px radii, no shadows anywhere on the clinic surface (regression-guarded), no gradients, no glassmorphism.
- Motion: hover/focus only, 150ms — link/arrow nudge + accent-colour shift.
- Imagery: images are progressive enhancement over an always-present SSR underlay, never required and never a bare grey box. Doctor/avatar slots (`ClinicAvatar`) render a monogram tile as a permanent underlay; the real photo (director_photo_url, else `photos[]` PHOTO_DOCTOR) overlays it client-side and is removed on 404/blank. Content covers (`ContentCover`) always show the type motif (+ a faint watermark echo so a motif-only banner never looks empty) and overlay the real `image_url` when it loads. Images render client-side after mount so an SSR image that 404s can never leave a broken-image glyph.

## Components
- Reused: `ClinicHeader`, `ClinicGallery`.
- Brand SVG (v4): `_components/brand.tsx` exports `HeroLineArt`, `IconExam` / `IconExplain` / `IconAftercare`, and `ContentMotif` (7 type motifs). `ContentCover` (client) composes a type motif + watermark echo + optional real image.
- `ClinicHero` (v4): hero abstract line-art anchor (`.clinic-hero-artwork`), specialty kicker + title + statement, thin-line care-scope chips (`.clinic-hero-scope`), single strong 전화 CTA + text link, byline with doctor avatar; right quick-facts/contact card adds a 진료과목 fact + today-status dot + call CTA.
- `HospitalFacts` (v4): weekly진료시간 calendar (`.clinic-week`, KST-today highlighted, 휴진 notice row) + three key-fact cards (전화 / 주소+길찾기 / 진료영역·지역), official channel chips + HIRA line.
- `AnswerClusters` (v4): `.clinic-answers-layout` = guidance rail (eyebrow, note, "처음 오시나요?" hint, all-content link) + main column with a blue-left-rule top-3 question index (`.clinic-qa-list`) and a compact question-only list (`.clinic-qa-compact`), then a treatment strip.
- `CarePrinciples` (v4): the 진료철학 section — blue-grey band, a fact-based lede (uses public_about only when clean; a front-end `sanitizePublicAbout` guard drops internal-pipeline language like "자료에서 확인된 핵심 메시지" so a not-yet-demoted polluted value never leaks), and three principle cards each with a thin-line icon.
- `TreatmentGrid` (v4): 4 lead cards (index + name + 2-line desc + more link) via `.clinic-tx-cards`, remaining as a 2-column compact index (`.clinic-tx-index`) — data-independent, works at 3 or 12 items.
- `CareFlow` (v4): 4-step connected timeline with per-step aux markers (`.clinic-flow-node-aux`); horizontal desktop, vertical mobile.
- `DoctorIntro` (v4): large profile — real PHOTO_DOCTOR illustration (from `photos[]`/director_photo_url) over a monogram, name/role figure, tags, career, credential chips, 3-cell fact row.
- `FeaturedContent` / `/contents` featured / article header: `ContentCover` banner (real image or type motif, never an empty box).
- `ContactCard` (v4): action-first 방문 안내 — three action buttons (전화하기 / 길찾기 / 진료시간 보기), a location frame, three visit checklists (주차 / 대중교통 / 초진 준비물), then official channel cards.
- `ClinicFooter` (v4): top CTA row ("진료 문의가 필요하신가요?" + phone button), then a 2-column info block (병원명·대표자·공식 홈페이지 / 연락처) and fine-print disclaimer + copyright.
- `ContentCard`, `/treatments`, article page reused with the shared v3/v4 chrome (`.clinic-section-head`, H2 accent-rule anchors, footnote-card references).
- `ClinicAvatar`: monogram is a permanent SSR underlay; the real image is rendered client-side after mount and removed on blank/error (never a grey box or broken-image glyph).
- Category colour: 7 content types get low-saturation chips (`.clinic-tag--faq/disease/treatment/column/health/local/notice`), distinguished by lightness/depth within the unified clinical blue system (plus the existing green/yellow/red/grey status accents) — purple is never used, per the Visual Language "no purple" rule.
- Variants and states: missing doctor photo → monogram; unsupported/slow/absent image URLs render no image (never crash, never empty box); sparse content feeds collapse to a single-column lead.
- Token/component ownership: keep global CSS tokens; clinical blue + neutral tuning is scoped under `.clinic-shell` (no new design-system dependency).

## Accessibility
- Target standard: WCAG AA-oriented contrast and keyboard semantics.
- Keyboard/focus behavior: links and buttons remain native anchors.
- Contrast/readability: no low-contrast text on image without overlay.
- Screen-reader semantics: use sections, headings, lists, and nav labels.
- Reduced motion: no required motion.

## Responsive Behavior
- Supported breakpoints/devices: desktop, tablet, 390px mobile.
- Layout adaptations: hero two-column becomes single-column; treatment/flow cards collapse; mobile bottom action bar appears.
- Touch/hover differences: mobile CTAs must be 44px+ high and not depend on hover.

## Interaction States
- Loading: existing Next rendering.
- Empty: missing photos/content should collapse gracefully.
- Error: unsupported asset URLs return `null` and avoid `next/image` crashes.
- Success: contact links use native phone/external anchors.
- Disabled: not applicable.
- Offline/slow network: images should not be required for text comprehension.

## Content Voice
- Tone: patient-facing, physician-accountable, non-promotional, no internal product language.
- Terminology: use “진료 원칙”, “진료 흐름”, “진료 영역”, “대표원장”, “정형외과 전문의”.
- Microcopy rules: avoid “AI”, “검색 시스템”, “브랜드 구조” in patient-facing pages; avoid claims of best, guaranteed, painless, or unique outcomes.

## Implementation Constraints
- Framework/styling system: Next.js app router, React 18, global CSS, `next/image` (gallery) + guarded `<img>` (avatar/cover).
- Design-token constraints: reuse `--color-revisit-*` tokens and existing component classes; clinical blue + neutral tuning stays scoped under `.clinic-shell`.
- Performance constraints: no new heavy visual dependencies; brand illustrations are hand-authored inline SVG (no icon package).
- CSP (`next.config.mjs`): production stays strict. Dev-only additions (`process.env.NODE_ENV !== 'production'`) allow `http://localhost:8000` in `img-src` (preview backend assets) and `'unsafe-eval'` in `script-src` (Next dev HMR uses eval; without it client hydration fails and the doctor photo / covers never load locally). Neither relaxation ships to production.
- Compatibility constraints: site test runner uses Node native tests with TypeScript stripping.
- Test/screenshot expectations: run site tests, lint, build, typecheck, backend content tests, and Playwright screenshots for desktop/mobile.

## Open Questions
- [ ] Should customer-specific brand colors override the default clinical blue palette? Owner: product. Impact: brand differentiation.
- [ ] Should mobile bottom actions include Kakao when present? Owner: product. Impact: conversion path.
