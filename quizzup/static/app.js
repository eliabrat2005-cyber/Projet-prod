/* QuizzUp — client SPA (vanilla JS, zéro dépendance) */
"use strict";

// ─── État global ────────────────────────────────────────────────────────────
const S = {
  ws: null,
  wsReady: false,
  player: null,          // {id, name}
  topics: [],            // [{id, name, icon, color, count}]
  profile: null,         // {global, topics: {id: stats+level}}
  currentTopic: null,    // topic sélectionné
  game: null,            // état du match en cours
  timerRAF: null,
  reconnectDelay: 500,
};

const $ = (id) => document.getElementById(id);
const SCREENS = ["name", "home", "topic", "friend", "ranking", "profile", "search", "vs", "game", "results"];

function show(name) {
  for (const s of SCREENS) $("screen-" + s).classList.toggle("hidden", s !== name);
  window.scrollTo(0, 0);
}

function toast(msg, ms = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), ms);
}

function initial(name) { return (name || "?").trim().charAt(0).toUpperCase() || "?"; }

// ─── Sons (WebAudio, pas d'assets) ──────────────────────────────────────────
let audioCtx = null;
function beep(freq, dur = 0.12, type = "sine", gain = 0.08) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(gain, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur);
    o.connect(g).connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + dur);
  } catch (e) { /* audio non dispo */ }
}
const sndTick = () => beep(880, 0.06, "square", 0.04);
const sndGood = () => { beep(660, 0.1); setTimeout(() => beep(990, 0.18), 90); };
const sndBad = () => beep(160, 0.3, "sawtooth", 0.07);
const sndGo = () => beep(1320, 0.2, "triangle", 0.09);

// ─── WebSocket ──────────────────────────────────────────────────────────────
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  S.ws = ws;

  ws.onopen = () => {
    S.reconnectDelay = 500;
    const pid = localStorage.getItem("quizzup_pid");
    const name = localStorage.getItem("quizzup_name");
    if (pid || name) send({ type: "hello", player_id: pid, name });
    else show("name");
  };
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
  ws.onclose = () => {
    S.wsReady = false;
    $("conn-banner").classList.remove("hidden");
    setTimeout(connect, S.reconnectDelay);
    S.reconnectDelay = Math.min(S.reconnectDelay * 2, 8000);
  };
}

function send(msg) {
  if (S.ws && S.ws.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify(msg));
}

// ─── Dispatch des messages serveur ──────────────────────────────────────────
function handle(msg) {
  switch (msg.type) {
    case "need_name": show("name"); break;
    case "welcome": onWelcome(msg); break;
    case "profile": S.profile = msg.profile; renderProfile(); break;
    case "queued": show("search"); break;
    case "find_cancelled": show("topic"); break;
    case "room_created": onRoomCreated(msg); break;
    case "room_not_found":
      $("join-error").classList.remove("hidden");
      setTimeout(() => $("join-error").classList.add("hidden"), 2500);
      break;
    case "room_cancelled": break;
    case "match_found": onMatchFound(msg); break;
    case "countdown": onCountdown(msg); break;
    case "question": onQuestion(msg); break;
    case "answer_ack": break;
    case "opponent_answered": onOpponentAnswered(msg); break;
    case "reveal": onReveal(msg); break;
    case "game_over": onGameOver(msg); break;
    case "opponent_left":
      if (S.game && !S.game.over) toast("Ton adversaire a quitté la partie 🏳️");
      break;
    case "rematch_offer":
      $("rematch-status").textContent = `${msg.from} veut sa revanche !`;
      $("rematch-status").classList.remove("hidden");
      break;
    case "rematch_declined":
      $("rematch-status").textContent = "Ton adversaire a quitté.";
      $("rematch-status").classList.remove("hidden");
      $("btn-rematch").disabled = true;
      break;
    case "renamed":
      localStorage.setItem("quizzup_name", msg.name);
      S.player.name = msg.name;
      break;
    case "error": toast(msg.message || "Erreur serveur"); break;
  }
}

