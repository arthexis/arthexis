const MOBILE_BREAKPOINT = '(max-width: 767.98px)';

const getLocalStorage = () => {
  try {
    return window.localStorage;
  } catch (error) {
    return null;
  }
};

const hasQueryFlag = names => {
  const decodeQueryPart = value => {
    try {
      return decodeURIComponent(value || '').toLowerCase();
    } catch (error) {
      return '';
    }
  };
  const falseValues = ['0', 'false', 'off', 'no'];
  const search = window.location.search.replace(/^\?/, '').split('&');
  return search.some(part => {
    if (!part) {
      return false;
    }
    const keyValue = part.split('=');
    const key = decodeQueryPart(keyValue[0]);
    const value = decodeQueryPart(keyValue[1]);
    return names.indexOf(key) !== -1 && falseValues.indexOf(value) === -1;
  });
};

const setupControllerMode = () => {
  const userAgent = window.navigator ? window.navigator.userAgent || '' : '';
  const isPlayStation = /PlayStation 4/i.test(userAgent);
  const isControllerRequested = hasQueryFlag(['controller', 'tv', 'ps4']);
  const isCoarsePointer = window.matchMedia && window.matchMedia('(hover: none), (pointer: coarse)').matches;
  if (!isPlayStation && !isControllerRequested && !isCoarsePointer) {
    return;
  }

  document.documentElement.classList.add('controller-mode');
  if (document.body) {
    document.body.classList.add('controller-mode');
  }
};

/**
 * Apply site theme variables from data attributes on the html element.
 */
const applySiteThemeVariables = () => {
  const root = document.documentElement;
  const mappings = [
    ['sitePrimary', '--site-primary', '#0d6efd'],
    ['sitePrimaryStrong', '--site-primary-strong', '#0b5ed7'],
    ['sitePrimaryRgb', '--site-primary-rgb', '13, 110, 253'],
    ['siteAccent', '--site-accent', '#facc15'],
    ['siteAccentStrong', '--site-accent-strong', '#fb923c'],
    ['siteAccentRgb', '--site-accent-rgb', '250, 204, 21'],
    ['siteSupport', '--site-support', '#15803d'],
    ['siteSupportStrong', '--site-support-strong', '#34d399'],
    ['siteSupportRgb', '--site-support-rgb', '21, 128, 61'],
    ['siteSupportText', '--site-support-text', '#f0fdf4'],
  ];

  mappings.forEach(([dataKey, cssVar, fallback]) => {
    const value = root.dataset[dataKey] || fallback;
    root.style.setProperty(cssVar, value);
  });
};

/**
 * Update the UI to match the chosen theme.
 */
const setTheme = (theme, persist = true) => {
  document.documentElement.setAttribute('data-bs-theme', theme);
  if (persist) {
    const storage = getLocalStorage();
    if (storage) {
      try {
        storage.setItem('theme', theme);
      } catch (error) {
        // Keep theme switching working when storage is restricted.
      }
    }
  }

  const toggle = document.getElementById('theme-toggle');
  if (toggle) {
    const icon = toggle.querySelector('use');
    const lightLabel = toggle.dataset.lightLabel || 'Light Mode';
    const darkLabel = toggle.dataset.darkLabel || 'Dark Mode';
    toggle.setAttribute('aria-label', theme === 'dark' ? lightLabel : darkLabel);
    if (icon) {
      icon.setAttribute('href', theme === 'dark' ? '#icon-sun' : '#icon-moon');
      icon.setAttribute('xlink:href', theme === 'dark' ? '#icon-sun' : '#icon-moon');
    }
  }

  const navbar = document.querySelector('nav.navbar');
  const buttons = document.querySelectorAll('.toolbar .btn');
  if (!navbar) {
    return;
  }

  if (theme === 'light') {
    navbar.classList.remove('navbar-dark', 'bg-dark');
    navbar.classList.add('navbar-light', 'bg-white');
    buttons.forEach(btn => {
      btn.classList.remove('btn-outline-light');
      btn.classList.add('btn-outline-dark');
    });
  } else {
    navbar.classList.remove('navbar-light', 'bg-white');
    navbar.classList.add('navbar-dark', 'bg-dark');
    buttons.forEach(btn => {
      btn.classList.remove('btn-outline-dark');
      btn.classList.add('btn-outline-light');
    });
  }
};

