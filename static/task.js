/**
 * task.js
 * Day-0 flow: create+confirm on one page -> 10min recall wait -> login recall.
 */

// ----------------- Helpers -----------------
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await res.json();
}

function nowMs() {
  return performance.now();
}

const $ = (id) => document.getElementById(id);

// Condition is written by task.html into window.__COND__
let cond = window.__COND__;
const INITIAL_RECALL_WAIT_SECONDS = Number(window.__INITIAL_RECALL_WAIT_SECONDS__ ?? 10 * 60);

// ----------------- Emoji Set (from backend) -----------------
const EMOJI_SET_ORDERED = Array.isArray(window.__EMOJIS__) ? window.__EMOJIS__ : [];

// Optional: limit emoji length (turn on if you want PIN-like design)
const LIMIT_EMOJI_LENGTH = false;
const EMOJI_LIMIT = 6;

let activeEmojiInput = null;

// ----------------- Emoji picker UI helpers -----------------
function showPalette(show) {
  const pal = $("emojiPalette");
  if (!pal) return;
  pal.style.display = show ? "block" : "none";
}

function countChars(str) {
  return Array.from(str).length;
}

function containsAnyEmoji(str) {
  return EMOJI_SET_ORDERED.some((e) => str.includes(e));
}

function insertAtCursor(input, text) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;

  const before = input.value.slice(0, start);
  const after = input.value.slice(end);
  const next = before + text + after;

  if (LIMIT_EMOJI_LENGTH) {
    const n = countChars(next);
    if (n > EMOJI_LIMIT) return false;
  }

  input.value = next;
  const pos = start + text.length;
  input.setSelectionRange(pos, pos);
  input.focus();
  return true;
}

function backspaceEmoji(input) {
  const arr = Array.from(input.value);
  if (arr.length === 0) return;
  arr.pop();
  input.value = arr.join("");
  input.focus();
}

function setupEmojiPickerIfNeeded() {
  if (cond !== "B") return;

  const grid = $("emojiGrid");
  if (!grid) return;

  // render emoji buttons using backend-provided order
  grid.innerHTML = "";
  for (const e of EMOJI_SET_ORDERED) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = e;

    btn.style.padding = "8px 0";
    btn.style.borderRadius = "10px";
    btn.style.border = "1px solid #ddd";
    btn.style.background = "white";
    btn.style.cursor = "pointer";

    btn.addEventListener("click", () => {
      if (!activeEmojiInput) return;
      insertAtCursor(activeEmojiInput, e);
    });

    grid.appendChild(btn);
  }

  // bind emoji inputs
  const inputs = document.querySelectorAll("input.emoji-input");
  inputs.forEach((inp) => {
    inp.addEventListener("focus", () => {
      activeEmojiInput = inp;
      showPalette(true);
    });
    inp.addEventListener("click", () => {
      activeEmojiInput = inp;
      showPalette(true);
    });
  });

  $("emojiBackspace")?.addEventListener("click", () => {
    if (!activeEmojiInput) return;
    backspaceEmoji(activeEmojiInput);
  });

  $("emojiClear")?.addEventListener("click", () => {
    if (!activeEmojiInput) return;
    activeEmojiInput.value = "";
    activeEmojiInput.focus();
  });

  $("emojiHide")?.addEventListener("click", () => showPalette(false));
}

// ----------------- Study flow -----------------
let createEventId = null;
let confirmEventId = null;
let loginEventId = null;

let createStart = 0;
let confirmStart = 0;
let loginStart = 0;

let loginAttempts = 0;
let loginWrongPositionsTotal = 0;
let waitTimer = null;

async function startEvent(type) {
  const r = await postJSON("/api/event/start", { condition: cond, event_type: type });
  if (!r.ok) throw new Error(r.error || "startEvent failed");
  return r.event_id;
}

async function endEvent(eventId, durationMs, success, attempts, note) {
  const r = await postJSON("/api/event/end", {
    event_id: eventId,
    duration_ms: Math.round(durationMs),
    success: success,
    attempts: attempts,
    note: note || null,
  });
  if (!r.ok) throw new Error(r.error || "endEvent failed");
}

async function setSecret(secretText) {
  // backend computes features; we just send raw for matching (not exported)
  const r = await postJSON("/api/secret/set", { condition: cond, secret_text: secretText });
  if (!r.ok) throw new Error(r.error || "setSecret failed");
}

async function checkSecretForStage(attemptText, stage) {
  const r = await postJSON("/api/secret/check", {
    condition: cond,
    attempt_text: attemptText,
    stage,
  });
  if (!r.ok) {
    const err = new Error(r.error || "checkSecret failed");
    err.remainingSeconds = Number(r.remaining_seconds || 0);
    throw err;
  }
  return {
    match: !!r.match,
    wrong_positions: Number(r.wrong_positions || 0),
  };
}

