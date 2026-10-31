#!/usr/bin/env python3
"""只读、开发期 Odoo LSP 评估；不是插件运行组件或通用 RPC 工具。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote, urlparse


def frame(message):
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def read_frame(stream):
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("LSP 流提前结束")
        if line == b"\r\n":
            break
        key, value = line.decode("ascii").split(":", 1)
        headers[key.lower()] = value.strip()
    remaining = int(headers["content-length"])
    if not 0 < remaining <= 32 * 1024 * 1024:
        raise ValueError("LSP 消息长度无效")
    chunks = []
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("LSP 消息不完整")
        chunks.append(chunk)
        remaining -= len(chunk)
    return json.loads(b"".join(chunks))


def source_file(root, relative):
    root = root.resolve()
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("源码路径越界或符号链接")
    if path.suffix not in {".py", ".xml", ".js"} or not path.is_file():
        raise ValueError("仅允许源码文件")
    return path.resolve()


def supported(capabilities, method):
    value = capabilities.get({"symbol": "workspaceSymbolProvider"}.get(method, method + "Provider"))
    return value is not None and value is not False


def result_status(result, *, ready):
    if result is None or result == []:
        return "not_found" if ready else "not_ready"
    return "ok"


def query_position(text, anchor):
    line = text.splitlines()[anchor["line"] - 1]
    offset = line.index(anchor["needle"]) + 1
    return {"line": anchor["line"] - 1, "character": len(line[:offset].encode("utf-16-le")) // 2}


class Session:
    def __init__(self, argv, directory):
        self.stderr = (directory / "stderr.log").open("wb")
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            cwd=directory, env={"PATH": "/usr/bin:/bin", "RUST_LOG": "info",
                                "XDG_CACHE_HOME": str(directory / "cache")},
        )
        self.inbox = queue.Queue()
        self.pending = {}
        self.notifications = []
        self.next_id = 0
        self.peak_rss_kib = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                self.inbox.put(read_frame(self.process.stdout))
        except (EOFError, ValueError, KeyError) as exc:
            self.inbox.put(exc)

    def send(self, message):
        self.process.stdin.write(frame({"jsonrpc": "2.0", **message}))
        self.process.stdin.flush()

    def notify(self, method, params):
        self.send({"method": method, "params": params})

    def pump(self, deadline):
        try:
            message = self.inbox.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise TimeoutError("等待 LSP 超时") from exc
        if isinstance(message, Exception):
            raise message
        if "method" in message and "id" in message:
            method = message["method"]
            if method == "workspace/applyEdit":
                self.send({"id": message["id"], "result": {"applied": False, "failureReason": "只读评估"}})
            elif method == "workspace/configuration":
                self.send({"id": message["id"], "result": [None for _ in message["params"]["items"]]})
            elif method in {"window/workDoneProgress/create", "client/registerCapability"}:
                self.send({"id": message["id"], "result": None})
            else:
                self.send({"id": message["id"], "error": {"code": -32601, "message": "只读评估不支持此请求"}})
        elif "method" in message:
            self.notifications.append(message)
        else:
            self.pending[message["id"]] = message
        status = Path(f"/proc/{self.process.pid}/status")
        if status.exists():
            for line in status.read_text().splitlines():
                if line.startswith("VmHWM:"):
                    self.peak_rss_kib = max(self.peak_rss_kib, int(line.split()[1]))

    def request(self, method, params, timeout=30):
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while request_id not in self.pending:
            self.pump(deadline)
        response = self.pending.pop(request_id)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response.get("result")

    def wait_open(self, uri, timeout=90):
        token = "odoo_lsp_open:" + unquote(urlparse(uri).path)
        deadline = time.monotonic() + timeout
        while not any(n.get("method") == "$/progress" and n["params"].get("token") == token
                      and n["params"]["value"].get("kind") == "end" for n in self.notifications):
            self.pump(deadline)

    def close(self):
        try:
            if self.process.poll() is None:
                self.request("shutdown", None, timeout=2)
                self.notify("exit", None)
                self.process.stdin.close()
                self.process.wait(timeout=2)
        except (TimeoutError, subprocess.TimeoutExpired, OSError, EOFError, RuntimeError):
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            self.process.stdout.close()
            if not self.process.stdin.closed:
                self.process.stdin.close()
            self.reader.join(timeout=1)
            self.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def location_rows(value, roots):
    if value is None:
        return []
    rows = []
    for item in value if isinstance(value, list) else [value]:
        item = item.get("location", item)
        uri = item.get("uri", item.get("targetUri"))
        if uri is None:
            continue
        path = Path(unquote(urlparse(uri).path)).resolve()
        label = next((name + ":" + str(path.relative_to(root)) for name, root in roots.items()
                      if path.is_relative_to(root)), "outside_configured_roots")
        rows.append({"file": label, "range": item.get("range", item.get("targetSelectionRange"))})
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def query_result(method, value, roots, symbol=None):
    if method == "symbol":
        value = [item for item in (value or []) if item["name"] == symbol]
    row = {"status": result_status(value, ready=False)}
    if method in {"hover", "diagnostic"}:
        row["hover" if method == "hover" else "diagnostics"] = value
    else:
        row["locations"] = location_rows(value, roots)
    return row


def stage_synthetic(fixture, directory):
    """只将明确标记的合成源码写入独立临时目录，从不导入源码。"""
    for name in fixture["files"]:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.suffix not in {".py", ".xml"}:
            raise ValueError("合成样本路径无效")
    source = directory / "synthetic"
    source.mkdir(parents=True)
    hashes = {}
    for name, text in fixture["files"].items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return source, {"source_namespace": "synthetic", "addon_roots": ["addons"],
                    "files": hashes, "cases": fixture["cases"]}


def compare_case(case, result):
    if result["status"] != "ok":
        return "unverified"
    if "expected_candidate_files" in case:
        actual = {row["file"] for row in result["locations"]}
        expected = set(case["expected_candidate_files"])
        if actual == expected:
            return "match"
        return "partial" if actual and actual < expected else "mismatch"
    if "expected_extension_files" in case:
        actual = {row["file"] for row in result["locations"]}
        return "subset_only" if set(case["expected_extension_files"]) <= actual else "mismatch"
    if "expected_definition" in case:
        expected = case["expected_definition"]
        rows = result["locations"]
        matches = [row for row in rows if row["file"] == expected["file"]
                   and row["range"]["start"]["line"] <= expected["line"] - 1 <= row["range"]["end"]["line"]]
        if len(rows) != 1:
            return "ambiguous"
        if not matches:
            return "mismatch"
        return "partial" if "expected_inherit" in case else "match"
    facts = [value for key, value in case.items() if key.startswith("expected_")]
    if not facts:
        return "unverified"
    text = json.dumps(result.get("hover"), ensure_ascii=False)
    return "match" if all(value in text for value in facts) else "partial"


def evaluate(server, source, base, baseline, directory):
    namespace = baseline.get("source_namespace", "hrp")
    if namespace not in {"hrp", "synthetic"}:
        raise ValueError("评估源码命名空间无效")
    roots = {namespace: source, "base": base}
    # 上游 glob 会跟随链接；先拒绝根内所有符号链接，防止越界读。
    addon_roots = [source / item for item in baseline["addon_roots"]] + [base / "addons", base / "odoo/addons"]
    for root in addon_roots:
        if not root.is_dir() or any(path.is_symlink() for path in root.rglob("*")):
            raise ValueError(f"源码根缺失或含符号链接：{root}")
    files = {}
    for case in baseline["cases"]:
        key = case["file"]
        path = source_file(source, key)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != baseline["files"][key]:
            raise ValueError(f"人工基准文件已变化：{key}")
        files[key] = (path, data.decode("utf-8"))
    started = time.monotonic()
    with Session([str(server)], directory) as session:
        initialized = session.request("initialize", {
            "processId": os.getpid(), "rootUri": directory.as_uri(),
            "capabilities": {"window": {"workDoneProgress": True}},
            "initializationOptions": {"module": {"roots": [str(p) for p in addon_roots]},
                                      "symbols": {"limit": 100000}},
        })
        initialization_seconds = time.monotonic() - started
        session.notify("initialized", {})
        opened = {}
        for key, (path, text) in files.items():
            uri = path.as_uri()
            session.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "python" if path.suffix == ".py" else "xml",
                "version": 1, "text": text,
            }})
            try:
                session.wait_open(uri)
                opened[key] = True
            except TimeoutError:
                opened[key] = False
        open_seconds = time.monotonic() - started
        rows = []
        for case in baseline["cases"] + baseline.get("probes", []):
            row = {"case_id": case["case_id"], "group": case["group"], "method": case["method"]}
            method = case["method"]
            if not supported(initialized["capabilities"], method):
                rows.append({**row, "status": "unsupported"})
                continue
            if not opened[case["file"]]:
                rows.append({**row, "status": "not_ready"})
                continue
            if method == "symbol":
                rpc, params = "workspace/symbol", {"query": case["symbol"]}
            else:
                rpc = "textDocument/" + method
                params = {"textDocument": {"uri": files[case["file"]][0].as_uri()}}
                if method != "diagnostic":
                    params["position"] = query_position(files[case["file"]][1], case["anchor"])
                if method == "references":
                    params["context"] = {"includeDeclaration": True}
            query_started = time.monotonic()
            try:
                value = session.request(rpc, params)
                # 就绪只证明 didOpen 完成，不证明依赖闭包完整；空响应保持不确定。
                row.update(query_result(method, value, roots, case.get("symbol")))
            except TimeoutError:
                row["status"] = "timeout"
            except (RuntimeError, EOFError) as exc:
                row.update(status="error", error=str(exc))
            row["query_seconds"] = time.monotonic() - query_started
            rows.append(row)
        diagnostics = {key: notification["params"]["diagnostics"]
                       for key, (path, _) in files.items() for notification in session.notifications
                       if notification.get("method") == "textDocument/publishDiagnostics"
                       and notification["params"].get("uri") == path.as_uri()}
        return {"source_namespace": namespace, "source_hashes": baseline["files"],
                "published_diagnostics": diagnostics,
                "server_info": initialized.get("serverInfo"), "capabilities": initialized["capabilities"],
                "initialization_seconds": initialization_seconds, "documents_ready_seconds": open_seconds,
                "opened": opened, "peak_rss_kib_sampled": session.peak_rss_kib, "cases": rows,
                "limitations": ["空响应未当作确定无结果", "模块按需加载，未证明完整依赖闭包",
                                "workspace/symbol 不枚举字段；未证明可批量导出完整投影"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("server", "base", "cases", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--synthetic", action="store_true", help="仅用于限制评估；将合成样本暂存而非读取HRP")
    args = parser.parse_args()
    audit = Path(__file__).resolve().parents[2] / "docs/research-sources/odoo-lsp"
    output = args.output.resolve()
    if not output.is_relative_to(audit) or output.exists():
        parser.error("输出必须是本地 odoo-lsp 审计目录中的新文件")
    if not args.synthetic and args.source is None:
        parser.error("真实源码评估必须指定 --source")
    baseline = json.loads(args.cases.read_text())
    with tempfile.TemporaryDirectory(prefix="dy-lsp-run-") as temp:
        directory = Path(temp)
        if args.synthetic:
            source, baseline = stage_synthetic(baseline, directory)
        else:
            source = args.source.resolve()
        result = evaluate(args.server.resolve(), source, args.base.resolve(), baseline, directory)
        # 上游 info 日志可能包含完整响应；仓库审计只保存计数与摘要。
        log = (directory / "stderr.log").read_bytes()
        result["stderr_summary"] = {"sha256": hashlib.sha256(log).hexdigest(),
                                    "warning_count": log.count(b"WARN"), "error_count": log.count(b"ERROR")}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": len(result["cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
