"use strict";

// ── State ──────────────────────────────────────────────────────────────────
const _votedPlaces  = {};   // placeId → +1 | -1
const _placeData    = {};   // placeId → {id,name,address,likes,dislikes,total_score,...}
let   _roomStatus   = typeof ROOM_STATUS   !== "undefined" ? ROOM_STATUS   : "waiting";
let   _votesUsed    = typeof VOTES_USED    !== "undefined" ? VOTES_USED    : 0;
const _maxVotes     = typeof MAX_VOTES     !== "undefined" ? MAX_VOTES     : 3;
const _roomCategory = typeof ROOM_CATEGORY !== "undefined" ? ROOM_CATEGORY : "food";
let   _activeFilter  = "all";   // aktif alt kategori filtresi
let   _searchQuery   = "";      // mekan arama kutusu
let   _resultsShown  = ROOM_STATUS === "completed";

// Alt kategori filtre tanımları
const _FILTERS = {
  food: [
    { key: "all",        label: "Tümü",      tags: null },
    { key: "restaurant", label: "Restoran",  tags: ["restaurant", "canteen", "diner", "bbq"] },
    { key: "cafe",       label: "Kafe",      tags: ["cafe", "juice_bar", "biergarten"] },
    { key: "fast_food",  label: "Fast Food", tags: ["fast_food", "bar", "pub", "food_court"] },
    { key: "pastane",    label: "Pastane",   tags: ["bakery", "ice_cream"] },
  ],
  activity: [
    { key: "all",     label: "Tümü",         tags: null },
    { key: "park",    label: "Park",          tags: ["park"] },
    { key: "cinema",  label: "Sinema",        tags: ["cinema", "theatre"] },
    { key: "arcade",  label: "Oyun Salonu",   tags: ["amusement_arcade", "bowling_alley", "escape_game"] },
    { key: "sports",  label: "Spor Tesisi",   tags: ["fitness_centre", "sports_centre", "swimming_pool", "stadium"] },
  ],
};

// ── Socket.IO ──────────────────────────────────────────────────────────────
const socket = io();

if (typeof ROOM_CODE !== "undefined") {
  socket.emit("join_room_ws", { code: ROOM_CODE });

  socket.on("vote_update", ({ summary, participants }) => {
    if (summary)      { renderSummary(summary); updateMarkersFromSummary(summary); }
    if (participants) updateParticipantsList(participants);
  });

  socket.on("participants_update", ({ participants }) => {
    if (participants) updateParticipantsList(participants);
  });

  socket.on("places_loaded", ({ places, lat, lng, actual_radius }) => {
    initMap(lat, lng);
    renderPlaces(places);
    addPlaceMarkers(places);
    setBadge("Oylama", "voting");
    _ensureVotingHeader();
    document.getElementById("no-places-msg")?.remove();
    const km = actual_radius ? ((actual_radius / 1000).toLocaleString("tr-TR", { maximumFractionDigits: 0 })) : "?";
    showToast(`${km} km çevresinde ${places.length} mekan bulundu — oy ver!`);
  });

  socket.on("show_results", ({ winner, summary, participants, score_log }) => {
    if (_resultsShown) return;
    _resultsShown = true;
    _showResults(winner, summary ?? [], participants ?? [], score_log ?? []);
  });

  // Seed client-side state from server-rendered data
  if (typeof MY_VOTES !== "undefined") {
    Object.entries(MY_VOTES).forEach(([pid, val]) => { _votedPlaces[parseInt(pid)] = val; });
  }
  if (typeof INITIAL_PLACES !== "undefined") {
    INITIAL_PLACES.forEach(p => { _placeData[p.id] = p; });
  }

  if (ROOM_LAT !== null && ROOM_LNG !== null) {
    initMap(ROOM_LAT, ROOM_LNG);
    if (INITIAL_PLACES && INITIAL_PLACES.length) addPlaceMarkers(INITIAL_PLACES);
  }

  // İlk yüklemede (voting/completed durumunda) filtre ve sayacı başlat
  if (INITIAL_PLACES && INITIAL_PLACES.length) {
    initFilterBar();
    _updateVoteCounter();
    if (_votesUsed >= _maxVotes) _disableAllUnvotedButtons();
  }

  if (ROOM_STATUS !== "completed") {
    setInterval(refreshStatus, 10000);
    setInterval(pollResults, 3000);
  }
}