function formatMMSS(totalSec) {
  const sec = Math.max(0, Number(totalSec || 0));
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(sec % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function showStage(stageId) {
  const ids = ["stage-create", "stage-wait", "stage-login", "stage-done"];
  for (const id of ids) {
    const el = $(id);
    if (!el) continue;
    el.style.display = id === stageId ? "block" : "none";
  }
}

function startWaitCountdown(totalSeconds) {
  const countdown = $("waitCountdown");
  const msg = $("waitMsg");
  let remain = Math.max(0, Number(totalSeconds || 0));

  if (countdown) countdown.textContent = formatMMSS(remain);
  if (msg) msg.textContent = "";
  showStage("stage-wait");

  if (waitTimer) {
    clearInterval(waitTimer);
    waitTimer = null;
  }

  if (remain <= 0) {
    showStage("stage-login");
    return;
  }

  waitTimer = setInterval(() => {
    remain -= 1;
    if (countdown) countdown.textContent = formatMMSS(remain);
    if (remain <= 0) {
      clearInterval(waitTimer);
      waitTimer = null;
      showStage("stage-login");
    }
  }, 1000);
}

function beginLoginStage() {
  loginAttempts = 0;
  loginWrongPositionsTotal = 0;
  loginEventId = null;
  loginStart = 0;
  const msg = $("loginMsg");
  if (msg) msg.textContent = "";
  startWaitCountdown(INITIAL_RECALL_WAIT_SECONDS);
}

async function main() {
  setupEmojiPickerIfNeeded();

  // Stage 1: create + confirm in one form
  createEventId = await startEvent("create");
  createStart = nowMs();

  showStage("stage-create");
  $("btnConfirm").addEventListener("click", async () => {
    const createValue = $("create1").value;
    const confirmValue = $("confirm1").value;
    const confirmMsg = $("confirmMsg");

    if (!createValue) {
      confirmMsg.textContent = "Please enter a password.";
      return;
    }
    if (!confirmValue) {
      confirmMsg.textContent = "Please re-enter the password.";
      return;
    }
    if (countChars(createValue) < 6) {
      confirmMsg.textContent = "Password must be at least 6 characters.";
      return;
    }
    if (cond === "B" && !containsAnyEmoji(createValue)) {
      confirmMsg.textContent = "Emoji password must include at least one emoji.";
      return;
    }
    if (createValue !== confirmValue) {
      confirmMsg.textContent = "Create and confirm do not match. Password is not created.";
      return;
    }

    try {
      await setSecret(createValue);

      const createDur = nowMs() - createStart;
      await endEvent(createEventId, createDur, 1, null, null);

      confirmEventId = await startEvent("confirm");
      confirmStart = nowMs();

      const res = await checkSecretForStage(confirmValue, "confirm");
      const ok = res.match;
      const confirmDur = nowMs() - confirmStart;

      await endEvent(confirmEventId, confirmDur, ok ? 1 : 0, 1, ok ? null : "confirm mismatch");

      if (!ok) {
        createEventId = await startEvent("create");
        createStart = nowMs();
        confirmMsg.textContent = "Does not match. Please try again.";
        return;
      }

      if (INITIAL_RECALL_WAIT_SECONDS > 0) {
        confirmMsg.textContent = "Saved. Please wait for 10min recall.";
      } else {
        confirmMsg.textContent = "Saved. Proceed to login recall.";
      }
      beginLoginStage();
    } catch (e) {
      confirmMsg.textContent = e?.message || "Error. Try again.";
    }
  });

  // Stage 2: 10min recall login (max 3 attempts)
  $("btnLogin").addEventListener("click", async () => {
    const v = $("login1").value;

    if (!v) {
      $("loginMsg").textContent = "Enter password.";
      return;
    }

    try {
      if (!loginEventId) {
        loginEventId = await startEvent("login");
        loginStart = nowMs();
      }

      loginAttempts += 1;
      const res = await checkSecretForStage(v, "login");
      const ok = res.match;
      loginWrongPositionsTotal += Number(res.wrong_positions || 0);
      const dur = nowMs() - loginStart;

      const notePayload = {
        wrong_positions_this_attempt: Number(res.wrong_positions || 0),
        character_error_total: loginWrongPositionsTotal,
      };

      await endEvent(loginEventId, dur, ok ? 1 : 0, loginAttempts, JSON.stringify(notePayload));

      if (ok) {
        $("loginMsg").textContent = "Login success.";
        showStage("stage-done");
        return;
      }

      if (loginAttempts >= 3) {
        $("loginMsg").textContent = "Login failed (3 attempts).";
        showStage("stage-done");
        return;
      }

      $("loginMsg").textContent = `Incorrect. Try again (${loginAttempts}/3).`;
      loginEventId = await startEvent("login");
      loginStart = nowMs();
    } catch (e) {
      const remain = Number(e?.remainingSeconds || 0);
      if (remain > 0) {
        $("loginMsg").textContent = `10min recall not ready. Remaining ${formatMMSS(remain)}.`;
        startWaitCountdown(remain);
        return;
      }
      $("loginMsg").textContent = e?.message || "Error. Try again.";
    }
  });
}

main().catch(() => {});
