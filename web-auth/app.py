"""OTP-protected browser gateway; the Muse connector calls Huobao directly."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import qrcode
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from qrcode.image.svg import SvgPathImage

DB_PATH = Path(os.getenv('WEB_AUTH_DB', '/data/auth.sqlite3'))
UPSTREAM = os.getenv('HUOBAO_UPSTREAM', 'http://app:5679').rstrip('/')
BOOTSTRAP_HASH = os.getenv('WEB_AUTH_BOOTSTRAP_HASH', '')
COOKIE = 'huobao_trusted_device'
TRUST_SECONDS = 30 * 24 * 3600
SESSION_SECONDS = 12 * 3600


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)')
    connection.commit()
    os.chmod(DB_PATH, 0o600)
    return connection


def get_setting(key):
    with connect() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None


def put_setting(key, value):
    with connect() as db:
        db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        db.commit()


def bootstrap_valid(password):
    try:
        salt, expected = BOOTSTRAP_HASH.split('$', 1)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False


def totp_step(secret, counter):
    key = base64.b32decode(secret + '=' * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff
    return f'{value % 1_000_000:06d}'


def valid_totp(secret, code):
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return False
    current = int(time.time()) // 30
    return any(hmac.compare_digest(totp_step(secret, current + delta), code) for delta in (-1, 0, 1))


def session_valid(request):
    token = request.cookies.get(COOKIE, '')
    if len(token) != 64:
        return False
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute('SELECT expires_at FROM sessions WHERE token_hash=?', (token_hash,)).fetchone()
    return bool(row and row['expires_at'] > int(time.time()))


def set_session(response, request, remember=True):
    token = secrets.token_hex(32)
    ttl = TRUST_SECONDS if remember else SESSION_SECONDS
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE expires_at <= ?', (int(time.time()),))
        db.execute('INSERT INTO sessions(token_hash,expires_at) VALUES (?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), int(time.time()) + ttl))
        db.commit()
    secure = request.headers.get('x-forwarded-proto', request.url.scheme).lower() == 'https'
    response.set_cookie(COOKIE, token, max_age=ttl if remember else None,
                        httponly=True, secure=secure, samesite='lax', path='/')
    return response


def error(message, status=400):
    return JSONResponse({'error': message}, status_code=status)


def rate_limit(failed=False):
    """A small persistent global lockout protects the six-digit code."""
    now = int(time.time())
    with connect() as db:
        raw = db.execute("SELECT value FROM settings WHERE key='rate_limit'").fetchone()
        state = json.loads(raw['value']) if raw else {'start': now, 'count': 0, 'locked_until': 0}
        if now - state['start'] >= 300:
            state = {'start': now, 'count': 0, 'locked_until': 0}
        locked = state['locked_until'] > now
        if failed and not locked:
            state['count'] += 1
            if state['count'] >= 8:
                state['locked_until'] = now + 300
            db.execute("INSERT INTO settings(key,value) VALUES ('rate_limit',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(state),))
            db.commit()
        return locked


def qr_for(secret):
    label = quote('Huobao Muse:anyong', safe='')
    uri = f'otpauth://totp/{label}?secret={secret}&issuer={quote("Huobao Muse")}&algorithm=SHA1&digits=6&period=30'
    svg = qrcode.make(uri, image_factory=SvgPathImage).to_string()
    return svg.decode() if isinstance(svg, bytes) else svg


@asynccontextmanager
async def lifespan(app):
    connect().close()
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(120, read=None), follow_redirects=False)
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/auth/health')
async def health():
    return {'status': 'ok', 'bound': bool(get_setting('totp_secret'))}


@app.get('/auth')
async def login_page():
    return FileResponse('/app/login.html', media_type='text/html', headers={'Cache-Control': 'no-store'})


@app.get('/auth/status')
async def status(request: Request):
    return {'bound': bool(get_setting('totp_secret')), 'authenticated': session_valid(request)}


@app.post('/auth/enroll/start')
async def enroll_start(request: Request):
    if rate_limit(): return error('尝试次数过多，请五分钟后重试', 429)
    data = await request.json()
    old_secret = get_setting('totp_secret')
    if old_secret and not session_valid(request): return error('请先登录再换绑', 401)
    permitted = (valid_totp(old_secret, data.get('current_code')) if old_secret else
                 bootstrap_valid(data.get('bootstrap_password', '')))
    if not permitted:
        rate_limit(failed=True)
        return error('当前验证码或首次绑定密码不正确', 401)
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    put_setting('pending_secret', secret)
    put_setting('pending_until', str(int(time.time()) + 600))
    return {'qr_svg': qr_for(secret), 'manual_secret': secret}


@app.post('/auth/enroll/confirm')
async def enroll_confirm(request: Request):
    if rate_limit(): return error('尝试次数过多，请五分钟后重试', 429)
    data = await request.json()
    old_secret = get_setting('totp_secret')
    if old_secret and not session_valid(request): return error('请先登录再换绑', 401)
    permitted = (valid_totp(old_secret, data.get('current_code')) if old_secret else
                 bootstrap_valid(data.get('bootstrap_password', '')))
    pending = get_setting('pending_secret')
    expires = int(get_setting('pending_until') or '0')
    if not permitted or not pending or expires < int(time.time()) or not valid_totp(pending, data.get('new_code')):
        rate_limit(failed=True)
        return error('绑定验证失败，请检查验证码或重新扫码', 401)
    put_setting('totp_secret', pending)
    put_setting('pending_secret', '')
    put_setting('pending_until', '0')
    with connect() as db:
        db.execute('DELETE FROM sessions')
        db.commit()
    return set_session(JSONResponse({'success': True}), request)


@app.post('/auth/login')
async def login(request: Request):
    if rate_limit(): return error('尝试次数过多，请五分钟后重试', 429)
    secret = get_setting('totp_secret')
    if not secret: return error('请先绑定验证器', 409)
    data = await request.json()
    if not valid_totp(secret, data.get('code')):
        rate_limit(failed=True)
        return error('验证码不正确', 401)
    return set_session(JSONResponse({'success': True}), request, bool(data.get('remember', True)))


@app.post('/auth/logout')
async def logout(request: Request):
    token = request.cookies.get(COOKIE, '')
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
        db.commit()
    response = JSONResponse({'success': True})
    response.delete_cookie(COOKIE, path='/')
    return response


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
async def proxy(path: str, request: Request):
    if not session_valid(request):
        if request.url.path.startswith('/api/'):
            return error('otp_required', 401)
        return RedirectResponse('/auth', status_code=302)
    headers = {key: value for key, value in request.headers.items()
               if key.lower() not in {'host', 'connection', 'content-length', 'transfer-encoding', 'cookie'}}
    url = UPSTREAM + request.url.path
    if request.url.query: url += '?' + request.url.query
    upstream_request = app.state.client.build_request(request.method, url, headers=headers, content=request.stream())
    try:
        upstream_response = await app.state.client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        return error('upstream_unavailable', 502)
    response_headers = {key: value for key, value in upstream_response.headers.items()
                        if key.lower() not in {'connection', 'content-length', 'transfer-encoding'}}

    async def chunks():
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()

    return StreamingResponse(chunks(), status_code=upstream_response.status_code, headers=response_headers)
