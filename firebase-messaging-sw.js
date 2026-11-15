// BKV Panel - arka plan push servis çalışanı
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  var p = {};
  try { p = event.data ? event.data.json() : {}; }
  catch (e) { try { p = { data: { body: event.data.text() } }; } catch (_) {} }

  var d = Object.assign({}, p.notification || {}, p.data || {});
  var title = d.title || 'BKV Panel';
  var opts = {
    body: d.body || 'Yeni mesajınız var.',
    icon: 'Logo.jpg',
    badge: 'Logo.jpg',
    data: { url: d.url || './' }
  };
  if (d.tag) opts.tag = d.tag;

  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var appRoot = new URL('../', self.registration.scope);
  var target = new URL((event.notification.data && event.notification.data.url) || './', appRoot).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if (c.url.indexOf(appRoot.href) === 0 && 'focus' in c) return c.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
