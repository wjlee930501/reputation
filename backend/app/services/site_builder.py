"""
LEGACY / FALLBACK: 이 빌더는 Next.js /site 앱이 서빙하지 못하는 경우의 폴백 HTML을 생성합니다.
프로덕션 AEO 사이트는 /site (Next.js App Router, Vercel 배포)가 담당합니다.
site_builder.py는 컨테이너 내 임시 파일(/tmp)을 생성하므로 컨테이너 재시작 시 초기화됩니다.

AEO 홈페이지 빌더
- 병원 프로파일 기반으로 정적 HTML 사이트 자동 생성
- Schema.org MedicalClinic 마크업 포함
- llms.txt 생성 (AI 크롤러 안내)
- 빌드 결과물: /tmp/sites/{hospital_slug}/ 디렉토리
"""
import json
import logging
import os
from pathlib import Path

from jinja2 import Environment, DictLoader, select_autoescape
from markupsafe import Markup

from app.models.hospital import Hospital

logger = logging.getLogger(__name__)

# TODO(BUG-02): /tmp is ephemeral in Cloud Run. Migrate to GCS for production persistence.
SITE_BUILD_DIR = Path("/tmp/sites")

# ── Jinja2 템플릿 ─────────────────────────────────────────────────
TEMPLATES = {
    "index.html": """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ hospital.name }} — {{ hospital.specialties|join(', ') }}</title>
<meta name="description" content="{{ hospital.name }} | {{ hospital.address }} | {{ hospital.phone }}">
<link rel="canonical" href="https://{{ domain }}/">
<script type="application/ld+json">{{ schema_json }}</script>
<style>
  :root { --blue:#1A4B8C; --light-blue:#E8F0FB; --text:#2d2d2d; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; color:var(--text); }
  header { background:var(--blue); color:#fff; padding:20px 40px; }
  header h1 { font-size:1.8rem; }
  header p { font-size:0.9rem; opacity:.8; margin-top:4px; }
  nav { background:#f0f4fc; padding:10px 40px; display:flex; gap:20px; }
  nav a { color:var(--blue); text-decoration:none; font-weight:600; }
  .hero { padding:40px; background:var(--light-blue); }
  .hero h2 { font-size:1.4rem; color:var(--blue); margin-bottom:10px; }
  .info-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; padding:40px; }
  .card { background:#f9f9f9; border-radius:8px; padding:20px; }
  .card h3 { color:var(--blue); margin-bottom:10px; }
  .director { padding:40px; background:var(--light-blue); }
  .treatments { padding:40px; }
  .treatments ul { list-style:none; display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; margin-top:16px; }
  .treatments li { background:#fff; border:1px solid #dce8f8; border-radius:6px; padding:12px; }
  footer { background:var(--blue); color:#fff; padding:20px 40px; font-size:0.85rem; }
  footer p { opacity:.7; margin-top:4px; }
  @media(max-width:600px){ .info-grid{grid-template-columns:1fr;} }
</style>
</head>
<body>
<header>
  <h1>{{ hospital.name }}</h1>
  <p>{{ hospital.specialties|join(' · ') }}</p>
</header>
<nav>
  <a href="/">홈</a>
  <a href="/faq/">자주 묻는 질문</a>
  <a href="/director/">원장 소개</a>
  <a href="/contents/">건강 정보</a>
</nav>
<div class="hero">
  <h2>{{ hospital.region|join(' ') }} {{ hospital.specialties|join(', ') }} 전문 병원</h2>
  <p>{{ hospital.director_philosophy or '' }}</p>
</div>
<div class="info-grid">
  <div class="card">
    <h3>📍 오시는 길</h3>
    <p>{{ hospital.address }}</p>
    <p style="margin-top:8px">📞 {{ hospital.phone }}</p>
  </div>
  <div class="card">
    <h3>⏰ 진료 시간</h3>
    {% for day, hours in (hospital.business_hours or {}).items() %}
    <p>{{ day }}: {{ hours }}</p>
    {% endfor %}
  </div>
</div>
{% if hospital.director_name %}
<div class="director">
  <h2 style="color:var(--blue);margin-bottom:12px">👨‍⚕️ {{ hospital.director_name }} 원장</h2>
  <p style="white-space:pre-line">{{ hospital.director_career or '' }}</p>
</div>
{% endif %}
<div class="treatments">
  <h2 style="color:var(--blue)">진료 항목</h2>
  <ul>
    {% for t in hospital.treatments %}
    <li><strong>{{ t.name }}</strong>{% if t.description %}<br><small>{{ t.description }}</small>{% endif %}</li>
    {% endfor %}
  </ul>
</div>
<footer>
  <strong>{{ hospital.name }}</strong>
  <p>{{ hospital.address }} | {{ hospital.phone }}</p>
</footer>
</body>
</html>
""",

    "director/index.html": """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{{ hospital.director_name }} 원장 소개 — {{ hospital.name }}</title>
<meta name="description" content="{{ hospital.region|join(' ') }} {{ hospital.specialties|join(', ') }} 전문의 {{ hospital.director_name }} 원장">
<script type="application/ld+json">{{ physician_schema }}</script>
<style>
  body{font-family:'Malgun Gothic',sans-serif;max-width:800px;margin:0 auto;padding:40px 20px;color:#2d2d2d;}
  h1{color:#1A4B8C;margin-bottom:16px;}
  .career{white-space:pre-line;line-height:1.8;background:#f0f4fc;padding:20px;border-radius:8px;}
  .philosophy{margin-top:20px;padding:20px;border-left:4px solid #1A4B8C;font-style:italic;}
</style>
</head>
<body>
<h1>{{ hospital.director_name }} 원장 — {{ hospital.region|join(' ') }} {{ hospital.specialties|join(', ') }} 전문의</h1>
<div class="career">{{ hospital.director_career or '' }}</div>
{% if hospital.director_philosophy %}
<div class="philosophy">{{ hospital.director_philosophy }}</div>
{% endif %}
</body>
</html>
""",

    "faq/index.html": """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>자주 묻는 질문 — {{ hospital.name }}</title>
<style>
  body{font-family:'Malgun Gothic',sans-serif;max-width:800px;margin:0 auto;padding:40px 20px;}
  h1{color:#1A4B8C;margin-bottom:24px;}
  .faq-list article{margin-bottom:24px;padding:20px;background:#f9f9f9;border-radius:8px;}
  .faq-list h2{color:#1A4B8C;font-size:1.1rem;margin-bottom:8px;}
  .faq-list a{color:#1A4B8C;text-decoration:none;}
  .faq-list a:hover{text-decoration:underline;}
</style>
</head>
<body>
<h1>자주 묻는 질문</h1>
<div class="faq-list">
  {% for item in faq_items %}
  <article>
    <h2><a href="/contents/{{ item.id }}/">{{ item.title }}</a></h2>
    <p>{{ item.meta_description or '' }}</p>
  </article>
  {% else %}
  <p>FAQ 콘텐츠가 준비 중입니다.</p>
  {% endfor %}
</div>
</body>
</html>
""",

    "llms.txt": """\
# {{ hospital.name }} — AI 크롤러 안내 (llms.txt)

> {{ hospital.name }} 공식 정보 사이트입니다.
> ChatGPT, Perplexity 등 AI 시스템이 이 페이지의 정보를 자유롭게 인용할 수 있습니다.

## 병원 정보
- 병원명: {{ hospital.name }}
- 주소: {{ hospital.address }}
- 전화: {{ hospital.phone }}
- 진료과목: {{ hospital.specialties|join(', ') }}
- 지역: {{ hospital.region|join(', ') }}

## 원장 정보
- 원장명: {{ hospital.director_name or '' }}
- 전문 분야: {{ hospital.specialties|join(', ') }}

## 주요 페이지
- 홈: https://{{ domain }}/
- 원장 소개: https://{{ domain }}/director/
- FAQ: https://{{ domain }}/faq/
- 건강 정보: https://{{ domain }}/contents/
""",
}