// ── Location ───────────────────────────────────────────────────────────────
function sendLocation() {
  const btn    = document.getElementById("btn-location");
  const status = document.getElementById("location-status");

  if (!navigator.geolocation) {
    showInlineError(status, "Tarayicin konum desteklemiyor.");
    return;
  }

  setLoading(btn, true);
  status.textContent = "Konum aliniyor...";
  status.className   = "hint";

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const { latitude: lat, longitude: lng } = pos.coords;
      status.textContent = "Mekanlar aranıyor...";
      initMap(lat, lng);
      showSkeletons(6);

      try {
        const radiusInput = document.querySelector('input[name="radius"]:checked');
        const radius = radiusInput ? parseInt(radiusInput.value, 10) : 2000;
        const res = await apiFetch(`/api/room/${ROOM_CODE}/search`, "POST", { lat, lng, radius });
        renderPlaces(res.places);
        addPlaceMarkers(res.places);
        setBadge("Oylama", "voting");
        _ensureVotingHeader();
        document.getElementById("no-places-msg")?.remove();
        const actualKm = ((res.actual_radius ?? radius) / 1000).toLocaleString("tr-TR", { maximumFractionDigits: 0 });
        status.textContent = `${actualKm} km çevresinde ${res.places.length} mekan bulundu.`;
        if (res.places.length === 0) showInlineError(status, "Civarda mekan bulunamadi. Yaricapi genisl etmeyi deneyin.");
      } catch (e) {
        clearSkeletons();
        showInlineError(status, "Hata: " + e.message);
        setLoading(btn, false);
      }
    },
    (err) => {
      status.textContent = "";
      showInlineError(status, "Konum alinamadi: " + err.message);
      setLoading(btn, false);
    },
    { timeout: 10000 }
  );
}

// ── Voting ─────────────────────────────────────────────────────────────────
async function castVote(placeId, value) {
  if (_votesUsed >= _maxVotes) {
    showToast(`Maksimum ${_maxVotes} oy hakkını kullandınız.`);
    return;
  }

  _votedPlaces[placeId] = value;  // optimistic
  _votesUsed++;
  _updateVoteCounter();

  const card = document.getElementById(`place-${placeId}`);
  card?.querySelectorAll(".btn--vote").forEach(b => (b.disabled = true));
  card?.querySelector(value === 1 ? ".btn--like" : ".btn--dislike")?.classList.add("active");
  _refreshMarkerPopup(placeId);

  if (_votesUsed >= _maxVotes) _disableAllUnvotedButtons();

  try {
    await apiFetch(`/api/room/${ROOM_CODE}/vote`, "POST", { place_id: placeId, value });
  } catch (e) {
    delete _votedPlaces[placeId];  // rollback
    _votesUsed--;
    _updateVoteCounter();
    card?.querySelectorAll(".btn--vote").forEach(b => {
      if (!b.classList.contains("active")) b.disabled = false;
    });
    card?.querySelector(value === 1 ? ".btn--like" : ".btn--dislike")?.classList.remove("active");
    _refreshMarkerPopup(placeId);
    showToast("Hata: " + e.message);
  }
}

async function finishVoting() {
  if (!confirm("Oylamayı bitirmek istediğinizden emin misiniz?")) return;
  const btn = document.getElementById("btn-finish");
  setLoading(btn, true);
  try {
    const res = await apiFetch(`/api/room/${ROOM_CODE}/finish`, "POST", {});
    if (!_resultsShown) {
      _resultsShown = true;
      _showResults(res.winner, res.summary ?? [], res.participants ?? [], res.score_log ?? []);
    }
  } catch (e) {
    setLoading(btn, false);
    showToast("Hata: " + e.message);
  }
}

