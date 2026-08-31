document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.copy-color-button').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var target = document.getElementById(btn.dataset.target);
      if (target) {
        navigator.clipboard.writeText(target.value);
      }
    });
  });
});