function onWelcome(msg) {
  S.wsReady = true;
  S.player = msg.player;
  S.topics = msg.topics;
  S.profile = msg.profile;
  localStorage.setItem("quizzup_pid", msg.player.id);
  localStorage.setItem("quizzup_name", msg.player.name);
  $("conn-banner").classList.add("hidden");
  $("home-name").textContent = msg.player.name;
  $("home-avatar").textContent = initial(msg.player.name);
  renderTopics();
  // Ne pas écraser un match en cours lors d'une reconnexion
  if (!S.game || S.game.over) show("home");
}

// ─── Accueil / thèmes ───────────────────────────────────────────────────────
function topicStats(topicId) {
  const t = (S.profile && S.profile.topics && S.profile.topics[topicId]) || null;
  return t || { xp: 0, level: 0, xp_in_level: 0, xp_for_next: 40, title: "Débutant",
                games: 0, wins: 0, losses: 0, draws: 0, best_score: 0 };
}

function renderTopics() {
  const grid = $("topics-grid");
  grid.innerHTML = "";
  for (const t of S.topics) {
    const st = topicStats(t.id);
    const btn = document.createElement("button");
    btn.className = "topic-card";
    btn.style.background = `linear-gradient(150deg, ${t.color} 0%, rgba(0,0,0,.45) 170%)`;
    btn.innerHTML = `<span class="t-level">Niv. ${st.level}</span>
      <span class="t-icon">${t.icon}</span><span class="t-name">${t.name}</span>`;
    btn.onclick = () => openTopic(t);
    grid.appendChild(btn);
  }
}

function openTopic(t) {
  S.currentTopic = t;
  const st = topicStats(t.id);
  $("topic-title").textContent = t.name;
  $("topic-icon").textContent = t.icon;
  $("topic-hero").style.setProperty("background",
    `linear-gradient(160deg, ${t.color} 0%, rgba(0,0,0,.4) 170%)`);
  $("topic-level").textContent = `Niv. ${st.level}`;
  $("topic-title-rank").textContent = st.title;
  $("topic-xp-fill").style.width = `${Math.round(100 * st.xp_in_level / st.xp_for_next)}%`;
  $("topic-xp-text").textContent = `${st.xp_in_level} / ${st.xp_for_next} XP`;
  $("topic-record").textContent =
    st.games ? `${st.games} matchs — ${st.wins} V · ${st.losses} D · ${st.draws} N — record : ${st.best_score} pts`
             : "Aucun match joué — lance-toi !";
  show("topic");
}

// ─── Recherche / salon ──────────────────────────────────────────────────────
function onRoomCreated(msg) {
  $("room-code").textContent = msg.code;
  $("room-code-box").classList.remove("hidden");
}

// ─── Match ──────────────────────────────────────────────────────────────────
function onMatchFound(msg) {
  S.game = {
    id: msg.game_id, topic: msg.topic, rounds: msg.rounds,
    opponent: msg.opponent, meScore: 0, oppScore: 0,
    roundResults: [], over: false, currentRound: 0,
  };
  $("room-code-box").classList.add("hidden");
  // Écran VS
  $("vs-me-avatar").textContent = initial(S.player.name);
  $("vs-me-name").textContent = S.player.name;
  const myLevel = topicStats(msg.topic.id).level;
  $("vs-me-level").textContent = `Niveau ${myLevel}`;
  $("vs-opp-avatar").textContent = initial(msg.opponent.name);
  $("vs-opp-name").textContent = msg.opponent.name;
  $("vs-opp-level").textContent = msg.opponent.is_bot ? "Bot" : `Niveau ${msg.opponent.level}`;
  $("vs-topic").textContent = `${msg.topic.icon} ${msg.topic.name}`;
  sndGo();
  show("vs");
  // Prépare l'écran de jeu
  $("g-me-avatar").textContent = initial(S.player.name);
  $("g-opp-avatar").textContent = initial(msg.opponent.name);
  $("g-me-score").textContent = "0";
  $("g-opp-score").textContent = "0";
  $("opp-answered-name").textContent = msg.opponent.name;
  renderDots();
}

