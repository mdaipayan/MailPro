# mailmerge

Send one personalised email per row of a CSV file.
Use it in two ways: a **browser app** (for office staff) or a **command-line tool**.

## The app

```bash
pip install ".[app]"
streamlit run app.py
```

The page has five tabs. Use them from left to right:

| Tab | What you do there |
|---|---|
| **1 · List** | Upload the CSV file, or click *Use sample data* to try it. |
| **2 · Email** | Write on the left (sender name, To, Cc, Bcc, Subject, Body, field buttons). The real email for any row shows on the right. |
| **3 · Attachments** | Optional. The same file for everyone, and/or personal files for each person. |
| **4 · Check & test** | Left: check all rows. Right: send a test email, which goes only to you. |
| **5 · Send** | Send to everyone, with a progress bar. The log and the retry button are here. |

A line under the title always shows where you are: rows in the list, files,
checked or not, test email sent or not, and how many emails are sent.

### Attachments (tab 3)

There are four kinds. All are optional, and each email gets the sum of them.

| Kind | Use it for | How the app knows whose it is |
|---|---|---|
| **A · Same for everyone** | Guidelines, bill form | Everybody gets it |
| **B · Letters** | Appointment letter, acceptance form, undertaking | The app makes one letter per row from the letter library |
| **C · Files named in the list** | Syllabus of the course, question paper template | A column of the list holds the file name |
| **D · Personal files, by ID** | Ready-made files of one person | The file name contains the person's ID / code |

**B · Letters.** The app has a letter library in the folder `letter_templates/`.
Each sub-folder is one letter set:

| Letter set | Letters it makes | List it fits |
|---|---|---|
| Paper setter | Appointment, Acceptance Form, Undertaking | paper setter list |
| Valuer central assessment | Appointment Letter (Form E5) | valuation list |
| External practical examiner | Appointment Letter | practical examiner list |

So the work is: upload the CSV in tab 1, choose the letter set in tab 3, and the app
makes each person's letters. You upload no template and no image.

- **Email text:** the button "Use this set's email text" fills the Subject and Body in tab 2.
- **Shared files:** put the syllabus PDFs, `Guidelines.pdf` and so on in the set's
  `files/` folder once. The app then finds the columns of the list that name them
  (kind C) by itself.
- **Wrong set for the list:** the app says which column the letter needs and the
  list does not have, and nothing can be sent.
- **To change a letter:** edit its `.tex` file in the library and reload the page.
- **To add a set:** make a new folder with your `.tex` files. See
  `letter_templates/README.txt`.
- **Other templates:** you can still upload `.tex` files for one-time use.
- **Missing letterhead check:** if one letter has fewer images than the others
  (a common template mistake hits only the first or last row), the app stops the send.
- Each letter is cut from one LaTeX run, one page per row. If a letter is longer
  than one page, the app compiles row by row, which is slower but stays correct.
- In the preview (tab 2) you can click each attachment to open and read it.
- The computer that runs the app needs LaTeX (`pdflatex`). On Streamlit Cloud,
  `packages.txt` installs it.
- Values with `&`, `%`, `#`, `_` or a comma (for example `Shahapur, Bhandara`) are
  made safe for LaTeX automatically.
- **Keep the library private.** `_shared/` holds the signature image. The
  `.gitignore` file leaves the library images out of GitHub. To keep the library
  somewhere else, set `MAILMERGE_TEMPLATES` to that folder.

**C · Files named in the list.** Upload the files (or one ZIP) and choose the
columns that hold file names, for example `syllabus`, `attachment_4`, `attachment_5`.
`3S_CE_SM` in the list matches `3S_CE_SM.pdf`. Many rows can share one file.
For two files in one cell write `Guidelines; Remuneration`.

**D · Personal files, by ID.** Choose the columns that together are unique for
one row, for example `person_id` and `course_code`. A row with `T014` and
`BECVE501T` gets every file whose name contains both, such as
`Bill_T014_BECVE501T.pdf`.

- Whole words only: `T01` does not match `T014`. Case, spaces, `_` and `-` do not matter.
- You can set how many files each person must get.
- A file that fits more than one row is a problem, and it is never sent.

In all kinds:

- A missing file is a problem, and nothing can be sent until it is fixed.
- Uploaded files that no row uses are listed in a warning.
- "Who gets which file" shows the full table before you send.
- Limit: about 18 MB of attachments per email.

### Example: paper setters and valuers

`examples/exam_appointments/` has a sample list (`appointments.csv`) and the email
text (`email_text.txt`). The names in it are invented.

