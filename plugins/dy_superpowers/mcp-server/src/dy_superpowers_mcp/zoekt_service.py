"""同一用户、同一索引共享本地 Zoekt；不持久化查询或结果。"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

IDLE_SECONDS = 300
REQUEST_SECONDS = 10
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
PROTOCOL_VERSION = b'dy-zoekt-shared-v2\0'


class _LockBusy(ValueError):
    pass


def _digest(path: Path) -> bytes:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def _signature(path: Path) -> tuple:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _locked(directory: Path):
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'service.lock').open('a') as stream:
        end = time.monotonic() + 20
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise _LockBusy('源码检索繁忙，请稍后重试')
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path: Path, state: dict):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


def _http(port: int, route: str, payload: dict | None = None, *, timeout=REQUEST_SECONDS):
    # 本地通信不读取宿主 HTTP_PROXY，不把代码发送到代理。
    request = Request(f'http://127.0.0.1:{int(port)}{route}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError('源码检索响应超限，请缩小检索范围')
    return json.loads(content) if payload is not None else content


def _ready(state: dict) -> bool:
    try:
        return bool(state) and all(_process_start(state[key + '_pid']) == state[key + '_start']
                                  for key in ('supervisor', 'server')) and _http(
                                      state['port'], '/readyz', timeout=0.2) in {b'', b'ok'}
    except (OSError, URLError, KeyError, ValueError):
        return False


def _process_start(pid: int) -> str:
    fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(') ', 1)[1].split()
    if fields[0] == 'Z':
        raise ValueError('共享检索进程已退出')
    return fields[19]


def _parent_death_signal(parent_pid: int):
    # 监督进程是独立单线程进程；原生子进程不能在监督进程死亡后遗留。
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), '无法设置检索子进程退出信号')
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGTERM)


def _environment() -> dict:
    return {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'SYSTEMROOT') if key in os.environ} | {
        'JAEGER_DISABLED': 'true', 'OPENTELEMETRY_DISABLED': 'true',
        'GOOGLE_CLOUD_PROFILER_ENABLED': '', 'GOMAXPROCS': '2', 'GOMEMLIMIT': '128MiB',
    }


class ZoektService:
    """客户端不拥有共享服务；退出工作区不会杀死别的工作区的查询。"""

    def __init__(self, index: Path, binary: Path, *, runtime_root: Path | None = None,
                 idle_seconds: float = IDLE_SECONDS):
        self.index, self.binary = Path(index).resolve(), Path(binary).resolve()
        self.idle_seconds = idle_seconds
        # 发布包用校验清单作为内容标识；不把安装路径或工作区放进共享键。
        checksums = self.index / 'SHA256SUMS'
        identity = hashlib.sha256()
        identity.update(PROTOCOL_VERSION)
        if checksums.is_file():
            content = checksums.read_bytes()
            entries = []
            for line in content.decode('utf-8').splitlines():
                match = re.fullmatch(r'([0-9a-f]{64})  ([^/\\]+\.zoekt)', line)
                if not match or _digest(self.index / match[2]).hex() != match[1]:
                    raise ValueError('Zoekt 索引分片校验不符')
                entries.append(match[2])
            if not entries or sorted(entries) != sorted(path.name for path in self.index.glob('*.zoekt')):
                raise ValueError('Zoekt 索引分片校验清单不完整')
            identity.update(content)
        else:
            for path in sorted(self.index.glob('*.zoekt')):
                identity.update(path.name.encode())
                identity.update(_digest(path))
        if self.binary.is_file():
            identity.update(_digest(self.binary))
        self.bundle_id = identity.hexdigest()[:24]
        runtime_root = runtime_root or Path('/tmp') / f'dy-zoekt-{os.getuid()}'
        self.directory = Path(runtime_root) / self.bundle_id
        self.state_path = self.directory / 'service.json'
        self._files = [self.binary, *sorted(self.index.glob('*.zoekt'))]
        if checksums.is_file():
            self._files.append(checksums)
        self._signatures = [_signature(path) for path in self._files if path.is_file()]

    def request(self, route: str, payload: dict) -> dict:
        if route not in {'/api/search', '/api/list'}:
            raise ValueError('仅支持本地源码检索接口')
        if not self.binary.is_file() or not any(self.index.glob('*.zoekt')):
            raise ValueError('Zoekt 程序或源码索引未打包，无法检索完整源码')
        if [_signature(path) for path in self._files] != self._signatures or set(
                self.index.glob('*.zoekt')) != {path for path in self._files if path.suffix == '.zoekt'}:
            raise ValueError('Zoekt 程序或索引已变化，请重新加载并校验')
        # 锁覆盖请求：跨 MCP 统一并发为 1；空闲回收也获取同一把锁。
        with _locked(self.directory):
            state = _load(self.state_path)
            if not _ready(state):
                self.state_path.unlink(missing_ok=True)
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), '--serve', str(self.index),
                     str(self.binary), str(self.directory), str(self.idle_seconds)],
                    env=_environment(), cwd=self.directory, start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                end = time.monotonic() + REQUEST_SECONDS
                while time.monotonic() < end:
                    state = _load(self.state_path)
                    if state and _ready(state):
                        break
                    if process.poll() is not None:
                        raise ValueError('Zoekt 共享服务启动失败，请检查平台和索引包')
                    time.sleep(0.05)
                else:
                    process.terminate()
                    process.wait(timeout=3)
                    raise ValueError('Zoekt 共享服务启动超时')
            try:
                return _http(state['port'], route, payload)
            except (OSError, URLError, ValueError) as exc:
                raise ValueError('源码检索失败或超时，不能据此认定无结果') from exc
            finally:
                state['last_used'] = time.monotonic()
                _save(self.state_path, state)


def _serve(index: Path, binary: Path, directory: Path, idle_seconds: float):
    """轻量守护仅监督 Zoekt 生命周期；服务不依附任一工作区。"""
    state_path = directory / 'service.json'
    state_parent = os.getpid()
    process = None
    pinned = None
    try:
        # 原生服务会监视索引目录；固定文件生命周期，避免首个安装被清理后静默丢失分片。
        pinned = tempfile.TemporaryDirectory(prefix='pinned-', dir=directory)
        pinned_index = Path(pinned.name)
        for source in index.glob('*.zoekt'):
            target = pinned_index / source.name
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        for _ in range(3):
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            process = subprocess.Popen(
                [str(binary), '-index', str(pinned_index), '-listen', f'127.0.0.1:{port}', '-rpc', '-html=false'],
                preexec_fn=lambda: _parent_death_signal(state_parent),
                env=_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            state = {'supervisor_pid': os.getpid(), 'server_pid': process.pid,
                     'supervisor_start': _process_start(os.getpid()), 'server_start': _process_start(process.pid),
                     'port': port, 'last_used': time.monotonic()}
            end = time.monotonic() + REQUEST_SECONDS
            while time.monotonic() < end and process.poll() is None:
                if _ready(state):
                    _save(state_path, state)
                    break
                time.sleep(0.05)
            else:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                continue
            break
        else:
            return
        while process.poll() is None:
            time.sleep(min(0.2, idle_seconds / 2))
            try:
                with _locked(directory):
                    current = _load(state_path)
                    if current.get('supervisor_pid') != os.getpid():
                        break
                    if time.monotonic() - current['last_used'] >= idle_seconds:
                        # 锁内先停止，再撤销发布；新客户端不会连接正在退出的服务。
                        process.terminate()
                        process.wait(timeout=3)
                        state_path.unlink(missing_ok=True)
                        break
            except _LockBusy:
                # 查询仍在进行：本轮不检查空闲，也不进入 finally 终止服务。
                continue
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        with _locked(directory):
            if _load(state_path).get('supervisor_pid') == os.getpid():
                state_path.unlink(missing_ok=True)
            if pinned is not None:
                pinned.cleanup()


if __name__ == '__main__':
    if len(sys.argv) != 6 or sys.argv[1] != '--serve':
        raise SystemExit('仅由 MCP 源码检索客户端启动')
    _serve(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), float(sys.argv[5]))
