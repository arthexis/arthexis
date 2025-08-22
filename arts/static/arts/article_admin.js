document.addEventListener('DOMContentLoaded', function() {
  var textarea = document.getElementById('id_content');
  if (textarea && window.EasyMDE) {
    new EasyMDE({ element: textarea });
  }
});
