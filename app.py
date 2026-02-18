from __future__ import annotations

import os
import csv
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple

from flask import Flask, g, redirect, render_template, request, session, url_for, abort, jsonify

APP_SECRET = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

DB_PATH = os.path.join(os.path.dirname(__file__), "emoji.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "data.csv") # CSV export file

app = Flask(__name__)
app.secret_key = APP_SECRET

# ---------------- Helpers ----------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat() # UTC timestamp in ISO format, e.g. "2024-06-01T12:34:56.789Z"

# Database connection per request, stored in `g`.
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

# Close the database connection at the end of request.
@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# Initialize database tables if they don't exist.
def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_code TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Plaintext secret for research prototype ONLY.
        CREATE TABLE IF NOT EXISTS secrets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id INTEGER NOT NULL,
            condition TEXT NOT NULL CHECK(condition IN ('A','B')),
            secret_text TEXT NOT NULL,
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
            strategy_b TEXT,
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

# Initialize database before handling any request.
@app.before_request
def _init():
    init_db()

# Get current participant ID from session, or None if not set/invalid.
def current_participant_id() -> Optional[int]:
    code = session.get("participant_code")
    if not code:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM participants WHERE participant_code=?", (code,)).fetchone()
    return int(row["id"]) if row else None

# Create a new participant with a unique code, store in DB, and return the code.
def create_participant() -> str:
    db = get_db()
    code = secrets.token_urlsafe(6)
    db.execute("INSERT INTO participants(participant_code, created_at) VALUES (?,?)", (code, utc_now_iso()))
    db.commit()
    return code

# Check if participant has completed the given condition (A or B) by looking for a secret.
def has_done_condition(pid: int, cond: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM secrets WHERE participant_id=? AND condition=?",
        (pid, cond),
    ).fetchone()
    return row is not None

# Get the order of conditions from session, or None if not set/invalid.
def get_order_from_session() -> Optional[Tuple[str, str]]:
    """
    Returns ('A','B') or ('B','A') if user chose order.
    """
    order = session.get("order_choice")
    if order == "A_first":
        return ("A", "B")
    if order == "B_first":
        return ("B", "A")
    return None

# Export participant's data to CSV (append one row). Called on /done.
def export_participant_to_csv(pid: int, participant_code: str) -> None:
    db = get_db()

    q = db.execute("SELECT * FROM questionnaire WHERE participant_id=?", (pid,)).fetchone()
    events = db.execute("SELECT * FROM events WHERE participant_id=? ORDER BY id", (pid,)).fetchall()
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

        "ease_a": None,
        "ease_b": None,
        "secure_a": None,
        "secure_b": None,
        "prefer": None,
        "willing": None,
        "comment": None,
        "memory_a": None,
        "memory_b": None,
        "effort_b": None,
        "strategy_b": None,
    }
    # Fill in event data into summary dict. Keys are prefixed by condition (A_ or B_).
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
    # Fill in questionnaire data if exists.
    if q:
        summary["ease_a"] = q["ease_a"]
        summary["ease_b"] = q["ease_b"]
        summary["secure_a"] = q["secure_a"]
        summary["secure_b"] = q["secure_b"]
        summary["prefer"] = q["prefer"]
        summary["willing"] = q["willing"]
        summary["comment"] = q["comment"]
        summary["memory_a"] = q["memory_a"]
        summary["memory_b"] = q["memory_b"]
        summary["effort_b"] = q["effort_b"]
        summary["strategy_b"] = q["strategy_b"]

    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(summary)


# ---------------- Routes ----------------

@app.route("/")
def home():
    return render_template("home.html")

# Consent page where participant must agree to proceed. On POST, create participant and redirect to order choice.
@app.route("/consent", methods=["GET", "POST"])
def consent():
    if request.method == "POST":
        if request.form.get("agree") != "yes":
            return render_template("consent.html", error="You must agree to continue.")
        code = create_participant()
        session["participant_code"] = code
        session.pop("order_choice", None)
        return redirect(url_for("choose_order"))
    return render_template("consent.html", error=None)

