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
  const ratingHint = document.getElementById('user-story-rating-hint');
  const defaultSuccessMessage = successAlert ? successAlert.textContent.trim() : '';
  const errorMessage = form.dataset.submitError;
  const networkErrorMessage = form.dataset.networkError;
  let previousFocus = null;

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

  const setRatingHint = () => {
    if (!ratingHint) {
      return;
    }
    const selected = Array.from(ratingInputs || []).find(input => input.checked);
    const ratingValue = selected ? Number(selected.value) : 0;
    ratingHint.textContent = ratingMessages[ratingValue] || ratingMessages[0] || '';
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
      input.addEventListener('change', setRatingHint);
    });
    setRatingHint();
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
