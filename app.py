from __future__ import annotations

import os
import csv
import json
import sqlite3
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from flask import Flask, g, redirect, render_template, request, session, url_for, abort, jsonify

# ---------------------------
# Basic config
# ---------------------------

APP_SECRET = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
ENABLE_48H_GATE = False
ENABLE_10MIN_GATE = False
TEN_MIN_DELAY_SECONDS = 10 * 60

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

        -- Stores raw password for matching and post-recall comparison export.
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
            effort_b INTEGER,

            structure_b TEXT,
            placement_b TEXT,
            strategy_b TEXT,
            semantic_b INTEGER,
            willing INTEGER,
            recall_difficulty_a INTEGER,
            recall_difficulty_b INTEGER,

            comment TEXT,
            created_at TEXT NOT NULL,

            FOREIGN KEY(participant_id) REFERENCES participants(id),
            UNIQUE(participant_id)
        );

        CREATE TABLE IF NOT EXISTS recall_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('A','B')),
            total_duration_ms INTEGER,
            attempts INTEGER,
            success INTEGER,
            final_attempt_text TEXT,
            final_edit_distance INTEGER,
            final_wrong_positions INTEGER,
            final_error_distribution TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(participant_id, condition),
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS recall_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('A','B')),
            attempt_no INTEGER NOT NULL,
            attempt_text TEXT NOT NULL,
            matched INTEGER NOT NULL,
            edit_distance INTEGER,
            wrong_positions INTEGER,
            error_distribution TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS export_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL UNIQUE,
            exported_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );
        """
    )

    # Questionnaire extension (for recall-stage survey)
    ensure_column(db, "participants", "group_condition", "TEXT")
    ensure_column(db, "questionnaire", "recall_difficulty_a", "INTEGER")
    ensure_column(db, "questionnaire", "recall_difficulty_b", "INTEGER")
    ensure_column(db, "questionnaire", "emoji_form_self", "TEXT")
    ensure_column(db, "questionnaire", "emoji_only_hardest", "TEXT")
    ensure_column(db, "questionnaire", "emoji_only_mistake", "TEXT")
    ensure_column(db, "questionnaire", "mixed_hardest_part", "TEXT")
    ensure_column(db, "questionnaire", "mixed_style", "TEXT")

    db.commit()


def table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def ensure_column(db: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = table_columns(db, table)
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
    code = normalize_participant_code(code)
    session["participant_code"] = code
    db = get_db()
    row = db.execute("SELECT id FROM participants WHERE participant_code=?", (code,)).fetchone()
    return int(row["id"]) if row else None


def allocate_balanced_group_condition(db: sqlite3.Connection) -> str:
    row = db.execute(
        """
        SELECT
            SUM(CASE WHEN group_condition='A' THEN 1 ELSE 0 END) AS cnt_a,
            SUM(CASE WHEN group_condition='B' THEN 1 ELSE 0 END) AS cnt_b
        FROM participants
        """
    ).fetchone()
    cnt_a = int(row["cnt_a"] or 0)
    cnt_b = int(row["cnt_b"] or 0)

    if cnt_a < cnt_b:
        return "A"
    if cnt_b < cnt_a:
        return "B"
    return random.choice(["A", "B"])


def get_assigned_condition(pid: int) -> str:
    db = get_db()
    row = db.execute("SELECT group_condition FROM participants WHERE id=?", (pid,)).fetchone()
    if row and row["group_condition"] in ("A", "B"):
        return str(row["group_condition"])

    # Backfill old rows created before this column existed.
    cond = allocate_balanced_group_condition(db)
    db.execute("UPDATE participants SET group_condition=? WHERE id=?", (cond, pid))
    db.commit()
    return cond


def is_valid_participant_code(code: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]{4,32}", code) is not None


def normalize_participant_code(code: str) -> str:
    return code.strip().lower()


def initial_done(pid: int) -> bool:
    cond = get_assigned_condition(pid)
    return has_done_condition(pid, cond)


def recall_done_condition(pid: int, cond: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM recall_results WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    return row is not None


def recall_done(pid: int) -> bool:
    cond = get_assigned_condition(pid)
    return recall_done_condition(pid, cond)


def parse_iso_dt(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def get_participant_created_at(pid: int) -> Optional[datetime]:
    db = get_db()
    row = db.execute(
        "SELECT created_at FROM participants WHERE id=?",
        (pid,),
    ).fetchone()
    if not row:
        return None
    return parse_iso_dt(row["created_at"])


def recall_delay_since_start_seconds(pid: int) -> Optional[int]:
    start_time = get_participant_created_at(pid)
    if start_time is None:
        return None
    now = datetime.now(timezone.utc)
    if start_time.tzinfo is None:
        now = now.replace(tzinfo=None)
    delta = now - start_time
    return max(0, int(delta.total_seconds()))


def format_duration_hms(total_seconds: Optional[int]) -> str:
    if total_seconds is None:
        return "N/A"
    secs = int(total_seconds)
    days = secs // 86400
    rem = secs % 86400
    hours = rem // 3600
    rem %= 3600
    minutes = rem // 60
    seconds = rem % 60
    return f"{days}d {hours}h {minutes}m {seconds}s"


def seconds_to_48h_window(pid: int) -> Optional[int]:
    if not ENABLE_48H_GATE:
        return 0
    created = get_participant_created_at(pid)
    if created is None:
        return None
    target = created + timedelta(hours=48)
    now = datetime.now(timezone.utc)
    if created.tzinfo is None:
        now = now.replace(tzinfo=None)
    delta = target - now
    return max(0, int(delta.total_seconds()))


def cond_label(cond: str) -> str:
    return "Traditional password" if cond == "A" else "Emoji password"


def has_done_condition(pid: int, cond: str) -> bool:
    db = get_db()
    row = db.execute(
        """
        SELECT 1 FROM events
        WHERE participant_id=? AND condition=?
          AND event_type='login'
          AND ended_at IS NOT NULL
          AND attempts IS NOT NULL
        LIMIT 1
        """,
        (pid, cond),
    ).fetchone()
    return row is not None


def seconds_until_initial_login_allowed(pid: int, cond: str) -> Optional[int]:
    if not ENABLE_10MIN_GATE:
        return 0

    db = get_db()
    row = db.execute(
        """
        SELECT ended_at
        FROM events
        WHERE participant_id=? AND condition=?
          AND event_type='confirm'
          AND success=1
          AND ended_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (pid, cond),
    ).fetchone()
    if not row:
        return TEN_MIN_DELAY_SECONDS

    ended_at = parse_iso_dt(row["ended_at"])
    if ended_at is None:
        return TEN_MIN_DELAY_SECONDS

    now = datetime.now(timezone.utc)
    if ended_at.tzinfo is None:
        now = now.replace(tzinfo=None)

    elapsed = int((now - ended_at).total_seconds())
    return max(0, TEN_MIN_DELAY_SECONDS - elapsed)


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
    owner_key = "emoji_order_B_owner"
    current_owner = normalize_participant_code(session.get("participant_code") or "")

    if session.get(owner_key) == current_owner and key in session:
        try:
            arr = session[key]
            if isinstance(arr, list) and len(arr) == len(EMOJI_SET_FIXED_80):
                return arr
        except Exception:
            pass

    arr = EMOJI_SET_FIXED_80[:]
    random.shuffle(arr)
    session[key] = arr
    session[owner_key] = current_owner
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


