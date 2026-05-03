(() => {
  const DIALOG_OPENED_EVENT = 'pages:dialog-opened';

  const toggle = document.getElementById('user-story-toggle');
  const overlay = document.getElementById('user-story-overlay');
  const form = document.getElementById('user-story-form');
  if (!toggle || !overlay || !form) {
    return;
  }

  const closeBtn = overlay.querySelector('[data-feedback-close]');
  const card = overlay.querySelector('.user-story-card');
  const successAlert = document.getElementById('user-story-success');
  const errorAlert = document.getElementById('user-story-error');
  const feedbackTextareas = form.querySelectorAll('textarea');
  const commentField = document.getElementById('user-story-comments');
  const counter = document.getElementById('user-story-char-count');
  const submitBtn = form.querySelector('button[type="submit"]');
  const ratingInputs = overlay.querySelectorAll('.user-story-rating input');
  const ratingLabels = overlay.querySelectorAll('.user-story-rating label');
  const ratingHint = document.getElementById('user-story-rating-hint');
  const copyLink = overlay.querySelector('[data-feedback-copy]');
  const defaultSuccessMessage = successAlert ? successAlert.textContent.trim() : '';
  const errorMessage = form.dataset.submitError;
  const networkErrorMessage = form.dataset.networkError;
  const copySuccessMessage = form.dataset.copySuccess;
  const copyErrorMessage = form.dataset.copyError;
  const copyAriaLabel = form.dataset.copyAriaLabel;
  const canCopyStaffDetails = form.dataset.copyStaffDetails === '1';
  const securityGroups = (form.dataset.securityGroups || '').trim();
  const copyFieldNamesToSkip = new Set(['csrfmiddlewaretoken', 'feedback_context', 'messages', 'path']);
  const messageField = form.querySelector('input[name="messages"]');
  const contextField = form.querySelector('input[name="feedback_context"]');
  const autocompleteUrl = form.dataset.autocompleteUrl || '';
  const autocompleteContainer = document.createElement('div');
  autocompleteContainer.className = 'user-story-autocomplete mt-2';
  autocompleteContainer.setAttribute('aria-live', 'polite');
  if (commentField && commentField.parentNode) {
    commentField.parentNode.appendChild(autocompleteContainer);
  }
  let autocompleteAbortController = null;
  let autocompleteRequestId = 0;

  const createBubblingEvent = eventName => {
    if (typeof Event === 'function') {
      return new Event(eventName, { bubbles: true });
    }
    const event = document.createEvent('Event');
    event.initEvent(eventName, true, false);
    return event;
  };

  const dispatchFeedbackDialogOpened = () => {
    if (typeof CustomEvent === 'function') {
      document.dispatchEvent(new CustomEvent(DIALOG_OPENED_EVENT, { detail: { source: 'feedback' } }));
      return;
    }
    const event = document.createEvent('CustomEvent');
    event.initCustomEvent(DIALOG_OPENED_EVENT, false, false, { source: 'feedback' });
    document.dispatchEvent(event);
  };

  const setCommentValue = value => {
    if (!commentField) {
      return;
    }
    commentField.value = value;
    commentField.dispatchEvent(createBubblingEvent('input'));
    commentField.focus();
    commentField.setSelectionRange(commentField.value.length, commentField.value.length);
  };

  const applyAutocompleteSuggestion = suggestion => {
    const currentValue = commentField.value;
    const trailingWhitespace = currentValue.match(/\s+$/);
    if (trailingWhitespace) {
      return `${currentValue}${suggestion}`;
    }
    const activeToken = currentValue.match(/\S+$/);
    if (activeToken && suggestion.toLowerCase().startsWith(activeToken[0].toLowerCase())) {
      return `${currentValue.slice(0, activeToken.index)}${suggestion}`;
    }
    const separator = currentValue.trim() ? ' ' : '';
    return `${currentValue.trimEnd()}${separator}${suggestion}`;
  };

  const clearAutocompleteSuggestions = () => {
    autocompleteContainer.textContent = '';
  };

  const renderAutocompleteSuggestions = suggestions => {
    clearAutocompleteSuggestions();
    if (!commentField || !Array.isArray(suggestions) || !suggestions.length) {
      return;
    }
    const list = document.createElement('div');
    list.className = 'd-flex flex-wrap gap-2';
    suggestions.forEach(suggestion => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm btn-outline-secondary';
      button.textContent = suggestion;
      button.addEventListener('click', () => {
        setCommentValue(applyAutocompleteSuggestion(suggestion));
      });
      list.appendChild(button);
    });
    autocompleteContainer.appendChild(list);
  };

  const fetchAutocompleteSuggestions = () => {
    if (autocompleteAbortController && autocompleteAbortController.abort) {
      autocompleteAbortController.abort();
      autocompleteAbortController = null;
    }

    if (!window.fetch || !autocompleteUrl || !commentField || commentField.value.trim().length < 2) {
      clearAutocompleteSuggestions();
      return;
    }

    const requestId = autocompleteRequestId + 1;
    autocompleteRequestId = requestId;
    const query = commentField.value;
    const abortController = typeof window.AbortController === 'function' ? new AbortController() : null;
    autocompleteAbortController = abortController;

    const csrfToken = form.querySelector('input[name="csrfmiddlewaretoken"]');
    const payload = `q=${encodeURIComponent(query)}&limit=5`;
    const headers = {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'XMLHttpRequest',
    };
    if (csrfToken) {
      headers['X-CSRFToken'] = csrfToken.value;
    }

    const isCurrentAutocompleteRequest = () =>
      autocompleteRequestId === requestId &&
      autocompleteAbortController === abortController &&
      commentField &&
      commentField.value === query;

    const fetchOptions = {
      method: 'POST',
      headers,
      body: payload,
    };
    if (abortController) {
      fetchOptions.signal = abortController.signal;
    }

    fetch(autocompleteUrl, fetchOptions)
      .then(response => {
        if (!response.ok) {
          if (isCurrentAutocompleteRequest()) {
            clearAutocompleteSuggestions();
          }
          return null;
        }
        return response.json();
      })
      .then(data => {
        if (data && isCurrentAutocompleteRequest()) {
          renderAutocompleteSuggestions(data.suggestions || []);
        }
      })
      .catch(error => {
        if (error && error.name === 'AbortError') {
          return;
        }
        if (isCurrentAutocompleteRequest()) {
          clearAutocompleteSuggestions();
        }
      });
  };

  const debounce = (fn, waitMs) => {
    let timeout = null;
    return (...args) => {
      if (timeout) {
        window.clearTimeout(timeout);
      }
      timeout = window.setTimeout(() => fn(...args), waitMs);
    };
  };

  const requestAutocompleteSuggestions = debounce(fetchAutocompleteSuggestions, 150);
  let previousFocus = null;
  let copyFeedbackTimeout = null;

  const focusableSelector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'textarea:not([disabled])',
    'select:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  const isInsideHiddenContainer = element => {
    let node = element;
    while (node && node !== overlay) {
      if (node.hasAttribute && node.hasAttribute('hidden')) {
        return true;
      }
      node = node.parentNode;
    }
    return false;
  };

  const getFocusableOverlayElements = () =>
    Array.from(overlay.querySelectorAll(focusableSelector)).filter(element => {
      if (isInsideHiddenContainer(element)) {
        return false;
      }
      const style = window.getComputedStyle(element);
      return style.visibility !== 'hidden' && style.display !== 'none';
    });

  const trapOverlayFocus = event => {
    if (event.key !== 'Tab' || overlay.hasAttribute('hidden')) {
      return;
    }
    const focusableElements = getFocusableOverlayElements();
    if (!focusableElements.length) {
      event.preventDefault();
      if (card) {
        card.focus();
      }
      return;
    }
    const first = focusableElements[0];
    const last = focusableElements[focusableElements.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const ratingMessages = Array.from({ length: 6 }, (_, index) => {
    if (!ratingHint) {
      return null;
    }
    return ratingHint.dataset[`ratingMessage${index}`] || null;
  });

  const setCharCount = () => {
    if (counter && commentField) {
      counter.textContent = commentField.value.length;
    }
  };

  const resizeTextarea = (field, { force = false } = {}) => {
    if (!field) {
      return;
    }

    if (!force && overlay.hasAttribute('hidden')) {
      return;
    }

    field.style.height = 'auto';

    const computed = window.getComputedStyle(field);
    const borderTop = parseFloat(computed.borderTopWidth) || 0;
    const borderBottom = parseFloat(computed.borderBottomWidth) || 0;
    const nextHeight = field.scrollHeight + borderTop + borderBottom;

    if (!force && nextHeight === 0) {
      return;
    }

    field.style.height = `${nextHeight}px`;
  };

  const resizeFeedbackTextareas = options => {
    feedbackTextareas.forEach(textarea => resizeTextarea(textarea, options));
  };

  const initializeTextareaAutoExpand = () => {
    if (!feedbackTextareas.length) {
      return;
    }

    feedbackTextareas.forEach(textarea => {
      textarea.addEventListener('input', () => resizeTextarea(textarea));
    });
  };

  const setRatingHintText = ratingValue => {
    if (!ratingHint) {
      return;
    }
    ratingHint.textContent = ratingMessages[ratingValue] || ratingMessages[0] || '';
  };

  const setRatingHint = () => {
    const selected = Array.from(ratingInputs || []).find(input => input.checked);
    const ratingValue = selected ? Number(selected.value) : 0;
    setRatingHintText(ratingValue);
  };

  const getRatingValueFromLabel = label => {
    const inputId = label.getAttribute('for');
    const input = inputId ? document.getElementById(inputId) : null;
    return input ? Number(input.value) : 0;
  };

  const resetAlerts = () => {
    if (successAlert) {
      successAlert.textContent = defaultSuccessMessage;
      successAlert.classList.add('is-hidden');
      successAlert.setAttribute('hidden', '');
    }
    if (errorAlert) {
      errorAlert.classList.add('is-hidden');
      errorAlert.textContent = '';
      errorAlert.setAttribute('hidden', '');
    }
  };

  const openOverlay = () => {
    if (!overlay.hasAttribute('hidden')) {
      return;
    }
    dispatchFeedbackDialogOpened();
    previousFocus = document.activeElement;
    overlay.removeAttribute('hidden');
    requestAnimationFrame(() => {
      overlay.classList.add('show');
      document.body.classList.add('user-story-open');
      toggle.setAttribute('aria-expanded', 'true');
      resizeFeedbackTextareas();
      if (card) {
        card.focus();
      }
    });
  };

  const closeOverlay = ({ restoreFocus = true } = {}) => {
    if (overlay.hasAttribute('hidden')) {
      return;
    }
    overlay.classList.remove('show');
    document.body.classList.remove('user-story-open');
    toggle.setAttribute('aria-expanded', 'false');
    setTimeout(() => {
      overlay.setAttribute('hidden', '');
      resetAlerts();
      if (restoreFocus && previousFocus) {
        previousFocus.focus();
      }
    }, 200);
  };

  toggle.addEventListener('click', () => {
    if (overlay.hasAttribute('hidden')) {
      openOverlay();
    } else {
      closeOverlay();
    }
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', closeOverlay);
  }

  overlay.addEventListener('click', event => {
    if (event.target === overlay) {
      closeOverlay();
    }
  });

  document.addEventListener('keydown', event => {
    trapOverlayFocus(event);
    if (event.key === 'Escape' && !overlay.hasAttribute('hidden')) {
      closeOverlay();
    }
  });

  document.addEventListener(DIALOG_OPENED_EVENT, event => {
    if (overlay.hasAttribute('hidden')) {
      return;
    }
    if (event.detail && event.detail.source !== 'feedback') {
      closeOverlay({ restoreFocus: false });
    }
  });

  if (commentField) {
    commentField.addEventListener('input', () => {
      setCharCount();
      requestAutocompleteSuggestions();
    });
    setCharCount();
  }

  initializeTextareaAutoExpand();

  if (ratingInputs && ratingInputs.length) {
    const ratingInputList = Array.from(ratingInputs);
    const selectRatingByValue = nextValue => {
      const targetInput = ratingInputList.find(input => Number(input.value) === nextValue);
      if (!targetInput) {
        return;
      }
      targetInput.checked = true;
      targetInput.focus();
      targetInput.dispatchEvent(createBubblingEvent('change'));
    };
    ratingInputs.forEach(input => {
      const showHint = () => setRatingHintText(Number(input.value));
      input.addEventListener('change', setRatingHint);
      input.addEventListener('focus', showHint);
      input.addEventListener('blur', setRatingHint);
      input.addEventListener('keydown', event => {
        const currentValue = Number(input.value);
        if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
          event.preventDefault();
          selectRatingByValue(Math.max(1, currentValue - 1));
        } else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
          event.preventDefault();
          selectRatingByValue(Math.min(5, currentValue + 1));
        } else if (event.key === 'Home') {
          event.preventDefault();
          selectRatingByValue(1);
        } else if (event.key === 'End') {
          event.preventDefault();
          selectRatingByValue(5);
        }
      });
    });
    setRatingHint();
  }

  if (ratingLabels && ratingLabels.length) {
    ratingLabels.forEach(label => {
      const ratingValue = getRatingValueFromLabel(label);
      const showHint = () => setRatingHintText(ratingValue);
      label.addEventListener('mouseenter', showHint);
      label.addEventListener('mouseleave', setRatingHint);
    });
  }

  const fallbackCopyText = value =>
    new Promise((resolve, reject) => {
      const textarea = document.createElement('textarea');
      textarea.value = value;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'absolute';
      textarea.style.left = '-9999px';
      document.body.appendChild(textarea);
      const selection = document.getSelection();
      const selected = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

      try {
        textarea.select();
        document.execCommand('copy');
        resolve();
      } catch (error) {
        reject(error);
      } finally {
        document.body.removeChild(textarea);
        if (selected && selection) {
          selection.removeAllRanges();
          selection.addRange(selected);
        }
      }
    });

  const copyText = value => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value).catch(() => fallbackCopyText(value));
    }

    return fallbackCopyText(value);
  };

  const getPageCopyValue = () => {
    const pageLabel = document.title.replace(/\s+/g, ' ').trim() || window.location.pathname;
    return `In ${pageLabel} (\`${window.location.href}\`)`;
  };

  const getAdminDashboardNextTask = () => {
    const nextTaskNode = document.querySelector(
      '.admin-home-operator-journey__link, .admin-home-operator-journey__text',
    );
    if (!nextTaskNode) {
      return '';
    }
    return nextTaskNode.textContent.replace(/\s+/g, ' ').trim();
  };

  const getAdminDashboardNetMessage = () => {
    const netMessageNode = document.querySelector('.admin-home-net-message__content');
    if (!netMessageNode) {
      return '';
    }
    return netMessageNode.textContent.replace(/\s+/g, ' ').trim();
  };

  const getRoleSiteNodeSummary = () => {
    const badgeNodes = document.querySelectorAll('#site-badges .badge');
    if (!badgeNodes.length) {
      return '';
    }
    const valuesByLabel = {};
    badgeNodes.forEach(badgeNode => {
      const labelNode = badgeNode.querySelector('.badge-link');
      const valueNode = badgeNode.querySelector('.badge-link-value');
      if (!labelNode || !valueNode) {
        return;
      }
      const normalizedLabel = labelNode.textContent.replace(':', '').trim().toLowerCase();
      const badgeValue = valueNode.textContent.replace(/\s+/g, ' ').trim();
      if (normalizedLabel && badgeValue) {
        valuesByLabel[normalizedLabel] = badgeValue;
      }
    });
    const role = valuesByLabel.role || '';
    const site = valuesByLabel.site || '';
    const node = valuesByLabel.node || '';
    const summaryParts = [role, site, node].filter(Boolean);
    return summaryParts.join(' / ');
  };

  const getFieldLabel = fieldName => {
    if (fieldName === 'rating') {
      const ratingLabel = document.getElementById('user-story-rating-group-label');
      return ratingLabel ? ratingLabel.textContent.trim() : 'Rating';
    }
    const field = form.querySelector(`[name="${fieldName}"]`);
    if (!field) {
      return fieldName;
    }
    const fieldId = field.getAttribute('id');
    const label = fieldId ? form.querySelector(`label[for="${fieldId}"]`) : null;
    return label ? label.textContent.trim() : fieldName;
  };

  const getRatingLabel = value => {
    const ratingValue = Number(value);
    return ratingMessages[ratingValue] || value;
  };

  const getRatingCopyValue = value => {
    const ratingValue = Number(value);
    const actionLabel = getRatingLabel(value);
    const displayValue = Number.isFinite(ratingValue) && ratingValue > 0 ? ratingValue : (ratingValue === 0 ? 0 : value);
    return `${displayValue}/5 (${actionLabel})`;
  };

  const getFormDetails = () => {
    const formData = new FormData(form);
    const details = [];
    for (const [name, value] of formData.entries()) {
      if (copyFieldNamesToSkip.has(name)) {
        continue;
      }
      if (typeof value !== 'string') {
        continue;
      }
      const trimmedValue = value.trim();
      if (!trimmedValue) {
        continue;
      }
      if (name === 'rating') {
        const ratingValue = Number(trimmedValue);
        if (!Number.isFinite(ratingValue) || ratingValue <= 0) {
          continue;
        }
        details.push(getRatingCopyValue(trimmedValue));
        continue;
      }
      const label = getFieldLabel(name);
      details.push(`${label}: ${trimmedValue}`);
    }
    return details;
  };

  const getPageMessages = () => {
    const messageNodes = document.querySelectorAll('.messagelist li');
    const messages = [];
    messageNodes.forEach(node => {
      const content = node.querySelector('.message-content') || node;
      const message = content.textContent.replace(/\s+/g, ' ').trim();
      if (message) {
        messages.push(message);
      }
    });
    return [...new Set(messages)];
  };

  const getFeedbackContextLines = () => {
    const contextNodes = document.querySelectorAll('[data-feedback-context]');
    const contexts = [];
    contextNodes.forEach(node => {
      const value = (node.dataset.feedbackContext || '').replace(/\s+/g, ' ').trim();
      if (value) {
        contexts.push(value);
      }
    });
    return [...new Set(contexts)];
  };

  const syncMessageField = messages => {
    if (!messageField) {
      return;
    }
    messageField.value = messages.join(' | ').substring(0, 2000);
  };

  const syncContextField = contexts => {
    if (!contextField) {
      return;
    }
    contextField.value = contexts.join(' | ').substring(0, 1000);
  };

  const buildCopyValue = () => {
    const baseValue = getPageCopyValue();
    const feedbackContexts = getFeedbackContextLines();
    syncContextField(feedbackContexts);
    if (!canCopyStaffDetails && !feedbackContexts.length) {
      return baseValue;
    }
    const details = getFormDetails();
    feedbackContexts.forEach(context => {
      details.push(context);
    });
    if (canCopyStaffDetails) {
      const nextOpsTask = getAdminDashboardNextTask();
      const netMessage = getAdminDashboardNetMessage();
      const roleSiteNode = getRoleSiteNodeSummary();
      if (nextOpsTask) {
        details.push(`Next: ${nextOpsTask}`);
      }
      if (netMessage) {
        details.push(`Net Message: ${netMessage}`);
      }
      if (roleSiteNode) {
        details.push(`Role / Site / Node: ${roleSiteNode}`);
      }
      if (securityGroups) {
        details.push(`Security groups: ${securityGroups}`);
      }
    }
    const messages = getPageMessages();
    syncMessageField(messages);
    if (!details.length && !messages.length) {
      return baseValue;
    }
    const lines = details.map(detail => `- ${detail}`);
    if (messages.length) {
      lines.push('- messages:');
      lines.push(...messages.map(message => `  - ${message}`));
    }
    return `${baseValue}\n\nFeedback:\n${lines.join('\n')}`;
  };

  if (copyLink) {
    const defaultCopyText = copyLink.textContent;
    if (copyAriaLabel) {
      copyLink.setAttribute('aria-label', copyAriaLabel);
    }
    const showCopyFeedback = message => {
      if (!message) {
        return;
      }
      if (copyFeedbackTimeout) {
        clearTimeout(copyFeedbackTimeout);
      }
      copyLink.textContent = message;
      copyFeedbackTimeout = window.setTimeout(() => {
        copyLink.textContent = defaultCopyText;
      }, 1500);
    };
    copyLink.addEventListener('click', event => {
      event.preventDefault();
      copyText(buildCopyValue())
        .then(() => showCopyFeedback(copySuccessMessage))
        .catch(error => {
          showCopyFeedback(copyErrorMessage);
          console.error('Failed to copy feedback details:', error);
        });
    });
  }

  form.addEventListener('submit', event => {
    if (!window.fetch || !window.FormData) {
      return;
    }
    event.preventDefault();
    resetAlerts();
    syncMessageField(getPageMessages());
    syncContextField(getFeedbackContextLines());
    if (submitBtn) {
      submitBtn.disabled = true;
    }

    const formData = new FormData(form);
    const enableSubmit = () => {
      if (submitBtn) {
        submitBtn.disabled = false;
      }
    };
    fetch(form.action, {
      method: 'POST',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: formData,
    })
      .then(response => {
        if (response.ok) {
          form.reset();
          setCharCount();
          clearAutocompleteSuggestions();
          setRatingHint();
          resizeFeedbackTextareas({ force: true });
          if (successAlert) {
            successAlert.textContent = defaultSuccessMessage;
            successAlert.classList.remove('is-hidden');
            successAlert.removeAttribute('hidden');
          }
          return null;
        }
        return response.json()
          .catch(() => ({}))
          .then(data => {
            let message = '';
            if (data && data.errors) {
              let values = [];
              Object.keys(data.errors).forEach(key => {
                const value = data.errors[key];
                values = values.concat(Array.isArray(value) ? value : [value]);
              });
              message = values.filter(Boolean).join(' ');
            }
            if (!message) {
              message = errorMessage || '';
            }
            if (errorAlert) {
              errorAlert.textContent = message;
              errorAlert.classList.remove('is-hidden');
              errorAlert.removeAttribute('hidden');
            }
            return null;
          });
      })
      .catch(() => {
        if (errorAlert) {
          errorAlert.textContent = networkErrorMessage || '';
          errorAlert.classList.remove('is-hidden');
          errorAlert.removeAttribute('hidden');
        }
      })
      .then(enableSubmit, enableSubmit);
  });
})();
