from __future__ import annotations

import os
import csv
import json
import secrets
import sqlite3
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any, List

from flask import Flask, g, redirect, render_template, request, session, url_for, abort, jsonify

# ---------------------------
# Basic config
# ---------------------------

APP_SECRET = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
ENABLE_48H_GATE = False

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

        CREATE TABLE IF NOT EXISTS questionnaire_d0 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL UNIQUE,
            prefer_ab INTEGER,
            secure_b INTEGER,
            strategy_b TEXT,
            comment TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
        );

        CREATE TABLE IF NOT EXISTS participant_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            contact TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(participant_id) REFERENCES participants(id)
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
    ensure_column(db, "participants", "order_choice", "TEXT")
    ensure_column(db, "questionnaire_d0", "immediate_ease_b_over_a", "INTEGER")
    ensure_column(db, "questionnaire_d0", "immediate_security_b_over_a", "INTEGER")
    ensure_column(db, "questionnaire_d0", "predicted_recall_a", "INTEGER")
    ensure_column(db, "questionnaire_d0", "predicted_recall_b", "INTEGER")
    ensure_column(db, "questionnaire_d0", "raw_form_json", "TEXT")
    ensure_column(db, "questionnaire", "recall_difficulty_a", "INTEGER")
    ensure_column(db, "questionnaire", "recall_difficulty_b", "INTEGER")
    ensure_column(db, "questionnaire", "help_used", "TEXT")
    ensure_column(db, "questionnaire", "recall_confidence_a", "INTEGER")
    ensure_column(db, "questionnaire", "recall_confidence_b", "INTEGER")
    ensure_column(db, "questionnaire", "emoji_form_self", "TEXT")
    ensure_column(db, "questionnaire", "emoji_only_hardest", "TEXT")
    ensure_column(db, "questionnaire", "emoji_only_mistake", "TEXT")
    ensure_column(db, "questionnaire", "mixed_hardest_part", "TEXT")
    ensure_column(db, "questionnaire", "mixed_style", "TEXT")
    ensure_column(db, "questionnaire", "raw_form_json", "TEXT")

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


def create_participant() -> str:
    db = get_db()
    code = normalize_participant_code("tmp_" + secrets.token_urlsafe(6))
    db.execute("INSERT INTO participants(participant_code, created_at) VALUES (?,?)", (code, utc_now_iso()))
    db.commit()
    return code


def is_temporary_code(code: Optional[str]) -> bool:
    return isinstance(code, str) and code.startswith("tmp_")


def is_valid_participant_code(code: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_]{4,32}", code) is not None


def normalize_participant_code(code: str) -> str:
    return code.strip().lower()


def get_participant_created_at(pid: int) -> Optional[datetime]:
    db = get_db()
    row = db.execute("SELECT created_at FROM participants WHERE id=?", (pid,)).fetchone()
    if not row or not row["created_at"]:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except Exception:
        return None


def has_profile(pid: int) -> bool:
    db = get_db()
    row = db.execute("SELECT 1 FROM participant_profile WHERE participant_id=?", (pid,)).fetchone()
    return row is not None


def initial_done(pid: int) -> bool:
    return has_done_condition(pid, "A") and has_done_condition(pid, "B")


