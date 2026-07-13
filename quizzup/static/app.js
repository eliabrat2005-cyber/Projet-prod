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
  sound: localStorage.getItem("quizzup_sound") !== "0",
  difficulty: [1, 2, 3, 4].includes(+localStorage.getItem("quizzup_diff"))
    ? +localStorage.getItem("quizzup_diff") : 2,
  rounds: [7, 10, 15, 20].includes(+localStorage.getItem("quizzup_rounds"))
    ? +localStorage.getItem("quizzup_rounds") : 7,
  friends: [],           // [{id, name, wins, losses, draws, online}]
  friendCode: null,
  challengeCtx: null,    // contexte de défi (solo ou tournoi) pour l'écran amis
  waitingChallenge: null, // {friend_id, name} en attente d'acceptation
  tournSize: 5,          // nombre de thèmes du tournoi
  tournTopics: [],       // ids des thèmes choisis pour le tournoi
  party: null,           // salon en attente {code, members, is_host}
};

const DIFF_LABELS = { 1: "😌 Facile", 2: "🎯 Moyen", 3: "🔥 Difficile", 4: "💀 Extrême" };

const $ = (id) => document.getElementById(id);
const SCREENS = ["name", "home", "topic", "tournament", "party", "friends", "ranking", "profile", "search", "vs", "game", "results"];

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
  if (!S.sound) return;
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
const sndUrgent = () => beep(1100, 0.05, "square", 0.05);
const sndGood = () => { beep(660, 0.1); setTimeout(() => beep(990, 0.18), 90); };
const sndBad = () => beep(160, 0.3, "sawtooth", 0.07);
const sndGo = () => beep(1320, 0.2, "triangle", 0.09);
const vibrate = (pattern) => { if (navigator.vibrate) try { navigator.vibrate(pattern); } catch (e) {} };

// ─── Confettis (canvas maison, zéro dépendance) ─────────────────────────────
function confetti(count = 120) {
  const canvas = $("confetti");
  const ctx = canvas.getContext("2d");
  canvas.width = innerWidth; canvas.height = innerHeight;
  const colors = ["#d9b665", "#b08d3e", "#171a30", "#9a8cf0", "#a8d8ff", "#f6efe0"];
  const parts = Array.from({ length: count }, () => ({
    x: Math.random() * canvas.width,
    y: -20 - Math.random() * canvas.height * 0.4,
    w: 6 + Math.random() * 6, h: 8 + Math.random() * 8,
    vy: 2.2 + Math.random() * 3.4, vx: -1.6 + Math.random() * 3.2,
    rot: Math.random() * Math.PI, vr: -0.12 + Math.random() * 0.24,
    color: colors[(Math.random() * colors.length) | 0],
  }));
  const t0 = performance.now();
  cancelAnimationFrame(confetti._raf);
  (function frame(now) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of parts) {
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
      ctx.fillStyle = p.color; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    }
    if (now - t0 < 3200) confetti._raf = requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, canvas.width, canvas.height);
  })(t0);
}

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
  if (S.ws && S.ws.readyState === WebSocket.OPEN) {
    S.ws.send(JSON.stringify(msg));
    return true;
  }
  toast("Connexion au serveur en cours…");
  return false;
}

// Anti double-clic : désactive un bouton pendant un court instant.
function debounceBtn(btn, ms = 1500) {
  btn.disabled = true;
  setTimeout(() => { btn.disabled = false; }, ms);
}