// ── Mobile side panel toggle ───────────────────────────────────────────────
function toggleSidePanel() {
  const btn  = document.getElementById("panel-toggle");
  const body = document.getElementById("side-panel-body");
  const open = btn.getAttribute("aria-expanded") === "true";
  btn.setAttribute("aria-expanded", open ? "false" : "true");
  // CSS handles show/hide via aria-expanded selector
}

// ── Render helpers ─────────────────────────────────────────────────────────
function renderPlaces(places) {
  clearSkeletons();
  const container = document.getElementById("places-list");
  if (!container) return;
  document.getElementById("no-places-msg")?.remove();
  document.getElementById("waiting-state")?.remove();
  const header = document.getElementById("places-header");
  if (header) header.style.display = "";

  places.forEach(p => { _placeData[p.id] = p; });

  // Arama kutusunu sıfırla
  const searchInput = document.getElementById("place-search");
  if (searchInput) { searchInput.value = ""; _searchQuery = ""; }

  // Stagger entrance animation delay
  const html = places.map((p, i) => {
    const voted   = _votedPlaces[p.id];
    const limited = _votesUsed >= _maxVotes;
    const likeDis  = (voted !== undefined || limited) ? "disabled" : "";
    const likeCls  = voted === 1  ? "active" : "";
    const disDis   = (voted !== undefined || limited) ? "disabled" : "";
    const disCls   = voted === -1 ? "active" : "";
    return `
    <div class="place-card place-card--enter" id="place-${p.id}"
         style="animation-delay:${i * 40}ms"
         data-lat="${p.lat ?? ""}" data-lng="${p.lng ?? ""}"
         data-category="${esc(p.category ?? "")}">
      <div class="place-info">
        <h4 class="place-name-link"
            onclick="panToPlace(${p.id}, ${p.lat ?? null}, ${p.lng ?? null})">
          ${esc(p.name)}
        </h4>
        <p class="place-address">${esc(p.address)}</p>
        <div class="place-score">
          <span class="likes">👍 ${p.likes ?? 0}</span>
          <span class="dislikes">👎 ${p.dislikes ?? 0}</span>
          <span class="net">Net: ${p.total_score ?? 0}</span>
        </div>
      </div>
      <div class="vote-actions">
        <button class="btn btn--vote btn--like ${likeCls}"    ${likeDis} onclick="castVote(${p.id},  1)">👍</button>
        <button class="btn btn--vote btn--dislike ${disCls}" ${disDis} onclick="castVote(${p.id}, -1)">👎</button>
      </div>
    </div>`;
  }).join("");

  container.innerHTML = html;

  const counter = document.querySelector(".place-count");
  if (counter) counter.textContent = `(${places.length})`;

  initFilterBar();
  _updateVoteCounter();
  _applyCurrentFilter();
}

function renderSummary(summary) {
  // Skorları güncelle
  summary.forEach(p => {
    if (_placeData[p.id]) Object.assign(_placeData[p.id], p);
    const card = document.getElementById(`place-${p.id}`);
    if (!card) return;
    const scoreEl = card.querySelector(".place-score");
    if (scoreEl) {
      scoreEl.innerHTML = `
        <span class="likes">👍 ${p.likes}</span>
        <span class="dislikes">👎 ${p.dislikes}</span>
        <span class="net">Net: ${p.total_score}</span>
      `;
    }
  });

  // FLIP animasyonlu sıralama
  const container = document.getElementById("places-list");
  if (!container) return;

  const cards    = [...container.children].filter(c => c.classList.contains("place-card"));
  const scoreMap = Object.fromEntries(summary.map(p => [p.id, p.total_score]));

  // First: pozisyonları kaydet
  const firsts = new Map(cards.map(c => [c.id, c.getBoundingClientRect().top]));

  // Sırala
  cards.sort((a, b) => {
    const idA = parseInt(a.id.replace("place-", ""));
    const idB = parseInt(b.id.replace("place-", ""));
    return (scoreMap[idB] ?? 0) - (scoreMap[idA] ?? 0);
  });
  cards.forEach(c => container.appendChild(c));
  _applyCurrentFilter();

  // Invert + Play
  requestAnimationFrame(() => {
    cards.forEach(card => {
      const first = firsts.get(card.id);
      if (first === undefined) return;
      const last = card.getBoundingClientRect().top;
      const dy = first - last;
      if (Math.abs(dy) < 2) return;

      card.style.transition = "none";
      card.style.transform  = `translateY(${dy}px)`;
      requestAnimationFrame(() => {
        card.style.transition = "transform .4s cubic-bezier(.4,0,.2,1)";
        card.style.transform  = "";
        card.addEventListener("transitionend", () => {
          card.style.transition = "";
        }, { once: true });
      });
    });
  });
}

