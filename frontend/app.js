"use strict";

const WS_URL      = `ws://${location.host}/ws`;
const MAX_HISTORY = 200;
const CARD_RESET_MS = 6000;   // ms before a card card reverts to "scanning"

let ws            = null;
let readers       = {};        // id → reader dict
let detections    = [];        // history array, newest first

// ------------------------------------------------------------------ //
// WebSocket                                                            //
// ------------------------------------------------------------------ //

function connect() {
  setConnStatus("connecting");
  ws = new WebSocket(WS_URL);

  ws.onopen = () => setConnStatus("connected");

  ws.onclose = () => {
    setConnStatus("disconnected");
    setTimeout(connect, 3000);
  };

  ws.onerror = () => ws.close();

  ws.onmessage = (evt) => {
    try { handleEvent(JSON.parse(evt.data)); }
    catch (e) { console.error("Bad WS message", e); }
  };
}

function setConnStatus(state) {
  document.getElementById("conn-dot").className   = `dot ${state}`;
  document.getElementById("conn-label").textContent =
    state === "connected"    ? "Connected"     :
    state === "connecting"   ? "Connecting…"   : "Disconnected";
}

// ------------------------------------------------------------------ //
// Event dispatch                                                       //
// ------------------------------------------------------------------ //

function handleEvent(ev) {
  switch (ev.type) {
    case "state":
      readers = {};
      (ev.readers || []).forEach(r => (readers[r.id] = r));
      renderGrid();
      break;

    case "reader_added":
      readers[ev.reader.id] = ev.reader;
      renderGrid();
      break;

    case "reader_removed":
      delete readers[ev.reader_id];
      onReaderRemoved(ev.reader_id);
      break;

    case "card_detected":
      onCardDetected(ev.reader_id, ev.card);
      break;

    case "reader_error":
      onReaderError(ev.reader_id, ev.error);
      break;
  }
}

// ------------------------------------------------------------------ //
// Grid rendering                                                       //
// ------------------------------------------------------------------ //

function renderGrid() {
  const grid      = document.getElementById("readers-grid");
  const noReaders = document.getElementById("no-readers");
  const ids       = Object.keys(readers);

  noReaders.style.display = ids.length === 0 ? "block" : "none";

  // Remove stale cards (skip ones already mid-animation)
  grid.querySelectorAll(".reader-card").forEach(el => {
    if (!readers[el.dataset.readerId] && !el.classList.contains("removing")) el.remove();
  });

  // Add new cards (existing ones stay untouched to preserve state)
  ids.forEach(id => {
    if (!grid.querySelector(`[data-reader-id="${CSS.escape(id)}"]`)) {
      grid.appendChild(buildReaderCard(readers[id]));
    }
  });
}

function buildReaderCard(reader) {
  const el       = document.createElement("div");
  el.className   = "reader-card";
  el.dataset.readerId = reader.id;

  const icon = reader.type === "proxmark3" ? "🔬"
             : reader.type === "uhf"       ? "📶"
             :                              "📻";

  el.innerHTML = `
    <div class="card-header">
      <div class="card-icon">${icon}</div>
      <div class="card-meta">
        <div class="card-name">${h(reader.name)}</div>
        <div class="card-port">${h(reader.port || reader.id)}</div>
      </div>
    </div>
    <div class="card-status">
      <div class="status-led scanning"></div>
      <span class="status-text">Scanning…</span>
    </div>
    <div class="card-body">
      <div class="waiting-msg">Tap a card to identify</div>
      <div class="detection">
        <div class="freq-badge"></div>
        <div class="protocol"></div>
        <div class="uid"></div>
        <div class="extras"></div>
        <div class="det-time"></div>
      </div>
    </div>
  `;
  return el;
}

// ------------------------------------------------------------------ //
// Card detection                                                       //
// ------------------------------------------------------------------ //