/**
 * Switch between light and dark themes when the toggle is clicked.
 */
const setupThemeToggle = () => {
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) {
    return;
  }
  toggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-bs-theme') || 'dark';
    setTheme(current === 'dark' ? 'light' : 'dark');
  });
};

/**
 * Initialize the theme state from storage or default.
 */
const initThemeState = () => {
  const storage = getLocalStorage();
  const saved = storage ? storage.getItem('theme') : null;
  if (saved) {
    setTheme(saved);
    return;
  }
  setTheme('dark', false);
};

/**
 * Sync the Django debug toolbar theme preference when available.
 */
const syncDebugToolbarTheme = () => {
  const button = document.getElementById('djToggleThemeButton');
  if (!button) {
    return;
  }
  const apply = () => {
    const storage = getLocalStorage();
    if (!storage) {
      return;
    }
    if (storage.getItem('theme')) {
      return;
    }
    const djdtTheme = storage.getItem('djdt.user-theme');
    if (!djdtTheme) {
      return;
    }
    const resolved = djdtTheme === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : djdtTheme;
    setTheme(resolved);
  };
  button.addEventListener('click', () => {
    setTimeout(apply);
  });
  apply();
};

/**
 * Enable click and hover behavior for navigation dropdowns.
 */
const setupDropdowns = () => {
  const dropdownItems = Array.from(document.querySelectorAll('.nav-item.dropdown'));
  if (!dropdownItems.length) {
    return;
  }

  const hideItem = item => {
    const menu = item.querySelector('.dropdown-menu');
    item.classList.remove('show');
    if (menu) {
      menu.classList.remove('show');
    }
    item.dataset.clickOpen = 'false';
  };

  const showItem = item => {
    const menu = item.querySelector('.dropdown-menu');
    item.classList.add('show');
    if (menu) {
      menu.classList.add('show');
    }
  };

  const closeOthers = current => {
    dropdownItems.forEach(other => {
      if (other !== current) {
        hideItem(other);
      }
    });
  };

  dropdownItems.forEach(item => {
    const link = item.querySelector('.nav-link');
    const menu = item.querySelector('.dropdown-menu');
    if (!link || !menu) {
      return;
    }

    item.dataset.clickOpen = 'false';

    link.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();

      const isAlreadyOpen = item.classList.contains('show') && item.dataset.clickOpen === 'true';
      closeOthers(item);

      if (isAlreadyOpen) {
        hideItem(item);
      } else {
        showItem(item);
        item.dataset.clickOpen = 'true';
      }
    });

    if (window.matchMedia('(hover: hover)').matches) {
      item.addEventListener('mouseenter', () => {
        if (item.dataset.clickOpen !== 'true') {
          closeOthers(item);
          showItem(item);
        }
      });
      item.addEventListener('mouseleave', () => {
        if (item.dataset.clickOpen !== 'true') {
          hideItem(item);
        }
      });
    }
  });

  document.addEventListener('click', event => {
    dropdownItems.forEach(item => {
      if (item.classList.contains('show') && !item.contains(event.target)) {
        hideItem(item);
      }
    });
  });
};

/**
 * Show and hide the signed-in user tooltip on hover.
 */
const setupUserInfoTooltip = () => {
  document.querySelectorAll('.user-info-trigger').forEach(btn => {
    const tooltip = btn.querySelector('.user-info-tooltip');
    if (!tooltip) {
      return;
    }
    btn.addEventListener('mouseenter', () => {
      tooltip.style.display = 'block';
    });
    btn.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
    });
  });
};

/**
 * Ensure redirects stay within the current origin.
 */
const getSafeRedirect = url => {
  const defaultPath = '/';
  if (!url) {
    return defaultPath;
  }
  try {
    const candidateUrl = new URL(url, window.location.origin);
    if (candidateUrl.origin === window.location.origin) {
      return candidateUrl.pathname + candidateUrl.search + candidateUrl.hash;
    }
  } catch (error) {
    console.error('Invalid redirect URL provided:', url, error);
  }
  return defaultPath;
};

/**
 * Submit the language change form asynchronously when possible.
 */
