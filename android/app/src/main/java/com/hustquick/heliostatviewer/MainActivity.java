package com.hustquick.heliostatviewer;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private static final int FILE_REQUEST = 41;
    private static final int EXPORT_REQUEST = 42;
    private byte[] exportContent;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            byte[] bundle = readAsset("mobile/plant_bundle.bundle");
            JSONObject initialized = new JSONObject(NativeCore.initializeBundle(bundle));
            if (initialized.has("error")) throw new IllegalStateException(initialized.getString("error"));
            String saved = getPreferences(MODE_PRIVATE).getString("offlineMirrorFieldStateV1", null);
            if (saved != null) nativeRequest("POST", "state/import", new JSONObject(saved));
            showOfflineViewer();
        } catch (Exception error) {
            Toast.makeText(this, "离线数据初始化失败：" + error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private byte[] readAsset(String name) throws Exception {
        try (InputStream input = getAssets().open(name); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[65536]; int count;
            while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            return output.toByteArray();
        }
    }

    private void showOfflineViewer() {
        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(8, 19, 26));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setSupportZoom(false);
        webView.getSettings().setBuiltInZoomControls(false);
        webView.getSettings().setAllowFileAccess(true);
        webView.addJavascriptInterface(new OfflineBridge(), "heliostatNative");
        webView.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!"heliostat.invalid".equals(uri.getHost())) return null;
                String path = uri.getPath();
                if (path == null || path.contains("..")) return null;
                try {
                    String mime = path.endsWith(".js") ? "application/javascript"
                        : path.endsWith(".css") ? "text/css"
                        : path.endsWith(".html") ? "text/html" : "application/octet-stream";
                    return new WebResourceResponse(mime, "UTF-8", getAssets().open(path.substring(1)));
                } catch (Exception error) {
                    return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found",
                        java.util.Collections.emptyMap(), new java.io.ByteArrayInputStream(new byte[0]));
                }
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) Toast.makeText(MainActivity.this,
                    "无法载入离线镜场界面。", Toast.LENGTH_LONG).show();
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try { startActivityForResult(params.createIntent(), FILE_REQUEST); }
                catch (Exception error) { fileCallback = null; return false; }
                return true;
            }
        });
        setContentView(webView, new ViewGroup.LayoutParams(-1, -1));
        // ES modules require a real origin. All requests are served from APK
        // assets above; this address never connects to a network service.
        webView.loadUrl("https://heliostat.invalid/viewer/index.html");
    }

    private String nativeRequest(String method, String path, JSONObject payload) throws Exception {
        String result = NativeCore.requestJson(new JSONObject().put("method", method).put("path", path).put("payload", payload).toString());
        JSONObject response = new JSONObject(result);
        if (method.equals("POST") && (path.equals("plants/select") || path.equals("plants/import") || path.equals("layout/rearrange")) && !response.has("error")) {
            String state = NativeCore.requestJson(new JSONObject().put("method", "GET").put("path", "state/export").put("payload", new JSONObject()).toString());
            getPreferences(MODE_PRIVATE).edit().putString("offlineMirrorFieldStateV1", state).apply();
        }
        return result;
    }

    public final class OfflineBridge {
        @JavascriptInterface public void saveFile(String name, String text) {
            runOnUiThread(() -> {
                exportContent = text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
                Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("text/csv");
                intent.putExtra(Intent.EXTRA_TITLE, "heliostat-coordinates.csv");
                startActivityForResult(intent, EXPORT_REQUEST);
            });
        }

        @JavascriptInterface public String request(String method, String path, String payload) {
            try {
                String body = payload == null || payload.isBlank() ? "{}" : payload;
                return nativeRequest(method, path, new JSONObject(body));
            } catch (Exception error) {
                return "{\"error\":" + JSONObject.quote(
                    "本地计算接口失败：" + error.getMessage()) + "}";
            }
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == EXPORT_REQUEST && result == RESULT_OK && data != null && exportContent != null) {
            try (java.io.OutputStream stream = getContentResolver().openOutputStream(data.getData())) {
                stream.write(exportContent);
                Toast.makeText(this, "坐标已导出", Toast.LENGTH_SHORT).show();
            } catch (Exception error) { Toast.makeText(this, "导出失败：" + error.getMessage(), Toast.LENGTH_LONG).show(); }
            exportContent = null;
        }

        if (request == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            fileCallback = null;
        }
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
