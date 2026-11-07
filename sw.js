// 🌿 BKV Sürdürülebilir Tarım Kulübü - PWA Service Worker (v1.0)
const CACHE_NAME = 'bkv-tarim-cache-v1';
const STATIC_ASSETS = [
    './',
    'Logo.jpg',
    'Login Screen.png',
    'manifest.json'
];

// 1. Kurulum (Install) & Temel Varlıkların Önbelleğe Alınması
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] PWA Statik varlıklar önbelleğe alınıyor...');
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn('[SW] Bazı varlıklar önbelleğe alınamadı:', err);
            });
        }).then(() => self.skipWaiting())
    );
});

// 2. Etkinleştirme (Activate) & Eski Önbellek Temizliği
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((key) => {
                    if (key !== CACHE_NAME) {
                        console.log('[SW] Eski önbellek siliniyor:', key);
                        return caches.delete(key);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// 3. İstek Yakalama (Fetch): Network-First with Cache Fallback
self.addEventListener('fetch', (event) => {
    const req = event.request;
    const url = new URL(req.url);

    // Canlı Firebase, API ve harici WebSocket / POST istekleri önbelleğe alınmaz, doğrudan ağa gider
    if (req.method !== 'GET' || 
        url.hostname.includes('firebase') || 
        url.hostname.includes('firestore') || 
        url.hostname.includes('open-meteo') ||
        url.pathname.startsWith('/__/')
    ) {
        return;
    }

    event.respondWith(
        fetch(req).then((networkRes) => {
            // Başarılı ağ yanıtını önbelleğe güncelle
            if (networkRes && networkRes.status === 200) {
                const resClone = networkRes.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(req, resClone);
                });
            }
            return networkRes;
        }).catch(() => {
            // Ağ yoksa önbellekten sun (Offline PWA desteği)
            return caches.match(req).then((cachedRes) => {
                if (cachedRes) return cachedRes;
                // HTML sayfası ise kök sayfayı döndür
                if (req.headers.get('accept')?.includes('text/html')) {
                    return caches.match('./');
                }
            });
        })
    );
});