const setupLanguageSelect = () => {
  document.querySelectorAll('.language-select').forEach(select => {
    select.addEventListener('change', () => {
      const form = select.form;
      const data = new FormData(form);
      const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
      const csrfToken = csrfInput ? csrfInput.value : '';
      fetch(form.action, {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: csrfToken ? { 'X-CSRFToken': csrfToken } : {},
      })
        .then(response => {
          if (!response || !response.ok) {
            form.submit();
            return;
          }
          const nextInput = form.querySelector('input[name="next"]');
          const rawNext = nextInput ? nextInput.value : '';
          window.location.href = getSafeRedirect(rawNext);
        })
        .catch(() => {
          form.submit();
        });
    });
  });
};

/**
 * Wire up the share modal and copy action.
 */
const setupShareModal = () => {
  const btn = document.getElementById('share-button');
  const modalEl = document.getElementById('shareModal');
  if (!btn || !modalEl || !window.bootstrap) {
    return;
  }
  const modal = new window.bootstrap.Modal(modalEl);
  const shortUrlInput = document.getElementById('share-short-url');
  const copyButton = document.getElementById('copy-short-url');
  const thumbnailFrame = document.getElementById('share-page-thumbnail');
  btn.addEventListener('click', () => {
    modal.show();
  });
  modalEl.addEventListener('shown.bs.modal', () => {
    const currentPageUrl = window.location.href;
    if (shortUrlInput && !shortUrlInput.value) {
      shortUrlInput.value = currentPageUrl;
    }
    if (thumbnailFrame && !window.matchMedia(MOBILE_BREAKPOINT).matches) {
      const previewUrl = new URL(currentPageUrl);
      previewUrl.searchParams.set('djdt', 'share-preview');
      previewUrl.searchParams.set('share_preview_public', '1');
      if (thumbnailFrame.src !== previewUrl.toString()) {
        thumbnailFrame.src = previewUrl.toString();
      }
    }
  });
  modalEl.addEventListener('hidden.bs.modal', () => {
    if (thumbnailFrame) {
      thumbnailFrame.src = 'about:blank';
    }
  });
  if (copyButton && shortUrlInput) {
    copyButton.addEventListener('click', () => {
      const value = shortUrlInput.value;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).catch(() => {});
      } else {
        shortUrlInput.select();
        document.execCommand('copy');
      }
    });
  }
};

/**
 * Dismiss the site highlight in browser-local storage only.
 */
const setupSiteHighlightDismissal = () => {
  const highlight = document.getElementById('site-highlight');
  if (!highlight) {
    return;
  }
  const cacheKey = highlight.dataset.highlightCacheKey;
  if (!cacheKey) {
    return;
  }

  let localStorageAvailable = true;
  try {
    if (window.localStorage.getItem(cacheKey) === '1') {
      highlight.remove();
      return;
    }
  } catch (error) {
    localStorageAvailable = false;
  }

  const closeLink = highlight.querySelector('[data-highlight-close="true"]');
  if (!closeLink) {
    return;
  }
  closeLink.addEventListener('click', event => {
    event.preventDefault();
    if (localStorageAvailable) {
      try {
        window.localStorage.setItem(cacheKey, '1');
      } catch (error) {
        // Keep close behavior even if storage is unavailable.
      }
    }
    highlight.remove();
  });
};

/**
 * Dismiss the funding banner in browser-local storage only.
 */
const setupFundingBannerDismissal = () => {
  const banner = document.querySelector('[data-funding-banner-cache-key]');
  if (!banner) {
    return;
  }
  const cacheKey = banner.dataset.fundingBannerCacheKey;
  if (!cacheKey) {
    return;
  }

  let localStorageAvailable = true;
  try {
    if (window.localStorage.getItem(cacheKey) === '1') {
      banner.remove();
      return;
    }
  } catch (error) {
    localStorageAvailable = false;
  }

  const closeButton = banner.querySelector('[data-funding-banner-close="true"]');
  if (!closeButton) {
    return;
  }
  closeButton.addEventListener('click', event => {
    event.preventDefault();
    if (localStorageAvailable) {
      try {
        window.localStorage.setItem(cacheKey, '1');
      } catch (error) {
        // Keep close behavior even if storage is unavailable.
      }
    }
    banner.remove();
  });
};

setupControllerMode();
applySiteThemeVariables();
setupThemeToggle();
initThemeState();
setupDropdowns();
setupUserInfoTooltip();
setupLanguageSelect();
setupShareModal();
setupSiteHighlightDismissal();
setupFundingBannerDismissal();
window.addEventListener('load', syncDebugToolbarTheme);
