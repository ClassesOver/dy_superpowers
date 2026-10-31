"""共享进程边界：真实子进程与 HTTP，不依赖 HRP 源码或外部网络。"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir()
    (root / 'sample.zoekt').write_bytes(b'fixture-index')
    server = tmp_path / 'zoekt-webserver'
    server.write_text(f'''#!{sys.executable}
import argparse, json, os, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
p = argparse.ArgumentParser()
p.add_argument('-listen')
p.add_argument('-index')
p.add_argument('-rpc', action='store_true')
p.add_argument('-html')
a = p.parse_args()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if payload['Q'] == 'slow': time.sleep(0.4)
        value = {{'pid': os.getpid(), 'query': payload['Q'],
                 'index_available': bool(list(Path(a.index).glob('*.zoekt'))),
                 'private_env': any(k in os.environ for k in ['APP_PASSWORD', 'DY_WIKI_TOKEN']),
                 'tracing_disabled': os.environ.get('OPENTELEMETRY_DISABLED')}}
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps(value).encode())
HTTPServer(tuple([a.listen.split(':')[0], int(a.listen.split(':')[1])]), Handler).serve_forever()
''')
    server.chmod(0o755)
    return root, server, tmp_path / 'runtime'


def client(bundle, *, idle=1.0):
    from dy_superpowers_mcp.zoekt_service import ZoektService
    return ZoektService(bundle[0], bundle[1], runtime_root=bundle[2], idle_seconds=idle)


def eventually(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate()


def test_construction_is_lazy_and_idle_releases_the_process(bundle):
    service = client(bundle)
    assert not service.state_path.exists()
    reply = service.request('/api/search', {'Q': '公司'})
    assert reply['query'] == '公司'
    assert service.state_path.is_file()
    eventually(lambda: not service.state_path.exists())
    with pytest.raises(ProcessLookupError):
        os.kill(reply['pid'], 0)


def test_independent_clients_share_one_process_and_refresh_global_idle(bundle):
    first, second = client(bundle), client(bundle)
    one = first.request('/api/search', {'Q': 'first-workspace'})
    time.sleep(0.6)
    two = second.request('/api/search', {'Q': 'second-workspace'})
    assert one['pid'] == two['pid']
    time.sleep(0.6)
    assert first.request('/api/search', {'Q': 'still-running'})['pid'] == one['pid']
    eventually(lambda: not first.state_path.exists())


def test_simultaneous_start_from_independent_python_processes_is_singleton(bundle):
    root, binary, runtime = bundle
    program = '''import json, sys
from pathlib import Path
from dy_superpowers_mcp.zoekt_service import ZoektService
s = ZoektService(Path(sys.argv[1]), Path(sys.argv[2]), runtime_root=Path(sys.argv[3]), idle_seconds=1)
print(json.dumps(s.request('/api/search', {'Q': 'parallel'})))
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}
    def launch(_):
        result = subprocess.run([sys.executable, '-c', program, str(root), str(binary), str(runtime)],
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(launch, range(4)))
    assert len({reply['pid'] for reply in replies}) == 1
    eventually(lambda: not client(bundle).state_path.exists())


def test_different_index_versions_do_not_share_processes(bundle, tmp_path):
    other = tmp_path / 'other-index'
    other.mkdir()
    (other / 'other.zoekt').write_bytes(b'different-index')
    from dy_superpowers_mcp.zoekt_service import ZoektService
    first = client(bundle)
    second = ZoektService(other, bundle[1], runtime_root=bundle[2], idle_seconds=1)
    assert first.request('/api/search', {'Q': 'one'})['pid'] != second.request('/api/search', {'Q': 'two'})['pid']
    eventually(lambda: not first.state_path.exists() and not second.state_path.exists())


def test_same_bundle_at_different_install_paths_shares_service(bundle, tmp_path):
    import shutil
    from dy_superpowers_mcp.zoekt_service import ZoektService
    copied = tmp_path / 'installed-index'
    shutil.copytree(bundle[0], copied)
    second = ZoektService(copied, bundle[1], runtime_root=bundle[2], idle_seconds=1)
    first = client(bundle)
    assert first.request('/api/search', {'Q': 'a'})['pid'] == second.request('/api/search', {'Q': 'b'})['pid']
    eventually(lambda: not first.state_path.exists())


def test_shared_index_survives_first_install_directory_removal(bundle, tmp_path):
    import shutil
    from dy_superpowers_mcp.zoekt_service import ZoektService

    copied = tmp_path / 'second-install'
    shutil.copytree(bundle[0], copied)
    first = client(bundle)
    second = ZoektService(copied, bundle[1], runtime_root=bundle[2], idle_seconds=1)
    initial = first.request('/api/search', {'Q': 'before-removal'})
    bundle[0].rename(tmp_path / 'retired-install')
    reply = second.request('/api/search', {'Q': 'after-removal'})
    assert reply['pid'] == initial['pid']
    assert reply['index_available'] is True
    eventually(lambda: not first.state_path.exists())
    assert not list(first.directory.glob('pinned-*'))


def test_child_does_not_inherit_business_credentials_or_telemetry(bundle, monkeypatch):
    monkeypatch.setenv('APP_PASSWORD', 'not-forwarded')
    monkeypatch.setenv('DY_WIKI_TOKEN', 'not-forwarded')
    monkeypatch.setenv('OPENTELEMETRY_DISABLED', 'false')
    reply = client(bundle).request('/api/search', {'Q': 'literal-not-persisted'})
    assert reply['private_env'] is False
    assert reply['tracing_disabled'] == 'true'
    assert 'literal-not-persisted' not in client(bundle).state_path.read_text()
    eventually(lambda: not client(bundle).state_path.exists())


def test_dead_child_is_replaced_and_stale_state_does_not_block(bundle):
    import signal
    service = client(bundle)
    first = service.request('/api/search', {'Q': 'one'})
    os.kill(first['pid'], signal.SIGTERM)
    time.sleep(0.1)
    second = service.request('/api/search', {'Q': 'two'})
    assert second['pid'] != first['pid']
    eventually(lambda: not service.state_path.exists())


def test_missing_index_fails_without_starting_a_service(bundle):
    (bundle[0] / 'sample.zoekt').unlink()
    with pytest.raises(ValueError, match='索引'):
        client(bundle).request('/api/search', {'Q': 'test'})


def test_damaged_shard_is_rejected_before_start(bundle):
    import hashlib
    root, _, _ = bundle
    digest = hashlib.sha256(b'fixture-index').hexdigest()
    (root / 'SHA256SUMS').write_text(digest + '  sample.zoekt\n')
    (root / 'sample.zoekt').write_bytes(b'damaged-index')
    with pytest.raises(ValueError, match='校验'):
        client(bundle).request('/api/search', {'Q': 'test'})


def test_read_access_time_change_does_not_invalidate_index(bundle):
    service = client(bundle)
    one = service.request('/api/search', {'Q': 'one'})
    shard = bundle[0] / 'sample.zoekt'
    info = shard.stat()
    os.utime(shard, ns=(info.st_atime_ns + 1000000000, info.st_mtime_ns))
    assert service.request('/api/search', {'Q': 'two'})['pid'] == one['pid']
    eventually(lambda: not service.state_path.exists())


def test_active_request_is_not_interrupted_by_global_idle(bundle):
    service = client(bundle, idle=0.15)
    result = service.request('/api/search', {'Q': 'slow'})
    assert result['query'] == 'slow'
    eventually(lambda: not service.state_path.exists())


def test_native_child_exits_when_supervisor_is_killed(bundle):
    import signal
    service = client(bundle)
    result = service.request('/api/search', {'Q': 'supervised'})
    state = json.loads(service.state_path.read_text())
    os.kill(state['supervisor_pid'], signal.SIGKILL)

    def exited():
        status = Path(f'/proc/{result["pid"]}/stat')
        return not status.exists() or status.read_text().split(') ', 1)[1].split()[0] == 'Z'

    eventually(exited)


def test_supervisor_lock_contention_does_not_stop_the_server(bundle):
    program = '''import sys
from pathlib import Path
from contextlib import contextmanager
from dy_superpowers_mcp import zoekt_service as m
original = m._locked
attempts = 0
@contextmanager
def busy_once(directory):
    global attempts
    attempts += 1
    if attempts == 1:
        raise getattr(m, '_LockBusy', ValueError)('隔离测试：监督者暂时未取得锁')
    with original(directory):
        yield
m._locked = busy_once
m._serve(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), 0.3)
'''
    bundle[2].mkdir()
    result = subprocess.run([sys.executable, '-c', program, *(str(path) for path in bundle)],
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert not (bundle[2] / 'service.json').exists()


def test_default_shared_root_does_not_depend_on_workspace_temp_configuration(bundle, monkeypatch):
    import tempfile
    from dy_superpowers_mcp.zoekt_service import ZoektService
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: '/tmp/workspace-a')
    first = ZoektService(bundle[0], bundle[1])
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: '/tmp/workspace-b')
    second = ZoektService(bundle[0], bundle[1])
    assert first.directory == second.directory
