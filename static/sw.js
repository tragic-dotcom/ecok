const CACHE = 'ecoblack-deploy-v17-map-admin-themes';
const APP_SHELL = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/icons/icon.svg',
  '/static/manifest.webmanifest'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith('ecoblack-') && key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Never cache API responses: these include user-specific balances, sessions and admin data.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(request).then(response => {
      if (response.ok && (url.pathname === '/' || url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest')) {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(request, copy));
      }
      return response;
    }).catch(() => {
      if (request.mode === 'navigate') return caches.match('/');
      return caches.match(request);
    })
  );
});