// ─── Dispatch des messages serveur ──────────────────────────────────────────
function handle(msg) {
  switch (msg.type) {
    case "need_name": show("name"); break;
    case "welcome": onWelcome(msg); break;
    case "profile": S.profile = msg.profile; renderProfile(); break;
    case "queued": show("search"); break;
    case "find_cancelled": show("topic"); break;
    case "friends": S.friends = msg.friends; renderFriends(); break;
    case "friend_added": onFriendAdded(msg); break;
    case "friend_error": onFriendError(msg); break;
    case "friend_presence": onFriendPresence(msg); break;
    case "challenge_received": onChallengeReceived(msg); break;
    case "challenge_sent": onChallengeSent(msg); break;
    case "challenge_cancelled": break;
    case "challenge_declined": onChallengeDeclined(msg); break;
    case "challenge_gone":
      toast("Ce défi n'est plus disponible.");
      if (!$("screen-search").classList.contains("hidden")) show("topic");
      break;
    case "party_created": onPartyLobby(msg, true); break;
    case "party_update": onPartyLobby(msg, false); break;
    case "party_not_found":
      $("party-join-error").classList.remove("hidden");
      setTimeout(() => $("party-join-error").classList.add("hidden"), 2500);
      break;
    case "party_error": toast(msg.message || "Erreur salon"); break;
    case "party_closed":
      toast("Le salon a été fermé.");
      S.party = null; renderTopics(); show("home");
      break;
    case "party_started": onPartyStarted(msg); break;
    case "party_reveal": onPartyReveal(msg); break;
    case "party_over": onPartyOver(msg); break;
    case "party_left":
      if (S.game && S.game.party) toast(`${msg.name} a quitté la partie`);
      break;
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
  S.friends = msg.friends || [];
  S.friendCode = msg.friend_code || null;
  localStorage.setItem("quizzup_pid", msg.player.id);
  localStorage.setItem("quizzup_name", msg.player.name);
  $("conn-banner").classList.add("hidden");
  $("home-name").textContent = msg.player.name;
  $("home-avatar").textContent = initial(msg.player.name);
  renderTopics();
  // Reconnexion en plein match : le serveur a déclaré forfait à la coupure,
  // on ne laisse pas le client figé sur l'écran de jeu.
  if (S.game && !S.game.over) {
    S.game = null;
    cancelAnimationFrame(S.timerRAF);
    toast("Connexion perdue — la partie a été interrompue 🏳️");
  }
  show("home");
}

// ─── Accueil / thèmes ───────────────────────────────────────────────────────
function topicStats(topicId) {
  const t = (S.profile && S.profile.topics && S.profile.topics[topicId]) || null;
  return t || { xp: 0, level: 0, xp_in_level: 0, xp_for_next: 40, title: "Débutant",
                games: 0, wins: 0, losses: 0, draws: 0, best_score: 0 };
}

function renderTopics(filter = "") {
  const grid = $("topics-grid");
  grid.innerHTML = "";
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const list = S.topics.filter((t) => !filter || norm(t.name).includes(norm(filter)));
  const totalQ = S.topics.reduce((acc, t) => acc + t.count, 0);
  $("home-stats").textContent =
    `${S.topics.length} thèmes · ${totalQ.toLocaleString("fr-FR")} questions — jamais deux fois la même`;
  if (!list.length) {
    grid.innerHTML = `<div class="topics-empty">Aucun thème ne correspond 😕</div>`;
    return;
  }
  for (const t of list) {
    const st = topicStats(t.id);
    const pct = Math.round(100 * st.xp_in_level / st.xp_for_next);
    const btn = document.createElement("button");
    btn.className = "topic-card";
    btn.style.background =
      `linear-gradient(165deg, ${t.color}30 0%, rgba(255,255,255,.9) 62%)`;
    btn.innerHTML = `<span class="t-level">Niv. ${st.level}</span>
      <span class="t-icon">${t.icon}</span><span class="t-name">${t.name}</span>
      <span class="t-count">${t.count} questions</span>
      <span class="t-progress"><i style="width:${pct}%"></i></span>`;
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
    `linear-gradient(170deg, ${t.color}42 0%, rgba(255,255,255,.92) 78%)`);
  $("topic-level").textContent = `Niv. ${st.level}`;
  $("topic-title-rank").textContent = st.title;
  $("topic-xp-fill").style.width = `${Math.round(100 * st.xp_in_level / st.xp_for_next)}%`;
  $("topic-xp-text").textContent = `${st.xp_in_level} / ${st.xp_for_next} XP`;
  $("topic-record").textContent =
    st.games ? `${st.games} matchs — ${st.wins} V · ${st.losses} D · ${st.draws} N — record : ${st.best_score} pts`
             : "Aucun match joué — lance-toi !";
  renderDiffChips(t);
  show("topic");
}

function renderDiffChips(t) {
  document.querySelectorAll(".diff-chip").forEach((chip) => {
    const d = +chip.dataset.diff;
    chip.classList.toggle("selected", d === S.difficulty);
    const n = (t.tiers && t.tiers[d - 1]) || 0;
    chip.querySelector("small").textContent = n ? `${n} q.` : "—";
  });
  document.querySelectorAll(".rounds-chip").forEach((chip) => {
    chip.classList.toggle("selected", +chip.dataset.rounds === S.rounds);
  });
}

// ─── Amis ─────────────────────────────────────────────────────────────────
function friendById(id) { return S.friends.find((f) => f.id === id) || null; }

function openFriends(challengeCtx = null) {
  S.challengeCtx = challengeCtx;
  send({ type: "list_friends" });
  const hint = $("friends-challenge-hint");
  if (challengeCtx) {
    $("friends-title").textContent = "Défier un ami";
    const desc = challengeCtx.kind === "tournoi"
      ? `🏆 Tournoi · ${challengeCtx.size} thèmes · ${DIFF_LABELS[challengeCtx.difficulty]}`
      : `${challengeCtx.topic.icon} ${challengeCtx.topic.name} · ${DIFF_LABELS[challengeCtx.difficulty]} · ${challengeCtx.rounds} q.`;
    hint.textContent = `${desc} — choisis qui défier`;
    hint.classList.remove("hidden");
  } else {
    $("friends-title").textContent = "Mes amis";
    hint.classList.add("hidden");
  }
  renderFriends();
  show("friends");
}

function renderFriends() {
  $("my-friend-code").textContent = S.friendCode || "······";
  const wrap = $("friends-list");
  wrap.innerHTML = "";
  if (!S.friends.length) {
    wrap.innerHTML = "<div class='ranking-empty'>Aucun ami pour l'instant. Ajoute le code d'un pote ci-dessus !</div>";
    return;
  }
  const challenge = S.challengeCtx;
  for (const f of S.friends) {
    const row = document.createElement("div");
    row.className = "friend-row";
    const score = `${f.wins} V · ${f.losses} D${f.draws ? " · " + f.draws + " N" : ""}`;
    let action;
    if (challenge) {
      action = f.online
        ? `<button class="btn btn-primary friend-defy" data-id="${f.id}">Défier</button>`
        : `<span class="friend-offline">hors ligne</span>`;
    } else {
      action = `<span class="friend-dot ${f.online ? "on" : "off"}" title="${f.online ? "en ligne" : "hors ligne"}"></span>`;
    }
    row.innerHTML = `<span class="avatar avatar-opp">${initial(f.name)}</span>
      <div class="friend-body"><div class="friend-name">${escapeHtml(f.name)}</div>
      <div class="friend-score">Toi ${score}</div></div>${action}`;
    wrap.appendChild(row);
  }
  wrap.querySelectorAll(".friend-defy").forEach((b) => {
    b.onclick = () => {
      const ctx = S.challengeCtx;
      if (!ctx) return;
      if (ctx.kind === "tournoi") {
        send({ type: "challenge_tournament", friend_id: b.dataset.id,
               topics: ctx.topics, difficulty: ctx.difficulty });
      } else {
        send({ type: "challenge_friend", friend_id: b.dataset.id,
               topic_id: ctx.topic.id, difficulty: ctx.difficulty, rounds: ctx.rounds });
      }
      debounceBtn(b, 2000);
    };
  });
}

function onFriendAdded(msg) {
  S.friends = msg.friends;
  $("add-friend-input").value = "";
  $("add-friend-error").classList.add("hidden");
  toast(`${msg.friend.name} est maintenant ton ami ! 🎉`);
  renderFriends();
}

function onFriendError(msg) {
  const err = $("add-friend-error");
  err.textContent = msg.message || "Erreur";
  err.classList.remove("hidden");
  setTimeout(() => err.classList.add("hidden"), 3000);
}

function onFriendPresence(msg) {
  const f = friendById(msg.friend_id);
  if (f) { f.online = msg.online; renderFriends(); }
}

function onChallengeReceived(msg) {
  $("challenge-from").textContent = msg.from_name;
  $("challenge-topic").textContent = msg.label || "";
  $("challenge-overlay").dataset.from = msg.from_id;
  $("challenge-overlay").classList.remove("hidden");
  sndGo(); vibrate([60, 40, 60]);
}

function onChallengeSent(msg) {
  const f = friendById(msg.friend_id);
  S.waitingChallenge = { friend_id: msg.friend_id, name: f ? f.name : "ton ami" };
  $("search-text").textContent = "Défi envoyé…";
  $("search-topic").textContent = `En attente de ${S.waitingChallenge.name}`;
  show("search");
}

function onChallengeDeclined(msg) {
  const f = friendById(msg.friend_id);
  toast(`${f ? f.name : "Ton ami"} a refusé le défi.`);
  S.waitingChallenge = null;
  if (!$("screen-search").classList.contains("hidden")) show("topic");
}

// ─── Tournoi ────────────────────────────────────────────────────────────────
function openTournament() {
  if (!S.tournTopics.length) {
    // pré-remplit avec des thèmes au hasard pour démarrer vite
    pickRandomThemes();
  }
  renderTournament();
  show("tournament");
}

function renderTournament(filter = "") {
  document.querySelectorAll(".tsize-chip").forEach((c) =>
    c.classList.toggle("selected", +c.dataset.size === S.tournSize));
  document.querySelectorAll(".tdiff-chip").forEach((c) =>
    c.classList.toggle("selected", +c.dataset.diff === S.difficulty));

  const label = $("tourn-picked-label");
  label.textContent = `Thèmes choisis : ${S.tournTopics.length} / ${S.tournSize}`;

  const picked = $("tourn-picked");
  picked.innerHTML = "";
  for (const id of S.tournTopics) {
    const t = S.topics.find((x) => x.id === id);
    if (!t) continue;
    const tag = document.createElement("button");
    tag.className = "tourn-tag";
    tag.innerHTML = `${t.icon} ${escapeHtml(t.name)}`;
    tag.onclick = () => { toggleTournTheme(id); };
    picked.appendChild(tag);
  }

  const grid = $("tourn-grid");
  grid.innerHTML = "";
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const list = S.topics.filter((t) => !filter || norm(t.name).includes(norm(filter)));
  for (const t of list) {
    const card = document.createElement("button");
    card.className = "tourn-grid-card" + (S.tournTopics.includes(t.id) ? " picked" : "");
    card.innerHTML = `<span class="t-icon">${t.icon}</span><span class="t-name">${escapeHtml(t.name)}</span>`;
    card.onclick = () => toggleTournTheme(t.id);
    grid.appendChild(card);
  }
}

function toggleTournTheme(id) {
  const i = S.tournTopics.indexOf(id);
  if (i >= 0) {
    S.tournTopics.splice(i, 1);
  } else if (S.tournTopics.length < S.tournSize) {
    S.tournTopics.push(id);
  } else {
    toast(`${S.tournSize} thèmes maximum — retire-en un d'abord`);
    return;
  }
  sndTick();
  renderTournament($("tourn-search").value);
}

function pickRandomThemes() {
  const pool = S.topics.slice();
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  S.tournTopics = pool.slice(0, S.tournSize).map((t) => t.id);
}

function launchTournament(mode, friendId) {
  if (S.tournTopics.length !== S.tournSize) {
    toast(`Choisis exactement ${S.tournSize} thèmes (ou 🎲 au hasard)`);
    return;
  }
  if (mode === "bot") {
    send({ type: "play_tournament", topics: S.tournTopics, difficulty: S.difficulty });
  } else {
    send({ type: "challenge_tournament", friend_id: friendId,
           topics: S.tournTopics, difficulty: S.difficulty });
  }
}

// ─── Partie à plusieurs (salon) ─────────────────────────────────────────────
function openPartyJoin() {
  S.party = null;
  $("party-join").classList.remove("hidden");
  $("party-lobby").classList.add("hidden");
  $("party-code-input").value = "";
  show("party");
}

function onPartyLobby(msg, created) {
  S.party = { code: msg.code, members: msg.members, is_host: msg.is_host };
  $("party-join").classList.add("hidden");
  $("party-lobby").classList.remove("hidden");
  $("party-label").textContent = msg.spec_label || "Salon";
  $("party-code").textContent = msg.code;
  renderPartyMembers();
  show("party");
  if (created) sndGo();
}

function renderPartyMembers() {
  const p = S.party;
  if (!p) return;
  $("party-count").textContent = p.members.length;
  const wrap = $("party-members");
  wrap.innerHTML = "";
  for (const m of p.members) {
    const row = document.createElement("div");
    row.className = "friend-row";
    row.innerHTML = `<span class="avatar">${initial(m.name)}</span>
      <div class="friend-body"><div class="friend-name">${escapeHtml(m.name)}${m.id === S.player.id ? " (toi)" : ""}</div>
      <div class="friend-score">${m.is_host ? "👑 Organisateur" : "Prêt"}</div></div>`;
    wrap.appendChild(row);
  }
  const canStart = p.is_host && p.members.length >= 2;
  $("btn-start-party").classList.toggle("hidden", !p.is_host);
  $("btn-start-party").disabled = !canStart;
  $("btn-start-party").textContent = canStart ? "🚀 Démarrer la partie"
    : (p.is_host ? "En attente d'un 2e joueur…" : "");
  $("party-wait").classList.toggle("hidden", p.is_host);
}

function onPartyStarted(msg) {
  const scores = {};
  msg.players.forEach((pl) => { scores[pl.id] = 0; });
  S.party = null;
  S.game = {
    id: msg.game_id, party: true, topic: msg.topic, rounds: msg.rounds,
    players: msg.players, scores, roundResults: [], over: false, currentRound: 0,
  };
  $("challenge-overlay").classList.add("hidden");
  // Bascule l'entête en tableau des scores multijoueur
  $("party-scoreboard").classList.remove("hidden");
  document.querySelector("#screen-game .game-head").classList.add("hidden");
  $("streak-badge").classList.add("hidden");
  $("question-text").textContent = "Prêts ? 🎯";
  $("answers").innerHTML = "";
  $("round-points").classList.add("hidden");
  $("double-banner").classList.add("hidden");
  $("round-topic").classList.add("hidden");
  $("g-round").textContent = `1/${msg.rounds}`;
  $("timer-fill").style.transform = "scaleX(1)";
  renderPartyScoreboard();
  sndGo();
  show("game");
}

function renderPartyScoreboard(gains = {}) {
  const g = S.game;
  if (!g || !g.party) return;
  const board = $("party-scoreboard");
  board.innerHTML = "";
  const sorted = g.players.slice().sort((a, b) => (g.scores[b.id] || 0) - (g.scores[a.id] || 0));
  for (const pl of sorted) {
    const el = document.createElement("div");
    el.className = "psb-player" + (pl.id === S.player.id ? " me" : "");
    const gain = gains[pl.id];
    el.innerHTML = `<span class="psb-avatar">${initial(pl.name)}</span>
      <span class="psb-name">${escapeHtml(pl.name)}</span>
      <span class="psb-score">${g.scores[pl.id] || 0}</span>
      ${gain ? `<span class="psb-gain">+${gain}</span>` : ""}`;
    board.appendChild(el);
  }
}

function onPartyReveal(msg) {
  if (!S.game) return;
  cancelAnimationFrame(S.timerRAF);
  $("timer-fill").style.transform = "scaleX(0)";
  $("timer-fill").parentElement.classList.remove("urgent");

  const buttons = document.querySelectorAll(".answer-btn");
  buttons.forEach((b, i) => {
    b.disabled = true;
    if (i === msg.correct) b.classList.add("correct");
    if (msg.you.choice !== null && i === msg.you.choice && i !== msg.correct) b.classList.add("wrong");
  });
  const gotIt = msg.you.choice === msg.correct;
  if (gotIt) { sndGood(); vibrate(30); } else { sndBad(); vibrate([70, 40, 70]); }

  const pts = $("round-points");
  pts.textContent = gotIt ? `+${msg.you.points} pts`
                          : (msg.you.choice === null ? "Temps écoulé !" : "Raté !");
  pts.classList.toggle("zero", !gotIt);
  pts.classList.remove("hidden");

  const gains = {};
  for (const r of msg.results) {
    S.game.scores[r.id] = r.total;
  }
  gains[S.player.id] = gotIt ? msg.you.points : 0;
  renderPartyScoreboard(gains);
}

function onPartyOver(msg) {
  if (!S.game) return;
  S.game.over = true;
  cancelAnimationFrame(S.timerRAF);

  const podium = msg.podium;
  const iWon = msg.result === "win";
  const banner = $("results-banner");
  banner.className = "results-banner " + (iWon ? "win" : msg.result);
  const myRank = podium.findIndex((p) => p.id === msg.your_id) + 1;
  banner.textContent = iWon ? "Victoire ! 🏆"
                     : msg.result === "draw" ? "Égalité en tête !"
                     : `${myRank}ᵉ place`;
  $("results-sub").classList.add("hidden");

  // Masque l'affichage 1v1, montre le podium
  document.querySelector("#screen-results .results-scores").classList.add("hidden");
  $("r-dots").classList.add("hidden");
  const pod = $("party-podium");
  pod.classList.remove("hidden");
  pod.innerHTML = "";
  const medals = ["🥇", "🥈", "🥉"];
  podium.forEach((p, i) => {
    const row = document.createElement("div");
    row.className = "podium-row" + (p.winner ? " win" : "") + (p.id === msg.your_id ? " me" : "");
    row.innerHTML = `<span class="podium-rank">${medals[i] || (i + 1)}</span>
      <span class="podium-name">${escapeHtml(p.name)}${p.id === msg.your_id ? " (toi)" : ""}</span>
      <span class="podium-score">${p.score}</span>`;
    pod.appendChild(row);
  });

  if (iWon) { sndGood(); vibrate([40, 60, 40, 60, 120]); confetti(160); }
  else sndBad();

  // Carte XP (réutilise l'affichage existant)
  $("r-xp-gain").textContent = `+${msg.xp_gained || 0} XP`;
  const after = msg.level_after || { level: 0, title: "Débutant", xp_in_level: 0, xp_for_next: 40 };
  $("r-level").textContent = `Niv. ${after.level}`;
  $("r-title").textContent = after.title;
  $("r-xp-text").textContent = `${after.xp_in_level} / ${after.xp_for_next} XP`;
  $("r-levelup").classList.toggle("hidden", !msg.level_up);
  const before = msg.level_before || after;
  const fill = $("r-xp-fill");
  fill.style.transition = "none";
  fill.style.width = `${Math.round(100 * before.xp_in_level / before.xp_for_next)}%`;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.style.transition = "";
    fill.style.width = `${Math.round(100 * after.xp_in_level / after.xp_for_next)}%`;
  }));

  $("rematch-status").classList.add("hidden");
  $("btn-rematch").classList.add("hidden");   // pas de revanche 1-clic en groupe
  setTimeout(() => show("results"), 900);
}

