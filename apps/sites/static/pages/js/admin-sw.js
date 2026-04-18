const CACHE_VERSION = "v3";
const STATIC_CACHE_NAME = `arthexis-admin-static-${CACHE_VERSION}`;
const PRECACHE_URLS = (new URL(self.location.href).searchParams.get("precache") || "")
  .split(",")
  .map((url) => url.trim())
  .filter(Boolean);
const STATIC_PREFIX = "/static/";
const CACHE_NAMES = [STATIC_CACHE_NAME];

function isCacheableStaticRequest(request) {
  if (request.method !== "GET") {
    return false;
  }

  const requestUrl = new URL(request.url);
  return requestUrl.origin === self.location.origin && requestUrl.pathname.startsWith(STATIC_PREFIX);
}

function shouldBypassCaching(request) {
  if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method)) {
    return true;
  }

  return request.headers.has("x-csrftoken") || request.headers.has("x-csrftoken".toUpperCase());
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .catch(() => Promise.resolve()),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames.map((cacheName) => {
          if (CACHE_NAMES.includes(cacheName)) {
            return Promise.resolve();
          }

          return caches.delete(cacheName);
        }),
      ),
    ),
  );
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (shouldBypassCaching(request)) {
    return;
  }

  if (!isCacheableStaticRequest(request)) {
    return;
  }

  event.respondWith(
    caches.open(STATIC_CACHE_NAME).then((cache) =>
      cache.match(request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }

        return fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.ok) {
            cache.put(request, networkResponse.clone());
          }
          return networkResponse;
        });
      }),
    ),
  );
});