function updateParticipantsList(participants) {
  const list = document.getElementById("participants-list");
  if (!list) return;

  // Skor değişenler için anlık animasyon
  const prevScores = {};
  list.querySelectorAll("[data-pid]").forEach(el => {
    prevScores[el.dataset.pid] = el.textContent;
  });

  list.innerHTML = participants.map(p => `
    <li class="participant">
      <span class="p-name">${esc(p.nickname)}${p.is_owner ? ' <span class="owner-crown">👑</span>' : ""}</span>
      <span class="p-score" data-pid="${p.id}">${p.score} puan</span>
    </li>
  `).join("");

  // Değişen puanları vurgula
  list.querySelectorAll("[data-pid]").forEach(el => {
    if (prevScores[el.dataset.pid] && prevScores[el.dataset.pid] !== el.textContent) {
      el.classList.add("p-score--changed");
      el.addEventListener("animationend", () => el.classList.remove("p-score--changed"), { once: true });
    }
  });

  const counter = document.querySelector(".participant-count");
  if (counter) counter.textContent = `(${participants.length})`;
}

function showWinnerBanner(winner, summary = [], participants = []) {
  const existing = document.getElementById("result-card");
  if (existing) {
    existing.classList.add("card--winner-glow");
    existing.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  // Mekan sıralaması (2.–4. yer)
  const runners = summary.slice(1, 4);
  const placeRankingHtml = runners.length ? `
    <div class="result-section-title">Mekan Sıralaması</div>
    <div class="result-ranking">
      ${runners.map((p, i) => `
        <div class="rank-row">
          <span class="rank-num">${i + 2}</span>
          <span class="rank-name">${esc(p.name)}</span>
          <span class="rank-score">${p.total_score > 0 ? "+" : ""}${p.total_score} oy</span>
        </div>`).join("")}
    </div>` : "";

  // Katılımcı sıralaması (puana göre)
  const sortedParts = [...participants].sort((a, b) => b.score - a.score);
  const medals = ["🥇", "🥈", "🥉"];
  const partRankingHtml = sortedParts.length ? `
    <div class="result-section-title">Katilimci Siralamasi</div>
    <div class="result-ranking">
      ${sortedParts.map((p, i) => `
        <div class="rank-row">
          <span class="rank-num">${medals[i] ?? i + 1}</span>
          <span class="rank-name">${esc(p.nickname)}${p.is_owner ? " 👑" : ""}</span>
          <span class="rank-score">${p.score} puan</span>
        </div>`).join("")}
    </div>` : "";

  const banner = document.createElement("div");
  banner.id = "result-card";
  banner.className = "card card--winner card--winner-glow";
  banner.innerHTML = `
    <div class="result-header">
      <span class="result-trophy">🏆</span>
      <h2>Kazanan!</h2>
    </div>
    <div class="winner-place">
      <div class="winner-name">${esc(winner.name)}</div>
      <p class="place-address">${esc(winner.address ?? "")}</p>
      <p class="score-big">${winner.total_score > 0 ? "+" : ""}${winner.total_score} net oy</p>
    </div>
    ${placeRankingHtml}
    ${partRankingHtml}
  `;
  const main = document.querySelector(".panel--main");
  if (main) {
    main.prepend(banner);
    setTimeout(() => banner.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  }
}

function _showResults(winner, summary, participants, scoreLog) {
  setBadge("Tamamlandi", "completed");
  _hideVotingControls();
  if (summary && summary.length)      { renderSummary(summary); updateMarkersFromSummary(summary); }
  if (participants && participants.length) updateParticipantsList(participants);
  if (winner) { showWinnerBanner(winner, summary ?? [], participants ?? []); highlightWinnerMarker(winner.id); }
  if (scoreLog && scoreLog.length) showScoreLog(scoreLog);
  showToast("Oylama tamamlandi!");
}

async function pollResults() {
  if (_resultsShown) return;
  try {
    const res = await apiFetch(`/api/room/${ROOM_CODE}/results`, "GET");
    if (res.completed && !_resultsShown) {
      _resultsShown = true;
      _showResults(res.winner, res.summary ?? [], res.participants ?? [], []);
    }
  } catch (_) {}
}

async function refreshStatus() {
  try {
    const res = await apiFetch(`/api/room/${ROOM_CODE}/status`, "GET");
    if (res.participants) updateParticipantsList(res.participants);
  } catch (_) {}
}

// ── Voting header yönetimi ─────────────────────────────────────────────────
function _ensureVotingHeader() {
  const header = document.getElementById("places-header");
  if (header) header.style.display = "";
  const vr = document.getElementById("votes-remaining");
  if (vr) vr.style.display = "";
  if (typeof IS_OWNER !== "undefined" && IS_OWNER) {
    const bf = document.getElementById("btn-finish");
    if (bf) bf.style.display = "";
  }
  _updateVoteCounter();
}

function _hideVotingControls() {
  document.getElementById("votes-remaining")?.style.setProperty("display", "none");
  document.getElementById("btn-finish")?.style.setProperty("display", "none");
  document.querySelectorAll(".vote-actions").forEach(el => { el.style.display = "none"; });
}

// ── Oy sayacı ──────────────────────────────────────────────────────────────
function _updateVoteCounter() {
  const el = document.getElementById("votes-remaining");
  if (!el) return;
  const remaining = _maxVotes - _votesUsed;
  el.textContent = remaining > 0 ? `${remaining} oy hakkı kaldı` : "Oy hakkın bitti";
  el.className = "votes-remaining" +
    (remaining === 0 ? " votes-remaining--empty" : remaining === 1 ? " votes-remaining--low" : "");
}

function _disableAllUnvotedButtons() {
  document.querySelectorAll(".place-card").forEach(card => {
    const placeId = parseInt(card.id.replace("place-", ""));
    if (_votedPlaces[placeId] !== undefined) return;  // already voted for this place
    card.querySelectorAll(".btn--vote").forEach(b => (b.disabled = true));
  });
}

// ── Filtre ─────────────────────────────────────────────────────────────────
function initFilterBar() {
  const bar = document.getElementById("filter-bar");
  if (!bar) return;
  const filters = _FILTERS[_roomCategory] ?? _FILTERS.food;
  bar.innerHTML = filters.map(f => `
    <button class="filter-chip ${f.key === _activeFilter ? "filter-chip--active" : ""}"
            onclick="filterPlaces('${f.key}')">
      ${esc(f.label)}
    </button>
  `).join("");
}

function filterPlaces(filterKey) {
  _activeFilter = filterKey;

  // Chip aktifliğini güncelle
  document.querySelectorAll(".filter-chip").forEach(el => {
    el.classList.toggle("filter-chip--active", el.dataset.key === filterKey ||
      el.textContent.trim() === (_FILTERS[_roomCategory] ?? []).find(f => f.key === filterKey)?.label);
  });
  // Daha güvenilir: bar'ı yeniden render et
  initFilterBar();
  _applyCurrentFilter();
}

function searchPlaces(query) {
  _searchQuery = query.toLowerCase().trim();
  _applyCurrentFilter();
}

function _applyCurrentFilter() {
  const filters  = _FILTERS[_roomCategory] ?? _FILTERS.food;
  const filter   = filters.find(f => f.key === _activeFilter);
  const allowAll = !filter || !filter.tags;
  const allowed  = new Set(filter?.tags ?? []);

  let visible = 0;
  document.querySelectorAll(".place-card").forEach(card => {
    const cat  = card.dataset.category ?? "";
    const name = (card.querySelector(".place-name-link")?.textContent ?? "").toLowerCase();
    const matchesFilter = allowAll || allowed.has(cat);
    const matchesSearch = !_searchQuery || name.includes(_searchQuery);
    const show = matchesFilter && matchesSearch;
    card.style.display = show ? "" : "none";
    if (show) visible++;

    // Harita işaretçisini göster/gizle
    const placeId = parseInt(card.id.replace("place-", ""));
    const m = _markers[placeId];
    if (m) {
      if (show) { if (!_map.hasLayer(m)) m.addTo(_map); }
      else       { if (_map.hasLayer(m)) _map.removeLayer(m); }
    }
  });

  const counter = document.querySelector(".place-count");
  if (counter) counter.textContent = `(${visible})`;
}

// ── Puan tablosu ───────────────────────────────────────────────────────────
function showScoreLog(log) {
  document.getElementById("score-log-card")?.remove();

  const sorted = [...log].sort((a, b) => b.delta - a.delta);

  const card = document.createElement("div");
  card.id = "score-log-card";
  card.className = "card card--score-log";
  card.innerHTML = `
    <h3>Puan Degisiklikleri</h3>
    <ul class="score-log-list">
      ${sorted.map(entry => {
        const sign  = entry.delta >= 0 ? "+" : "";
        const cls   = entry.delta >= 0 ? "delta--pos" : "delta--neg";
        const items = entry.breakdown.map(b => `<li>${esc(b)}</li>`).join("");
        return `
          <li class="score-log-entry">
            <div class="sle-header">
              <span class="sle-nick">${esc(entry.nickname)}</span>
              <span class="sle-delta ${cls}">${sign}${entry.delta}</span>
            </div>
            <ul class="sle-breakdown">${items}</ul>
          </li>`;
      }).join("")}
    </ul>
  `;

  const winner = document.querySelector(".card--winner");
  winner
    ? winner.insertAdjacentElement("afterend", card)
    : document.querySelector(".panel--main")?.prepend(card);
}

// ── Invite copy ────────────────────────────────────────────────────────────
function copyInvite() {
  const input = document.getElementById("invite-link");
  const btn   = document.getElementById("btn-copy");
  input?.select();
  navigator.clipboard?.writeText(input.value).catch(() => document.execCommand("copy"));
  const orig = btn.textContent;
  btn.textContent = "Kopyalandi!";
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

// ── Toast ──────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("toast--show"));
  setTimeout(() => {
    t.classList.remove("toast--show");
    t.addEventListener("transitionend", () => t.remove(), { once: true });
  }, 3000);
}

// ── Skeleton loader ────────────────────────────────────────────────────────
function showSkeletons(count) {
  const container = document.getElementById("places-list");
  if (!container) return;
  container.innerHTML = Array.from({ length: count }, () => `
    <div class="skeleton-card">
      <div class="skeleton-line skeleton--title"></div>
      <div class="skeleton-line skeleton--subtitle"></div>
      <div class="skeleton-line skeleton--score"></div>
    </div>
  `).join("");
}

function clearSkeletons() {
  const container = document.getElementById("places-list");
  if (!container) return;
  container.querySelectorAll(".skeleton-card").forEach(el => el.remove());
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function setLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.dataset.orig = btn.innerHTML;
    btn.innerHTML    = "<span class='spinner'></span>";
    btn.disabled     = true;
  } else {
    btn.innerHTML = btn.dataset.orig || btn.innerHTML;
    btn.disabled  = false;
  }
}

function showInlineError(el, msg) {
  if (!el) { showToast(msg); return; }
  el.textContent = msg;
  el.className   = "error-msg";
  setTimeout(() => { el.textContent = ""; el.className = "hint"; }, 6000);
}

function setBadge(text, cls) {
  const badge = document.getElementById("room-status");
  if (!badge) return;
  badge.textContent = text;
  badge.className   = `badge badge--${cls}`;
  _roomStatus = cls;
}

// ── Map module ─────────────────────────────────────────────────────────────
let _map = null;
const _markers = {};

function initMap(lat, lng) {
  const el = document.getElementById("map");
  if (!el) return;
  el.classList.remove("map-hidden");

  if (_map) { _map.setView([lat, lng], 15); return; }

  _map = L.map("map", { zoomControl: true }).setView([lat, lng], 15);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OSM</a> © <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19,
  }).addTo(_map);

  L.circleMarker([lat, lng], {
    radius: 9, color: "#7c6fff", fillColor: "#7c6fff", fillOpacity: .95, weight: 3,
  }).addTo(_map).bindPopup("<b>Senin konumun</b>");
}

