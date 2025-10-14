(function () {
  function getCookie(name) {
    if (!document.cookie) {
      return null;
    }
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i += 1) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith(name + '=')) {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return null;
  }

  function setStatus(element, message, level) {
    if (!element) {
      return;
    }
    element.textContent = message || '';
    element.classList.remove('success', 'error');
    if (level) {
      element.classList.add(level);
    }
  }

  function setBusy(button, busy, savingLabel, defaultLabel) {
    if (!button) {
      return;
    }
    if (busy) {
      button.disabled = true;
      if (savingLabel) {
        button.textContent = savingLabel;
      }
      button.classList.add('is-busy');
    } else {
      button.disabled = false;
      if (defaultLabel) {
        button.textContent = defaultLabel;
      }
      button.classList.remove('is-busy');
    }
  }

  function buildSnapshot(iframe) {
    if (!iframe) {
      return Promise.reject(new Error('frame-unavailable'));
    }
    let doc;
    try {
      if (!iframe.contentWindow) {
        return Promise.reject(new Error('frame-unavailable'));
      }
      doc = iframe.contentDocument;
    } catch (error) {
      return Promise.reject(new Error('frame-unavailable'));
    }
    if (!doc) {
      return Promise.reject(new Error('frame-unavailable'));
    }
    if (typeof window.html2canvas !== 'function') {
      return Promise.reject(new Error('canvas-missing'));
    }

    const target = doc.documentElement;
    const width = Math.max(target.scrollWidth, target.clientWidth, iframe.clientWidth);
    const height = Math.max(target.scrollHeight, target.clientHeight, iframe.clientHeight);

    return window.html2canvas(target, {
      backgroundColor: '#ffffff',
      logging: false,
      scale: 1,
      windowWidth: width,
      windowHeight: height,
      scrollX: 0,
      scrollY: 0,
    }).then(function (canvas) {
      return canvas.toDataURL('image/png');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    const button = document.getElementById('todo-snapshot-btn');
    if (!button) {
      return;
    }
    const iframe = document.querySelector('iframe.todo-frame');
    const status = document.getElementById('todo-snapshot-status');
    const snapshotUrl = button.dataset.snapshotUrl;
    const savingLabel = button.dataset.labelSaving || '';
    const defaultLabel = button.dataset.labelDefault || button.textContent;
    const successMessage = button.dataset.messageSuccess || '';
    const frameError = button.dataset.errorUnavailable || '';
    const captureError = button.dataset.errorCapture || '';
    const uploadError = button.dataset.errorUpload || '';

    button.addEventListener('click', function (event) {
      event.preventDefault();
      if (!snapshotUrl) {
        setStatus(status, frameError, 'error');
        return;
      }
      setBusy(button, true, savingLabel, defaultLabel);
      setStatus(status, '', null);

      buildSnapshot(iframe)
        .then(function (dataUrl) {
          if (!dataUrl) {
            throw new Error('capture');
          }
          const payload = { image: dataUrl };
          const headers = { 'Content-Type': 'application/json' };
          const csrfToken = getCookie('csrftoken');
          if (csrfToken) {
            headers['X-CSRFToken'] = csrfToken;
          }
          return fetch(snapshotUrl, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload),
            credentials: 'same-origin',
          });
        })
        .then(function (response) {
          if (!response) {
            throw new Error('upload');
          }
          return response.json().then(function (body) {
            if (!response.ok) {
              const error = body && body.detail ? body.detail : uploadError;
              const err = new Error(error || 'upload');
              err._fromResponse = true;
              throw err;
            }
            return body;
          });
        })
        .then(function (body) {
          const message = (body && body.detail) || successMessage;
          setStatus(status, message, 'success');
        })
        .catch(function (error) {
          if (error && error.message === 'frame-unavailable') {
            setStatus(status, frameError, 'error');
          } else if (error && error.message === 'canvas-missing') {
            setStatus(status, captureError, 'error');
          } else if (error && error._fromResponse) {
            setStatus(status, error.message, 'error');
          } else if (error && error.message === 'capture') {
            setStatus(status, captureError, 'error');
          } else {
            setStatus(status, uploadError, 'error');
          }
        })
        .finally(function () {
          setBusy(button, false, savingLabel, defaultLabel);
        });
    });
  });
})();
