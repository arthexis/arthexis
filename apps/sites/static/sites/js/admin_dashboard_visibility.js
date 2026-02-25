/**
 * Persist per-user hidden app choices for the admin dashboard.
 */
(function () {
  const dashboardRoot = document.querySelector("#admin-dashboard #content-main");
  const appList = document.querySelector("[data-dashboard-app-list]");
  if (!dashboardRoot || !appList) {
    return;
  }

  const hideNarrowMode = () => window.matchMedia("(max-width: 640px)").matches;
  const hiddenToggleWrap = document.querySelector("[data-hidden-apps-toggle-wrap]");
  const hiddenToggleButton = document.querySelector("[data-hidden-apps-toggle]");
  const appModules = Array.from(appList.querySelectorAll(".module[data-app-label]"));
  const appToggleButtons = Array.from(appList.querySelectorAll("[data-app-visibility-toggle]"));
  const storageUserKey = window.__adminDashboardUserKey || "anon";
  const storageKey = `admin-dashboard-hidden-apps:${window.location.origin}:${storageUserKey}`;

  const readHiddenApps = () => {
    try {
      const value = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
      return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
    } catch (error) {
      return [];
    }
  };

  const writeHiddenApps = (labels) => {
    window.localStorage.setItem(storageKey, JSON.stringify(Array.from(new Set(labels)).sort()));
  };

  let hiddenLabels = new Set(readHiddenApps());
  let revealHiddenApps = false;

  const updateToggleLabel = (appModule) => {
    const appLabel = appModule.dataset.appLabel;
    const toggle = appModule.querySelector("[data-app-visibility-toggle]");
    if (!toggle || !appLabel) {
      return;
    }

    const isHidden = hiddenLabels.has(appLabel);
    const hideLabel = toggle.dataset.hideLabel || "Hide";
    const unhideLabel = toggle.dataset.unhideLabel || "Unhide";
    toggle.textContent = isHidden ? unhideLabel : hideLabel;
  };

  const syncVisibility = () => {
    const shouldHideForMode = hideNarrowMode();
    let hiddenCount = 0;

    appModules.forEach((appModule) => {
      const appLabel = appModule.dataset.appLabel;
      if (!appLabel) {
        return;
      }

      const hidden = hiddenLabels.has(appLabel);
      const displayHiddenApp = hidden && revealHiddenApps;
      const shouldHide = hidden && !displayHiddenApp;

      appModule.hidden = shouldHideForMode ? false : shouldHide;
      appModule.classList.toggle("dashboard-app-hidden", shouldHide);
      appModule.classList.toggle("dashboard-app-revealed", displayHiddenApp);
      if (hidden) {
        hiddenCount += 1;
      }
      updateToggleLabel(appModule);
    });

    if (hiddenToggleWrap && hiddenToggleButton) {
      const canShowToggle = !shouldHideForMode && hiddenCount > 0;
      hiddenToggleWrap.hidden = !canShowToggle;
      const showLabel = hiddenToggleWrap.dataset.showLabel || "Show Hidden apps";
      const hideLabel = hiddenToggleWrap.dataset.hideLabel || "Hide Hidden apps";
      hiddenToggleButton.textContent = revealHiddenApps ? hideLabel : showLabel;
    }
  };

  appToggleButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (hideNarrowMode()) {
        return;
      }

      const appLabel = button.dataset.appLabel;
      if (!appLabel) {
        return;
      }

      if (hiddenLabels.has(appLabel)) {
        hiddenLabels.delete(appLabel);
      } else {
        hiddenLabels.add(appLabel);
        revealHiddenApps = false;
      }

      writeHiddenApps(Array.from(hiddenLabels));
      syncVisibility();
    });
  });

  hiddenToggleButton?.addEventListener("click", () => {
    if (hideNarrowMode()) {
      return;
    }

    revealHiddenApps = !revealHiddenApps;
    syncVisibility();
  });

  window.addEventListener("resize", syncVisibility);

  const bootstrapStyle = document.getElementById("dashboard-app-visibility-bootstrap");
  if (bootstrapStyle) {
    bootstrapStyle.remove();
  }

  syncVisibility();
})();
