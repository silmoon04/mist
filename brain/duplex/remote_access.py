"""Authentication boundary for an opt-in, loopback-bound HTTPS tunnel."""
from collections import deque
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web


SESSION_SECONDS = 8 * 60 * 60


class RemoteAccess:
    def __init__(self, origin, pair_code, app):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path or parsed.query or parsed.fragment or
                parsed.port not in (None, 443) or origin != f'https://{parsed.hostname}'):
            raise ValueError('Remote origin must be an exact HTTPS origin without a path or port.')
        if not isinstance(pair_code, str) or len(pair_code) < 32 or len(set(pair_code)) < 12 or not pair_code.isascii():
            raise ValueError('Remote pairing code must be a high-entropy ASCII token of at least 32 characters.')
        self.origin = origin
        self.host = parsed.hostname
        self.code = pair_code
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.expires = 0.0
        self.failures = deque()
        self.secret_values = (pair_code,)

    def scrub(self, value):
        if isinstance(value, str):
            for secret in self.secret_values:
                value = value.replace(secret, '[redacted]')
            return value
        if isinstance(value, dict):
            return {key:self.scrub(item) for key,item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value

    def request_origin_ok(self, request, *, required=False):
        # The proxy only reaches our loopback listener. Never consult Forwarded or X-Forwarded-*.
        return (request.remote in ('127.0.0.1', '::1') and
                request.host == self.host and
                (request.headers.get('Origin') == self.origin if required else
                 request.headers.get('Origin') in (None, self.origin)))

    def authenticated(self, request):
        return (time.monotonic() < self.expires and
                secrets.compare_digest(request.cookies.get('mist_session', ''), self.token))

    def revoke(self):
        self.token = secrets.token_urlsafe(32)
        self.expires = 0.0

    def pair(self, code):
        now = time.monotonic()
        while self.failures and self.failures[0] <= now - 60:
            self.failures.popleft()
        if len(self.failures) >= 5:
            return False
        if not isinstance(code, str) or not code.isascii() or not secrets.compare_digest(code, self.code):
            self.failures.append(now)
            return False
        self.failures.clear()
        self.revoke()
        self.expires = now + SESSION_SECONDS
        return True

    def set_cookie(self, response):
        response.set_cookie('mist_session', self.token, httponly=True,
                            samesite='Strict', secure=True, max_age=SESSION_SECONDS, path='/')

    @web.middleware
    async def middleware(self, request, handler):
        if not self.request_origin_ok(request, required=request.method not in ('GET', 'HEAD')):
            raise web.HTTPForbidden(text='Unrecognised host or origin.')
        path = request.path
        public = ((path == '/try' and request.method == 'GET') or
                  (path in ('/duplex/trials.js', '/duplex/trials.css', '/duplex/trial_memory.mjs') and request.method == 'GET') or
                  (path == '/trial/session' and request.method == 'GET') or
                  (path == '/trial/pair' and request.method == 'POST'))
        if not public and not self.authenticated(request):
            return web.json_response({'error':'pairing_required'}, status=401,
                                     headers={'Cache-Control':'no-store'})
        # /pair is the original local server entry point; it must never pair a remote browser.
        if path == '/pair':
            raise web.HTTPNotFound()
        if request.method not in ('GET', 'HEAD') and request.headers.get('Origin') != self.origin:
            raise web.HTTPForbidden(text='Unrecognised origin.')
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
