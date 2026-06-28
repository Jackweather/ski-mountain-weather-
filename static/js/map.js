mapboxgl.accessToken = MAPBOX_TOKEN;

// Gore Mountain coordinates (decimal): 43°40'20"N 74°00'25"W
const goreCoords = [-74.0069444, 43.6722222];

const map = new mapboxgl.Map({
  container: 'map',
  style: 'mapbox://styles/mapbox/dark-v10',
  center: goreCoords,
  zoom: 10
});

map.on('load', () => {
  document.getElementById('zoom').textContent = map.getZoom().toFixed(2);

  // Add a custom text label for Gore Mountain
  const labelEl = document.createElement('div');
  labelEl.className = 'label-marker';
  labelEl.textContent = 'Gore';

  // Use a Marker with a custom element so it stays synced with the map
  new mapboxgl.Marker(labelEl)
    .setLngLat(goreCoords)
    .addTo(map);

  // Click handler to fetch and show full weather panel
  labelEl.style.cursor = 'pointer';
  labelEl.addEventListener('click', (e) => {
    e.stopPropagation();
    fetch('/api/gore')
      .then(r => r.json())
      .then(resp => {
        if (resp.error) {
          const widget = document.getElementById('weather-widget');
          const body = document.getElementById('weather-body');
          body.innerHTML = `<div class="err">${resp.error}</div>`;
          widget.classList.remove('hidden');
          return;
        }
        if (window.showDetailedWeather) {
          window.showDetailedWeather(resp);
        } else {
          const widget = document.getElementById('weather-widget');
          const body = document.getElementById('weather-body');
          body.innerHTML = '<div>No detailed panel available.</div>';
          widget.classList.remove('hidden');
        }
      }).catch(err => {
        const body = document.getElementById('weather-body');
        body.innerHTML = `<div class="err">${err}</div>`;
        document.getElementById('weather-widget').classList.remove('hidden');
      });
  });

  // close button for small widget
  const closeBtn = document.getElementById('weather-close');
  if (closeBtn) closeBtn.addEventListener('click', () => {
    document.getElementById('weather-widget').classList.add('hidden');
  });
});

map.on('zoom', () => {
  document.getElementById('zoom').textContent = map.getZoom().toFixed(2);
});