// ─── Match ──────────────────────────────────────────────────────────────────
function onMatchFound(msg) {
  S.game = {
    id: msg.game_id, topic: msg.topic, rounds: msg.rounds,
    opponent: msg.opponent, meScore: 0, oppScore: 0,
    roundResults: [], over: false, currentRound: 0,
  };
  S.waitingChallenge = null;
  $("challenge-overlay").classList.add("hidden");
  // Rétablit l'affichage duel 1v1 (au cas où on sortait d'une partie de groupe)
  $("party-scoreboard").classList.add("hidden");
  document.querySelector("#screen-game .game-head").classList.remove("hidden");
  document.querySelector("#screen-results .results-scores").classList.remove("hidden");
  $("party-podium").classList.add("hidden");
  $("r-dots").classList.remove("hidden");
  $("btn-rematch").classList.remove("hidden");
  // Écran VS
  $("vs-me-avatar").textContent = initial(S.player.name);
  $("vs-me-name").textContent = S.player.name;
  const myLevel = topicStats(msg.topic.id).level;
  $("vs-me-level").textContent = `Niveau ${myLevel}`;
  $("vs-opp-avatar").textContent = initial(msg.opponent.name);
  $("vs-opp-name").textContent = msg.opponent.name;
  $("vs-opp-level").textContent = msg.opponent.is_bot ? "Bot" : `Niveau ${msg.opponent.level}`;
  const vsCount = msg.tournament ? `${msg.tournament.count} thèmes` : `${msg.rounds} questions`;
  $("vs-topic").textContent =
    `${msg.topic.icon} ${msg.topic.name} · ${msg.difficulty_label || DIFF_LABELS[msg.difficulty] || ""} · ${vsCount}`;
  sndGo();
  show("vs");
  // Prépare l'écran de jeu
  $("g-me-avatar").textContent = initial(S.player.name);
  $("g-opp-avatar").textContent = initial(msg.opponent.name);
  $("g-me-score").textContent = "0";
  $("g-opp-score").textContent = "0";
  $("opp-answered-name").textContent = msg.opponent.name;
  $("streak-badge").classList.add("hidden");
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

  // Tournoi : thème de la manche en cours
  const rt = msg.round_topic;
  const rtEl = $("round-topic");
  if (rt) {
    rtEl.textContent = `${rt.icon} ${rt.name}`;
    rtEl.classList.remove("hidden");
  } else {
    rtEl.classList.add("hidden");
  }

  const overlay = $("countdown-overlay");
  const num = $("countdown-num");
  $("countdown-sub").textContent = rt
    ? `Manche ${msg.round}/${msg.rounds} · ${rt.icon} ${rt.name}`
    : `Question ${msg.round}/${msg.rounds}`;
  $("countdown-double").classList.toggle("hidden", !msg.double);
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
  const keys = ["A", "B", "C", "D"];
  msg.choices.forEach((choice, i) => {
    const btn = document.createElement("button");
    btn.className = "answer-btn";
    const key = document.createElement("span");
    key.className = "key";
    key.textContent = keys[i];
    const txt = document.createElement("span");
    txt.className = "txt";
    txt.textContent = choice;
    btn.append(key, txt);
    btn.onclick = () => answer(i, btn);
    wrap.appendChild(btn);
  });
  startTimer(msg.duration);
  sndGo();
}

