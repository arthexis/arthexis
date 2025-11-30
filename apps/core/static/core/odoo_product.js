document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('select[data-odoo-products-url]').forEach(function (sel) {
    fetch(sel.dataset.odooProductsUrl)
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        var current = sel.dataset.currentId;
        var blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '---------';
        sel.appendChild(blank);
        data.forEach(function (item) {
          var opt = document.createElement('option');
          opt.value = JSON.stringify(item);
          opt.textContent = item.name;
          if (current && String(item.id) === current) {
            opt.selected = true;
          }
          sel.appendChild(opt);
        });
      });
  });
});
