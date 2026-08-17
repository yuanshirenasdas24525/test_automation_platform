from database.migrations.data_migrations.migrate_test_accounts_to_pool import (
    build_pool_from_legacy,
)


def test_shared_admin_maps_to_admin_pool_entry():
    legacy = {"shared_username": "demo_admin", "shared_password": "enc:v1:x"}
    pool, dynamic = build_pool_from_legacy(legacy)
    assert dynamic == ""
    assert pool == [{
        "label": "共享账号", "username": "demo_admin",
        "password": "enc:v1:x", "state": "admin", "enabled": True,
    }]


def test_script_provider_maps_to_dynamic_script():
    legacy = {"provider": "script", "prepare_script": "provision"}
    pool, dynamic = build_pool_from_legacy(legacy)
    assert dynamic == "provision"


def test_empty_legacy_yields_empty():
    assert build_pool_from_legacy({}) == ([], "")