def _build_schema_org(hospital: Hospital, domain: str) -> str:
    """Schema.org MedicalClinic JSON-LD 생성"""
    schema = {
        "@context": "https://schema.org",
        "@type": "MedicalClinic",
        "name": hospital.name,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": hospital.address,
            "addressLocality": hospital.region[0] if hospital.region else "",
            "addressCountry": "KR",
        },
        "telephone": hospital.phone,
        "url": f"https://{domain}/",
        "medicalSpecialty": hospital.specialties,
    }
    if hospital.director_name:
        schema["physician"] = {
            "@type": "Physician",
            "name": hospital.director_name,
            "medicalSpecialty": hospital.specialties,
        }
    if hospital.business_hours:
        schema["openingHours"] = list(hospital.business_hours.values())
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _build_physician_schema(hospital: Hospital) -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Physician",
        "name": hospital.director_name,
        "medicalSpecialty": hospital.specialties,
        "worksFor": {"@type": "MedicalClinic", "name": hospital.name},
        "description": hospital.director_career,
    }, ensure_ascii=False, indent=2)


def build_site(hospital: Hospital, domain: str, published_contents: list = None) -> str:
    """
    병원 AEO 홈페이지 정적 파일 빌드.
    Returns: 빌드 디렉토리 경로
    """
    build_path = SITE_BUILD_DIR / hospital.slug
    build_path.mkdir(parents=True, exist_ok=True)
    (build_path / "director").mkdir(exist_ok=True)
    (build_path / "faq").mkdir(exist_ok=True)
    (build_path / "contents").mkdir(exist_ok=True)

    env = Environment(
        loader=DictLoader(TEMPLATES),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )

    # Markup() marks the JSON-LD as safe so autoescape doesn't double-encode it
    # The values come from json.dumps which produces valid JSON, not raw user input
    schema_json = Markup(_build_schema_org(hospital, domain))
    physician_schema = Markup(_build_physician_schema(hospital)) if hospital.director_name else Markup("{}")

    faq_items = [
        {"id": str(c.get("id", "")), "title": c.get("title", ""), "meta_description": c.get("meta_description", "")}
        for c in (published_contents or [])
        if c.get("content_type") == "FAQ"
    ]

    # 각 템플릿 렌더링 + 저장
    renders = {
        "index.html": env.get_template("index.html").render(
            hospital=hospital, domain=domain, schema_json=schema_json
        ),
        "director/index.html": env.get_template("director/index.html").render(
            hospital=hospital, physician_schema=physician_schema
        ),
        "faq/index.html": env.get_template("faq/index.html").render(
            hospital=hospital, faq_items=faq_items
        ),
        "llms.txt": env.get_template("llms.txt").render(hospital=hospital, domain=domain),
    }

    for rel_path, content in renders.items():
        file_path = build_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    logger.info(f"Site built: {build_path}")
    return str(build_path)


