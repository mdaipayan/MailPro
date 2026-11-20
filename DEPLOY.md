# Put the app on GitHub + Streamlit Community Cloud

This guide is for a **public** GitHub repository. The app is built so that
nothing confidential goes into the repository:

| Item | Where it lives |
|---|---|
| App code, letter wording (`.tex`), email texts | GitHub (public) |
| Letterhead and signature images | Uploaded in the app each time; never saved |
| App password | Streamlit **Secrets** (private) |
| Examiner lists (CSV) | Uploaded each time; never saved |
| Gmail App Password | Typed each time; never saved |

## Step 1. The repository

The code is in the GitHub repository `mdaipayan/MailPro`. It contains no image,
no list and no password.

Do **not** upload `mailmerge_private_files.zip` (letterhead and signature) or any
examiner list to GitHub. Keep them on the office PC.

## Step 2. Deploy

1. Go to share.streamlit.io and sign in with GitHub.
2. **Create app** → repository `mdaipayan/MailPro`, branch `main`, main file `app.py`.
3. Open **Advanced settings**. In the **Secrets** box type this one line, with
   your own password of 12 or more characters:

   ```toml
   app_password = "write-a-long-password-here"
   ```

4. Click **Deploy**. The first start takes several minutes, because LaTeX is
   installed from `packages.txt`.

Give the app link and this password only to the one person who will use the app.

## Step 3. Each time you use the app

1. Open the link and type the app password.
2. Tab 1: upload the list (CSV).
3. Tab 3 → **B · Letters** → choose the letter set.
4. Two boxes appear: **Letterhead** and **Signature**. Upload the two pictures
   from the office PC. Any file name is fine, PNG or JPG.
5. Continue with tab 2, 4 and 5 as usual.

The pictures stay only while the page is open. They are not saved on the
server and are never in GitHub, so you upload them again next time.

## Step 4. First-run checks (do these once)

| Check | Where | If it fails |
|---|---|---|
| The app asks for the app password | Opening the link | If it says "locked: no app password", the Secrets were not saved |
| Gmail login works | Left panel → **Check login** | "Cannot connect" means the host blocks email ports; use the office PC instead |
| Letters are made | Tab 3 → B · Letters → choose a set | A LaTeX error naming a `.sty` file means a package is missing; add it to `packages.txt` |
| Letterhead and signature appear | Tab 2 → preview → click `Appointment.pdf` | Upload the two pictures again in tab 3 → B · Letters |
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

## Optional: store the pictures in Secrets instead

If you do not want to upload the two pictures each time, they can be stored in
the app's Secrets. Run `python tools/make_secrets.py` on a PC that has the
pictures in `letter_templates/_shared/`, and paste the file it writes into the
Secrets box. The two upload boxes then do not appear.

## Optional: save the sender App Passwords

So that nobody has to type the Gmail App Password, add the sender addresses to
the Secrets box (Streamlit Cloud → app → Settings → Secrets):

```toml
app_password = "a-long-password-to-open-the-app"

[sender_passwords]
"coe@kits.edu" = "xxxx xxxx xxxx xxxx"
"deputycoe@kits.edu" = "xxxx xxxx xxxx xxxx"
```

The left panel then has a **Send from** list with these addresses. The App
Password is filled in by itself and is never shown on the page.

Never write these passwords in any file in GitHub: this repository is public.
Anyone who opens the app can send email as these addresses, so the app password
(first line) must be long and known only to the person who uses the app.
