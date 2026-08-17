from __future__ import annotations

import pytest

from utils.script_runtime import (
    ScriptAssertionError,
    ScriptRuntimeError,
    ScriptTimeoutError,
    run_script,
)


def test_script_runs_in_isolated_process_and_can_import_installed_package() -> None:
    result = run_script(
        "import httpx\n"
        "def handler(input, vars=None, config=None):\n"
        "    return {'version': httpx.__version__, 'value': input['value'] + 1}\n",
        kind="workflow",
        body={"value": 4},
    )
    assert result["value"] == 5
    assert result["version"]


def test_script_does_not_receive_platform_secret_environment(monkeypatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "must-not-cross-process")
    result = run_script(
        "import os\n"
        "def handler(input, vars=None, config=None):\n"
        "    return os.environ.get('DB_PASSWORD')\n",
        kind="workflow",
    )
    assert result is None


def test_script_cannot_import_platform_source_package() -> None:
    with pytest.raises(ScriptRuntimeError, match="ModuleNotFoundError"):
        run_script(
            "import server\n"
            "def handler(input, vars=None, config=None):\n"
            "    return server.__name__\n",
            kind="workflow",
        )

def test_script_timeout_terminates_process() -> None:
    with pytest.raises(ScriptTimeoutError):
        run_script(
            "import time\n"
            "def handler(input, vars=None, config=None):\n"
            "    time.sleep(2)\n",
            kind="workflow",
            timeout=0.1,
        )


def test_script_assertion_is_distinguished_from_runtime_error() -> None:
    with pytest.raises(ScriptAssertionError, match="业务条件不满足"):
        run_script(
            "def handler(input, vars=None, config=None):\n"
            "    raise AssertionError('业务条件不满足')\n",
            kind="workflow",
        )
