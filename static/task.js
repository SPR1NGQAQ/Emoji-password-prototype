/**
 * task.js (final)
 * - Drives create -> confirm -> login flow for A/B conditions
 * - Records timing events via backend API
 * - Emoji picker for condition B
 * - Emoji order is provided by backend per participant session (stable within session)
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

// ----------------- Study flow (create -> confirm -> login) -----------------
let createEventId = null;
let confirmEventId = null;
let loginEventId = null;

let createStart = 0;
let confirmStart = 0;
let loginStart = 0;

let loginAttempts = 0;

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

async function checkSecret(attemptText) {
  const r = await postJSON("/api/secret/check", { condition: cond, attempt_text: attemptText });
  if (!r.ok) throw new Error(r.error || "checkSecret failed");
  return !!r.match;
}

async function main() {
  setupEmojiPickerIfNeeded();

  // -------- Stage 1: Create --------
  createEventId = await startEvent("create");
  createStart = nowMs();

  $("btnCreate").addEventListener("click", async () => {
    const v = $("create1").value;

    if (!v) {
      $("createMsg").textContent = "Please enter a password.";
      return;
    }

    try {
      const dur = nowMs() - createStart;
      await setSecret(v);
      await endEvent(createEventId, dur, 1, null, null);

      $("stage-create").style.display = "none";
      $("stage-confirm").style.display = "block";
      $("createMsg").textContent = "";

      confirmEventId = await startEvent("confirm");
      confirmStart = nowMs();
    } catch (e) {
      $("createMsg").textContent = "Error saving. Please try again (or refresh).";
    }
  });

  // -------- Stage 2: Confirm --------
  $("btnConfirm").addEventListener("click", async () => {
    const v = $("confirm1").value;

    if (!v) {
      $("confirmMsg").textContent = "Please re-enter the password.";
      return;
    }

    try {
      const ok = await checkSecret(v);
      const dur = nowMs() - confirmStart;

      await endEvent(confirmEventId, dur, ok ? 1 : 0, null, ok ? null : "confirm mismatch");

      if (!ok) {
        $("confirmMsg").textContent = "Does not match. Try again.";
        confirmEventId = await startEvent("confirm");
        confirmStart = nowMs();
        return;
      }

      $("stage-confirm").style.display = "none";
      $("stage-login").style.display = "block";
      $("confirmMsg").textContent = "";

      loginAttempts = 0;
      loginEventId = await startEvent("login");
      loginStart = nowMs();
    } catch (e) {
      $("confirmMsg").textContent = "Error. Try again.";
    }
  });

  // -------- Stage 3: Login --------
  $("btnLogin").addEventListener("click", async () => {
    const v = $("login1").value;

    if (!v) {
      $("loginMsg").textContent = "Enter password.";
      return;
    }

    try {
      loginAttempts += 1;
      const ok = await checkSecret(v);
      const dur = nowMs() - loginStart;

      await endEvent(loginEventId, dur, ok ? 1 : 0, loginAttempts, ok ? null : "login failed");

      if (ok) {
        $("loginMsg").textContent = "Login success.";
        $("stage-login").style.display = "none";
        $("stage-done").style.display = "block";
        return;
      }

      if (loginAttempts >= 3) {
        $("loginMsg").textContent = "Login failed (3 attempts).";
        $("stage-login").style.display = "none";
        $("stage-done").style.display = "block";
        return;
      }

      $("loginMsg").textContent = `Incorrect. Try again (${loginAttempts}/3).`;
      loginEventId = await startEvent("login");
      loginStart = nowMs();
    } catch (e) {
      $("loginMsg").textContent = "Error. Try again.";
    }
  });
}

main().catch(() => {});
