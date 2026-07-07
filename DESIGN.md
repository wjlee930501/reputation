# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-08
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
- Color: white paper on a faintly blue-grey neutral (`--clinic-paper` #fafbfd, hairlines `--clinic-hair` #e7ebf1). A single unified clinical deep blue (`--color-revisit-primary-40` scoped to `#0b53b8`, hover `#083f8c`) carries every accent — links, CTAs, category chips, active states. No secondary navy CTA, no Tailwind bright blue, no purple. Point colour is restrained.
- Typography (Pretendard only; weight contrast 400/600/800): editorial hierarchy is built from scale + weight, not new fonts. Hero display `.clinic-hero-title` 44px desktop / 30px mobile with the specialty declared in accent blue (`.clinic-hero-title-sub`). Section titles `.clinic-section-title` 30px / 24px. Article title 40px / 27px. Article body 17px / line-height 1.8, measure ~680px. Body/notes ≥16px, mobile included.
- Rhythm — editorial density over card sprawl: each section carries a distinct rhythm rather than a repeated card grid. FAQ = numbered typographic question index (`.clinic-qa-list`). Treatments = definition list with an emphasised lead row (`.clinic-tx-deflist`). Featured / content list = a large typographic lead + compact hairline rows (`.clinic-lead`, `.clinic-feed-*`). Structure is expressed with hairline dividers + whitespace, not bordered boxes.
- Section headers are lightweight: title (and an optional one-line note) only. Mechanical eyebrow+title+description triplets are removed — the title carries the message.
- Shape/radius/elevation: 8–18px radii, no shadows anywhere on the clinic surface (regression-guarded), no gradients, no glassmorphism.
- Motion: hover/focus only, 150ms — link/arrow nudge + accent-colour shift.
- Imagery: the surface is typography-first and must be beautiful with zero images. Hero has no image band. Content cards/feeds and the article top carry no cover image; a large empty grey box is never rendered. Doctor/avatar slots degrade to a monogram tile that is always the underlay — a real photo fades in over it only once it genuinely loads (`ClinicAvatar`).

## Components
- Header/footer/gallery reused as-is: `ClinicHeader`, `ClinicFooter`, `ClinicGallery`, `HospitalFacts`.
- Typography-first rebuilds (v3): `ClinicHero` (no image band; left = specialty declaration + statement + CTA + byline, right = refined quick-facts/contact card with today-status dot, phone, location, call CTA), `AnswerClusters` (numbered question index), `TreatmentGrid` + `/treatments` (emphasised-lead definition list), `FeaturedContent` (typographic lead + compact rows), `ContentCard` (chip + title + excerpt + date, no image), `/contents` (category filter chips via query param, featured + unified chronological feed), article page (no cover, inline meta row, refined core-answer callout, H2 accent-rule anchors, footnote-card references, thumbnail-free related list).
- Section chrome: light `.clinic-section-head` / `.clinic-section-title` / `.clinic-section-note` replaces the eyebrow+heading+lede triplet; `CareFlow` keeps numbered nodes with a connecting hairline; `DoctorIntro` is an editorial monogram/photo profile + credential chips + fact table.
- `ClinicAvatar`: monogram is a permanent underlay; the real image overlays and fades in only on confirmed load (blank/errored/pending → monogram, never a grey box).
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
- Framework/styling system: Next.js app router, React 18, global CSS, `next/image`.
- Design-token constraints: reuse `--color-revisit-*` tokens and existing component classes.
- Performance constraints: no new heavy visual dependencies.
- Compatibility constraints: site test runner uses Node native tests with TypeScript stripping.
- Test/screenshot expectations: run site tests, lint, build, typecheck, backend content tests, and Playwright screenshots for desktop/mobile.

## Open Questions
- [ ] Should customer-specific brand colors override the default clinical blue palette? Owner: product. Impact: brand differentiation.
- [ ] Should mobile bottom actions include Kakao when present? Owner: product. Impact: conversion path.