function _markerStyle(score) {
  let color = "#7c6fff";
  if (score > 0) color = "#3fcf6e";
  if (score < 0) color = "#ff4f4f";
  return { radius: 11, color, fillColor: color, fillOpacity: .82, weight: 2 };
}

function addPlaceMarkers(places) {
  if (!_map) return;
  places.forEach(p => {
    _placeData[p.id] = _placeData[p.id] ?? p;
    if (p.lat == null || p.lng == null || _markers[p.id]) return;
    const marker = L.circleMarker([p.lat, p.lng], _markerStyle(p.total_score ?? 0))
      .addTo(_map)
      .bindPopup(() => _popupHTML(p.id), { maxWidth: 260 });
    marker.on("click", () => {
      const card = document.getElementById("place-" + p.id);
      if (card) { card.scrollIntoView({ behavior: "smooth", block: "center" }); _flashCard(card); }
    });
    _markers[p.id] = marker;
  });
  const pts = places.filter(p => p.lat != null && p.lng != null).map(p => [p.lat, p.lng]);
  if (pts.length) _map.fitBounds(pts, { padding: [50, 50], maxZoom: 16 });
}

function _popupHTML(placeId) {
  const p = _placeData[placeId];
  if (!p) return "";
  const voted    = _votedPlaces[placeId];
  const canVote  = _roomStatus === "voting";
  const likeCls  = voted === 1  ? "active" : "";
  const disCls   = voted === -1 ? "active" : "";
  const dis      = (voted !== undefined || _votesUsed >= _maxVotes) ? "disabled" : "";

  let html = `<div class="map-popup">
    <b>${esc(p.name)}</b>`;
  if (p.address && p.address !== "—") html += `<br><small>${esc(p.address)}</small>`;
  html += `<div class="map-popup-score">
    <span class="likes">👍 ${p.likes ?? 0}</span>
    <span class="dislikes">👎 ${p.dislikes ?? 0}</span>
    <span class="net">Net: ${p.total_score ?? 0}</span>
  </div>`;
  if (canVote) {
    html += `<div class="map-popup-btns">
      <button class="btn btn--vote btn--like ${likeCls}" ${dis}
              onclick="castVote(${placeId}, 1)">👍</button>
      <button class="btn btn--vote btn--dislike ${disCls}" ${dis}
              onclick="castVote(${placeId}, -1)">👎</button>
    </div>`;
  }
  return html + `</div>`;
}

function _refreshMarkerPopup(placeId) {
  const m = _markers[placeId];
  if (m && m.isPopupOpen()) m.setPopupContent(_popupHTML(placeId));
}

function updateMarkersFromSummary(summary) {
  summary.forEach(p => {
    _markers[p.id]?.setStyle(_markerStyle(p.total_score ?? 0));
    _refreshMarkerPopup(p.id);
  });
}

function panToPlace(placeId, lat, lng) {
  if (!_map || lat == null || lng == null) return;
  _map.setView([lat, lng], 17, { animate: true });
  _markers[placeId]?.openPopup();
}

function highlightWinnerMarker(placeId) {
  const m = _markers[placeId];
  if (!m) return;
  m.setStyle({ radius: 16, color: "#f5a623", fillColor: "#f5a623", fillOpacity: 1, weight: 3 });
  m.openPopup();
}

function _flashCard(card) {
  card.classList.add("place-card--highlight");
  setTimeout(() => card.classList.remove("place-card--highlight"), 1200);
}

// ── Utils ──────────────────────────────────────────────────────────────────
async function apiFetch(url, method, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body && method !== "GET") opts.body = JSON.stringify(body);
  const res  = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Sunucu hatasi");
  return data;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
