// Service Worker для Web Push уведомлений Tabletone
self.addEventListener('push', function(event) {
    if (!event.data) return;
    const data = event.data.json();
    const options = {
        body: data.body || '',
        icon: '/static/images/logo.png',
        badge: '/static/images/logo.png',
        tag: data.tag || 'tabletone-msg',
        renotify: true,
        data: { url: data.url || '/' }
    };
    event.waitUntil(self.registration.showNotification(data.title || 'Tabletone', options));
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const url = event.notification.data.url || '/';
    event.waitUntil(clients.matchAll({ type: 'window' }).then(function(clientList) {
        for (const client of clientList) {
            if (client.url === url && 'focus' in client) return client.focus();
        }
        if (clients.openWindow) return clients.openWindow(url);
    }));
});
