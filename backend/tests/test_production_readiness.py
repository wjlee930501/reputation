from app.utils import production_readiness


def test_workflow_registry_contains_onboarding_automation() -> None:
    assert all(production_readiness._workflow_facts().values())


def test_build_report_requires_schema_owner_and_runtime_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        production_readiness,
        "_database_facts",
        lambda: {
            "schema_current": True,
            "schema_revision": "0031_add_hospital_visual_theme",
            "expected_schema_revision": "0031_add_hospital_visual_theme",
            "active_owner_count": 1,
            "hospital_count": 1,
            "live_site_count": 1,
        },
    )
    monkeypatch.setattr(production_readiness, "_redis_ready", lambda: True)
    monkeypatch.setattr(
        production_readiness,
        "_workflow_facts",
        lambda: {
            "required_tasks_registered": True,
            "required_tasks_routed": True,
            "required_schedules_declared": True,
        },
    )
    monkeypatch.setattr(
        production_readiness,
        "_configuration_facts",
        lambda: {
            "generation_keys_configured": True,
            "operator_secrets_configured": True,
            "asset_bucket_configured": True,
            "report_bucket_configured": True,
            "certificate_auto_provisioning_enabled": True,
            "web_search_enabled": True,
        },
    )

    report = production_readiness.build_report()

    assert report["ready"] is True
    assert report["checks"]["active_owner_available"] is True


def test_build_report_fails_closed_without_active_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        production_readiness,
        "_database_facts",
        lambda: {
            "schema_current": True,
            "schema_revision": "0031_add_hospital_visual_theme",
            "expected_schema_revision": "0031_add_hospital_visual_theme",
            "active_owner_count": 0,
            "hospital_count": 0,
            "live_site_count": 0,
        },
    )
    monkeypatch.setattr(production_readiness, "_redis_ready", lambda: True)
    monkeypatch.setattr(
        production_readiness,
        "_workflow_facts",
        lambda: {"required_tasks_registered": True},
    )
    monkeypatch.setattr(
        production_readiness,
        "_configuration_facts",
        lambda: {"generation_keys_configured": True},
    )

    report = production_readiness.build_report()

    assert report["ready"] is False
    assert report["checks"]["active_owner_available"] is False
