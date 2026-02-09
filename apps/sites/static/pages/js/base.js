/**
 * Apply CSS custom properties from data attributes on the root element.
 */
function applySiteThemeVars() {
  const root = document.documentElement;
  if (!root || !root.dataset) {
    return;
  }
  const mapping = {
    sitePrimary: '--site-primary',
    sitePrimaryStrong: '--site-primary-strong',
    sitePrimaryRgb: '--site-primary-rgb',
    siteAccent: '--site-accent',
    siteAccentStrong: '--site-accent-strong',
    siteAccentRgb: '--site-accent-rgb',
    siteSupport: '--site-support',
    siteSupportStrong: '--site-support-strong',
    siteSupportRgb: '--site-support-rgb',
    siteSupportText: '--site-support-text',
  };

  Object.entries(mapping).forEach(([dataKey, cssVar]) => {
    const value = root.dataset[dataKey];
    if (value) {
      root.style.setProperty(cssVar, value);
    }
  });
}

/**
 * Update the active theme and apply toolbar styles.
 * @param {string} theme
 * @param {boolean} [persist=true]
 */
function setTheme(theme, persist = true) {
  const root = document.documentElement;
  if (!root) {
    return;
  }
  root.setAttribute('data-bs-theme', theme);
  if (persist) {
    localStorage.setItem('theme', theme);
  }
  const toggle = document.getElementById('theme-toggle');
  const icon = toggle ? toggle.querySelector('use') : null;
  const labelLight = toggle?.dataset?.themeLabelLight || 'Light Mode';
  const labelDark = toggle?.dataset?.themeLabelDark || 'Dark Mode';
  if (toggle) {
    toggle.setAttribute('aria-label', theme === 'dark' ? labelLight : labelDark);
  }
  if (icon) {
    const iconRef = theme === 'dark' ? '#icon-sun' : '#icon-moon';
    icon.setAttribute('href', iconRef);
    icon.setAttribute('xlink:href', iconRef);
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
}

/**
 * Toggle between light and dark modes.
 */
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-bs-theme') || 'dark';
  setTheme(current === 'dark' ? 'light' : 'dark');
}

/**
 * Sync theme with the Django Debug Toolbar when present.
 */
function syncDebugToolbarTheme() {
  const button = document.getElementById('djToggleThemeButton');
  if (!button) {
    return;
  }
  const apply = () => {
    if (localStorage.getItem('theme')) {
      return;
    }
    const djdtTheme = localStorage.getItem('djdt.user-theme');
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
}

/**
 * Ensure redirect URLs remain on the current origin.
 * @param {string} url
 * @returns {string}
 */
function getSafeRedirect(url) {
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
}

applySiteThemeVars();

const themeToggle = document.getElementById('theme-toggle');
if (themeToggle) {
  themeToggle.addEventListener('click', toggleTheme);
}

(() => {
  const saved = localStorage.getItem('theme');
  if (saved) {
    setTheme(saved);
    return;
  }
  setTheme('dark', false);
})();

window.addEventListener('load', syncDebugToolbarTheme);

(() => {
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
})();

(() => {
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
})();

(() => {
  document.querySelectorAll('.language-select').forEach(select => {
    select.addEventListener('change', () => {
      const form = select.form;
      const data = new FormData(form);
      const csrfToken = form.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
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
          const rawNext = form.querySelector('input[name="next"]')?.value;
          window.location.href = getSafeRedirect(rawNext);
        })
        .catch(() => {
          form.submit();
        });
    });
  });
})();

(() => {
  const btn = document.getElementById('share-button');
  const modalEl = document.getElementById('shareModal');
  if (!btn || !modalEl || typeof bootstrap === 'undefined') {
    return;
  }
  const modal = new bootstrap.Modal(modalEl);
  const shortUrlInput = document.getElementById('share-short-url');
  const copyButton = document.getElementById('copy-short-url');
  btn.addEventListener('click', () => {
    modal.show();
  });
  modalEl.addEventListener('shown.bs.modal', () => {
    if (shortUrlInput && !shortUrlInput.value) {
      shortUrlInput.value = window.location.href;
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
})();
