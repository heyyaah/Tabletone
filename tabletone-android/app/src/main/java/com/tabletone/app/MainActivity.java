package com.tabletone.app;

import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.biometric.BiometricManager;
import androidx.biometric.BiometricPrompt;
import androidx.core.content.ContextCompat;

import com.google.firebase.messaging.FirebaseMessaging;

import java.util.concurrent.Executor;

public class MainActivity extends AppCompatActivity {

    private static final String APP_URL = "https://tabletone.site";
    private static final String PREFS_NAME = "tabletone_prefs";
    private static final String KEY_BIOMETRIC_ENABLED = "biometric_enabled";
    private static final String KEY_HAS_SESSION = "has_session";
    private static final String KEY_BIOMETRIC_ASKED = "biometric_asked";

    private WebView webView;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE |
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
        );

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);

        FrameLayout layout = new FrameLayout(this);
        webView = new WebView(this);
        layout.addView(webView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        ));
        setContentView(layout);

        setupWebView();

        // FCM токен
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

        if (!isOnline()) {
            showOfflineDialog();
            return;
        }

        boolean biometricEnabled = prefs.getBoolean(KEY_BIOMETRIC_ENABLED, false);
        boolean hasSession = prefs.getBoolean(KEY_HAS_SESSION, false);

        if (biometricEnabled && hasSession && isBiometricAvailable()) {
            showBiometricPrompt();
        } else {
            loadApp();
        }
    }

    private boolean isBiometricAvailable() {
        BiometricManager bm = BiometricManager.from(this);
        return bm.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_WEAK)
               == BiometricManager.BIOMETRIC_SUCCESS;
        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);
        // Принудительно сохраняем куки на диск чтобы сессия не терялась
        cookieManager.flush();
    private void showBiometricPrompt() {
        Executor executor = ContextCompat.getMainExecutor(this);
        BiometricPrompt prompt = new BiometricPrompt(this, executor,
            new BiometricPrompt.AuthenticationCallback() {
                @Override
                public void onAuthenticationSucceeded(@NonNull BiometricPrompt.AuthenticationResult result) {
                    loadApp();
                }
                @Override
                public void onAuthenticationError(int errorCode, @NonNull CharSequence errString) {
                    showBiometricFallback();
                }
                @Override
                public void onAuthenticationFailed() { }
            });

        BiometricPrompt.PromptInfo info = new BiometricPrompt.PromptInfo.Builder()
            .setTitle("Вход в Tabletone")
            .setSubtitle("Подтвердите личность для входа")
            .setNegativeButtonText("Войти без биометрии")
            .build();

        prompt.authenticate(info);
    }

    private void showBiometricFallback() {
        runOnUiThread(() ->
            new AlertDialog.Builder(this, R.style.OfflineDialogTheme)
                .setTitle("Вход в Tabletone")
                .setMessage("Используйте биометрию для входа или войдите без неё.")
                .setPositiveButton("Повторить", (d, w) -> showBiometricPrompt())
                .setNegativeButton("Войти без биометрии", (d, w) -> loadApp())
                .setCancelable(false)
                .show()
        );
    }

    // Вызывается из JS после успешного входа
    private void onUserLoggedIn() {
        prefs.edit().putBoolean(KEY_HAS_SESSION, true).apply();
        // Предлагаем биометрику только один раз после первого входа
        if (!prefs.getBoolean(KEY_BIOMETRIC_ASKED, false) && isBiometricAvailable()) {
            prefs.edit().putBoolean(KEY_BIOMETRIC_ASKED, true).apply();
            runOnUiThread(() ->
                new AlertDialog.Builder(this, R.style.OfflineDialogTheme)
                    .setTitle("Защита биометрией")
                    .setMessage("Хотите использовать отпечаток пальца или Face ID для входа в Tabletone?")
                    .setPositiveButton("Включить", (d, w) ->
                        prefs.edit().putBoolean(KEY_BIOMETRIC_ENABLED, true).apply()
                    )
                    .setNegativeButton("Не сейчас", null)
                    .show()
            );
        }
    }

    private void onUserLoggedOut() {
        prefs.edit().putBoolean(KEY_HAS_SESSION, false).apply();
    }

    private void loadApp() {
        webView.loadUrl(APP_URL);
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
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
    @Override
    protected void onPause() {
        super.onPause();
        webView.onPause();
        // Сохраняем куки на диск при сворачивании
        CookieManager.getInstance().flush();
    }       "Chrome/124.0.0.0 Mobile Safari/537.36 TabletoneApp/1.0"
        );

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient());

        // JS-интерфейс для связи с нативным кодом
        webView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void onLogin() { onUserLoggedIn(); }

            @android.webkit.JavascriptInterface
            public void onLogout() { onUserLoggedOut(); }

            @android.webkit.JavascriptInterface
            public boolean isBiometricEnabled() {
                return prefs.getBoolean(KEY_BIOMETRIC_ENABLED, false);
            }

            @android.webkit.JavascriptInterface
            public void setBiometricEnabled(boolean enabled) {
                prefs.edit().putBoolean(KEY_BIOMETRIC_ENABLED, enabled).apply();
            }
        }, "TabletoneNative");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith("https://tabletone.site") ||
                    url.startsWith("http://tabletone.site")) {
                    return false;
                }
                try {
                    Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(intent);
                } catch (Exception e) { /* ignore */ }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // Отслеживаем вход/выход через перехват fetch
                view.evaluateJavascript(
                    "(function(){" +
                    "  if(window._tabletoneNativeHooked) return;" +
                    "  window._tabletoneNativeHooked = true;" +
                    "  var orig = window.fetch;" +
                    "  window.fetch = function(u, o){" +
                    "    var r = orig.apply(this, arguments);" +
                    "    if(typeof u === 'string'){" +
                    "      if((u.includes('/login') || u.includes('/register')) && window.TabletoneNative)" +
                    "        r.then(function(res){ if(res.ok || res.redirected) TabletoneNative.onLogin(); return res; });" +
                    "      if(u.includes('/logout') && window.TabletoneNative) TabletoneNative.onLogout();" +
                    "    }" +
                    "    return r;" +
                    "  };" +
                    "  if(document.cookie.includes('session') && window.TabletoneNative) TabletoneNative.onLogin();" +
                    "})();", null
                );
            }

            @Override
            public void onReceivedError(WebView view, int errorCode,
                                        String description, String failingUrl) {
                showOfflineDialog();
            }
        });
    }

    private boolean isOnline() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
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
                        loadApp();
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
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onPause() { super.onPause(); webView.onPause(); }

    @Override
    protected void onResume() { super.onResume(); webView.onResume(); }
}
