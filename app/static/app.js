/**
 * app.js - Sky Cowboy
 * Sam Elliott Backcountry Weather Briefing Client
 */

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('searchForm');
  const input = document.getElementById('stationInput');
  const searchBtn = document.getElementById('searchBtn');
  const reportsList = document.getElementById('reportsList');
  const loadingIndicator = document.getElementById('loadingIndicator');
  const errorMessage = document.getElementById('errorMessage');

  async function fetchWeather(stations) {
    const query = (stations || '').trim();
    if (!query) return;

    // Reset UI state
    loadingIndicator.classList.remove('hidden');
    errorMessage.classList.add('hidden');
    reportsList.innerHTML = '';
    searchBtn.disabled = true;

    try {
      const response = await fetch(`/api/metar?ids=${encodeURIComponent(query)}`);
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `Server responded with status ${response.status}`);
      }

      const payload = await response.json();
      const records = payload.data || [];

      if (records.length === 0) {
        reportsList.innerHTML = `
          <div class="empty-state">
            <p>Ain't no observations coming back for <strong>"${escapeHtml(query)}"</strong>.</p>
            <p style="font-size: 0.85rem; color: #705e4f; margin-top: 0.5rem;">
              Make sure you're using the 4-letter ICAO identifier (e.g. KLXV, KJAC, KMYL).
            </p>
          </div>
        `;
        return;
      }

      reportsList.innerHTML = records.map(renderCowboyCard).join('');
      attachTapeCopyHandlers();
    } catch (err) {
      errorMessage.textContent = `Wire Error: ${err.message}`;
      errorMessage.classList.remove('hidden');
    } finally {
      loadingIndicator.classList.add('hidden');
      searchBtn.disabled = false;
    }
  }

  function renderCowboyCard(r) {
    const cat = (r.flight_category || 'VFR').toUpperCase();
    const catLower = cat.toLowerCase();

    // Winds
    const windVal = r.wspd_kt !== null 
      ? `${r.wspd_kt} KT ${r.wdir_cardinal || ''}` 
      : 'CALM';
    const windSub = r.wdir !== null 
      ? `${r.wdir}°${r.wgst_kt ? ' GUST ' + r.wgst_kt + ' KT' : ''}` 
      : 'No wind';

    // Visibility
    const visVal = r.visib_display || 'N/A';
    const visSub = (r.visib_sm !== null && r.visib_sm >= 10) ? 'Unrestricted' : 'Reduced';

    // Ceiling
    const ceilVal = r.ceiling_ft !== null ? `${r.ceiling_ft.toLocaleString()}' AGL` : 'UNLIMITED';
    const ceilSub = r.cover || (r.ceiling_ft !== null ? 'Ceiling Layer' : 'Clear Sky');

    // Temperature & Dewpoint
    const tempVal = r.temp_f !== null ? `${Math.round(r.temp_f)}°F` : '--';
    const tempSub = r.temp_c !== null ? `${r.temp_c}°C (Dew ${r.dewp_f !== null ? Math.round(r.dewp_f) + '°F' : '--'})` : '';

    // Altimeter
    const altimVal = r.altim_inhg ? `${r.altim_inhg.toFixed(2)}"` : '--';
    const altimSub = r.altim_hpa ? `${r.altim_hpa.toFixed(0)} hPa` : 'inHg';

    // Timestamps & stale flag
    const ageText = r.age_minutes !== null ? `${r.age_minutes}m old` : 'Fresh';
    const staleStamp = r.is_stale ? `<span class="stale-stamp">⚠️ STALE (${r.age_minutes}m)</span>` : '';

    // Strip "Sam's Take:" from headline
    let rawHeadline = r.headline || r.sam_headline || '';
    let headline = rawHeadline.replace(/^Sam's Take:\s*/i, '').trim();
    if (!headline) headline = `${cat} Flight Conditions`;

    let quote = r.briefing || r.sam_briefing || r.plain_english || 'Weather transmission parsed.';
    quote = quote.replace(/altimeter dial is dialed into/gi, 'altimeter sits at');

    return `
      <article class="cowboy-card ${catLower}-card" id="airfield-${escapeHtml(r.icao)}">
        <!-- Airfield Header Row -->
        <div class="card-top-row">
          <div class="station-brand">
            <span class="icao-brand">${escapeHtml(r.icao)}</span>
            <span class="name-brand">${escapeHtml(r.name)}</span>
          </div>
          <span class="patch-stamp ${catLower}">${cat}</span>
        </div>

        <!-- Briefing Headline & Natural Narrative -->
        <div class="card-briefing">
          <h2 class="briefing-headline">${escapeHtml(headline)}</h2>
          <p class="briefing-quote">${escapeHtml(quote)}</p>
        </div>

        <!-- Open Instruments Stat Row (No Heavy Grid Boxes) -->
        <div class="stats-row">
          <div class="stat-item">
            <span class="stat-label">WIND</span>
            <span class="stat-val">${windVal}</span>
            <span class="stat-sub">${windSub}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">VISIBILITY</span>
            <span class="stat-val">${visVal}</span>
            <span class="stat-sub">${visSub}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">CEILING</span>
            <span class="stat-val">${ceilVal}</span>
            <span class="stat-sub">${ceilSub}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">TEMP / DEW</span>
            <span class="stat-val">${tempVal}</span>
            <span class="stat-sub">${tempSub}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">ALTIMETER</span>
            <span class="stat-val">${altimVal}</span>
            <span class="stat-sub">${altimSub}</span>
          </div>
        </div>

        <!-- Raw Tape Row -->
        <div class="tape-row">
          <code class="tape-code">${escapeHtml(r.raw_metar || '')}</code>
          <button type="button" class="tape-copy-btn" data-tape="${escapeHtml(r.raw_metar || '')}">COPY</button>
        </div>

        <!-- Footer Meta Row -->
        <div class="card-footer">
          <span>Observed: ${ageText} ${staleStamp}</span>
          <span>Field Elev: ${r.elev_ft !== null ? r.elev_ft.toLocaleString() + ' FT' : '--'}</span>
        </div>
      </article>
    `;
  }

  function attachTapeCopyHandlers() {
    document.querySelectorAll('.tape-copy-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const text = btn.getAttribute('data-tape');
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          const orig = btn.textContent;
          btn.textContent = 'COPIED!';
          btn.style.background = '#bd4d32';
          btn.style.color = '#ffffff';
          setTimeout(() => {
            btn.textContent = orig;
            btn.style.background = '';
            btn.style.color = '';
          }, 1500);
        } catch {
          // Clipboard fallback
        }
      });
    });
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Quick Backcountry Chips
  document.querySelectorAll('.airfield-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const code = chip.getAttribute('data-code');
      if (code) {
        input.value = code;
        fetchWeather(code);
      }
    });
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    fetchWeather(input.value);
  });

  // Initial load
  fetchWeather(input.value);
});
