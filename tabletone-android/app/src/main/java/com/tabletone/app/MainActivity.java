package com.tabletone.app;

import android.app.AlertDialog;
import android.content.Context;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;

import androidx.appcompat.app.AppCompatActivity;

import com.google.firebase.messaging.FirebaseMessaging;

public class MainActivity extends AppCompatActivity {

    private static final String APP_URL = "https://hi-j5rs.onrender.com";
    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE |
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
        );

        FrameLayout layout = new FrameLayout(this);
        webView = new WebView(this);
        layout.addView(webView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        ));
        setContentView(layout);

        setupWebView();

        // Получаем FCM токен и передаём в WebView после загрузки
        FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
            if (task.isSuccessful() && task.getResult() != null) {
                String token = task.getResult();
                webView.post(() ->
                    webView.evaluateJavascript(
                        "window._fcmToken = '" + token + "'; " +
                        "if(window.onFcmToken) window.onFcmToken('" + token + "');",
                        null
                    )
                );
            }
        });

        if (isOnline()) {
            webView.loadUrl(APP_URL);
        } else {
            showOfflineDialog();
        }
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();

        // Максимально скрываем WebView природу
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setTextZoom(100);

        // Убираем стандартный WebView User-Agent, ставим как нативное приложение
        settings.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/124.0.0.0 Mobile Safari/537.36 TabletoneApp/1.0"
        );

        // Cookies
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient());

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                // Все ссылки открываем внутри приложения
                if (url.startsWith("https://hi-j5rs.onrender.com") ||
                    url.startsWith("http://hi-j5rs.onrender.com")) {
                    return false;
                }
                return false;
            }

            @Override
            public void onReceivedError(WebView view, int errorCode,
                                        String description, String failingUrl) {
                showOfflineDialog();
            }
        });
    }

    private boolean isOnline() {
        ConnectivityManager cm = (ConnectivityManager)
            getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        NetworkCapabilities caps = cm.getNetworkCapabilities(cm.getActiveNetwork());
        return caps != null && (
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
        );
    }

    private void showOfflineDialog() {
        runOnUiThread(() -> {
            AlertDialog.Builder builder = new AlertDialog.Builder(this, R.style.OfflineDialogTheme);
            View dialogView = getLayoutInflater().inflate(R.layout.dialog_offline, null);
            builder.setView(dialogView);
            builder.setCancelable(false);

            AlertDialog dialog = builder.create();

            Button retryBtn = dialogView.findViewById(R.id.btn_retry);
            retryBtn.setOnClickListener(v -> {
                if (isOnline()) {
                    dialog.dismiss();
                    String currentUrl = webView.getUrl();
                    if (currentUrl == null || currentUrl.isEmpty() || currentUrl.equals("about:blank")) {
                        webView.loadUrl(APP_URL);
                    } else {
                        webView.reload();
                    }
                } else {
                    dialog.dismiss();
                    showOfflineDialog();
                }
            });

            dialog.show();
        });
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        webView.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
    }
}