def levenshtein_distance(a: List[str], b: List[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ta in enumerate(a, start=1):
        cur = [i]
        for j, tb in enumerate(b, start=1):
            cost = 0 if ta == tb else 1
            cur.append(min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + cost,
            ))
        prev = cur
    return prev[-1]


def analyze_recall_error(target_text: str, attempt_text: str) -> Dict[str, Any]:
    target_tokens = tokenize_with_whitelist(target_text, EMOJI_WHITELIST)
    attempt_tokens = tokenize_with_whitelist(attempt_text, EMOJI_WHITELIST)

    min_len = min(len(target_tokens), len(attempt_tokens))
    substitutions = sum(1 for i in range(min_len) if target_tokens[i] != attempt_tokens[i])
    missing = max(0, len(target_tokens) - len(attempt_tokens))
    extra = max(0, len(attempt_tokens) - len(target_tokens))
    wrong_positions = substitutions + missing + extra

    return {
        "edit_distance": levenshtein_distance(target_tokens, attempt_tokens),
        "wrong_positions": wrong_positions,
        "error_distribution": {
            "substitution": substitutions,
            "missing": missing,
            "extra": extra,
            "target_len": len(target_tokens),
            "attempt_len": len(attempt_tokens),
        }
    }


# ---------------------------
# Feature extraction
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
    assigned_cond = get_assigned_condition(pid)

    create_event = db.execute(
        """
        SELECT duration_ms
        FROM events
        WHERE participant_id=? AND condition=? AND event_type='create'
        ORDER BY id DESC LIMIT 1
        """,
        (pid, assigned_cond),
    ).fetchone()
    login_event = db.execute(
        """
        SELECT duration_ms, attempts, note
        FROM events
        WHERE participant_id=? AND condition=? AND event_type='login'
        ORDER BY id DESC LIMIT 1
        """,
        (pid, assigned_cond),
    ).fetchone()
    recall_row = db.execute(
        """
        SELECT total_duration_ms, attempts, final_wrong_positions
        FROM recall_results
        WHERE participant_id=? AND condition=?
        """,
        (pid, assigned_cond),
    ).fetchone()
    recall_error_row = db.execute(
        """
        SELECT SUM(wrong_positions) AS total_wrong_positions
        FROM recall_attempts
        WHERE participant_id=? AND condition=?
        """,
        (pid, assigned_cond),
    ).fetchone()
    secret_row = db.execute(
        "SELECT secret_text FROM secrets WHERE participant_id=? AND condition=?",
        (pid, assigned_cond),
    ).fetchone()

    ten_min_error = None
    if login_event and login_event["note"]:
        try:
            note_json = json.loads(login_event["note"])
            if isinstance(note_json, dict):
                ten_min_error = note_json.get("character_error_total")
        except Exception:
            ten_min_error = None

    post_ease = q["ease_a"] if (q and assigned_cond == "A") else (q["ease_b"] if q else None)
    post_security = q["secure_a"] if (q and assigned_cond == "A") else (q["secure_b"] if q else None)
    post_recall_conf = q["recall_difficulty_a"] if (q and assigned_cond == "A") else (q["recall_difficulty_b"] if q else None)

    summary = {
        "Unique participant ID (username)": participant_code,
        "Participant group number": 1 if assigned_cond == "A" else 2,
        "Participant password": secret_row["secret_text"] if secret_row else None,
        "Time to create password (ms)": create_event["duration_ms"] if create_event else None,
        "Time to recall password (10 minutes)": login_event["duration_ms"] if login_event else None,
        "Number of login attempts (10 minutes)": login_event["attempts"] if login_event else None,
        "Character error rate (10 minutes)": ten_min_error,
        "Time to recall password (48 hours)": recall_row["total_duration_ms"] if recall_row else None,
        "Number of login attempts (48 hours)": recall_row["attempts"] if recall_row else None,
        "Character error rate (48 hours)": int(recall_error_row["total_wrong_positions"] or 0) if recall_error_row else None,

        "Post recall ease rating": post_ease,
        "Post recall security rating": post_security,
        "Post recall confidence rating": post_recall_conf,
        "Emoji password required more effort": q["effort_b"] if q else None,
        "Emoji password structure": q["structure_b"] if q else None,
        "Emoji selection strategy": q["strategy_b"] if q else None,
        "Willing to use emoji password": q["willing"] if q else None,
        "Emoji only hardest part": q["emoji_only_hardest"] if q else None,
        "Emoji only common mistake": q["emoji_only_mistake"] if q else None,
        "Mixed password hardest part": q["mixed_hardest_part"] if q else None,
        "Mixed password style": q["mixed_style"] if q else None,
        "Questionnaire comment": q["comment"] if q else None,
    }

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
        session.pop("participant_code", None)
        session.pop("recall_mode", None)
        session.pop("emoji_order_B", None)  # reset emoji order for fresh participant session
        session.pop("emoji_order_B_owner", None)
        return redirect(url_for("set_participant_code"))
    return render_template("consent.html", error=None)


@app.route("/set-participant-code", methods=["GET", "POST"])
def set_participant_code():
    pid = current_participant_id()

    if request.method == "POST":
        new_code = normalize_participant_code(request.form.get("participant_code") or "")
        if not is_valid_participant_code(new_code):
            return render_template(
                "set_participant_code.html",
                error="Code must be 4–32 chars and use only letters, numbers, underscore (_).",
            )

        db = get_db()
        if pid is None:
            exists = db.execute(
                "SELECT 1 FROM participants WHERE participant_code=?",
                (new_code,),
            ).fetchone()
        else:
            exists = db.execute(
                "SELECT 1 FROM participants WHERE participant_code=? AND id<>?",
                (new_code, pid),
            ).fetchone()
        if exists:
            return render_template("set_participant_code.html", error="This code is already used. Please choose another one.")

        if pid is None:
            group_condition = allocate_balanced_group_condition(db)
            db.execute(
                "INSERT INTO participants(participant_code, created_at, group_condition) VALUES (?,?,?)",
                (new_code, utc_now_iso(), group_condition),
            )
        else:
            db.execute("UPDATE participants SET participant_code=? WHERE id=?", (new_code, pid))
        db.commit()
        session["participant_code"] = new_code
        session.pop("recall_mode", None)
        session.pop("emoji_order_B", None)
        session.pop("emoji_order_B_owner", None)
        return redirect(url_for("start"))

    return render_template("set_participant_code.html", error=None)


@app.route("/recall-access", methods=["GET", "POST"])
def recall_access():
    if request.method == "POST":
        code = normalize_participant_code(request.form.get("participant_code") or "")
        if code == "":
            return render_template("recall_access.html", error="Please enter participant code.", remain_hours=None)

        db = get_db()
        row = db.execute("SELECT id FROM participants WHERE participant_code=?", (code,)).fetchone()
        if not row:
            return render_template("recall_access.html", error="Participant code not found.", remain_hours=None)

        pid = int(row["id"])
        if not initial_done(pid):
            return render_template("recall_access.html", error="Initial stage is not finished yet.", remain_hours=None)

        remain = seconds_to_48h_window(pid)
        if remain is not None and remain > 0:
            remain_hours = round(remain / 3600, 2)
            return render_template(
                "recall_access.html",
                error="Recall is available 48 hours after first session.",
                remain_hours=remain_hours,
            )

        session["participant_code"] = code
        session["recall_mode"] = True
        session["recall_delay_seconds"] = recall_delay_since_start_seconds(pid)
        session.pop("emoji_order_B", None)
        session.pop("emoji_order_B_owner", None)
        return redirect(url_for("start"))

    return render_template("recall_access.html", error=None, remain_hours=None)


@app.route("/wait-recall")
def wait_recall():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    if not initial_done(pid):
        return redirect(url_for("start"))

    remain = seconds_to_48h_window(pid)
    remain_hours = round((remain or 0) / 3600, 2)
    return render_template("wait_recall.html", remain_hours=remain_hours)


@app.route("/start")
def start():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    assigned_cond = get_assigned_condition(pid)
    mode = "initial"

    if not has_done_condition(pid, assigned_cond):
        next_cond = assigned_cond
    else:
        if not session.get("recall_mode"):
            return redirect(url_for("wait_recall"))

        mode = "recall"
        if not recall_done_condition(pid, assigned_cond):
            next_cond = assigned_cond
        else:
            db = get_db()
            q = db.execute("SELECT 1 FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
            exported = db.execute("SELECT 1 FROM export_log WHERE participant_id=?", (pid,)).fetchone()
            if exported:
                return redirect(url_for("done"))
            if q:
                return redirect(url_for("done"))
            return redirect(url_for("questionnaire"))

    return render_template(
        "start.html",
        participant_code=session["participant_code"],
        assigned_cond=assigned_cond,
        assigned_label=cond_label(assigned_cond),
        done_task=has_done_condition(pid, assigned_cond),
        done_recall=recall_done_condition(pid, assigned_cond),
        next_cond=next_cond,
        mode=mode,
        recall_delay_text=format_duration_hms(session.get("recall_delay_seconds")) if mode == "recall" else None,
        next_label=cond_label(next_cond),
    )


@app.route("/task/<cond>", methods=["GET"])
def task(cond: str):
    if cond not in ("A", "B"):
        abort(404)

    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    assigned_cond = get_assigned_condition(pid)
    if cond != assigned_cond:
        return redirect(url_for("task", cond=assigned_cond))

    recall_mode = bool(session.get("recall_mode"))

    if not recall_mode:
        if has_done_condition(pid, cond):
            return redirect(url_for("start"))
    else:
        if not initial_done(pid):
            return redirect(url_for("start"))
        if recall_done_condition(pid, cond):
            return redirect(url_for("start"))

    emojis = []
    if cond == "B":
        emojis = get_or_make_emoji_order_for_session()

    if recall_mode:
        return render_template("recall_task.html", condition=cond, emojis=emojis, cond_label=cond_label(cond))
    return render_template(
        "task.html",
        condition=cond,
        emojis=emojis,
        initial_recall_wait_seconds=(TEN_MIN_DELAY_SECONDS if ENABLE_10MIN_GATE else 0),
    )


@app.route("/questionnaire", methods=["GET", "POST"])
def questionnaire():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    assigned_cond = get_assigned_condition(pid)
    if not recall_done_condition(pid, assigned_cond):
        return redirect(url_for("start"))

    db = get_db()
    existing = db.execute("SELECT 1 FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
    if existing:
        return redirect(url_for("done"))

    if request.method == "POST":
        if request.form.get("skip") == "1":
            return redirect(url_for("done"))

        def to_int(name: str) -> Optional[int]:
            v = request.form.get(name)
            if v is None or v == "":
                return None
            try:
                return int(v)
            except ValueError:
                return None

        payload = {
            "ease": to_int("ease_self"),
            "secure": to_int("secure_self"),
            "recall_confidence": to_int("recall_confidence_self"),
            "effort_b": to_int("effort_b"),
            "emoji_form_self": (request.form.get("emoji_form_self") or "").strip(),
            "strategy_b": (request.form.get("strategy_b") or "").strip(),
            "emoji_only_hardest": (request.form.get("emoji_only_hardest") or "").strip(),
            "emoji_only_mistake": (request.form.get("emoji_only_mistake") or "").strip(),
            "mixed_hardest_part": (request.form.get("mixed_hardest_part") or "").strip(),
            "mixed_style": (request.form.get("mixed_style") or "").strip(),
            "willing": to_int("willing"),
            "comment": (request.form.get("comment") or "").strip(),
        }

        if payload["ease"] is not None and not (1 <= payload["ease"] <= 7):
            return render_template("questionnaire.html", error="Ease rating must be 1–7.", assigned_cond=assigned_cond)
        if payload["secure"] is not None and not (1 <= payload["secure"] <= 7):
            return render_template("questionnaire.html", error="Security rating must be 1–7.", assigned_cond=assigned_cond)
        if payload["recall_confidence"] is not None and not (1 <= payload["recall_confidence"] <= 7):
            return render_template("questionnaire.html", error="Recall confidence must be 1–7.", assigned_cond=assigned_cond)

        if payload["willing"] is not None and not (1 <= payload["willing"] <= 7):
            return render_template("questionnaire.html", error="Willingness rating must be 1–7.", assigned_cond=assigned_cond)

        structure_b = payload["emoji_form_self"]
        placement_b = ""
        if structure_b == "emoji_only":
            placement_b = "emoji_only"
        elif structure_b == "mixed":
            placement_b = "mixed"

        if structure_b and structure_b not in ("emoji_only", "mixed"):
            return render_template("questionnaire.html", error="Invalid emoji password structure option.", assigned_cond=assigned_cond)

        if payload["strategy_b"] and payload["strategy_b"] not in ("random", "meaning", "pattern", "easy", "first"):
            return render_template("questionnaire.html", error="Invalid emoji strategy option.", assigned_cond=assigned_cond)

        if payload["effort_b"] is not None and not (1 <= payload["effort_b"] <= 7):
            return render_template("questionnaire.html", error="Emoji effort rating must be 1–7.", assigned_cond=assigned_cond)

        emoji_only_hardest = payload["emoji_only_hardest"]
        emoji_only_mistake = payload["emoji_only_mistake"]
        mixed_hardest_part = payload["mixed_hardest_part"]
        mixed_style = payload["mixed_style"]

        if emoji_only_hardest and emoji_only_hardest not in ("order", "similar", "recognition", "not_hard", "other"):
            return render_template("questionnaire.html", error="Invalid emoji-only hardest-part option.", assigned_cond=assigned_cond)
        if emoji_only_mistake and emoji_only_mistake not in ("wrong_emoji", "wrong_order", "missing_extra", "not_sure", "no_mistake"):
            return render_template("questionnaire.html", error="Invalid emoji-only mistake option.", assigned_cond=assigned_cond)
        if mixed_hardest_part and mixed_hardest_part not in ("emoji_part", "text_part", "number_part", "combination", "not_hard"):
            return render_template("questionnaire.html", error="Invalid mixed-password hardest-part option.", assigned_cond=assigned_cond)
        if mixed_style and mixed_style not in ("text_emoji", "number_emoji", "text_number_emoji"):
            return render_template("questionnaire.html", error="Invalid mixed-password style option.", assigned_cond=assigned_cond)

        ease_a = payload["ease"] if assigned_cond == "A" else None
        ease_b = payload["ease"] if assigned_cond == "B" else None
        secure_a = payload["secure"] if assigned_cond == "A" else None
        secure_b = payload["secure"] if assigned_cond == "B" else None
        recall_a = payload["recall_confidence"] if assigned_cond == "A" else None
        recall_b = payload["recall_confidence"] if assigned_cond == "B" else None

        effort_b = payload["effort_b"] if assigned_cond == "B" else None
        strategy_b = payload["strategy_b"] if assigned_cond == "B" else ""
        structure_store = structure_b if assigned_cond == "B" else ""
        placement_store = placement_b if assigned_cond == "B" else ""
        emoji_form_store = payload["emoji_form_self"] if assigned_cond == "B" else ""
        emoji_only_hardest_store = emoji_only_hardest if assigned_cond == "B" else ""
        emoji_only_mistake_store = emoji_only_mistake if assigned_cond == "B" else ""
        mixed_hardest_store = mixed_hardest_part if assigned_cond == "B" else ""
        mixed_style_store = mixed_style if assigned_cond == "B" else ""
        willing_store = payload["willing"] if assigned_cond == "B" else None

        db.execute(
            """
            INSERT INTO questionnaire(
                participant_id,
                ease_a, ease_b,
                secure_a, secure_b,
                effort_b,
                structure_b, placement_b,
                strategy_b,
                semantic_b,
                recall_difficulty_a, recall_difficulty_b,
                willing,
                emoji_form_self,
                emoji_only_hardest,
                emoji_only_mistake,
                mixed_hardest_part,
                mixed_style,
                comment,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                ease_a, ease_b,
                secure_a, secure_b,
                effort_b,
                structure_store, placement_store,
                strategy_b,
                None,
                recall_a, recall_b,
                willing_store,
                emoji_form_store,
                emoji_only_hardest_store,
                emoji_only_mistake_store,
                mixed_hardest_store,
                mixed_style_store,
                payload["comment"],
                utc_now_iso(),
            ),
        )
        db.commit()
        return redirect(url_for("done"))

    return render_template("questionnaire.html", error=None, assigned_cond=assigned_cond)


@app.route("/done")
def done():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    code = session.get("participant_code")
    db = get_db()
    has_exported = db.execute("SELECT 1 FROM export_log WHERE participant_id=?", (pid,)).fetchone()

    if not has_exported and recall_done(pid):
        try:
            export_participant_to_csv(pid, code)
            db.execute(
                "INSERT INTO export_log(participant_id, exported_at) VALUES (?,?)",
                (pid, utc_now_iso()),
            )
            db.commit()
            exported = True
        except Exception:
            exported = False
    else:
        exported = has_exported is not None

    return render_template("done.html", participant_code=code, exported=exported)


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
    Stores raw secret_text for confirm/login matching and later recall comparison.
    Also stores derived features for analysis/export.
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
    if feats["pw_tokens_len"] < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters."}), 400
    if cond == "B" and feats["emoji_count"] < 1:
        return jsonify({"ok": False, "error": "Emoji password must include at least one emoji."}), 400

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
    stage = data.get("stage") or "confirm"
    if cond not in ("A", "B") or not isinstance(attempt_text, str) or stage not in ("confirm", "login"):
        return jsonify({"ok": False, "error": "bad params"}), 400

    # In initial session, login is a 10-minute delayed recall after successful confirm.
    if not session.get("recall_mode") and stage == "login":
        remain = seconds_until_initial_login_allowed(pid, cond)
        if remain is not None and remain > 0:
            return jsonify(
                {
                    "ok": False,
                    "error": "10-minute wait not finished",
                    "remaining_seconds": remain,
                }
            ), 400

    db = get_db()
    row = db.execute(
        "SELECT secret_text FROM secrets WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "no secret set"}), 400

    target = row["secret_text"]
    ok = (attempt_text == target)
    metrics = analyze_recall_error(target, attempt_text)
    return jsonify(
        {
            "ok": True,
            "match": ok,
            "edit_distance": metrics["edit_distance"],
            "wrong_positions": metrics["wrong_positions"],
        }
    )


@app.route("/api/recall/attempt", methods=["POST"])
def api_recall_attempt():
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401
    if not session.get("recall_mode"):
        return jsonify({"ok": False, "error": "not in recall mode"}), 400

    data = request.get_json(force=True)
    cond = data.get("condition")
    attempt_text = data.get("attempt_text")
    total_duration_ms = data.get("total_duration_ms")
    if cond not in ("A", "B") or not isinstance(attempt_text, str):
        return jsonify({"ok": False, "error": "bad params"}), 400

    if not initial_done(pid):
        return jsonify({"ok": False, "error": "initial not done"}), 400
    if recall_done_condition(pid, cond):
        return jsonify({"ok": False, "error": "condition already finished"}), 400

    db = get_db()
    row = db.execute(
        "SELECT secret_text FROM secrets WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "no target secret"}), 400

    target = row["secret_text"]
    matched = int(attempt_text == target)

    metrics = analyze_recall_error(target, attempt_text)

    cnt_row = db.execute(
        "SELECT COUNT(1) AS c FROM recall_attempts WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    attempt_no = int(cnt_row["c"] or 0) + 1

    db.execute(
        """
        INSERT INTO recall_attempts(
            participant_id, condition, attempt_no,
            attempt_text, matched,
            edit_distance, wrong_positions, error_distribution,
            created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            pid, cond, attempt_no,
            attempt_text, matched,
            metrics["edit_distance"], metrics["wrong_positions"], json.dumps(metrics["error_distribution"], ensure_ascii=False),
            utc_now_iso(),
        ),
    )

    finished = bool(matched or attempt_no >= 3)
    if finished:
        db.execute(
            """
            INSERT INTO recall_results(
                participant_id, condition,
                total_duration_ms, attempts, success,
                final_attempt_text, final_edit_distance, final_wrong_positions, final_error_distribution,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(participant_id, condition)
            DO UPDATE SET
                total_duration_ms=excluded.total_duration_ms,
                attempts=excluded.attempts,
                success=excluded.success,
                final_attempt_text=excluded.final_attempt_text,
                final_edit_distance=excluded.final_edit_distance,
                final_wrong_positions=excluded.final_wrong_positions,
                final_error_distribution=excluded.final_error_distribution,
                created_at=excluded.created_at
            """,
            (
                pid, cond,
                int(total_duration_ms) if isinstance(total_duration_ms, (int, float)) else None,
                attempt_no, matched,
                attempt_text, metrics["edit_distance"], metrics["wrong_positions"],
                json.dumps(metrics["error_distribution"], ensure_ascii=False),
                utc_now_iso(),
            ),
        )

    db.commit()
    return jsonify({
        "ok": True,
        "matched": bool(matched),
        "attempt_no": attempt_no,
        "finished": finished,
    })


if __name__ == "__main__":
    app.run(debug=True)