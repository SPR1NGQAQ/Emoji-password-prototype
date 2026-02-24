from __future__ import annotations

import os
import csv
import json
import secrets
import sqlite3
import random
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from flask import Flask, g, redirect, render_template, request, session, url_for, abort, jsonify

# ---------------------------
# Basic config
# ---------------------------

APP_SECRET = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

DB_PATH = os.path.join(os.path.dirname(__file__), "emoji.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "data.csv")

EMOJI_SET_FIXED_80 = [
    # Faces (20)
    "😀","😃","😄","😁","😆","😅","😂","🙂","😉","😊",
    "😇","😍","😘","😜","🤔","😎","🥳","😴","😭","😡",

    # Animals (20)
    "🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯",
    "🦁","🐸","🐵","🐧","🐦","🐤","🐙","🐬","🐢","🐝",

    # Food (15)
    "🍎","🍊","🍌","🍉","🍓","🍒","🍕","🍔","🍟","🍩",
    "🍪","🍰","🍫","🍿","🍣",

    # Objects (15)
    "🚗","🚲","✈️","🚀","📱","💻","⌚","📷","🎧","🎮",
    "📚","✏️","🔑","💡","🧸",

    # Symbols / misc (10)
    "⭐","🔥","🌈","☀️","🌙","⚡","💎","🎵","⚽","🏆"
]

EMOJI_WHITELIST = set(EMOJI_SET_FIXED_80)

app = Flask(__name__)
app.secret_key = APP_SECRET


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------
# DB helpers
# ---------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """
    Creates tables if not exist.
    NOTE: If you changed schema, delete emoji.db and restart.
    """
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_code TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Stores raw password for confirm/login matching (prototype only)
        -- but exports only derived features (no raw password in CSV).
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('A','B')),

            secret_text TEXT NOT NULL,

            -- derived features (exportable)
            pw_tokens_len INTEGER,
            emoji_count INTEGER,
            emoji_single INTEGER,
            emoji_first INTEGER,
            emoji_at_end INTEGER,
            emoji_within INTEGER,
            emoji_only INTEGER,
            emojis_used TEXT,          -- comma-separated sequence of emojis used
            first_emoji_bias INTEGER,  -- 1 if first emoji used equals first emoji shown in menu

            created_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id),
            UNIQUE(participant_id, condition)
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('A','B')),
            event_type TEXT NOT NULL CHECK(event_type IN ('create','confirm','login')),
            started_at TEXT,
            ended_at TEXT,
            duration_ms INTEGER,
            success INTEGER,
            attempts INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS questionnaire (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,

            ease_a INTEGER,
            ease_b INTEGER,
            secure_a INTEGER,
            secure_b INTEGER,

            memory_a INTEGER,
            memory_b INTEGER,
            effort_b INTEGER,

            structure_b TEXT,
            placement_b TEXT,
            strategy_b TEXT,
            semantic_b INTEGER,

            prefer INTEGER,
            willing INTEGER,

            comment TEXT,
            created_at TEXT NOT NULL,

            FOREIGN KEY(participant_id) REFERENCES participants(id),
            UNIQUE(participant_id)
        );
        """
    )
    db.commit()


@app.before_request
def _init():
    init_db()


# ---------------------------
# Participant + order helpers
# ---------------------------

def current_participant_id() -> Optional[int]:
    code = session.get("participant_code")
    if not code:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM participants WHERE participant_code=?", (code,)).fetchone()
    return int(row["id"]) if row else None


def create_participant() -> str:
    db = get_db()
    code = secrets.token_urlsafe(6)
    db.execute("INSERT INTO participants(participant_code, created_at) VALUES (?,?)", (code, utc_now_iso()))
    db.commit()
    return code


def get_order_from_session() -> Optional[Tuple[str, str]]:
    order = session.get("order_choice")
    if order == "A_first":
        return ("A", "B")
    if order == "B_first":
        return ("B", "A")
    return None


