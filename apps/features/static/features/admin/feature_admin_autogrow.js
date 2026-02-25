(function () {
  function resize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  function initializeTextarea(textarea) {
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }
    if (textarea.dataset.featureAdminAutogrowInitialized === 'true') {
      return;
    }

    textarea.dataset.featureAdminAutogrowInitialized = 'true';
    textarea.setAttribute('rows', '1');
    textarea.style.overflowY = 'hidden';
    resize(textarea);

    textarea.addEventListener('input', function () {
      resize(textarea);
    });
  }

  function initializeIn(container) {
    if (!(container instanceof Element || container instanceof Document)) {
      return;
    }

    if (
      container instanceof HTMLTextAreaElement &&
      container.matches('textarea.feature-admin-autogrow')
    ) {
      initializeTextarea(container);
    }

    container
      .querySelectorAll('textarea.feature-admin-autogrow')
      .forEach(initializeTextarea);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initializeIn(document);
  });

  document.addEventListener('formset:added', function (event) {
    initializeIn(event.target);
  });
})();
