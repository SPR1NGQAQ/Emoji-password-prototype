Emoji Password Study (Coursework Prototype)
===========================================

Thank you for participating in this short password study.

IMPORTANT
---------
- Do NOT use any real password you use elsewhere.
- This is a coursework prototype run locally on the researcher's laptop.
- Your responses are used for research/analysis only.
- You can stop at any time.

What You Will Do
----------------
This study has two sessions:

Session 1 (today, Day 0)
1) Complete two password tasks:
   - Condition A: traditional password (text/numbers)
   - Condition B: emoji password (emoji-only or mixed is up to you)
   Each task includes: create → confirm → login (up to 3 attempts).
2) Enter your Participant ID (you create it yourself).
3) Complete a short questionnaire.

Session 2 (about 48 hours later, Day 2)
1) Enter the SAME Participant ID again.
2) Repeat the password login tasks for recall.
3) Complete a short questionnaire.

Participant ID Rules (Very Important)
-------------------------------------
- You must create your own Participant ID and remember it for Session 2.
- Use something anonymous (e.g., "P_07", "demo01", "u23").
- Do not use your real name, email, or student ID number.
- If you forget your Participant ID, Session 2 data cannot be matched to Session 1.

Data Collected
--------------
We record anonymous interaction data, such as:
- Time to create / confirm / login
- Number of login attempts and whether login succeeded
- Password length and structural features about emojis (e.g., how many emojis, whether emojis appear at the start/end/within, whether password is emoji-only or mixed)

For recall analysis (Session 2), we also store the password in plain text locally
so we can compare Session 1 and Session 2 attempts (e.g., whether the recalled
password matches and how many characters/emojis differ). This data stays on the
researcher's laptop and is not published.

After Session 2 is completed, a summary row is exported to:
- data.csv

How to Run (Windows)
--------------------
1) Ensure Python 3.10+ is installed.
2) Open PowerShell in this project folder.
3) (Recommended) Create and activate a virtual environment:

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

4) Install requirements:

   pip install -r requirements.txt

5) Start the server:

   python app.py

6) Open the study in a browser:

   http://127.0.0.1:5000

How to Run (macOS / Linux)
--------------------------
1) Ensure Python 3.10+ is installed.
2) In Terminal, go to this folder.
3) (Recommended) Create and activate a virtual environment:

   python3 -m venv .venv
   source .venv/bin/activate

4) Install requirements:

   pip install -r requirements.txt

5) Start the server:

   python3 app.py

6) Open:

   http://127.0.0.1:5000

Recall Timing Config (Enable/Disable 48h Gate)
----------------------------------------------
By default, the recall gate is enabled and requires 48 hours.

You can control it at runtime with environment variables:

- ENABLE_48H_GATE
  - `1` / `true` / `yes` / `on` = enable delay gate
  - `0` / `false` / `no` / `off` = disable delay gate

- RECALL_GATE_HOURS
  - Number of hours to wait before recall is allowed (default `48`)

Windows PowerShell examples:

   $env:ENABLE_48H_GATE = "1"
   $env:RECALL_GATE_HOURS = "48"
   python app.py

Disable gate for internal testing:

   $env:ENABLE_48H_GATE = "0"
   python app.py

macOS / Linux examples:

   ENABLE_48H_GATE=1 RECALL_GATE_HOURS=48 python3 app.py

Disable gate:

   ENABLE_48H_GATE=0 python3 app.py

Troubleshooting
---------------
- Port already in use (5000):
  Close other programs using port 5000, or change the port in app.py.

- Emojis look like squares / missing:
  Some older systems or browsers may not support newer emojis.
  If this happens, try a different browser or device.

- Data not matching between sessions:
  Make sure the same Participant ID is used in Session 1 and Session 2.

- If you changed database schema during development:
  Delete emoji.db and restart the server to re-create tables.
  (Do not do this once real data collection has started.)

Files You May See
-----------------
- app.py              : Flask backend (routes, database, export)
- templates/          : HTML pages
- static/task.js      : Frontend logic for the task flow + emoji menu
- emoji.db            : Local SQLite database (created automatically)
- data.csv            : Exported summary after Session 2 (created automatically)
