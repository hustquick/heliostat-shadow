"""Serve the offline-capable 3D/2D viewer on localhost: python -m scripts.serve_viewer."""
import argparse
from http.cookies import SimpleCookie
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import socket
from socketserver import TCPServer
from urllib.parse import parse_qs, urlparse

from rust_core import available as rust_available, version as rust_version
from viewer.model import ROOT, ViewerModel
from viewer.workspace import ViewerWorkspace


class LocalViewerServer(ThreadingHTTPServer):
    def server_bind(self):
        # A loopback-only server has no need for reverse DNS (which can stall
        # for a minute on machines without a working local resolver).
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class ViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, model, **kwargs):
        self.model = model
        super().__init__(*args, directory=str(ROOT/'viewer'), **kwargs)

    def do_GET(self):
        url = urlparse(self.path)
        if not self._authorize(url):
            return
        if not url.path.startswith('/api/'):
            return super().do_GET()
        try:
            query = parse_qs(url.query)
            if url.path == '/api/health':
                data = {'status': 'ok', 'rust_core': rust_available(),
                        'rust_core_version': rust_version()}
            elif url.path == '/api/meta':
                data = self.model.metadata()
            elif url.path == '/api/frame':
                data = self.model.frame(query.get('time', [''])[0])[1]
            elif url.path == '/api/target':
                data = self.model.target(query.get('time', [''])[0], query.get('mirror', [''])[0])
            elif url.path == '/api/efficiencies':
                data = self.model.efficiencies(query.get('time', [''])[0])
            else:
                self.send_error(404)
                return
            self.json_response(data)
        except ValueError as exc:
            self.json_response({'error': str(exc)}, status=400)
        except Exception:
            import traceback
            traceback.print_exc()
            self.json_response({'error': '计算失败，请查看启动终端中的详细信息'}, status=500)

    def do_POST(self):
        if not self._authorize(urlparse(self.path)):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 20_000_000:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/plants/select':
                result = self.model.select(str(data.get('plant_id', '')))
            elif self.path == '/api/plants/import':
                result = self.model.import_csv(data)
            elif self.path == '/api/layout/rearrange':
                result = self.model.rearrange(data)
            else:
                self.send_error(404)
                return
            self.json_response(result)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.json_response({'error': str(exc)}, status=400)
        except Exception:
            import traceback
            traceback.print_exc()
            self.json_response({'error': '操作失败，请查看应用日志'}, status=500)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self, url):
        """Use an HttpOnly cookie after a temporary token in the launch URL."""
        expected = getattr(self.server, 'access_token', None)
        if not expected:
            return True
        supplied = parse_qs(url.query).get('token', [''])[0]
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        remembered = cookie.get('heliostat_access')
        authenticated = ((supplied and hmac.compare_digest(supplied, expected)) or
                         (remembered and hmac.compare_digest(remembered.value, expected)))
        if not authenticated:
            self.json_response({'error': '需要有效的镜场服务访问令牌'}, status=403)
            return False
        if supplied and not url.path.startswith('/api/'):
            self.send_response(303)
            self.send_header('Location', url.path or '/')
            self.send_header('Set-Cookie',
                             f'heliostat_access={expected}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            return False
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1',
                        help='listen address; non-loopback use requires --access-token')
    parser.add_argument('--access-token',
                        help='secret of at least 16 characters required for LAN/mobile access')
    parser.add_argument('--mobile', action='store_true',
                        help='listen on the local network and generate a temporary access token')
    parser.add_argument('--port-file', type=Path,
                        help='write the bound port here after the server is ready')
    args = parser.parse_args()
    if args.mobile:
        args.host = '0.0.0.0'
        args.access_token = args.access_token or secrets.token_urlsafe(18)
    loopback = args.host in {'127.0.0.1', '::1', 'localhost'}
    if not loopback and (not args.access_token or len(args.access_token) < 16):
        parser.error('--access-token with at least 16 characters is required for non-loopback use')
    server = LocalViewerServer((args.host, args.port), partial(ViewerHandler, model=ViewerWorkspace()))
    server.access_token = args.access_token
    if args.port_file:
        args.port_file.parent.mkdir(parents=True, exist_ok=True)
        args.port_file.write_text(f'{server.server_port}\n', encoding='utf-8')
    launch_host = _local_network_address() if args.host in {'0.0.0.0', '::'} else args.host
    suffix = f'/?token={args.access_token}' if args.access_token else '/'
    print(f'Heliostat viewer: http://{launch_host}:{server.server_port}{suffix}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.port_file:
            args.port_file.unlink(missing_ok=True)


def _local_network_address():
    """Best-effort LAN address for the URL shown to a mobile device."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('192.0.2.1', 9))
        return probe.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        probe.close()


if __name__ == '__main__':
    main()