function startTimer(duration) {
  cancelAnimationFrame(S.timerRAF);
  const fill = $("timer-fill");
  const track = fill.parentElement;
  track.classList.remove("urgent");
  const start = performance.now();
  const total = duration * 1000;
  let lastWholeSec = Math.ceil(duration);
  const tick = (now) => {
    const left = Math.max(0, 1 - (now - start) / total);
    fill.style.transform = `scaleX(${left})`;
    const secsLeft = left * duration;
    // Urgence sur les 3 dernières secondes : pulse rouge + bip par seconde
    if (secsLeft <= 3 && !S.game?.answered) {
      track.classList.add("urgent");
      const whole = Math.ceil(secsLeft);
      if (whole < lastWholeSec && whole > 0) { sndUrgent(); lastWholeSec = whole; }
    }
    if (left > 0) {
      S.timerRAF = requestAnimationFrame(tick);
    } else if (S.game && !S.game.answered) {
      // Temps écoulé : on fige les réponses en attendant la révélation.
      S.game.answered = true;
      for (const b of document.querySelectorAll(".answer-btn")) b.disabled = true;
      const pts = $("round-points");
      pts.textContent = "Temps écoulé…";
      pts.classList.add("zero");
      pts.classList.remove("hidden");
    }
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

  $("timer-fill").parentElement.classList.remove("urgent");

  const gotIt = msg.you.choice === msg.correct;
  if (gotIt) { sndGood(); vibrate(30); } else { sndBad(); vibrate([70, 40, 70]); }

  // Série de bonnes réponses 🔥
  S.game.streak = gotIt ? (S.game.streak || 0) + 1 : 0;
  const badge = $("streak-badge");
  if (S.game.streak >= 2) {
    $("streak-count").textContent = S.game.streak;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const pts = $("round-points");
  const time = msg.you.time !== null ? ` · ${msg.you.time.toFixed(1).replace(".", ",")} s` : "";
  pts.textContent = gotIt ? `+${msg.you.points} pts${time}`
                          : (msg.you.choice === null ? "Temps écoulé !" : "Raté !");
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
  banner.textContent = msg.result === "win" ? "Victoire ! 🏆"
                     : msg.result === "loss" ? "Défaite…"
                     : "Égalité !";
  const sub = $("results-sub");
  if (msg.forfeit) {
    sub.textContent = msg.result === "win" ? "Ton adversaire a abandonné"
                                           : "Tu as abandonné la partie";
    sub.classList.remove("hidden");
  } else if (msg.scores.you === 160) {
    sub.textContent = "🌟 PARTIE PARFAITE — 160/160 🌟";
    sub.classList.remove("hidden");
  } else {
    sub.classList.add("hidden");
  }
  if (msg.result === "win") { sndGood(); vibrate([40, 60, 40, 60, 120]); confetti(140); }
  else if (msg.result === "loss") sndBad();
  if (msg.level_up) setTimeout(() => confetti(80), 900);

  // Recap round par round (vert = gagné, rouge = perdu, jaune = égalité)
  const dots = $("r-dots");
  dots.innerHTML = "";
  for (let i = 0; i < S.game.rounds; i++) {
    const dot = document.createElement("i");
    const r = S.game.roundResults[i];
    if (r === "won") dot.classList.add("won");
    else if (r === "lost") dot.classList.add("lost");
    else if (r === "tied") dot.classList.add("tied");
    dots.appendChild(dot);
  }

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
    const medals = ["🥇", "🥈", "🥉"];
    rows.forEach((r, i) => {
      const li = document.createElement("li");
      if (r.player_id === S.player.id) li.classList.add("me");
      li.innerHTML = `<span class="rank">${medals[i] || i + 1}</span>
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
    // Quitter l'écran salon annule la participation au salon
    if (!$("screen-party").classList.contains("hidden") && S.party) {
      send({ type: "leave_party" });
      S.party = null;
    }
    if (dest === "topic" && !S.currentTopic) { show("home"); return; }
    if (dest === "home") { renderTopics(); }
    show(dest);
  };
});

$("profile-btn").onclick = () => { send({ type: "get_profile" }); renderProfile(); show("profile"); };
$("friends-btn").onclick = () => openFriends(null);
$("btn-tournament").onclick = () => openTournament();

document.querySelectorAll(".tsize-chip").forEach((c) => {
  c.onclick = () => {
    S.tournSize = +c.dataset.size;
    if (S.tournTopics.length > S.tournSize) S.tournTopics = S.tournTopics.slice(0, S.tournSize);
    sndTick(); renderTournament($("tourn-search").value);
  };
});
document.querySelectorAll(".tdiff-chip").forEach((c) => {
  c.onclick = () => { S.difficulty = +c.dataset.diff; localStorage.setItem("quizzup_diff", S.difficulty); sndTick(); renderTournament($("tourn-search").value); };
});
$("btn-random-themes").onclick = () => { pickRandomThemes(); sndTick(); renderTournament($("tourn-search").value); };
$("tourn-search").addEventListener("input", (e) => renderTournament(e.target.value));
$("btn-tourn-bot").onclick = () => launchTournament("bot");
$("btn-tourn-friend").onclick = () => {
  if (S.tournTopics.length !== S.tournSize) { toast(`Choisis ${S.tournSize} thèmes`); return; }
  openFriends({ kind: "tournoi", topics: S.tournTopics.slice(), difficulty: S.difficulty, size: S.tournSize });
};

// Parties à plusieurs
$("btn-join-party-home").onclick = () => openPartyJoin();
$("btn-party-topic").onclick = () => {
  if (!S.currentTopic) return;
  send({ type: "create_party", topic_id: S.currentTopic.id, difficulty: S.difficulty, rounds: S.rounds });
};
$("btn-tourn-party").onclick = () => {
  if (S.tournTopics.length !== S.tournSize) { toast(`Choisis ${S.tournSize} thèmes`); return; }
  send({ type: "create_party", topics: S.tournTopics.slice(), difficulty: S.difficulty });
};
$("btn-join-party").onclick = () => {
  const code = $("party-code-input").value.trim().toUpperCase();
  if (code.length !== 4) { toast("Le code fait 4 caractères"); return; }
  send({ type: "join_party", code });
};
$("party-code-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-join-party").click(); });
$("btn-copy-party").onclick = () => {
  const code = S.party ? S.party.code : "";
  if (navigator.clipboard && code) navigator.clipboard.writeText(code).then(() => toast("Code copié !")).catch(() => toast(code));
  else toast(code);
};
$("btn-start-party").onclick = () => send({ type: "start_party" });
$("btn-quit-party").onclick = () => { send({ type: "leave_party" }); S.party = null; renderTopics(); show("home"); };

document.querySelectorAll(".diff-chip").forEach((chip) => {
  chip.onclick = () => {
    S.difficulty = +chip.dataset.diff;
    localStorage.setItem("quizzup_diff", S.difficulty);
    if (S.currentTopic) renderDiffChips(S.currentTopic);
    sndTick();
  };
});

document.querySelectorAll(".rounds-chip").forEach((chip) => {
  chip.onclick = () => {
    S.rounds = +chip.dataset.rounds;
    localStorage.setItem("quizzup_rounds", S.rounds);
    if (S.currentTopic) renderDiffChips(S.currentTopic);
    sndTick();
  };
});

$("btn-quick").onclick = () => {
  if (!S.currentTopic) return;
  if (send({ type: "find_match", topic_id: S.currentTopic.id, difficulty: S.difficulty, rounds: S.rounds })) {
    $("search-topic").textContent =
      `${S.currentTopic.icon} ${S.currentTopic.name} · ${DIFF_LABELS[S.difficulty]} · ${S.rounds} q.`;
    debounceBtn($("btn-quick"));
  }
};
$("btn-bot").onclick = () => {
  if (!S.currentTopic) return;
  if (send({ type: "play_bot", topic_id: S.currentTopic.id, difficulty: S.difficulty, rounds: S.rounds })) {
    debounceBtn($("btn-bot"));
  }
};
$("btn-ranking").onclick = openRanking;
$("btn-friend").onclick = () => {
  if (!S.currentTopic) return;
  openFriends({ topic: S.currentTopic, difficulty: S.difficulty });
};
$("btn-cancel-search").onclick = () => {
  if (S.waitingChallenge) { send({ type: "cancel_challenge" }); S.waitingChallenge = null; show("topic"); }
  else send({ type: "cancel_find" });
};

// Amis : ajouter, copier le code, accepter/refuser un défi
$("btn-add-friend").onclick = () => {
  const code = $("add-friend-input").value.trim().toUpperCase();
  if (code.length < 4) { toast("Entre le code de ton ami"); return; }
  send({ type: "add_friend", code });
};
$("add-friend-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-add-friend").click(); });
$("btn-copy-code").onclick = () => {
  const code = S.friendCode || "";
  if (navigator.clipboard && code) {
    navigator.clipboard.writeText(code).then(() => toast("Code copié !")).catch(() => toast(code));
  } else { toast(code); }
};
$("btn-accept-challenge").onclick = () => {
  const from = $("challenge-overlay").dataset.from;
  $("challenge-overlay").classList.add("hidden");
  if (from) send({ type: "accept_challenge", from_id: from });
};
$("btn-decline-challenge").onclick = () => {
  const from = $("challenge-overlay").dataset.from;
  $("challenge-overlay").classList.add("hidden");
  if (from) send({ type: "decline_challenge", from_id: from });
};

$("btn-rematch").onclick = () => {
  if (!S.game) return;
  send({ type: "rematch", game_id: S.game.id });
  $("rematch-status").textContent = "En attente de ton adversaire…";
  $("rematch-status").classList.remove("hidden");
  $("btn-rematch").disabled = true;
};

$("btn-giveup").onclick = () => {
  if (!S.game || S.game.over) return;
  const msg = S.game.party
    ? "Quitter la partie de groupe ?"
    : "Abandonner la partie ? Ton adversaire gagnera par forfait.";
  if (confirm(msg)) {
    send({ type: "leave_game", game_id: S.game.id });
    if (S.game.party) { S.game = null; renderTopics(); show("home"); }
  }
};

$("sound-btn").onclick = () => {
  S.sound = !S.sound;
  localStorage.setItem("quizzup_sound", S.sound ? "1" : "0");
  $("sound-btn").textContent = S.sound ? "🔊" : "🔇";
  $("sound-btn").classList.toggle("off", !S.sound);
  if (S.sound) sndTick();
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

// Recherche de thème en direct
$("topic-search").addEventListener("input", (e) => renderTopics(e.target.value));

// État initial du bouton son (préférence persistée)
$("sound-btn").textContent = S.sound ? "🔊" : "🔇";
$("sound-btn").classList.toggle("off", !S.sound);

connect();
