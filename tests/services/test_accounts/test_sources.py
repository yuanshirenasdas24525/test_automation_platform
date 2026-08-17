from server.services.test_accounts.secrets import encode_test_account_secret
from server.services.test_accounts.sources import load_account_sources


class _Row:
    def __init__(self, key, value):
        self.config_key = key
        self.config_value = value


class _Query:
    def __init__(self, rows):
        self._rows = rows
    def filter(self, *a, **k):
        return self
    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
    def query(self, *a, **k):
        return _Query(self._rows)


def test_load_pool_decrypts_password_and_defaults_enabled():
    rows = [
        _Row("accounts", [
            {"label": "普通", "username": "u1", "password": encode_test_account_secret("p1"), "state": "normal"},
        ]),
        _Row("dynamic_script", "provision_fresh"),
    ]
    src = load_account_sources(_Session(rows), project_id=1)
    assert src["dynamic_script"] == "provision_fresh"
    acc = src["accounts"][0]
    assert acc["username"] == "u1"
    assert acc["password"] == "p1"
    assert acc["enabled"] is True
    assert acc["state"] == "normal"


def test_missing_config_yields_empty_sources():
    src = load_account_sources(_Session([]), project_id=1)
    assert src["accounts"] == []
    assert src["dynamic_script"] == ""


def test_malformed_accounts_value_is_ignored():
    rows = [_Row("accounts", "not-a-list")]
    src = load_account_sources(_Session(rows), project_id=1)
    assert src["accounts"] == []
