// Renders a large weather panel occupying 75% of the viewport
(function(){
  function createPanel() {
    let panel = document.getElementById('weather-panel-full');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'weather-panel-full';
    panel.className = 'weather-panel hidden';
    panel.innerHTML = `
      <button id="weather-panel-close">×</button>
      <div id="weather-panel-content"><h3>Loading...</h3></div>
    `;
    document.body.appendChild(panel);
    document.getElementById('weather-panel-close').addEventListener('click', () => {
      panel.classList.add('hidden');
    });
    return panel;
  }

  function formatSection(title, html) {
    return `<section class="wp-section"><h4>${title}</h4>${html}</section>`;
  }

  function buildTables(resp) {
    const payload = resp.payload || resp;
    const cloud = resp.cloud || null;
    const wind = resp.wind || null;
    const snow = resp.snow || null;
    const vis = resp.vis || null;
    const wind925 = resp.wind925 || null;

    const recs = (payload && payload.records) ? payload.records : [];
    const cloudMap = new Map(); if (cloud && cloud.records) for (const c of cloud.records) cloudMap.set(String(c.forecast_hour), c);
    const windMap = new Map(); if (wind && wind.records) for (const w of wind.records) windMap.set(String(w.forecast_hour), w);
    const snowMap = new Map(); if (snow && snow.records) for (const s of snow.records) snowMap.set(String(s.forecast_hour), s);
    const visMap = new Map(); if (vis && vis.records) for (const v of vis.records) visMap.set(String(v.forecast_hour), v);
    const wind925Map = new Map(); if (wind925 && wind925.records) for (const w of wind925.records) wind925Map.set(String(w.forecast_hour), w);

    let rowsHtml = '<table class="wtable"><tr><th>Hour</th><th>Temp (°F)</th><th>Cloud</th><th>Precip</th><th>Surface Wind</th><th>Snow (in)</th><th>Vis (mi)</th><th>Summit Wind</th><th>Valid</th></tr>';
    const rows = recs.length ? recs : (cloud && cloud.records ? cloud.records : []);
    for (const r of rows) {
      const fhRaw = r.forecast_hour;
      const fh = (fhRaw === undefined || fhRaw === null) ? NaN : (typeof fhRaw === 'number' ? fhRaw : (isNaN(Number(fhRaw)) ? NaN : Number(fhRaw)));
      const tempCell = r.temp_f !== undefined ? r.temp_f : '';
      // Try to find cloud/wind records by matching the raw forecast_hour string first,
      // then falling back to numeric string form.
      const key = String(r.forecast_hour);
      let cloudRec = cloudMap.get(key);
      if (!cloudRec) cloudRec = cloudMap.get(String(Number(key)));
      let cloudCell = '';
      if (cloudRec) cloudCell = `${cloudRec.symbol} ${cloudRec.category}`; // omit percent from webpage
      let precipPct = '';
      let precipCat = '';
      if (r.precip_percent !== undefined && r.precip_percent !== null) {
        precipPct = Number(r.precip_percent).toFixed(1);
        precipCat = r.precip_category || '';
      } else if (cloudRec && cloudRec.precip_percent !== undefined) {
        precipPct = Number(cloudRec.precip_percent).toFixed(1);
        precipCat = cloudRec.precip_category || '';
      }
      let precipCell = '';
      if (precipPct !== '') precipCell += `${precipPct}% `;
      if (precipCat) precipCell += `(${precipCat})`;

      let windCell = '';
      let windRec = null;
      if (r.wind_speed_mph !== undefined && r.wind_speed_mph !== null) windRec = r;
      else {
        windRec = windMap.get(key) || windMap.get(String(Number(key)));
      }
      if (windRec && windRec.wind_speed_mph !== undefined && windRec.wind_speed_mph !== null) {
        windCell = `${windRec.wind_dir || ''} ${Number(windRec.wind_speed_mph).toFixed(1)} mph`;
      }

      const valid = r.valid_time || (cloudRec && cloudRec.valid_time) || (windRec && windRec.valid_time) || '';
      // Snow: prefer sde_in on the main record, else lookup in snowMap
      let snowCell = '';
      let snowRec = null;
      if (r.sde_in !== undefined && r.sde_in !== null) snowRec = r;
      else snowRec = snowMap.get(key) || snowMap.get(String(Number(key)));
      if (snowRec && snowRec.sde_in !== undefined && snowRec.sde_in !== null) {
        snowCell = `${Number(snowRec.sde_in).toFixed(2)} in`;
      }

      // Visibility: prefer vis_mi on main record, else lookup in visMap
      let visCell = '';
      let visRec = null;
      if (r.vis_mi !== undefined && r.vis_mi !== null) visRec = r;
      else visRec = visMap.get(key) || visMap.get(String(Number(key)));
      if (visRec && visRec.vis_mi !== undefined && visRec.vis_mi !== null) {
        const desc = visRec.vis_desc ? ` — ${visRec.vis_desc}` : '';
        visCell = `${Number(visRec.vis_mi).toFixed(2)} mi${desc}`;
      }
      // Summit wind lookup (925 mb)
      let summitCell = '';
      let summitRec = wind925Map.get(key) || wind925Map.get(String(Number(key)));
      if (r.wind925_speed_mph !== undefined && r.wind925_speed_mph !== null) summitRec = r;
      if (summitRec && summitRec.wind925_speed_mph !== undefined && summitRec.wind925_speed_mph !== null) {
        summitCell = `${Number(summitRec.wind925_speed_mph).toFixed(1)} mph ${summitRec.wind925_dir || ''}`.trim();
      }

      // display hour without leading 'f'; if forecast_hour is an AM/PM label, show it directly
      let displayHour = '';
      if (!isNaN(fh)) displayHour = ('0'+fh).slice(-2);
      else displayHour = key;
      rowsHtml += `<tr><td>${displayHour}</td><td>${tempCell}</td><td>${cloudCell}</td><td>${precipCell}</td><td>${windCell}</td><td>${snowCell}</td><td>${visCell}</td><td>${summitCell}</td><td>${valid}</td></tr>`;
    }
    rowsHtml += '</table>';

    return rowsHtml;
  }

  function showDetailedWeather(resp) {
    const panel = createPanel();
    const content = document.getElementById('weather-panel-content');
    if (!content) return;
    const payload = resp.payload || resp;
    let html = '';
    html += `<div class="wp-header"><h2>Gore Mountain — ${payload && payload.model_run ? payload.model_run : ''}</h2></div>`;
    html += buildTables(resp);
    content.innerHTML = html;
    // show panel and size to 75% of viewport
    panel.classList.remove('hidden');
  }

  // expose globally for map.js to call
  window.showDetailedWeather = showDetailedWeather;

})();
