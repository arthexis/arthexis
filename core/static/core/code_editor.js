// Simple code editor enhancements for textareas with class 'code-editor'
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('textarea.code-editor').forEach(function (el) {
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        var start = this.selectionStart;
        var end = this.selectionEnd;
        this.value = this.value.substring(0, start) + '    ' + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + 4;
      }
    });
  });
});
