(function () {
  if (!window.htmx) {
    return;
  }

  const observed = new WeakMap();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const el = entry.target;
      const options = observed.get(el) || {};
      if (!entry.isIntersecting && options.once) {
        return;
      }

      const detail = {
        intersectionRatio: entry.intersectionRatio,
        boundingClientRect: entry.boundingClientRect,
        intersectionRect: entry.intersectionRect,
        isIntersecting: entry.isIntersecting,
      };

      window.htmx.trigger(el, "intersect", detail);

      if (options.once && entry.isIntersecting) {
        observer.unobserve(el);
        observed.delete(el);
      }
    });
  });

  function parseOptions(triggerValue) {
    const lower = triggerValue.toLowerCase();
    return {
      once: lower.includes("once"),
    };
  }

  function track(element) {
    if (observed.has(element)) {
      return;
    }
    const trigger = element.getAttribute("hx-trigger") || "";
    const options = parseOptions(trigger);
    observed.set(element, options);
    observer.observe(element);
  }

  function scan(root) {
    root.querySelectorAll('[hx-trigger~="intersect"]').forEach(track);
  }

  function init() {
    scan(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail && event.detail.target;
    if (target instanceof Element) {
      scan(target);
    }
  });
})();
