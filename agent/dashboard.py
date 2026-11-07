"""Local-only dashboard for managing SpaceQA runs and inspecting reports."""
import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse
from uuid import uuid4
from dotenv import load_dotenv


class Dashboard:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.reports = self.root / 'reports'
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.process = None
        self.current = None
        self.logs = deque(maxlen=2000)
        self.secret = os.getenv('GEMINI_API_KEY', '')

    def report_list(self):
        items = []
        for path in self.reports.glob('*/bug.json'):
            try:
                if not path.resolve().is_relative_to(self.reports.resolve()):
                    continue
                report = json.loads(path.read_text())
                items.append({'id': path.parent.name, 'title': report.get('title', 'Untitled issue'),
                    'status': report.get('status', 'unverified'),
                    'severity': report.get('suspected_severity', 'unknown'),
                    'confidence': report.get('confidence', 'unknown'),
                    'created': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
            except (OSError, ValueError, TypeError):
                continue
        return sorted(items, key=lambda x: x['created'], reverse=True)

    def artifact(self, relative):
        path = (self.reports / relative).resolve()
        if not path.is_relative_to(self.reports.resolve()) or path.suffix not in {'.png', '.json', '.md'} or not path.is_file():
            raise FileNotFoundError('Artifact not found')
        return path

    def status(self):
        with self.lock:
            return {'run': dict(self.current) if self.current else None, 'logs': list(self.logs),
                    'settings': {'game_url': os.getenv('GAME_URL', 'http://localhost:5173'),
                                 'model': os.getenv('GEMINI_MODEL', ''),
                                 'max_steps': os.getenv('MAX_AGENT_STEPS', '20'),
                                 'seed': os.getenv('GAME_SEED', '42'),
                                 'configured': bool(self.secret and os.getenv('GEMINI_MODEL'))}}

    def start(self, data):
        with self.lock:
            if self.current and self.current['status'] in {'running', 'stopping'}:
                raise ValueError('A run is already active. Stop it before starting another.')
            if not self.secret or not os.getenv('GEMINI_MODEL'):
                raise ValueError('Set GEMINI_API_KEY and GEMINI_MODEL in .env, then restart the dashboard.')
            steps, seed = data.get('max_steps', 20), data.get('seed', 42)
            watch = data.get('watch', True)
            if type(steps) is not int or not 1 <= steps <= 500 or type(seed) is not int or abs(seed) > 2**31 - 1:
                raise ValueError('Use 1–500 actions and a 32-bit integer seed.')
            if type(watch) is not bool:
                raise ValueError('Watch must be true or false.')
            url = data.get('game_url', os.getenv('GAME_URL', 'http://localhost:5173'))
            if not isinstance(url, str):
                raise ValueError('Game URL must be text.')
            parsed = urlparse(url)
            if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.username or parsed.password:
                raise ValueError('Use a localhost game URL, such as http://localhost:5173.')
            run_id = uuid4().hex[:12]
            folder = self.reports / 'dashboard-runs' / run_id
            folder.mkdir(parents=True)
            command = [sys.executable, '-u', '-m', 'agent.main', '--reports', str(self.reports)]
            command.append('--watch' if watch else '--headless')
            env = dict(os.environ, MAX_AGENT_STEPS=str(steps), GAME_SEED=str(seed), GAME_URL=url)
            process = subprocess.Popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, start_new_session=True)
            self.process = process
            self.logs.clear()
            self.current = {'id': run_id, 'status': 'running', 'started': time.time(),
                            'ended': None, 'max_steps': steps, 'seed': seed, 'watch': watch,
                            'game_url': url, 'exit_code': None}
            (folder / 'run.json').write_text(json.dumps(self.current, indent=2))
            threading.Thread(target=self._collect, args=(process, run_id, folder), daemon=True).start()
            return dict(self.current)

    def _collect(self, process, run_id, folder):
        with (folder / 'activity.log').open('w', encoding='utf-8') as output:
            for line in process.stdout:
                safe = line.rstrip()
                if self.secret:
                    safe = safe.replace(self.secret, '[REDACTED]')
                output.write(safe + '\n')
                output.flush()
                with self.lock:
                    if self.current and self.current['id'] == run_id:
                        self.logs.append(safe)
        code = process.wait()
        with self.lock:
            if self.current and self.current['id'] == run_id:
                self.current.update(ended=time.time(), exit_code=code,
                    status='stopped' if self.current['status'] == 'stopping' else ('completed' if code == 0 else 'failed'))
                (folder / 'run.json').write_text(json.dumps(self.current, indent=2))

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.current['status'] = 'stopping'
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, data, content_type='application/json'):
            body = json.dumps(data).encode() if content_type == 'application/json' else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Local access only'})
            path = unquote(urlparse(self.path).path)
            try:
                if path in {'/', '/app.js', '/style.css'}:
                    name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                    data = (Path(__file__).parent / 'dashboard_ui' / name).read_bytes()
                    if path == '/':
                        data = data.replace(b'__SESSION_TOKEN__', app.token.encode())
                    return self.send(200, data, {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path])
                if path == '/api/status':
                    return self.send(200, app.status())
                if path == '/api/reports':
                    return self.send(200, app.report_list())
                if path.startswith('/api/reports/'):
                    key = path.removeprefix('/api/reports/')
                    if '/' in key or key in {'.', '..'}:
                        raise FileNotFoundError()
                    return self.send(200, json.loads(app.artifact(key + '/bug.json').read_text()))
                if path.startswith('/artifacts/'):
                    artifact = app.artifact(path.removeprefix('/artifacts/'))
                    return self.send(200, artifact.read_bytes(), {'.png': 'image/png', '.md': 'text/plain; charset=utf-8', '.json': 'application/json'}[artifact.suffix])
                return self.send(404, {'error': 'Not found'})
            except (FileNotFoundError, ValueError, OSError):
                return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.valid_host() or not secrets.compare_digest(self.headers.get('X-SpaceQA-Token', ''), app.token):
                return self.send(403, {'error': 'Invalid local session'})
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
                return self.send(403, {'error': 'Invalid origin'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Expected a small JSON request')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                if self.path == '/api/runs/start':
                    return self.send(201, app.start(data))
                if self.path == '/api/runs/stop':
                    app.stop()
                    return self.send(200, {'ok': True})
                return self.send(404, {'error': 'Not found'})
            except (ValueError, TypeError):
                return self.send(400, {'error': str(sys.exception())})
            except OSError:
                return self.send(500, {'error': 'Could not launch the agent process'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='SpaceQA local dashboard')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='Agent project directory')
    args = parser.parse_args()
    load_dotenv(args.root / '.env')
    app = Dashboard(args.root)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    print(f'SpaceQA dashboard: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        if app.process is not None:
            try:
                app.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(app.process.pid, signal.SIGKILL)
        server.server_close()


if __name__ == '__main__':
    main()