# Page to choose order of conditions (A first or B first). On POST, save choice in session and redirect to start.
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

# Start page that shows progress and next steps. Enforces order of tasks and questionnaire.
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

# Task page for condition A or B. Enforces order and prevents redoing completed tasks.
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

    # enforce chosen order
    if cond == second and not has_done_condition(pid, first):
        return redirect(url_for("task", cond=first))

    # if already done this cond, go start
    if has_done_condition(pid, cond):
        return redirect(url_for("start"))

    return render_template("task.html", condition=cond)

# Questionnaire page after completing both tasks. On POST, save responses and redirect to done.
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
            if not v:
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
            "strategy_b": request.form.get("strategy_b"),
            "prefer": to_int("prefer"),
            "willing": to_int("willing"),
            "comment": (request.form.get("comment") or "").strip(),
        }

        required = [
            "ease_a", "ease_b",
            "secure_a", "secure_b",
            "memory_a", "memory_b",
            "effort_b",
            "prefer", "willing"
        ]
        if any(payload[k] is None or not (1 <= payload[k] <= 7) for k in required):
            return render_template("questionnaire.html", error="Please answer all scale questions (1–7).")

        db.execute(
            """
                INSERT INTO questionnaire(
                participant_id,
                ease_a, ease_b,
                secure_a, secure_b,
                memory_a, memory_b,
                effort_b,
                strategy_b,
                prefer, willing,
                comment,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                payload["ease_a"], payload["ease_b"],
                payload["secure_a"], payload["secure_b"],
                payload["memory_a"], payload["memory_b"],
                payload["effort_b"],
                payload["strategy_b"],
                payload["prefer"], payload["willing"],
                payload["comment"],
                utc_now_iso()
            ),
        )
        db.commit()
        return redirect(url_for("done"))

    return render_template("questionnaire.html", error=None)

# Done page that thanks participant and shows their code. Exports data to CSV.
@app.route("/done")
def done():
    pid = current_participant_id()
    if pid is None:
        return redirect(url_for("consent"))

    code = session.get("participant_code")

    # export one row to data.csv
    try:
        export_participant_to_csv(pid, code)
    except Exception:
        pass

    return render_template("done.html", participant_code=code)


# ---------------- API ----------------
# These API endpoints are called by frontend JS to record events and secrets. They return JSON responses and do not render templates.
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

# API endpoint to end an event by event_id. Updates the event with ended_at, duration_ms, success, attempts, and note.
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

# API endpoint to set the secret for a condition. Uses UPSERT to allow updating the secret if already set. This is for research prototype ONLY and should not be used in production.
@app.route("/api/secret/set", methods=["POST"])
def api_secret_set():
    """
    Plaintext secret stored for research prototype ONLY.
    Uses UPSERT so repeated saves won't fail.
    """
    pid = current_participant_id()
    if pid is None:
        return jsonify({"ok": False, "error": "no session"}), 401

    data = request.get_json(force=True)
    cond = data.get("condition")
    secret_text = data.get("secret_text")
    if cond not in ("A", "B") or not isinstance(secret_text, str) or len(secret_text) < 1:
        return jsonify({"ok": False, "error": "bad params"}), 400

    db = get_db()
    db.execute(
        """
        INSERT INTO secrets(participant_id, condition, secret_text, created_at)
        VALUES (?,?,?,?)
        ON CONFLICT(participant_id, condition)
        DO UPDATE SET secret_text=excluded.secret_text, created_at=excluded.created_at
        """,
        (pid, cond, secret_text, utc_now_iso()),
    )
    db.commit()
    return jsonify({"ok": True})

# API endpoint to check if the attempted secret matches the stored secret for the condition. Returns {"ok": True, "match": True/False} if successful.
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
