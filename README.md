Emoji Password Study (Coursework Prototype)
============================================

This application is a coursework prototype for the project:

Improving Passwords using Emojis

The study compares traditional text passwords and emoji-based passwords
to evaluate:

- Usability (ease of use and memorability)
- Login performance
- Structural patterns in emoji passwords

The study takes approximately 5–8 minutes.


--------------------------------------------------
IMPORTANT
--------------------------------------------------

- Do NOT use any real password that you use elsewhere.
- This is a coursework prototype.
- Data is stored locally on your computer only.
- No personal information is collected.
- You may stop the study at any time.


--------------------------------------------------
Study Overview
--------------------------------------------------

Participants complete two conditions:


Condition A — Traditional Password

Participants create and use a normal text password.

Example:
cat123house


Condition B — Emoji Password

Participants create and use a password containing emojis.

Example:
cat123🐬🔥

Participants select emojis from a menu containing 80 emojis.

- All participants use the same set of 80 emojis.
- The order of emojis is randomized per participant.
- This reduces position bias while keeping conditions consistent.


--------------------------------------------------
How to Run (Windows)
--------------------------------------------------

1. Extract the zip file.

2. Double-click:

run_emoji.bat

3. A browser window should open automatically.

If not, manually open:

http://127.0.0.1:5000

4. Complete the study.

5. When finished, close the window.

6. Send the file:

data.csv

to the researcher.


--------------------------------------------------
What Data Is Collected?
--------------------------------------------------

Interaction Data:

- Time to create password
- Time to confirm password
- Login time
- Login attempts
- Login success


Emoji Password Structure:

For emoji passwords, the system records structural features such as:

- Number of emojis used
- Whether emojis appear at the end of the password
- Whether emojis appear inside the password
- Which emojis were used
- Whether the first emoji in the menu was selected


Questionnaire:

Participants answer a short questionnaire about:

- Ease of use
- Perceived security
- Memorability
- Mental effort
- Emoji selection strategy
- Overall preference


--------------------------------------------------
Privacy and Security
--------------------------------------------------

Raw passwords are NOT exported.

Passwords are temporarily stored locally only for login verification,
and are NOT included in the final dataset.

The exported dataset (data.csv) contains only:

- Timing information
- Structural features
- Questionnaire responses

No names, emails, or identifying information are collected.


--------------------------------------------------
Output File
--------------------------------------------------

After completing the study, a file called:

data.csv

will be generated.

Each row represents one participant.


--------------------------------------------------
Important Note (Excel Users)
--------------------------------------------------

If emojis appear as unreadable characters in Excel:

Open the file using:

Data → From Text/CSV → Select UTF-8 encoding

Alternatively open the file in:

- VS Code
- Notepad++
- Google Sheets


--------------------------------------------------
Troubleshooting
--------------------------------------------------

If Python is not installed:

Install Python 3.10 or newer:

https://www.python.org/downloads/


If the page does not load:

Make sure no other program is using port 5000.

Then restart the program.


--------------------------------------------------
Coursework Context
--------------------------------------------------

This system was developed for a university coursework project investigating:

Improving Passwords using Emojis

The study evaluates whether emoji passwords improve usability
while introducing predictable patterns.

The study focuses on:

- Emoji placement (start / end / within password)
- Number of emojis used
- Emoji selection behaviour
- Usability vs security trade-offs
