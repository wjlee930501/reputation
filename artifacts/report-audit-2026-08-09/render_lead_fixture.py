import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app.models.lead_diagnosis import LeadDiagnosis, LeadDiagnosisResult
from app.services import lead_report
import uuid

d = LeadDiagnosis(
    id=uuid.uuid4(), lead_id=uuid.uuid4(), applicant_email_hash='x', subject_phone_hash='y',
    subject_hospital_name='장편한외과의원', subject_region='수서역',
    queries=[
      {'slot':1,'kind':'진료과형','text':'수서역 근처 외과 병원 추천해줘'},
      {'slot':2,'kind':'시술형','text':'수서역 근처 대장내시경 병원 추천해줘'},
      {'slot':3,'kind':'증상형','text':'치질이 있는데 수서역 근처 병원 어디로 가야해?'},
    ],
    requested_models={'openai':'gpt-5.6-luna','gemini':'gemini-3.6-flash','judge':'gpt-4o-mini-2024-07-18'}, repeat_count=3,
)
rows=[]; base=datetime(2026,7,25,9,0,tzinfo=timezone.utc)
for platform in ('chatgpt','gemini'):
    made_m=made_f=0
    for q in d.queries:
      for no in range(1,4):
        failed=made_f<1; mentioned=(not failed and made_m<2)
        if failed: made_f+=1
        elif mentioned: made_m+=1
        rows.append(LeadDiagnosisResult(
          diagnosis_id=d.id, platform=platform, query_slot=q['slot'], repeat_no=no, attempt_no=1,
          query_text=q['text'], requested_model='m', answer_model='m-actual',
          is_mentioned=None if failed else mentioned,
          measurement_status='FAILED' if failed else 'SUCCESS',
          failure_reason='provider_query_failed:TimeoutError' if failed else None,
          raw_response='' if failed else '장편한외과의원이 추천됩니다', source_urls=['https://competitor.example.com/secret-listing'],
          answer_source='LIVE', measured_at=base + timedelta(minutes=no),
        ))
p=lead_report.build_lead_report_payload(d,rows,generated_at=datetime(2026,7,30,10,0,tzinfo=timezone.utc))
out=Path(__file__).parent/'lead-fixture.pdf'; out.write_bytes(lead_report.render_lead_report_pdf(p)); print(out); print(lead_report.render_lead_report_html(p)[:1000])
