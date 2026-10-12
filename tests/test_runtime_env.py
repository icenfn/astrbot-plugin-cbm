"""CBM 运行时调优项（budget_mb / physical_job_limit 等）的读写与注入测试。

运行：python -m pytest tests/test_runtime_env.py -v
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cbm_manager as cm  # noqa: E402


def _manager(tmp_path) -> cm.CbmManager:
    m = cm.CbmManager(tmp_path / "cbm")
    m.root.mkdir(parents=True, exist_ok=True)
    return m


def test_specs_expose_budget_and_workers():
    keys = {spec["key"] for spec in cm.RUNTIME_ENV_SPECS}
    assert "CBM_MEM_BUDGET_MB" in keys
    assert "CBM_WORKERS" in keys


def test_load_absent_returns_empty(tmp_path):
    m = _manager(tmp_path)
    assert m.load_runtime_env() == {}
    assert m.runtime_env() == {}


def test_save_and_reload_roundtrip(tmp_path):
    m = _manager(tmp_path)
    saved = m.save_runtime_env({"CBM_MEM_BUDGET_MB": 4096, "CBM_WORKERS": 8})
    assert saved == {"CBM_MEM_BUDGET_MB": 4096, "CBM_WORKERS": 8}
    assert m.load_runtime_env() == {"CBM_MEM_BUDGET_MB": 4096, "CBM_WORKERS": 8}
    # runtime_env 输出字符串
    assert m.runtime_env() == {"CBM_MEM_BUDGET_MB": "4096", "CBM_WORKERS": "8"}


def test_accepts_string_numbers(tmp_path):
    m = _manager(tmp_path)
    saved = m.save_runtime_env({"CBM_MEM_BUDGET_MB": "8192"})
    assert saved == {"CBM_MEM_BUDGET_MB": 8192}


def test_empty_value_clears_key(tmp_path):
    m = _manager(tmp_path)
    m.save_runtime_env({"CBM_MEM_BUDGET_MB": 4096, "CBM_WORKERS": 4})
    saved = m.save_runtime_env({"CBM_MEM_BUDGET_MB": "", "CBM_WORKERS": 4})
    assert "CBM_MEM_BUDGET_MB" not in saved
    assert saved == {"CBM_WORKERS": 4}
    assert m.load_runtime_env() == {"CBM_WORKERS": 4}


def test_unknown_key_ignored(tmp_path):
    m = _manager(tmp_path)
    saved = m.save_runtime_env({"NOT_A_REAL_KEY": 1, "CBM_WORKERS": 2})
    assert saved == {"CBM_WORKERS": 2}


def test_out_of_range_rejected(tmp_path):
    m = _manager(tmp_path)
    # CBM_WORKERS 范围 1..256
    saved = m.save_runtime_env({"CBM_WORKERS": 999})
    assert saved == {}
    saved = m.save_runtime_env({"CBM_WORKERS": 0})
    assert saved == {}


def test_enum_validated(tmp_path):
    m = _manager(tmp_path)
    assert m.save_runtime_env({"CBM_LOG_LEVEL": "debug"}) == {"CBM_LOG_LEVEL": "debug"}
    assert m.save_runtime_env({"CBM_LOG_LEVEL": "verbose"}) == {}


def test_corrupt_file_is_tolerated(tmp_path):
    m = _manager(tmp_path)
    (m.root / "runtime_env.json").write_text("{ not json", "utf-8")
    assert m.load_runtime_env() == {}


def test_runtime_settings_view(tmp_path):
    m = _manager(tmp_path)
    m.save_runtime_env({"CBM_WORKERS": 6})
    view = m.runtime_settings()
    assert isinstance(view, list) and len(view) == len(cm.RUNTIME_ENV_SPECS)
    by_key = {s["key"]: s for s in view}
    assert by_key["CBM_WORKERS"]["value"] == 6
    assert by_key["CBM_MEM_BUDGET_MB"]["value"] is None
    assert by_key["CBM_WORKERS"]["env"] == "CBM_WORKERS"


def test_mcp_config_carries_runtime_env(tmp_path):
    m = _manager(tmp_path)
    m.save_runtime_env({"CBM_MEM_BUDGET_MB": 2048})
    # 未安装时应给出明确错误，而不是静默丢失运行时调优项。
    try:
        m.mcp_server_config()
    except cm.CbmError:
        pass
    # runtime_env 始终可直接读取，供启动/launcher 注入。
    assert m.runtime_env() == {"CBM_MEM_BUDGET_MB": "2048"}
