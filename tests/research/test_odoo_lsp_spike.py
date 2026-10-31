"""开发期 LSP 客户端的协议边界；替身不证明真实 Odoo 兼容性。"""

import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/research/odoo_lsp_spike.py"


@pytest.fixture
def spike():
    assert SCRIPT.exists(), "尚未实现开发期评估客户端"
    spec = importlib.util.spec_from_file_location("odoo_lsp_spike", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frame_uses_utf8_byte_length(spike):
    framed = spike.frame({"result": "中文"})
    header, body = framed.split(b"\r\n\r\n", 1)
    assert header == f"Content-Length: {len(body)}".encode()
    assert json.loads(body) == {"result": "中文"}


def test_frame_reader_handles_partial_reads(spike):
    class Chunked(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    assert spike.read_frame(Chunked(b'Content-Length: 14\r\n\r\n{"result":"x"}')) == {"result": "x"}


def test_truncated_frame_is_an_error_not_empty_result(spike):
    with pytest.raises(EOFError):
        spike.read_frame(io.BytesIO(b'Content-Length: 20\r\n\r\n{}'))


@pytest.mark.parametrize("value,ready,status", [(None, False, "not_ready"), ([], True, "not_found"), ([1], True, "ok")])
def test_empty_response_requires_readiness_evidence(spike, value, ready, status):
    assert spike.result_status(value, ready=ready) == status


def test_source_paths_reject_escape_and_symlinks(spike, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass", encoding="utf-8")
    (root / "escape.py").symlink_to(outside)
    with pytest.raises(ValueError):
        spike.source_file(root, "escape.py")
    with pytest.raises(ValueError):
        spike.source_file(root, "../outside.py")


def test_rpc_matches_ids_services_requests_and_ignores_stderr(spike, tmp_path):
    # 错把通知/其他 id 当响应、允许 applyEdit、泄漏父进程秘密均应使此测试失败。
    server = tmp_path / "server.py"
    server.write_text('''import json, os, sys
def send(value):
    data=json.dumps(value).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\\r\\n\\r\\n".encode()+data)
    sys.stdout.buffer.flush()
def read():
    line=sys.stdin.buffer.readline()
    n=int(line.split(b":")[1]); sys.stdin.buffer.readline()
    return json.loads(sys.stdin.buffer.read(n))
request=read()
print("这不是协议", file=sys.stderr, flush=True)
send({"jsonrpc":"2.0","method":"window/logMessage","params":{"message":"notice"}})
send({"jsonrpc":"2.0","id":99,"result":"wrong id"})
send({"jsonrpc":"2.0","id":"edit","method":"workspace/applyEdit","params":{}})
edit=read()
send({"jsonrpc":"2.0","id":request["id"],"result":{"edit":edit["result"],"secret":os.getenv("APP_PASSWORD")}})
read()
''', encoding="utf-8")
    import os
    old = os.environ.get("APP_PASSWORD")
    os.environ["APP_PASSWORD"] = "不得传递"
    try:
        with spike.Session([sys.executable, str(server)], tmp_path) as session:
            result = session.request("test", {}, timeout=2)
            assert result == {"edit": {"applied": False, "failureReason": "只读评估"}, "secret": None}
            assert session.notifications[0]["method"] == "window/logMessage"
    finally:
        if old is None:
            os.environ.pop("APP_PASSWORD", None)
        else:
            os.environ["APP_PASSWORD"] = old


def test_rpc_timeout_is_explicit(spike, tmp_path):
    with spike.Session([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], tmp_path) as session:
        with pytest.raises(TimeoutError):
            session.request("test", {}, timeout=0.05)


def test_unsupported_capability_does_not_send_request(spike):
    assert spike.supported({}, "definition") is False
    assert spike.supported({"definitionProvider": {}}, "definition") is True


def test_query_position_uses_utf16_and_checks_anchor(spike):
    assert spike.query_position("# 😀 中文 plan_ids\n", {"line": 1, "needle": "plan_ids"}) == {"line": 0, "character": 9}
    with pytest.raises(ValueError):
        spike.query_position("other", {"line": 1, "needle": "plan_ids"})


def test_compare_requires_definition_line_and_full_field_facts(spike):
    case = {"expected_definition": {"file": "hrp:model.py", "line": 10}}
    result = {"status": "ok", "locations": [{"file": "hrp:model.py", "range": {
        "start": {"line": 8}, "end": {"line": 12}}}]}
    assert spike.compare_case(case, result) == "match"
    result["locations"][0]["range"]["start"]["line"] = 11
    assert spike.compare_case(case, result) == "mismatch"
    field = {"expected_type": "One2many", "expected_relation": "a.line", "expected_inverse": "head_id"}
    assert spike.compare_case(field, {"status": "ok", "hover": {
        "contents": {"value": '(field) One2many("a.line", …)'}}}) == "partial"


def test_diagnostic_response_preserves_items_without_claiming_valid_syntax(spike):
    value = {"kind": "full", "items": [{"message": "静态诊断", "severity": 1}]}
    assert spike.query_result("diagnostic", value, {}) == {"status": "ok", "diagnostics": value}
    assert spike.query_result("diagnostic", {"kind": "full", "items": []}, {}) == {
        "status": "ok", "diagnostics": {"kind": "full", "items": []}}


def test_definition_response_preserves_all_candidates(spike, tmp_path):
    value = [{"uri": (tmp_path / name).as_uri(), "range": {
        "start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}}}
        for name in ("b.py", "a.py")]
    result = spike.query_result("definition", value, {"synthetic": tmp_path})
    assert [row["file"] for row in result["locations"]] == ["synthetic:a.py", "synthetic:b.py"]
    assert result["status"] == "ok"


def test_synthetic_fixture_staging_never_imports_source_and_rejects_escape(spike, tmp_path):
    fixture = {"files": {"addons/probe/models.py": "raise RuntimeError('不得执行')\n"}, "cases": []}
    source, baseline = spike.stage_synthetic(fixture, tmp_path)
    assert source.joinpath("addons/probe/models.py").read_text() == "raise RuntimeError('不得执行')\n"
    assert baseline["source_namespace"] == "synthetic"
    assert len(baseline["files"]["addons/probe/models.py"]) == 64
    with pytest.raises(ValueError):
        spike.stage_synthetic({"files": {"../../escape.py": "pass"}, "cases": []}, tmp_path / "bad")


def test_compare_does_not_accept_single_target_for_duplicate_declarations(spike):
    case = {"expected_candidate_files": ["synthetic:a.py", "synthetic:b.py"]}
    result = {"status": "ok", "locations": [{"file": "synthetic:a.py"}]}
    assert spike.compare_case(case, result) == "partial"
    result["locations"].append({"file": "synthetic:b.py"})
    assert spike.compare_case(case, result) == "match"


def test_compare_without_expected_facts_remains_unverified(spike):
    assert spike.compare_case({}, {"status": "ok", "hover": {"contents": "任意信息"}}) == "unverified"