function renderDots() {
  const wrap = $("g-dots");
  wrap.innerHTML = "";
  for (let i = 0; i < S.game.rounds; i++) {
    const dot = document.createElement("i");
    const r = S.game.roundResults[i];
    if (r === "won") dot.classList.add("won");
    else if (r === "lost") dot.classList.add("lost");
    else if (r === "tied") dot.classList.add("tied");
    if (i === S.game.currentRound - 1 && !r) dot.classList.add("current");
    wrap.appendChild(dot);
  }
}

function onCountdown(msg) {
  if (!S.game) return;
  S.game.currentRound = msg.round;
  show("game");
  $("g-round").textContent = `${msg.round}/${msg.rounds}`;
  $("double-banner").classList.toggle("hidden", !msg.double);
  $("opp-answered").classList.add("hidden");
  $("round-points").classList.add("hidden");
  $("question-text").textContent = "…";
  $("answers").innerHTML = "";
  $("timer-fill").style.transform = "scaleX(1)";
  renderDots();

  const overlay = $("countdown-overlay");
  const num = $("countdown-num");
  overlay.classList.remove("hidden");
  const secs = Math.max(1, Math.round(msg.seconds));
  let n = secs;
  num.className = "countdown-num";
  num.textContent = n;
  sndTick();
  clearInterval(overlay._int);
  overlay._int = setInterval(() => {
    n -= 1;
    if (n <= 0) { clearInterval(overlay._int); overlay.classList.add("hidden"); return; }
    num.textContent = n;
    sndTick();
  }, (msg.seconds * 1000) / secs);
}

function onQuestion(msg) {
  if (!S.game) return;
  $("countdown-overlay").classList.add("hidden");
  clearInterval($("countdown-overlay")._int);
  S.game.currentQuestion = msg;
  S.game.answered = false;
  $("question-text").textContent = msg.q;

  const wrap = $("answers");
  wrap.innerHTML = "";
  msg.choices.forEach((choice, i) => {
    const btn = document.createElement("button");
    btn.className = "answer-btn";
    btn.textContent = choice;
    btn.onclick = () => answer(i, btn);
    wrap.appendChild(btn);
  });
  startTimer(msg.duration);
  sndGo();
}

function startTimer(duration) {
  cancelAnimationFrame(S.timerRAF);
  const fill = $("timer-fill");
  const start = performance.now();
  const total = duration * 1000;
  const tick = (now) => {
    const left = Math.max(0, 1 - (now - start) / total);
    fill.style.transform = `scaleX(${left})`;
    if (left > 0) S.timerRAF = requestAnimationFrame(tick);
  };
  S.timerRAF = requestAnimationFrame(tick);
}

function answer(choice, btn) {
  if (!S.game || S.game.answered) return;
  S.game.answered = true;
  S.game.myChoice = choice;
  btn.classList.add("selected");
  for (const b of document.querySelectorAll(".answer-btn")) b.disabled = true;
  send({ type: "answer", game_id: S.game.id, round: S.game.currentRound, choice });
}

function onOpponentAnswered(msg) {
  if (!S.game || msg.round !== S.game.currentRound) return;
  $("opp-answered").classList.remove("hidden");
}

function onReveal(msg) {
  if (!S.game) return;
  cancelAnimationFrame(S.timerRAF);
  $("timer-fill").style.transform = "scaleX(0)";
  $("opp-answered").classList.add("hidden");

  const buttons = document.querySelectorAll(".answer-btn");
  buttons.forEach((b, i) => {
    b.disabled = true;
    if (i === msg.correct) b.classList.add("correct");
    if (msg.you.choice !== null && i === msg.you.choice && i !== msg.correct) b.classList.add("wrong");
    if (msg.opp.choice !== null && i === msg.opp.choice) b.classList.add("opp-pick");
  });

  const gotIt = msg.you.choice === msg.correct;
  if (gotIt) sndGood(); else sndBad();

  const pts = $("round-points");
  pts.textContent = gotIt ? `+${msg.you.points} pts` : (msg.you.choice === null ? "Temps écoulé !" : "Raté !");
  pts.classList.toggle("zero", !gotIt);
  pts.classList.remove("hidden");

  S.game.meScore = msg.scores.you;
  S.game.oppScore = msg.scores.opp;
  updateScore("g-me-score", msg.scores.you);
  updateScore("g-opp-score", msg.scores.opp);

  const r = msg.you.points > msg.opp.points ? "won"
          : msg.you.points < msg.opp.points ? "lost" : "tied";
  S.game.roundResults[msg.round - 1] = r;
  renderDots();
}

