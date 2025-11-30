(function($) {
  $(function() {
    var $lat = $('#id_latitude');
    var $lng = $('#id_longitude');
    if (!$lat.length || !$lng.length) {
      return;
    }

    var $mapDiv = $('#charger-map');
    if (!$mapDiv.length) {
      $mapDiv = $('<div id="charger-map" style="height: 400px;" class="mb-3"></div>');
      var $row = $lng.closest('.form-row');
      $row.after($mapDiv);
    }

    var startLat = parseFloat($lat.val()) || 25.6866;
    var startLng = parseFloat($lng.val()) || -100.3161;

    var map = L.map('charger-map').setView([startLat, startLng], 13);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    var marker = L.marker([startLat, startLng], {draggable: true}).addTo(map);

    function updateInputs(lat, lng) {
      $lat.val(lat.toFixed(6));
      $lng.val(lng.toFixed(6));
    }

    marker.on('dragend', function(e) {
      var pos = e.target.getLatLng();
      updateInputs(pos.lat, pos.lng);
    });

    map.on('click', function(e) {
      marker.setLatLng(e.latlng);
      updateInputs(e.latlng.lat, e.latlng.lng);
    });
  });
})(django.jQuery);
