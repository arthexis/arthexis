(function () {
  function getSelectedIds() {
    return Array.from(
      document.querySelectorAll('#changelist-form input.action-select:checked')
    )
      .map((input) => input.value)
      .filter(Boolean);
  }

  function attachHandlers() {
    var links = document.querySelectorAll('[data-related-filter-lookups]');
    links.forEach(function (link) {
      link.addEventListener('click', function () {
        var selectedIds = getSelectedIds();
        if (!selectedIds.length) {
          return;
        }

        var lookups = link.dataset.relatedFilterLookups;
        if (!lookups) {
          return;
        }

        var sourceModel = link.dataset.relatedSourceModel || '';
        var url = new URL(link.href, window.location.origin);
        url.searchParams.set('__selected_ids', selectedIds.join(','));
        url.searchParams.set('__relation_lookups', lookups);
        if (sourceModel) {
          url.searchParams.set('__source_model', sourceModel);
        }
        link.href = url.toString();
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachHandlers);
    return;
  }
  attachHandlers();
})();
