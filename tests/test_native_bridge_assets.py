from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_viewer_assets_use_relative_urls_for_native_webviews():
    html = (ROOT / "viewer/index.html").read_text()
    app = (ROOT / "viewer/app.js").read_text()
    assert 'href="./style.css"' in html
    assert '"three": "./vendor/three.module.js"' in html
    assert 'src="./app.js"' in html
    assert 'from "./vendor/OrbitControls.js"' in app


def test_frontend_prefers_native_api_bridge_before_http():
    app = (ROOT / "viewer/app.js").read_text()
    native = app.index("async function nativeRequest")
    http = app.index('fetch("/api/"')
    assert native < http
    assert "window.heliostatNative?.request" in app
    assert "messageHandlers?.heliostatNative" in app
