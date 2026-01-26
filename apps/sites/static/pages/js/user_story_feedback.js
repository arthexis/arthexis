(() => {
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
  let previousFocus = null;
  let copyFeedbackTimeout = null;

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
      successAlert.classList.add('d-none');
      successAlert.setAttribute('hidden', '');
    }
    if (errorAlert) {
      errorAlert.classList.add('d-none');
      errorAlert.textContent = '';
      errorAlert.setAttribute('hidden', '');
    }
  };

  const openOverlay = () => {
    if (!overlay.hasAttribute('hidden')) {
      return;
    }
    previousFocus = document.activeElement;
    overlay.removeAttribute('hidden');
    requestAnimationFrame(() => {
      overlay.classList.add('show');
      document.body.classList.add('user-story-open');
      toggle.setAttribute('aria-expanded', 'true');
      if (card) {
        card.focus();
      }
    });
  };

  const closeOverlay = () => {
    if (overlay.hasAttribute('hidden')) {
      return;
    }
    overlay.classList.remove('show');
    document.body.classList.remove('user-story-open');
    toggle.setAttribute('aria-expanded', 'false');
    setTimeout(() => {
      overlay.setAttribute('hidden', '');
      resetAlerts();
      form.reset();
      setCharCount();
      setRatingHint();
      if (previousFocus) {
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
    if (event.key === 'Escape' && !overlay.hasAttribute('hidden')) {
      closeOverlay();
    }
  });

  if (commentField) {
    commentField.addEventListener('input', setCharCount);
    setCharCount();
  }

  if (ratingInputs && ratingInputs.length) {
    ratingInputs.forEach(input => {
      const showHint = () => setRatingHintText(Number(input.value));
      input.addEventListener('change', setRatingHint);
      input.addEventListener('focus', showHint);
      input.addEventListener('blur', setRatingHint);
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
    return `In ${pageLabel} (${window.location.href})`;
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

  const getFormDetails = () => {
    const formData = new FormData(form);
    const details = [];
    for (const [name, value] of formData.entries()) {
      if (name === 'csrfmiddlewaretoken' || name === 'path') {
        continue;
      }
      if (typeof value !== 'string') {
        continue;
      }
      const trimmedValue = value.trim();
      if (!trimmedValue) {
        continue;
      }
      const label = getFieldLabel(name);
      const displayValue = name === 'rating' ? getRatingLabel(trimmedValue) : trimmedValue;
      details.push(`${label}: ${displayValue}`);
    }
    return details;
  };

  const buildCopyValue = () => {
    const baseValue = getPageCopyValue();
    const details = getFormDetails();
    if (!details.length) {
      return baseValue;
    }
    return `${baseValue}\n\nFeedback form:\n${details.map(detail => `- ${detail}`).join('\n')}`;
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

  form.addEventListener('submit', async event => {
    event.preventDefault();
    resetAlerts();

    if (submitBtn) {
      submitBtn.disabled = true;
    }

    const formData = new FormData(form);
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: formData,
      });

      if (response.ok) {
        form.reset();
        setCharCount();
        setRatingHint();
        if (successAlert) {
          successAlert.textContent = defaultSuccessMessage;
          successAlert.classList.remove('d-none');
          successAlert.removeAttribute('hidden');
        }
      } else {
        const data = await response.json().catch(() => ({}));
        let message = '';
        if (data && data.errors) {
          message = Object.values(data.errors)
            .flat()
            .join(' ');
        }
        if (!message) {
          message = errorMessage || '';
        }
        if (errorAlert) {
          errorAlert.textContent = message;
          errorAlert.classList.remove('d-none');
          errorAlert.removeAttribute('hidden');
        }
      }
    } catch (error) {
      if (errorAlert) {
        errorAlert.textContent = networkErrorMessage || '';
        errorAlert.classList.remove('d-none');
        errorAlert.removeAttribute('hidden');
      }
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
      }
    }
  });
})();