Safety rules in the app:

- The Send button is locked until both parts of tab 4 pass. If you change the list, the email or a file, you must check and test again.
- Every person gets a separate email. Nobody sees the other names or addresses.
- Rows already sent are never sent again by the retry button.
- The password is typed in the left panel and kept only while the page is open. It is never saved.
- Column names are turned into field names: `Roll No.` becomes `{{ Roll_No }}`.

### Running it online (GitHub + Streamlit Community Cloud)

See **`DEPLOY.md`**. In short: the code goes to GitHub, and the letterhead,
signature and app password go into the app's private Secrets, never into GitHub.
Online, the app refuses to open until an app password is set.

On a PC the app password is optional. To set one, use the environment variable
`MAILMERGE_APP_PASSWORD`, or `app_password` in `.streamlit/secrets.toml`.

The left panel has a **Check login** button. It tries to log in to the mail
server without sending anything.

## The command-line tool

Dry run by default: nothing is sent until you add `--send`.

## Install

```bash
pip install .            # from this folder
# or, after pushing to GitHub:
pip install git+https://github.com/<user>/mailmerge.git
```

This installs the `mailmerge` command. `python -m mailmerge` also works.

## Quick start

```bash
mailmerge --sample       # creates the 3 sample files below
mailmerge                # dry run: checks every row, shows the first email
mailmerge --send         # really sends
```

| File | What it holds |
|---|---|
| `mailmerge_template.txt` | headers + blank line + body, with `{{ column }}` fields |
| `mailmerge_database.csv` | one row per email; the first row holds the column names |
| `mailmerge_server.conf` | SMTP host, port, security and username (no password) |

### Template

```
From: Exam Cell KITS <your.name@gmail.com>
To: {{ name }} <{{ email }}>
Cc: hod@kits.edu
Subject: Marks for {{ course }}

Dear {{ name }},
Your marks in {{ course }} are {{ marks }}.
{% if marks|int < 40 %}
Please meet your course teacher for the re-test.
{% endif %}
```

- The template uses Jinja2, so you can use `{% if %}` and `{% for %}` blocks and filters.
- If a `{{ field }}` is missing from the CSV, you get an error, not a blank.
- Use `Bcc:` for hidden copies. It is removed from the email that is sent.
- `Content-Type: text/html` sends an HTML body. A plain-text copy is added automatically.
- `Attachment: admit_cards/{{ roll }}.pdf` attaches a file (repeat the line for more than one file).
  The path is relative to the template's folder.

### CSV

UTF-8 (with or without BOM) or Windows-1252, comma / semicolon / tab separated.
Blank lines are skipped and spaces around values are removed.

### Password

The password is never stored in a file. Set it before sending:

```bash
export MAILMERGE_PASSWORD='xxxx xxxx xxxx xxxx'     # Linux / macOS
set MAILMERGE_PASSWORD=xxxx xxxx xxxx xxxx          # Windows cmd
```

If you don't set it, the tool asks for it. For **Gmail**, use an
[App Password](https://myaccount.google.com/apppasswords) (2-step verification
must be on). Your normal Gmail password will not work.

## Options

| Option | Meaning |
|---|---|
| `--send` | really send (default is dry run) |
| `--limit N` | only the first N emails |
| `--start N` | start from data row N (use this to resume after a failure) |
| `--sleep S` | seconds between emails (default 1) |
| `--show N` | print the first N emails in full (default 1) |
| `--save-eml DIR` | save every email as a `.eml` file (you can open these in Outlook or Thunderbird) |
| `--log FILE` | log of sent and failed rows (default `mailmerge_log.csv`) |
| `-t / -d / -c` | other template / CSV / config file |

## Safety

1. **Every row is checked before anything is sent.** One bad row (missing field,
   bad address, missing attachment) stops the whole run.
2. If the server refuses one address, that row is logged as `failed` and the
   others are still sent. The exit code is then 2.
3. Gmail allows about 500 emails a day from a normal account. Use `--limit`
   for large lists.

## Use from Python

```python
from mailmerge import load_template, read_database, render_message, ServerConfig, Sender

tpl = load_template("mailmerge_template.txt")
with Sender(ServerConfig.from_file("mailmerge_server.conf")) as s:
    for row in read_database("mailmerge_database.csv"):
        msg, rcpts = render_message(tpl, row)
        s.send(msg, rcpts)
```

## Tests

```bash
pip install -e ".[test,app]"
pytest
```