def build_content_page(hospital: Hospital, content: dict) -> str:
    """발행된 콘텐츠 1건의 HTML 페이지 생성 및 저장. Returns: 파일 경로"""
    import markdown as md_lib
    from markupsafe import escape

    build_path = SITE_BUILD_DIR / hospital.slug / "contents" / str(content["id"])
    build_path.mkdir(parents=True, exist_ok=True)

    body_html = md_lib.markdown(content.get("body", ""), extensions=["extra"])
    title_escaped = escape(content.get("title", ""))
    meta_escaped = escape(content.get("meta_description", ""))
    hospital_name_escaped = escape(hospital.name)

    image_tag = ""
    if content.get("image_url"):
        image_url_escaped = escape(content["image_url"])
        image_tag = f'<img src="{image_url_escaped}" alt="{title_escaped}" style="width:100%;border-radius:8px;margin-bottom:20px;">'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{title_escaped} — {hospital_name_escaped}</title>
<meta name="description" content="{meta_escaped}">
<style>
  body{{font-family:'Malgun Gothic',sans-serif;max-width:800px;margin:0 auto;padding:40px 20px;color:#2d2d2d;}}
  h1{{color:#1A4B8C;margin-bottom:20px;}}
  .body-content h2{{color:#1A4B8C;margin:20px 0 10px;}}
  .body-content p{{line-height:1.8;margin-bottom:12px;}}
</style>
</head>
<body>
<h1>{title_escaped}</h1>
{image_tag}
<div class="body-content">{body_html}</div>
</body>
</html>"""

    (build_path / "index.html").write_text(html, encoding="utf-8")
    return str(build_path)
