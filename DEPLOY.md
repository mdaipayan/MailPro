# Put the app on GitHub + Streamlit Community Cloud

This guide is for a **public** GitHub repository. The app is built so that
nothing confidential goes into the repository:

| Item | Where it lives |
|---|---|
| App code, letter wording (`.tex`), email texts | GitHub (public) |
| Letterhead and signature images | Streamlit **Secrets** (private) |
| App password | Streamlit **Secrets** (private) |
| Examiner lists (CSV) | Uploaded each time; never saved |
| Gmail App Password | Typed each time; never saved |

## Step 1. The repository

The code is in the GitHub repository `mdaipayan/MailPro`. It contains no image,
no list and no password.

Do **not** upload `mailmerge_private_files.zip` (letterhead and signature) or any
examiner list to GitHub. Keep them on the office PC.

## Step 2. Make the Secrets text (on your PC)

1. Download the repository to your PC (green **Code** button → Download ZIP) and unzip it.
2. Unzip `mailmerge_private_files.zip` and copy its `letter_templates/_shared/` folder
   into the same place in the downloaded folder.
3. Run:

   ```bash
   python tools/make_secrets.py
   ```

4. It asks for an app password (or makes a strong one) and writes
   `.streamlit/secrets.toml`. Open that file and copy all of it.

## Step 3. Deploy

1. Go to share.streamlit.io and sign in with GitHub.
2. **Create app** → choose the repository, branch `main`, main file `app.py`.
3. Open **Advanced settings** and paste the Secrets text into the **Secrets** box.
4. Click **Deploy**. The first start takes several minutes, because LaTeX is
   installed from `packages.txt`.

## Step 4. First-run checks (do these once)

| Check | Where | If it fails |
|---|---|---|
| The app asks for the app password | Opening the link | If it says "locked: no app password", the Secrets were not saved |
| Gmail login works | Left panel → **Check login** | "Cannot connect" means the host blocks email ports; use the office PC instead |
| Letters are made | Tab 3 → B · Letters → choose a set | A LaTeX error naming a `.sty` file means a package is missing; add it to `packages.txt` |
| Letterhead and signature appear | Tab 2 → preview → click `Appointment.pdf` | If the app asks you to upload the images, the `[letter_images]` part of the Secrets is missing or too large; upload them in that box each session instead |
| A real email arrives | Use a CSV with only your own address | Check the spam folder, then the App Password |

## Rules for daily use

- Never upload an examiner list or an image to GitHub.
- Give the app password only to exam section staff. Change it each session
  (edit the Secrets; the app restarts by itself).
- If the app password or the Gmail App Password leaks, change it at once.
  A Gmail App Password is removed in Google Account → Security → App passwords.
- The lists and the Gmail App Password are sent to the Streamlit server while
  you work. For the most confidential work, run the app on the office PC:
  `pip install ".[app]"` then `streamlit run app.py`.

## To change a letter later

Edit the `.tex` file in `letter_templates/` on GitHub (the pencil button).
Streamlit Cloud updates the app in about a minute.
