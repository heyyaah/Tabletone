package com.tabletone.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import androidx.core.app.NotificationCompat;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

public class MyFirebaseMessagingService extends FirebaseMessagingService {

    private static final String CHANNEL_MESSAGES = "messages";
    private static final String CHANNEL_CALLS = "calls";
    private static final String CHANNEL_POSTS = "posts";

    @Override
    public void onMessageReceived(RemoteMessage remoteMessage) {
        Map<String, String> data = remoteMessage.getData();
        String type = data.getOrDefault("type", "message");
        String title = data.getOrDefault("title", "Tabletone");
        String body = data.getOrDefault("body", "");

        String channelId;
        int icon;
        switch (type) {
            case "call":
                channelId = CHANNEL_CALLS;
                icon = android.R.drawable.ic_menu_call;
                break;
            case "post":
                channelId = CHANNEL_POSTS;
                icon = android.R.drawable.ic_menu_send;
                break;
            default:
                channelId = CHANNEL_MESSAGES;
                icon = android.R.drawable.ic_dialog_email;
                break;
        }

        createChannels();

        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_ONE_SHOT | PendingIntent.FLAG_IMMUTABLE
        );

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, channelId)
            .setSmallIcon(icon)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setPriority(type.equals("call")
                ? NotificationCompat.PRIORITY_MAX
                : NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent);

        NotificationManager nm = (NotificationManager)
            getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify((int) System.currentTimeMillis(), builder.build());
    }

    @Override
    public void onNewToken(String token) {
        // Токен обновился — отправляем на сервер
        // Это вызывается автоматически при первом запуске и при обновлении токена
        // MainActivity подхватит его при следующем открытии
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = (NotificationManager)
            getSystemService(Context.NOTIFICATION_SERVICE);

        nm.createNotificationChannel(new NotificationChannel(
            CHANNEL_MESSAGES, "Сообщения",
            NotificationManager.IMPORTANCE_HIGH));

        nm.createNotificationChannel(new NotificationChannel(
            CHANNEL_CALLS, "Звонки",
            NotificationManager.IMPORTANCE_MAX));

        nm.createNotificationChannel(new NotificationChannel(
            CHANNEL_POSTS, "Посты каналов",
            NotificationManager.IMPORTANCE_DEFAULT));
    }
}
