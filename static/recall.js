async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await res.json();
}

const $ = (id) => document.getElementById(id);
const root = $("recallRoot");
const cond = root?.dataset.cond || "A";
let EMOJI_SET_ORDERED = [];
try {
  EMOJI_SET_ORDERED = JSON.parse(root?.dataset.emojis || "[]");
  if (!Array.isArray(EMOJI_SET_ORDERED)) EMOJI_SET_ORDERED = [];
} catch (_e) {
  EMOJI_SET_ORDERED = [];
}

let activeEmojiInput = null;

function showPalette(show) {
  const pal = $("emojiPalette");
  if (!pal) return;
  pal.style.display = show ? "block" : "none";
}

function insertAtCursor(input, text) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  const before = input.value.slice(0, start);
  const after = input.value.slice(end);
  input.value = before + text + after;
  const pos = start + text.length;
  input.setSelectionRange(pos, pos);
  input.focus();
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

async function main() {
  setupEmojiPickerIfNeeded();

  const startedAt = performance.now();
  let attempts = 0;

  $("btnRecall").addEventListener("click", async () => {
    const v = $("recall1").value;
    if (!v) {
      $("recallMsg").textContent = "Please enter password.";
      return;
    }

    try {
      attempts += 1;
      const totalDuration = performance.now() - startedAt;
      const r = await postJSON("/api/recall/attempt", {
        condition: cond,
        attempt_text: v,
        total_duration_ms: Math.round(totalDuration),
      });

      if (!r.ok) {
        $("recallMsg").textContent = r.error || "Error. Please try again.";
        return;
      }

      if (r.matched) {
        $("recallMsg").textContent = "Correct.";
      } else {
        $("recallMsg").textContent = `Incorrect. Try again (${r.attempt_no}/3).`;
      }

      if (r.finished) {
        $("stage-recall").style.display = "none";
        $("stage-done").style.display = "block";
      }
    } catch (_e) {
      $("recallMsg").textContent = "Network/server error. Please try again.";
    }
  });
}

main().catch(() => {});