def has_done_condition(pid: int, cond: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM secrets WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    return row is not None


# ---------------------------
# Emoji order per participant session (B only)
# ---------------------------

def get_or_make_emoji_order_for_session() -> List[str]:
    """
    All participants share the SAME 80 emoji set.
    Each participant gets a RANDOM ORDER (to reduce position bias),
        stable within the session.
    """
    key = "emoji_order_B"
    if key in session:
        try:
            arr = session[key]
            if isinstance(arr, list) and len(arr) == len(EMOJI_SET_FIXED_80):
                return arr
        except Exception:
            pass

    arr = EMOJI_SET_FIXED_80[:]
    random.shuffle(arr)
    session[key] = arr
    return arr


# ---------------------------
# Robust tokenization for multi-codepoint emojis
# ---------------------------

def tokenize_with_whitelist(text: str, whitelist: set[str]) -> List[str]:
    """
    Split string into tokens where any whitelist emoji is treated as ONE token.
    This handles emojis like '✈️' that are multiple codepoints.
    """
    if not text:
        return []

    # sort by length desc so longest match wins
    wl = sorted(whitelist, key=len, reverse=True)

    tokens: List[str] = []
    i = 0
    while i < len(text):
        matched = None
        for e in wl:
            if text.startswith(e, i):
                matched = e
                break
        if matched is not None:
            tokens.append(matched)
            i += len(matched)
        else:
            tokens.append(text[i])
            i += 1
    return tokens


# ---------------------------
# Feature extraction (no raw export)
# ---------------------------

def extract_secret_features(secret_text: str, cond: str) -> Dict[str, Any]:
    tokens = tokenize_with_whitelist(secret_text, EMOJI_WHITELIST)
    pw_tokens_len = len(tokens)
    
    # Analyze emoji usage and positions
    emoji_positions = [i for i, t in enumerate(tokens) if t in EMOJI_WHITELIST]
    emoji_count = len(emoji_positions)

    emoji_first = 1 if (emoji_count > 0 and emoji_positions[0] == 0) else 0
    emoji_only = 1 if (emoji_count > 0 and emoji_count == pw_tokens_len) else 0
    emoji_single = 1 if emoji_count == 1 else 0

    emoji_at_end = 1 if (emoji_count > 0 and emoji_positions[-1] == pw_tokens_len - 1) else 0
    emoji_within = 1 if any(pos < pw_tokens_len - 1 for pos in emoji_positions) else 0

    emojis_used_seq = [tokens[i] for i in emoji_positions]
    emojis_used = ",".join(emojis_used_seq) if emojis_used_seq else ""

    # Bias: If condition B and at least one emoji used, check if first emoji used equals first emoji shown in menu
    first_emoji_bias = 0
    if cond == "B" and emoji_count > 0:
        shown = get_or_make_emoji_order_for_session()
        first_shown = shown[0] if shown else None
        first_used = emojis_used_seq[0] if emojis_used_seq else None
        if first_shown is not None and first_used == first_shown:
            first_emoji_bias = 1

    return {
        "pw_tokens_len": pw_tokens_len,
        "emoji_count": emoji_count,
        "emoji_single": emoji_single,
        "emoji_first": emoji_first,
        "emoji_at_end": emoji_at_end,
        "emoji_within": emoji_within,
        "emoji_only": emoji_only,
        "emojis_used": emojis_used,
        "first_emoji_bias": first_emoji_bias,
    }


# ---------------------------
# CSV export (one row per participant)
# ---------------------------

def export_participant_to_csv(pid: int, participant_code: str) -> None:
    db = get_db()

    q = db.execute("SELECT * FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
    events = db.execute("SELECT * FROM events WHERE participant_id=? ORDER BY id", (pid,)).fetchall()

    secrets_rows = db.execute(
        """
        SELECT condition,
               pw_tokens_len, emoji_count, emoji_single, emoji_at_end, emoji_within, emojis_used, first_emoji_bias, emoji_first, emoji_only
        FROM secrets
        WHERE participant_id=?
        """,
        (pid,),
    ).fetchall()

    summary = {
        "participant_code": participant_code,

        "A_create_time_ms": None,
        "A_confirm_time_ms": None,
        "A_login_time_ms": None,
        "A_login_success": None,
        "A_login_attempts": None,

        "B_create_time_ms": None,
        "B_confirm_time_ms": None,
        "B_login_time_ms": None,
        "B_login_success": None,
        "B_login_attempts": None,

        # derived structure features (no raw secret)
        "A_pw_tokens_len": None,
        "B_pw_tokens_len": None,
        "B_emoji_count": None,
        "B_emoji_single": None,
        "B_emoji_at_end": None,
        "B_emoji_within": None,
        "B_emoji_first": None,
        "B_emoji_only": None,
        "B_emojis_used": None,
        "B_first_emoji_bias": None,

        # questionnaire
        "ease_a": None,
        "ease_b": None,
        "secure_a": None,
        "secure_b": None,
        "memory_a": None,
        "memory_b": None,
        "effort_b": None,
        "strategy_b": None,
        "semantic_b": None,
        "prefer": None,
        "willing": None,
        "comment": None,
        "structure_b": None,
        "placement_b": None,
    }

    # events -> last observed values
    for e in events:
        cond = e["condition"]
        etype = e["event_type"]
        prefix = cond + "_"

        if etype == "create":
            summary[prefix + "create_time_ms"] = e["duration_ms"]
        elif etype == "confirm":
            summary[prefix + "confirm_time_ms"] = e["duration_ms"]
        elif etype == "login":
            summary[prefix + "login_time_ms"] = e["duration_ms"]
            summary[prefix + "login_success"] = e["success"]
            summary[prefix + "login_attempts"] = e["attempts"]

    # secrets -> derived features
    for r in secrets_rows:
        c = r["condition"]
        if c == "A":
            summary["A_pw_tokens_len"] = r["pw_tokens_len"]
        elif c == "B":
            summary["B_pw_tokens_len"] = r["pw_tokens_len"]
            summary["B_emoji_count"] = r["emoji_count"]
            summary["B_emoji_single"] = r["emoji_single"]
            summary["B_emoji_at_end"] = r["emoji_at_end"]
            summary["B_emoji_within"] = r["emoji_within"]
            summary["B_emojis_used"] = r["emojis_used"]
            summary["B_first_emoji_bias"] = r["first_emoji_bias"]
            summary["B_emoji_first"] = r["emoji_first"]
            summary["B_emoji_only"] = r["emoji_only"]

    # questionnaire
    if q:
        summary["ease_a"] = q["ease_a"]
        summary["ease_b"] = q["ease_b"]
        summary["secure_a"] = q["secure_a"]
        summary["secure_b"] = q["secure_b"]
        summary["memory_a"] = q["memory_a"]
        summary["memory_b"] = q["memory_b"]
        summary["effort_b"] = q["effort_b"]
        summary["strategy_b"] = q["strategy_b"]
        summary["semantic_b"] = q["semantic_b"]
        summary["prefer"] = q["prefer"]
        summary["willing"] = q["willing"]
        summary["comment"] = q["comment"]
        summary["structure_b"] = q["structure_b"]
        summary["placement_b"] = q["placement_b"]

    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(summary)


# ---------------------------
# Pages
# ---------------------------

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/consent", methods=["GET", "POST"])
def consent():
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            return render_template("consent.html", error="You must agree to continue.")
        code = create_participant()
        session["participant_code"] = code
        session.pop("order_choice", None)
        session.pop("emoji_order_B", None)  # reset emoji order for fresh participant session
        return redirect(url_for("choose_order"))
    return render_template("consent.html", error=None)


@app.route("/choose-order", methods=["GET", "POST"])
def choose_order():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    if request.method == "POST":
        choice = request.form.get("order")
        if choice not in ("A_first", "B_first"):
            return render_template("choose_order.html", error="Please select an order.")
        session["order_choice"] = choice
        return redirect(url_for("start"))

    return render_template("choose_order.html", error=None)


@app.route("/start")
def start():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    order = get_order_from_session()
    if order is None:
        return redirect(url_for("choose_order"))

    first, second = order
    done_first = has_done_condition(pid, first)
    done_second = has_done_condition(pid, second)

    if not done_first:
        next_cond = first
    elif not done_second:
        next_cond = second
    else:
        db = get_db()
        q = db.execute("SELECT 1 FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
        if q:
            return redirect(url_for("done"))
        return redirect(url_for("questionnaire"))

    return render_template(
        "start.html",
        participant_code=session["participant_code"],
        first=first,
        second=second,
        done_first=done_first,
        done_second=done_second,
        next_cond=next_cond,
    )


@app.route("/task/<cond>", methods=["GET"])
def task(cond: str):
    if cond not in ("A", "B"):
        abort(404)

    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    order = get_order_from_session()
    if order is None:
        return redirect(url_for("choose_order"))

    first, second = order

    if cond == second and not has_done_condition(pid, first):
        return redirect(url_for("task", cond=first))

    if has_done_condition(pid, cond):
        return redirect(url_for("start"))

    emojis = []
    if cond == "B":
        emojis = get_or_make_emoji_order_for_session()

    return render_template("task.html", condition=cond, emojis=emojis)


@app.route("/questionnaire", methods=["GET", "POST"])
def questionnaire():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    order = get_order_from_session()
    if order is None:
        return redirect(url_for("choose_order"))

    first, second = order
    if not (has_done_condition(pid, first) and has_done_condition(pid, second)):
        return redirect(url_for("start"))

    db = get_db()
    existing = db.execute("SELECT 1 FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
    if existing:
        return redirect(url_for("done"))

    if request.method == "POST":
        def to_int(name: str) -> Optional[int]:
            v = request.form.get(name)
            if v is None or v == "":
                return None
            try:
                return int(v)
            except ValueError:
                return None

        payload = {
            "ease_a": to_int("ease_a"),
            "ease_b": to_int("ease_b"),
            "secure_a": to_int("secure_a"),
            "secure_b": to_int("secure_b"),
            "memory_a": to_int("memory_a"),
            "memory_b": to_int("memory_b"),
            "effort_b": to_int("effort_b"),
            "structure_b": (request.form.get("structure_b") or "").strip(),
            "placement_b": (request.form.get("placement_b") or "").strip(),
            "strategy_b": (request.form.get("strategy_b") or "").strip(),
            "semantic_b": to_int("semantic_b"),
            "prefer": to_int("prefer"),
            "willing": to_int("willing"),
            "comment": (request.form.get("comment") or "").strip(),
        }

        # --- branch override for emoji_only ---
        if payload["structure_b"] == "emoji_only":
            payload["placement_b"] = ""  # not applicable
            payload["strategy_b"] = (request.form.get("strategy_b_emoji_only") or "").strip()
            payload["semantic_b"] = to_int("semantic_b_emoji_only")

        required_scale = [
            "ease_a","ease_b","secure_a","secure_b",
            "memory_a","memory_b","effort_b",
            "prefer","willing",
        ]
        if any(payload[k] is None or not (1 <= payload[k] <= 7) for k in required_scale):
            return render_template("questionnaire.html", error="Please answer all required scale questions (1–7).")

        # structure_b is required and must be one of the options
        if payload["structure_b"] == "":
            return render_template("questionnaire.html", error="Please choose the emoji-password structure option.")

        # If structure_b is emoji_only, placement_b is not applicable, but strategy_b and semantic_b are still required.
        if payload["structure_b"] == "emoji_only":
            if payload["strategy_b"] == "":
                return render_template("questionnaire.html", error="Please choose a strategy for the emoji-only password.")
            if payload["semantic_b"] is None or not (1 <= payload["semantic_b"] <= 7):
                return render_template("questionnaire.html", error="Please answer the meaning/story question (1–7).")
        else:
            if payload["placement_b"] == "":
                return render_template("questionnaire.html", error="Please choose the emoji placement option.")
            if payload["strategy_b"] == "":
                return render_template("questionnaire.html", error="Please choose a strategy option for emoji selection.")
            if payload["semantic_b"] is None or not (1 <= payload["semantic_b"] <= 7):
                return render_template("questionnaire.html", error="Please answer the semantic relation question (1–7).")

        db.execute(
            """
            INSERT INTO questionnaire(
                participant_id,
                ease_a, ease_b,
                secure_a, secure_b,
                memory_a, memory_b,
                effort_b,
                structure_b, placement_b,
                strategy_b,
                semantic_b,
                prefer, willing,
                comment,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                payload["ease_a"], payload["ease_b"],
                payload["secure_a"], payload["secure_b"],
                payload["memory_a"], payload["memory_b"],
                payload["effort_b"],
                payload["structure_b"], payload["placement_b"],
                payload["strategy_b"],
                payload["semantic_b"],
                payload["prefer"], payload["willing"],
                payload["comment"],
                utc_now_iso(),
            ),
        )
        db.commit()
        return redirect(url_for("done"))

    return render_template("questionnaire.html", error=None)


@app.route("/done")
def done():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    code = session.get("participant_code")

    try:
        export_participant_to_csv(pid, code)
    except Exception:
        pass

    return render_template("done.html", participant_code=code)


# ---------------------------
# API
# ---------------------------

@app.route("/api/event/start", methods=["POST"])
def api_event_start():
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401

    data = request.get_json(force=True)
    cond = data.get("condition")
    event_type = data.get("event_type")
    if cond not in ("A", "B") or event_type not in ("create", "confirm", "login"):
        return jsonify({"ok": False, "error": "bad params"}), 400

    db = get_db()
    cur = db.execute(
        """
        INSERT INTO events(participant_id, condition, event_type, started_at, created_at)
        VALUES (?,?,?,?,?)
        """,
        (pid, cond, event_type, utc_now_iso(), utc_now_iso()),
    )
    db.commit()
    return jsonify({"ok": True, "event_id": cur.lastrowid})


@app.route("/api/event/end", methods=["POST"])
def api_event_end():
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401

    data = request.get_json(force=True)
    event_id = data.get("event_id")
    duration_ms = data.get("duration_ms")
    success = data.get("success")
    attempts = data.get("attempts")
    note = data.get("note")

    if not isinstance(event_id, int):
        return jsonify({"ok": False, "error": "bad event_id"}), 400

    db = get_db()
    row = db.execute("SELECT id FROM events WHERE id=? AND participant_id=?", (event_id, pid)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404

    db.execute(
        """
        UPDATE events
        SET ended_at=?, duration_ms=?, success=?, attempts=?, note=?
        WHERE id=? AND participant_id=?
        """,
        (utc_now_iso(), duration_ms, success, attempts, note, event_id, pid),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/secret/set", methods=["POST"])
def api_secret_set():
    """
    Stores raw secret_text for confirm/login matching (prototype only).
    Also stores derived features for analysis (exportable).
    Raw secret is NOT exported to CSV.
    """
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401

    data = request.get_json(force=True)
    cond = data.get("condition")
    secret_text = data.get("secret_text")
    if cond not in ("A", "B") or not isinstance(secret_text, str) or len(secret_text) < 1:
        return jsonify({"ok": False, "error": "bad params"}), 400

    feats = extract_secret_features(secret_text, cond)

    db = get_db()
    db.execute(
        """
        INSERT INTO secrets(
            participant_id, condition, secret_text,
            pw_tokens_len, emoji_count, emoji_single, emoji_first, emoji_at_end, emoji_within, emoji_only,
            emojis_used, first_emoji_bias,
            created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(participant_id, condition)
        DO UPDATE SET
            secret_text=excluded.secret_text,
            pw_tokens_len=excluded.pw_tokens_len,
            emoji_count=excluded.emoji_count,
            emoji_single=excluded.emoji_single,
            emoji_first=excluded.emoji_first,
            emoji_at_end=excluded.emoji_at_end,
            emoji_within=excluded.emoji_within,
            emoji_only=excluded.emoji_only,
            emojis_used=excluded.emojis_used,
            first_emoji_bias=excluded.first_emoji_bias,
            created_at=excluded.created_at
        """,
        (
            pid, cond, secret_text,
            feats["pw_tokens_len"], feats["emoji_count"], feats["emoji_single"], feats["emoji_first"],
            feats["emoji_at_end"], feats["emoji_within"], feats["emoji_only"],
            feats["emojis_used"], feats["first_emoji_bias"],
            utc_now_iso(),
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/secret/check", methods=["POST"])
def api_secret_check():
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401

    data = request.get_json(force=True)
    cond = data.get("condition")
    attempt_text = data.get("attempt_text")
    if cond not in ("A", "B") or not isinstance(attempt_text, str):
        return jsonify({"ok": False, "error": "bad params"}), 400

    db = get_db()
    row = db.execute(
        "SELECT secret_text FROM secrets WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "no secret set"}), 400

    ok = (attempt_text == row["secret_text"])
    return jsonify({"ok": True, "match": ok})


if __name__ == "__main__":
    app.run(debug=True)