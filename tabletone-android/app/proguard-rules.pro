# Скрываем WebView природу приложения
-keep class com.tabletone.app.** { *; }
-keepattributes *Annotation*
-dontwarn android.webkit.**
