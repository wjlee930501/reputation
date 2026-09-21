"""Report branch previews must stay fictional, read-only and font-file free."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_preview_workflow_only_runs_on_report_feature_pushes():
    path = ROOT / '.github/workflows/report-preview.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'push'}
    assert workflow['on']['push']['branches'] == ['feat/report-*']
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['preview']
    assert job['env']['REPUTATION_DISABLE_DOTENV'] == '1'
    assert job['env']['APP_ENV'] == 'test'
    assert job['env']['OPENROUTER_API_KEY'] == 'test-openrouter-key'
    steps = job['steps']
    assert any('scripts/report_design_samples.py' in step.get('run', '') for step in steps)
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    patterns = upload['with']['path'].splitlines()
    assert all(pattern.endswith(('.pdf', '.png', 'manifest.json')) for pattern in patterns)
    assert upload['with']['include-hidden-files'] == 'false'
    assert 'secrets.' not in path.read_text()
    assert 'deploy.sh' not in path.read_text()
