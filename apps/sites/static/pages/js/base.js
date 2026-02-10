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
    localStorage.setItem('theme', theme);
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
  const saved = localStorage.getItem('theme');
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
};

applySiteThemeVariables();
setupThemeToggle();
initThemeState();
setupDropdowns();
setupUserInfoTooltip();
setupLanguageSelect();
setupShareModal();
window.addEventListener('load', syncDebugToolbarTheme);