function updateScore(id, value) {
  const el = $(id);
  if (el.textContent !== String(value)) {
    el.textContent = value;
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }
}

// ─── Fin de partie ──────────────────────────────────────────────────────────
function onGameOver(msg) {
  if (!S.game) return;
  S.game.over = true;
  cancelAnimationFrame(S.timerRAF);

  const banner = $("results-banner");
  banner.className = "results-banner " + msg.result;
  banner.textContent = msg.result === "win" ? (msg.forfeit ? "Victoire par abandon !" : "Victoire ! 🏆")
                     : msg.result === "loss" ? "Défaite…"
                     : "Égalité !";
  if (msg.result === "win") sndGood(); else if (msg.result === "loss") sndBad();

  $("r-me-avatar").textContent = initial(S.player.name);
  $("r-me-name").textContent = S.player.name;
  $("r-me-score").textContent = msg.scores.you;
  $("r-opp-avatar").textContent = initial(msg.opponent);
  $("r-opp-name").textContent = msg.opponent;
  $("r-opp-score").textContent = msg.scores.opp;

  $("r-xp-gain").textContent = `+${msg.xp_gained || 0} XP`;
  const after = msg.level_after || { level: 0, title: "Débutant", xp_in_level: 0, xp_for_next: 40 };
  $("r-level").textContent = `Niv. ${after.level}`;
  $("r-title").textContent = after.title;
  $("r-xp-text").textContent = `${after.xp_in_level} / ${after.xp_for_next} XP`;
  $("r-levelup").classList.toggle("hidden", !msg.level_up);

  // Anime la barre d'XP depuis l'état d'avant
  const before = msg.level_before || after;
  const fill = $("r-xp-fill");
  fill.style.transition = "none";
  fill.style.width = `${Math.round(100 * before.xp_in_level / before.xp_for_next)}%`;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.style.transition = "";
    fill.style.width = `${Math.round(100 * after.xp_in_level / after.xp_for_next)}%`;
  }));

  // Met à jour le profil local
  if (msg.topic_stats && S.profile) {
    S.profile.topics[S.game.topic.id] = { ...msg.topic_stats, ...after };
  }

  $("btn-rematch").disabled = false;
  $("rematch-status").classList.add("hidden");
  setTimeout(() => show("results"), msg.forfeit ? 300 : 1200);
}

