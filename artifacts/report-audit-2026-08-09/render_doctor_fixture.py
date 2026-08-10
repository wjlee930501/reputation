import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.services.report_engine import TEMPLATE_DIR
from tests.test_doctor_report_view import _view
view = _view()
env=Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),autoescape=select_autoescape(enabled_extensions=('html',)))
html=env.get_template('doctor_report.html').render(view=view, period_label='2026년 07월')
Path(__file__).with_name('doctor-fixture.html').write_text(html,encoding='utf-8')
from weasyprint import HTML
out=Path(__file__).with_name('doctor-fixture.pdf'); HTML(string=html).write_pdf(out); print(out)