def recall_done_condition(pid: int, cond: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM recall_results WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    return row is not None


def recall_done(pid: int) -> bool:
    return recall_done_condition(pid, "A") and recall_done_condition(pid, "B")


def parse_iso_dt(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def get_d0_questionnaire_time(pid: int) -> Optional[datetime]:
    db = get_db()
    row = db.execute(
        "SELECT created_at FROM questionnaire_d0 WHERE participant_id=?",
        (pid,),
    ).fetchone()
    if not row:
        return None
    return parse_iso_dt(row["created_at"])


def recall_delay_since_d0_seconds(pid: int) -> Optional[int]:
    d0_time = get_d0_questionnaire_time(pid)
    if d0_time is None:
        return None
    now = datetime.now(timezone.utc)
    if d0_time.tzinfo is None:
        now = now.replace(tzinfo=None)
    delta = now - d0_time
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


def auto_structure_and_placement_for_b(pid: int) -> Tuple[str, str]:
    db = get_db()
    row = db.execute(
        """
        SELECT pw_tokens_len, emoji_count, emoji_first, emoji_at_end, emoji_within, emoji_only
        FROM secrets
        WHERE participant_id=? AND condition='B'
        """,
        (pid,),
    ).fetchone()
    if not row:
        return "", ""

    emoji_count = int(row["emoji_count"] or 0)
    emoji_only = int(row["emoji_only"] or 0)
    if emoji_only == 1:
        structure = "emoji_only"
    elif emoji_count > 0:
        structure = "mixed"
    else:
        structure = "no_emoji"

    first = int(row["emoji_first"] or 0)
    at_end = int(row["emoji_at_end"] or 0)
    within = int(row["emoji_within"] or 0)
    if emoji_count == 0:
        placement = "none"
    elif first == 1 and at_end == 1:
        placement = "beginning_end"
    elif first == 1:
        placement = "beginning"
    elif at_end == 1 and within == 0:
        placement = "end"
    elif within == 1:
        placement = "middle_or_mixed"
    else:
        placement = "mixed"

    return structure, placement


def get_order_from_session() -> Optional[Tuple[str, str]]:
    order = session.get("order_choice")
    if order == "A_first":
        return ("A", "B")
    if order == "B_first":
        return ("B", "A")

    pid = current_participant_id()
    if pid is not None:
        db = get_db()
        row = db.execute("SELECT order_choice FROM participants WHERE id=?", (pid,)).fetchone()
        if row and row["order_choice"] in ("A_first", "B_first"):
            session["order_choice"] = row["order_choice"]
            if row["order_choice"] == "A_first":
                return ("A", "B")
            return ("B", "A")
    return None


def get_recall_order_from_session() -> Optional[Tuple[str, str]]:
    order = session.get("recall_order_choice")
    if order == "A_first":
        return ("A", "B")
    if order == "B_first":
        return ("B", "A")
    return None


def cond_label(cond: str) -> str:
    return "Traditional password" if cond == "A" else "Emoji password"


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
    q_d0 = db.execute("SELECT * FROM questionnaire_d0 WHERE participant_id=?", (pid,)).fetchone()
    events = db.execute("SELECT * FROM events WHERE participant_id=? ORDER BY id", (pid,)).fetchall()
    profile = db.execute("SELECT * FROM participant_profile WHERE participant_id=?", (pid,)).fetchone()
    recall_rows = db.execute(
        "SELECT * FROM recall_results WHERE participant_id=? ORDER BY condition",
        (pid,),
    ).fetchall()

    secrets_rows = db.execute(
        """
        SELECT condition, secret_text,
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

        # derived structure features
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

        # profile
        "profile_name": None,
        "profile_contact": None,
        "profile_note": None,

        # D0 immediate questionnaire
        "d0_immediate_ease_b_over_a": None,
        "d0_immediate_security_b_over_a": None,
        "d0_strategy_b": None,
        "d0_predicted_recall_a": None,
        "d0_predicted_recall_b": None,

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

        # plaintext comparison (new)
        "A_plain_initial": None,
        "B_plain_initial": None,
        "A_plain_recall": None,
        "B_plain_recall": None,

        # recall metrics (new)
        "A_recall_success": None,
        "A_recall_attempts": None,
        "A_recall_time_ms": None,
        "A_recall_edit_distance": None,
        "A_recall_wrong_positions": None,
        "A_recall_error_distribution": None,

        "B_recall_success": None,
        "B_recall_attempts": None,
        "B_recall_time_ms": None,
        "B_recall_edit_distance": None,
        "B_recall_wrong_positions": None,
        "B_recall_error_distribution": None,

        # questionnaire extension (new)
        "recall_difficulty_a": None,
        "recall_difficulty_b": None,
        "help_used": None,
        "emoji_form_self": None,
        "emoji_only_hardest": None,
        "emoji_only_mistake": None,
        "mixed_hardest_part": None,
        "mixed_style": None,

        # recall delay from D0 questionnaire
        "recall_delay_seconds": None,
        "recall_delay_human": None,
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
            summary["A_plain_initial"] = r["secret_text"]
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
            summary["B_plain_initial"] = r["secret_text"]

    if profile:
        summary["profile_name"] = profile["display_name"]
        summary["profile_contact"] = profile["contact"]
        summary["profile_note"] = profile["note"]

    if q_d0:
        summary["d0_immediate_ease_b_over_a"] = q_d0["immediate_ease_b_over_a"]
        summary["d0_immediate_security_b_over_a"] = q_d0["immediate_security_b_over_a"]
        summary["d0_strategy_b"] = q_d0["strategy_b"]
        summary["d0_predicted_recall_a"] = q_d0["predicted_recall_a"]
        summary["d0_predicted_recall_b"] = q_d0["predicted_recall_b"]

    for r in recall_rows:
        cond = r["condition"]
        prefix = cond + "_recall_"
        summary[prefix + "success"] = r["success"]
        summary[prefix + "attempts"] = r["attempts"]
        summary[prefix + "time_ms"] = r["total_duration_ms"]
        summary[prefix + "edit_distance"] = r["final_edit_distance"]
        summary[prefix + "wrong_positions"] = r["final_wrong_positions"]
        summary[prefix + "error_distribution"] = r["final_error_distribution"]

        if cond == "A":
            summary["A_plain_recall"] = r["final_attempt_text"]
        elif cond == "B":
            summary["B_plain_recall"] = r["final_attempt_text"]

    delay_secs = recall_delay_since_d0_seconds(pid)
    summary["recall_delay_seconds"] = delay_secs
    summary["recall_delay_human"] = format_duration_hms(delay_secs)

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
        summary["recall_difficulty_a"] = q["recall_difficulty_a"]
        summary["recall_difficulty_b"] = q["recall_difficulty_b"]
        summary["help_used"] = q["help_used"]
        summary["emoji_form_self"] = q["emoji_form_self"]
        summary["emoji_only_hardest"] = q["emoji_only_hardest"]
        summary["emoji_only_mistake"] = q["emoji_only_mistake"]
        summary["mixed_hardest_part"] = q["mixed_hardest_part"]
        summary["mixed_style"] = q["mixed_style"]

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
        session.pop("recall_mode", None)
        session.pop("emoji_order_B", None)  # reset emoji order for fresh participant session
        return redirect(url_for("choose_order"))
    return render_template("consent.html", error=None)


@app.route("/set-participant-code", methods=["GET", "POST"])
def set_participant_code():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))
    if not initial_done(pid):
        return redirect(url_for("start"))

    current_code = session.get("participant_code")
    if current_code and not is_temporary_code(current_code):
        return redirect(url_for("questionnaire_d0"))

    if request.method == "POST":
        new_code = normalize_participant_code(request.form.get("participant_code") or "")
        if not is_valid_participant_code(new_code):
            return render_template(
                "set_participant_code.html",
                error="Code must be 4–32 chars and use only letters, numbers, underscore (_).",
            )

        db = get_db()
        exists = db.execute(
            "SELECT 1 FROM participants WHERE participant_code=? AND id<>?",
            (new_code, pid),
        ).fetchone()
        if exists:
            return render_template("set_participant_code.html", error="This code is already used. Please choose another one.")

        db.execute("UPDATE participants SET participant_code=? WHERE id=?", (new_code, pid))
        db.commit()
        session["participant_code"] = new_code
        return redirect(url_for("questionnaire_d0"))

    return render_template("set_participant_code.html", error=None)


@app.route("/recall-access", methods=["GET", "POST"])
def recall_access():
    if request.method == "POST":
        code = normalize_participant_code(request.form.get("participant_code") or "")
        if code == "":
            return render_template("recall_access.html", error="Please enter participant code.", remain_hours=None)
        if is_temporary_code(code):
            return render_template("recall_access.html", error="Temporary code is not valid for recall. Please finish D0 and set your own participant code.", remain_hours=None)

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
        session["recall_delay_seconds"] = recall_delay_since_d0_seconds(pid)
        session.pop("recall_order_choice", None)
        return redirect(url_for("choose_recall_order"))

    return render_template("recall_access.html", error=None, remain_hours=None)


@app.route("/choose-recall-order", methods=["GET", "POST"])
def choose_recall_order():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))
    if not session.get("recall_mode"):
        return redirect(url_for("start"))
    if not initial_done(pid):
        return redirect(url_for("start"))

    if request.method == "POST":
        choice = request.form.get("order")
        if choice not in ("A_first", "B_first"):
            return render_template("choose_recall_order.html", error="Please select an order.")
        session["recall_order_choice"] = choice
        return redirect(url_for("start"))

    return render_template("choose_recall_order.html", error=None)


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
        db = get_db()
        db.execute("UPDATE participants SET order_choice=? WHERE id=?", (choice, pid))
        db.commit()
        return redirect(url_for("start"))

    return render_template("choose_order.html", error=None)


@app.route("/start")
def start():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    base_order = get_order_from_session()
    if base_order is None:
        return redirect(url_for("choose_order"))

    first, second = base_order
    mode = "initial"
    done_first = has_done_condition(pid, first)
    done_second = has_done_condition(pid, second)

    if not (done_first and done_second):
        next_cond = first if not done_first else second
    else:
        if not session.get("recall_mode"):
            if is_temporary_code(session.get("participant_code")):
                return redirect(url_for("set_participant_code"))
            db = get_db()
            d0 = db.execute("SELECT 1 FROM questionnaire_d0 WHERE participant_id=?", (pid,)).fetchone()
            if not d0:
                return redirect(url_for("questionnaire_d0"))
            return redirect(url_for("wait_recall"))

        mode = "recall"
        recall_order = get_recall_order_from_session()
        if recall_order is None:
            return redirect(url_for("choose_recall_order"))
        first, second = recall_order
        done_first = recall_done_condition(pid, first)
        done_second = recall_done_condition(pid, second)
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
        mode=mode,
        recall_delay_text=format_duration_hms(session.get("recall_delay_seconds")) if mode == "recall" else None,
        first_label=cond_label(first),
        second_label=cond_label(second),
        next_label=cond_label(next_cond),
    )


@app.route("/questionnaire-d0", methods=["GET", "POST"])
def questionnaire_d0():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    if not initial_done(pid):
        return redirect(url_for("start"))

    db = get_db()
    existing = db.execute("SELECT 1 FROM questionnaire_d0 WHERE participant_id=?", (pid,)).fetchone()
    if existing:
        return redirect(url_for("wait_recall"))

    if request.method == "POST":
        def to_int(name: str) -> Optional[int]:
            v = request.form.get(name)
            if v is None or v == "":
                return None
            try:
                return int(v)
            except ValueError:
                return None

        immediate_ease = to_int("immediate_ease_b_over_a")
        immediate_security = to_int("immediate_security_b_over_a")
        strategy_b = (request.form.get("strategy_b") or "").strip()
        predicted_recall_a = to_int("predicted_recall_a")
        predicted_recall_b = to_int("predicted_recall_b")

        if immediate_ease is None or not (1 <= immediate_ease <= 7):
            return render_template("questionnaire_d0.html", error="Please answer immediate ease (1–7).")
        if immediate_security is None or not (1 <= immediate_security <= 7):
            return render_template("questionnaire_d0.html", error="Please answer immediate security (1–7).")
        if strategy_b == "":
            return render_template("questionnaire_d0.html", error="Please choose your emoji strategy.")
        if strategy_b not in ("random", "meaning", "pattern", "easy", "first"):
            return render_template("questionnaire_d0.html", error="Please choose a valid emoji strategy option.")
        if predicted_recall_a is None or not (0 <= predicted_recall_a <= 7):
            return render_template("questionnaire_d0.html", error="Please answer traditional-password predicted recall.")
        if predicted_recall_b is None or not (0 <= predicted_recall_b <= 7):
            return render_template("questionnaire_d0.html", error="Please answer emoji-password predicted recall.")

        db.execute(
            """
            INSERT INTO questionnaire_d0(
                participant_id,
                prefer_ab, secure_b,
                immediate_ease_b_over_a, immediate_security_b_over_a,
                strategy_b, predicted_recall_a, predicted_recall_b,
                comment,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                immediate_ease, immediate_security,
                immediate_ease, immediate_security,
                strategy_b, predicted_recall_a, predicted_recall_b,
                "",
                utc_now_iso(),
            ),
        )
        db.commit()
        return redirect(url_for("wait_recall"))

    return render_template("questionnaire_d0.html", error=None)


@app.route("/task/<cond>", methods=["GET"])
def task(cond: str):
    if cond not in ("A", "B"):
        abort(404)

    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    recall_mode = bool(session.get("recall_mode"))
    if recall_mode:
        order = get_recall_order_from_session()
        if order is None:
            return redirect(url_for("choose_recall_order"))
    else:
        order = get_order_from_session()
        if order is None:
            return redirect(url_for("choose_order"))
    first, second = order

    if not recall_mode:
        if cond == second and not has_done_condition(pid, first):
            return redirect(url_for("task", cond=first))
        if has_done_condition(pid, cond):
            return redirect(url_for("start"))
    else:
        if not initial_done(pid):
            return redirect(url_for("start"))
        if cond == second and not recall_done_condition(pid, first):
            return redirect(url_for("task", cond=first))
        if recall_done_condition(pid, cond):
            return redirect(url_for("start"))

    emojis = []
    if cond == "B":
        emojis = get_or_make_emoji_order_for_session()

    if recall_mode:
        return render_template("recall_task.html", condition=cond, emojis=emojis, cond_label=cond_label(cond))
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
    if not recall_done(pid):
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
            "recall_confidence_a": to_int("recall_confidence_a"),
            "recall_confidence_b": to_int("recall_confidence_b"),
            "effort_b": to_int("effort_b"),
            "emoji_form_self": (request.form.get("emoji_form_self") or "").strip(),
            "strategy_b": (request.form.get("strategy_b") or "").strip(),
            "semantic_b": to_int("semantic_b"),
            "emoji_only_hardest": (request.form.get("emoji_only_hardest") or "").strip(),
            "emoji_only_mistake": (request.form.get("emoji_only_mistake") or "").strip(),
            "mixed_hardest_part": (request.form.get("mixed_hardest_part") or "").strip(),
            "mixed_style": (request.form.get("mixed_style") or "").strip(),
            "prefer": to_int("prefer"),
            "willing": to_int("willing"),
            "comment": (request.form.get("comment") or "").strip(),
        }

        structure_b = payload["emoji_form_self"]
        strategy_b = payload["strategy_b"]
        semantic_b = payload["semantic_b"]
        placement_b = ""

        if structure_b == "emoji_only":
            placement_b = "emoji_only"
        elif structure_b == "mixed":
            placement_b = "mixed"

        required_scale = [
            "ease_a","ease_b","secure_a","secure_b",
            "recall_confidence_a","recall_confidence_b","effort_b",
            "prefer","willing",
        ]
        if any(payload[k] is None or not (1 <= payload[k] <= 7) for k in required_scale):
            return render_template("questionnaire.html", error="Please answer all required scale questions (1–7).")

        if structure_b not in ("emoji_only", "mixed"):
            return render_template("questionnaire.html", error="Please choose whether your emoji password was pure emoji or mixed.")
        if strategy_b not in ("random", "meaning", "pattern", "easy", "first"):
            return render_template("questionnaire.html", error="Please choose a valid emoji selection strategy.")
        if semantic_b is None or not (1 <= semantic_b <= 7):
            return render_template("questionnaire.html", error="Please answer the semantic relation question (1–7).")

        emoji_only_hardest = payload["emoji_only_hardest"]
        emoji_only_mistake = payload["emoji_only_mistake"]
        mixed_hardest_part = payload["mixed_hardest_part"]
        mixed_style = payload["mixed_style"]

        if structure_b == "emoji_only":
            if emoji_only_hardest not in ("order", "similar", "recognition", "not_hard", "other"):
                return render_template("questionnaire.html", error="Please answer emoji-only hardest part.")
            if emoji_only_mistake not in ("wrong_emoji", "wrong_order", "missing_extra", "not_sure", "no_mistake"):
                return render_template("questionnaire.html", error="Please answer emoji-only mistake type.")
            mixed_hardest_part = ""
            mixed_style = ""
        else:
            if mixed_hardest_part not in ("emoji_part", "text_part", "number_part", "combination", "not_hard"):
                return render_template("questionnaire.html", error="Please answer mixed-password hardest part.")
            if mixed_style not in ("text_emoji", "number_emoji", "text_number_emoji"):
                return render_template("questionnaire.html", error="Please answer mixed-password style.")
            emoji_only_hardest = ""
            emoji_only_mistake = ""

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
                recall_difficulty_a, recall_difficulty_b,
                help_used,
                emoji_form_self,
                emoji_only_hardest,
                emoji_only_mistake,
                mixed_hardest_part,
                mixed_style,
                comment,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                payload["ease_a"], payload["ease_b"],
                payload["secure_a"], payload["secure_b"],
                payload["recall_confidence_a"], payload["recall_confidence_b"],
                payload["effort_b"],
                structure_b, placement_b,
                strategy_b,
                semantic_b,
                payload["prefer"], payload["willing"],
                payload["recall_confidence_a"], payload["recall_confidence_b"],
                "",
                payload["emoji_form_self"],
                emoji_only_hardest,
                emoji_only_mistake,
                mixed_hardest_part,
                mixed_style,
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