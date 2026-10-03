from types import SimpleNamespace

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from fastapi import FastAPI
from starlette.requests import Request

from api.auth import authorize
from api.main import protect_api_routes

ROOT = Path(__file__).resolve().parents[1]


def test_all_api_routes_require_token_but_health_stays_open():
    api = FastAPI()
    api.state.settings = SimpleNamespace(grid_api_token="phase17d-secret")
    async def passthrough(_request): return "ok"
    def request(path, headers=()):
        return Request({"type": "http", "headers": list(headers), "client": ("127.0.0.1", 1),
                        "app": api, "method": "GET", "path": path, "query_string": b"",
                        "server": ("localhost", 80), "scheme": "http"})
    denied = asyncio.run(protect_api_routes(request("/api/models/status"), passthrough))
    allowed = asyncio.run(protect_api_routes(request("/api/models/status",
        [(b"x-api-token", b"phase17d-secret")]), passthrough))
    health = asyncio.run(protect_api_routes(request("/api/health"), passthrough))
    assert denied.status_code == 403
    assert allowed == "ok" and health == "ok"


def test_loopback_without_token_remains_allowed_and_remote_without_token_is_rejected():
    api = FastAPI()
    api.state.settings = SimpleNamespace(grid_api_token="")
    loopback = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1),
                        "app": api, "method": "GET", "path": "/api/models/status",
                        "query_string": b"", "server": ("localhost", 80), "scheme": "http"})
    authorize(loopback)
    remote = Request({"type": "http", "headers": [], "client": ("192.0.2.1", 1),
                      "app": api, "method": "GET", "path": "/api/models/status",
                      "query_string": b"", "server": ("example", 80), "scheme": "http"})
    try:
        authorize(remote)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("remote clients must be rejected without a token")


def test_app_js_sends_configured_api_token_with_node():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js no está instalado; no se ejecuta la prueba de app.js")
    harness = r'''const fs=require('fs'),vm=require('vm'); const calls=[]; const window={location:{protocol:'http:',origin:'http://local'},APP:{}};
const document={addEventListener(){}}; const fetch=async(url,options)=>{calls.push(options.headers);return {status:200,ok:true,json:async()=>({})}};
const context={window,document,fetch,URL,AbortController,setTimeout,clearTimeout,console,localStorage:{getItem(){return null},setItem(){}}};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8')+';globalThis.client=new ApiClient("http://local");',context);
window.gridApiToken='check-token'; context.client.get('/api/models/status').then(()=>process.stdout.write(JSON.stringify(calls[0])));'''
    env = dict(os.environ, APP_JS=str(ROOT / "frontend/app.js"))
    result = subprocess.run([node, "-e", harness], cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '"X-API-Token":"check-token"' in result.stdout


def test_app_js_declined_prompt_is_not_repeated_and_token_retries_once():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js no está instalado; no se ejecuta la prueba de app.js")
    harness = r'''const fs=require('fs'),vm=require('vm'); let prompts=0,calls=0; const window={location:{protocol:'http:',origin:'http://local'},APP:{},prompt(){prompts++;return null}};
const document={addEventListener(){}}; const fetch=async(_url,options)=>{calls++;return {status:403,ok:false,json:async()=>({})}};
const context={window,document,fetch,URL,AbortController,setTimeout,clearTimeout,console,localStorage:{getItem(){return null},setItem(){}}};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8')+';globalThis.client=new ApiClient("http://local");',context);
(async()=>{await context.client._fetch('/first');await context.client._fetch('/second');const declined={prompts,calls,flag:window.gridTokenPromptDeclined};
window.gridTokenPromptDeclined=false;window.prompt=()=>{prompts++;return 'once'};await context.client._fetch('/third');process.stdout.write(JSON.stringify({declined,prompts,calls,token:window.gridApiToken}));})().catch(e=>{throw e});'''
    env = dict(os.environ, APP_JS=str(ROOT / "frontend/app.js"))
    result = subprocess.run([node, "-e", harness], cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '"declined":{"prompts":1,"calls":2,"flag":true}' in result.stdout
    assert '"prompts":2,"calls":4,"token":"once"' in result.stdout
