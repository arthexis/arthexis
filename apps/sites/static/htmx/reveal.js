(function () {
  if (!window.htmx) {
    return;
  }

  const observed = new WeakSet();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) {
        return;
      }
      const el = entry.target;
      const trigger = (el.getAttribute("hx-trigger") || "").toLowerCase();
      const once = trigger.includes("once");

      window.htmx.trigger(el, "revealed");

      if (once) {
        observer.unobserve(el);
        observed.delete(el);
      }
    });
  }, { threshold: 0 });

  function track(element) {
    if (observed.has(element)) {
      return;
    }
    observer.observe(element);
    observed.add(element);
  }

  function scan(root) {
    root.querySelectorAll('[hx-trigger~="revealed"]').forEach(track);
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
