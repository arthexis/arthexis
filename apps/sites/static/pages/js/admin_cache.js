(() => {
  const CACHE_PREFIX = "arthexis-admin-cache-v1:";
  const memoryCache = new Map();
  const inFlightRequests = new Map();

  const toAbsoluteUrl = (url) => new URL(url, window.location.origin);

  const stableParams = (urlObject, keyParams) => {
    const params = new URLSearchParams();
    if (Array.isArray(keyParams) && keyParams.length) {
      keyParams
        .slice()
        .sort()
        .forEach((name) => {
          urlObject.searchParams.getAll(name).forEach((value) => {
            params.append(name, value);
          });
        });
      return params;
    }

    Array.from(urlObject.searchParams.entries())
      .sort(([aName, aValue], [bName, bValue]) => {
        if (aName === bName) {
          return aValue.localeCompare(bValue);
        }
        return aName.localeCompare(bName);
      })
      .forEach(([name, value]) => {
        params.append(name, value);
      });
    return params;
  };

  const buildCacheKey = (url, keyParams) => {
    const absolute = toAbsoluteUrl(url);
    const params = stableParams(absolute, keyParams);
    const keyUrl = `${absolute.origin}${absolute.pathname}`;
    const query = params.toString();
    return `${CACHE_PREFIX}${query ? `${keyUrl}?${query}` : keyUrl}`;
  };

  const readStoredEntry = (cacheKey) => {
    if (memoryCache.has(cacheKey)) {
      return memoryCache.get(cacheKey);
    }

    try {
      const raw = window.localStorage.getItem(cacheKey);
      if (!raw) {
        return null;
      }
      const entry = JSON.parse(raw);
      if (!entry || typeof entry !== "object" || !("data" in entry)) {
        return null;
      }
      memoryCache.set(cacheKey, entry);
      return entry;
    } catch (error) {
      return null;
    }
  };

  const storeEntry = (cacheKey, entry, maxPayloadBytes) => {
    try {
      const serialized = JSON.stringify(entry);
      if (
        typeof maxPayloadBytes === "number" &&
        maxPayloadBytes > 0 &&
        new TextEncoder().encode(serialized).length > maxPayloadBytes
      ) {
        return;
      }
      memoryCache.set(cacheKey, entry);
      window.localStorage.setItem(cacheKey, serialized);
    } catch (error) {
      // Ignore serialization and storage failures.
    }
  };

  const isPlainObject = (value) =>
    Object.prototype.toString.call(value) === "[object Object]";

  const sameData = (first, second) => {
    if (Object.is(first, second)) {
      return true;
    }

    if (typeof first !== typeof second || first == null || second == null) {
      return false;
    }

    if (Array.isArray(first)) {
      if (!Array.isArray(second) || first.length !== second.length) {
        return false;
      }
      return first.every((value, index) => sameData(value, second[index]));
    }

    if (isPlainObject(first)) {
      if (!isPlainObject(second)) {
        return false;
      }

      const firstKeys = Object.keys(first).sort();
      const secondKeys = Object.keys(second).sort();
      if (firstKeys.length !== secondKeys.length) {
        return false;
      }

      return firstKeys.every(
        (key, index) => key === secondKeys[index] && sameData(first[key], second[key]),
      );
    }

    return false;
  };

  const fetchNetworkData = (url, requestInit) =>
    fetch(url, requestInit).then((response) => {
      if (!response.ok) {
        throw new Error(`Fetch failed with status ${response.status}`);
      }
      return response.json();
    });

  const fetchJSON = (url, options = {}) => {
    const {
      keyParams = [],
      ttlMs = 60_000,
      staleWhileRevalidate = true,
      maxPayloadBytes,
      requestInit = {},
      onRevalidate,
    } = options;

    const cacheKey = buildCacheKey(url, keyParams);
    const entry = readStoredEntry(cacheKey);
    const now = Date.now();
    const isExpired = !entry || now - (entry.fetchedAt || 0) > ttlMs;

    const revalidate = () => {
      if (inFlightRequests.has(cacheKey)) {
        return inFlightRequests.get(cacheKey);
      }

      const pending = fetchNetworkData(url, requestInit)
        .then((data) => {
          const previous = readStoredEntry(cacheKey);
          const changed = !previous || !sameData(previous.data, data);
          const nextEntry = { data, fetchedAt: Date.now() };
          storeEntry(cacheKey, nextEntry, maxPayloadBytes);
          if (typeof onRevalidate === "function") {
            onRevalidate(data, { changed, cacheKey });
          }
          return data;
        })
        .finally(() => {
          inFlightRequests.delete(cacheKey);
        });

      inFlightRequests.set(cacheKey, pending);
      return pending;
    };

    if (!entry) {
      return revalidate().then((data) => ({
        data,
        fromCache: false,
        isStale: false,
      }));
    }

    if (staleWhileRevalidate) {
      if (isExpired) {
        void revalidate().catch(() => {});
      }
    } else if (isExpired) {
      return revalidate().then((data) => ({
        data,
        fromCache: false,
        isStale: false,
      }));
    }

    return Promise.resolve({
      data: entry.data,
      fromCache: true,
      isStale: isExpired,
    });
  };

  window.ArthexisAdminCache = {
    fetchJSON,
  };
})();
