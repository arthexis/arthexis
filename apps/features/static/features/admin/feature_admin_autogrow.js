(function () {
  function resize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight}px`;
  }

  function initializeTextarea(textarea) {
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }
    textarea.setAttribute('rows', '1');
    textarea.style.overflowY = 'hidden';
    resize(textarea);
    textarea.addEventListener('input', function () {
      resize(textarea);
    });
  }

  function initialize() {
    document
      .querySelectorAll('textarea.feature-admin-autogrow')
      .forEach(initializeTextarea);
  }

  document.addEventListener('DOMContentLoaded', initialize);
})();