// ─── Classement / profil ────────────────────────────────────────────────────
async function openRanking() {
  const t = S.currentTopic;
  $("ranking-title").textContent = `🏆 ${t.name}`;
  const list = $("ranking-list");
  list.innerHTML = "<li class='ranking-empty'>Chargement…</li>";
  show("ranking");
  try {
    const rows = await (await fetch(`/api/leaderboard/${t.id}`)).json();
    list.innerHTML = "";
    if (!rows.length) {
      list.innerHTML = "<li class='ranking-empty'>Personne n'a encore joué ce thème. Sois le premier !</li>";
      return;
    }
    rows.forEach((r, i) => {
      const li = document.createElement("li");
      if (r.player_id === S.player.id) li.classList.add("me");
      li.innerHTML = `<span class="rank">${i + 1}</span>
        <span class="avatar">${initial(r.name)}</span>
        <span class="r-name">${escapeHtml(r.name)}<small>Niv. ${r.level} — ${r.title}</small></span>
        <span class="r-xp">${r.xp} XP</span>`;
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = "<li class='ranking-empty'>Impossible de charger le classement.</li>";
  }
}

function renderProfile() {
  const g = (S.profile && S.profile.global) || { games: 0, wins: 0, losses: 0, draws: 0, xp: 0 };
  $("profile-avatar").textContent = initial(S.player.name);
  $("profile-name").textContent = S.player.name;
  $("profile-stats").innerHTML = `
    <div class="stat-card"><div class="v">${g.games}</div><div class="l">Matchs</div></div>
    <div class="stat-card"><div class="v">${g.wins}</div><div class="l">Victoires</div></div>
    <div class="stat-card"><div class="v">${g.losses}</div><div class="l">Défaites</div></div>
    <div class="stat-card"><div class="v">${g.xp}</div><div class="l">XP total</div></div>`;
  const wrap = $("profile-topics");
  wrap.innerHTML = "";
  const played = S.topics.filter((t) => topicStats(t.id).games > 0);
  if (!played.length) {
    wrap.innerHTML = "<div class='ranking-empty'>Joue ton premier match pour voir tes stats ici !</div>";
    return;
  }
  played.sort((a, b) => topicStats(b.id).xp - topicStats(a.id).xp);
  for (const t of played) {
    const st = topicStats(t.id);
    const row = document.createElement("div");
    row.className = "profile-topic-row";
    row.innerHTML = `<span class="pt-icon">${t.icon}</span>
      <div class="pt-body"><div class="pt-name">${t.name}</div>
      <div class="pt-sub">${st.title} — ${st.wins} V · ${st.losses} D · ${st.draws} N</div></div>
      <span class="pt-level">Niv. ${st.level}</span>`;
    wrap.appendChild(row);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ─── Événements UI ──────────────────────────────────────────────────────────
$("name-go").onclick = () => {
  const name = $("name-input").value.trim();
  if (name.length < 2) { toast("Choisis un pseudo d'au moins 2 caractères"); return; }
  localStorage.setItem("quizzup_name", name);
  send({ type: "hello", player_id: localStorage.getItem("quizzup_pid"), name });
};
$("name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("name-go").click(); });

document.querySelectorAll(".back-btn").forEach((b) => {
  b.onclick = () => {
    const dest = b.dataset.back;
    if (dest === "topic" && !S.currentTopic) { show("home"); return; }
    if (dest === "home") { renderTopics(); }
    // Quitter l'écran ami annule un salon en attente
    if (!$("screen-friend").classList.contains("hidden")) send({ type: "cancel_room" });
    show(dest);
  };
});

$("profile-btn").onclick = () => { send({ type: "get_profile" }); renderProfile(); show("profile"); };

$("btn-quick").onclick = () => send({ type: "find_match", topic_id: S.currentTopic.id });
$("btn-bot").onclick = () => send({ type: "play_bot", topic_id: S.currentTopic.id });
$("btn-ranking").onclick = openRanking;
$("btn-friend").onclick = () => {
  $("room-code-box").classList.add("hidden");
  $("join-code-input").value = "";
  show("friend");
};
$("btn-cancel-search").onclick = () => send({ type: "cancel_find" });
$("btn-create-room").onclick = () => send({ type: "create_room", topic_id: S.currentTopic.id });
$("btn-join-room").onclick = () => {
  const code = $("join-code-input").value.trim().toUpperCase();
  if (code.length !== 4) { toast("Le code fait 4 caractères"); return; }
  send({ type: "join_room", code });
};
$("join-code-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-join-room").click(); });

$("btn-rematch").onclick = () => {
  send({ type: "rematch", game_id: S.game.id });
  $("rematch-status").textContent = "En attente de ton adversaire…";
  $("rematch-status").classList.remove("hidden");
  $("btn-rematch").disabled = true;
};
$("btn-results-home").onclick = () => {
  if (S.game) send({ type: "decline_rematch", game_id: S.game.id });
  S.game = null;
  send({ type: "get_profile" });
  renderTopics();
  show("home");
};

// Quitter un match en cours si on ferme l'onglet : le serveur gère via disconnect.
window.addEventListener("beforeunload", () => { if (S.ws) S.ws.close(); });

connect();