function onCardDetected(readerId, card) {
  const el = cardEl(readerId);
  if (!el) return;

  const isHF      = card.frequency.includes("13.56");
  const isUHF     = card.frequency.includes("860");
  const freqKey   = isHF ? "hf" : isUHF ? "uhf" : "lf";
  const cardClass = isHF ? "hf-active" : isUHF ? "uhf-active" : "lf-active";

  // Glow border + flash
  el.className = `reader-card ${cardClass} flash`;
  setTimeout(() => el.classList.remove("flash"), 600);

  // Status LED
  const led  = el.querySelector(".status-led");
  const text = el.querySelector(".status-text");
  led.className  = `status-led ${freqKey}`;
  text.textContent = "Card detected";

  // Show detection panel
  el.querySelector(".waiting-msg").style.display = "none";
  const det = el.querySelector(".detection");
  det.classList.add("visible");

  // Frequency badge
  const badge = det.querySelector(".freq-badge");
  badge.className   = `freq-badge ${freqKey}`;
  badge.textContent = card.frequency;

  det.querySelector(".protocol").textContent = card.protocol;

  const uid = det.querySelector(".uid");
  uid.textContent = card.uid ? `UID: ${card.uid}` : "";

  // Extra fields (FC/CN for access control, SAK/ATQA for HF)
  const raw    = card.raw_details || {};
  const parts  = [];
  if (raw.facility_code) parts.push(`FC: ${raw.facility_code}`);
  if (raw.card_number)   parts.push(`CN: ${raw.card_number}`);
  if (raw.sak)           parts.push(`SAK: ${raw.sak}`);
  if (raw.atqa)          parts.push(`ATQA: ${raw.atqa}`);
  if (raw.rssi_dbm != null) parts.push(`RSSI: ${raw.rssi_dbm} dBm`);
  if (raw.antenna  != null) parts.push(`Ant: ${raw.antenna}`);
  if (raw.epc_type)      parts.push(raw.epc_type);
  det.querySelector(".extras").innerHTML = parts.map(p => `<span>${h(p)}</span>`).join("");

  det.querySelector(".det-time").textContent =
    new Date(card.timestamp).toLocaleTimeString();

  // Auto-reset after CARD_RESET_MS
  clearTimeout(el._resetTimer);
  el._resetTimer = setTimeout(() => resetCard(el), CARD_RESET_MS);

  // History
  addHistory(readerId, card);
}

function onReaderError(readerId, error) {
  const el = cardEl(readerId);
  if (!el) return;
  el.className = "reader-card err-state";
  el.querySelector(".status-led").className  = "status-led error";
  el.querySelector(".status-text").textContent = "Error";
}

function onReaderRemoved(readerId) {
  const el = cardEl(readerId);
  if (!el) { renderGrid(); return; }
  el.classList.add("removing");
  el.addEventListener("animationend", () => { el.remove(); renderGrid(); }, { once: true });
}

function resetCard(el) {
  el.className = "reader-card";
  el.querySelector(".status-led").className  = "status-led scanning";
  el.querySelector(".status-text").textContent = "Scanning…";
  el.querySelector(".waiting-msg").style.display = "";
  el.querySelector(".detection").classList.remove("visible");
}

// ------------------------------------------------------------------ //
// History                                                              //
// ------------------------------------------------------------------ //

function addHistory(readerId, card) {
  detections.unshift({ readerId, card, at: new Date() });
  if (detections.length > MAX_HISTORY) detections.length = MAX_HISTORY;
  renderHistory();
}

function renderHistory() {
  const table = document.getElementById("history-table");
  const empty = document.getElementById("history-empty");
  const body  = document.getElementById("history-body");

  if (detections.length === 0) {
    table.classList.remove("visible");
    empty.style.display = "";
    return;
  }

  table.classList.add("visible");
  empty.style.display = "none";

  body.innerHTML = detections.map(({ readerId, card, at }) => {
    const isHF    = card.frequency.includes("13.56");
    const isUHF   = card.frequency.includes("860");
    const fk      = isHF ? "hf" : isUHF ? "uhf" : "lf";
    const raw     = card.raw_details || {};
    const extras  = [];
    if (raw.facility_code) extras.push(`FC:${raw.facility_code}`);
    if (raw.card_number)   extras.push(`CN:${raw.card_number}`);
    if (raw.sak)           extras.push(`SAK:${raw.sak}`);
    if (raw.rssi_dbm != null) extras.push(`${raw.rssi_dbm} dBm`);
    if (raw.epc_type)      extras.push(raw.epc_type);
    const readerName = readers[readerId]?.name ?? readerId;

    return `<tr>
      <td class="t-time">${at.toLocaleTimeString()}</td>
      <td>${h(readerName)}</td>
      <td><strong>${h(card.protocol)}</strong></td>
      <td class="t-freq ${fk}">${h(card.frequency)}</td>
      <td class="t-uid">${card.uid ? h(card.uid) : "—"}</td>
      <td class="t-extra">${extras.map(h).join(" &nbsp;")}</td>
    </tr>`;
  }).join("");
}

// ------------------------------------------------------------------ //
// Utilities                                                            //
// ------------------------------------------------------------------ //

function cardEl(readerId) {
  return document.querySelector(`[data-reader-id="${CSS.escape(readerId)}"]`);
}

function h(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ------------------------------------------------------------------ //
// Boot                                                                 //
// ------------------------------------------------------------------ //

connect();
