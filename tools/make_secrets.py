"""Make the text for the app's Secrets box on Streamlit Cloud.

Run on your own PC, from the main folder of the project:

    python tools/make_secrets.py

It reads the images in letter_templates/_shared/ (letterhead, signature),
asks for an app password, and writes  .streamlit/secrets.toml .
Open that file, copy everything, and paste it into:
    Streamlit Cloud > your app > Settings > Secrets

The file is listed in .gitignore, so it does not go to GitHub.
"""

import base64
import getpass
import secrets as pysecrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "letter_templates" / "_shared"
OUT = ROOT / ".streamlit" / "secrets.toml"
KINDS = (".png", ".jpg", ".jpeg", ".pdf")


def main() -> int:
    images = sorted(p for p in IMAGES.glob("*") if p.suffix.lower() in KINDS)
    if not images:
        print(f"No images found in {IMAGES}")
        return 1

    password = getpass.getpass("App password (press Enter to let me make a strong one): ")
    if not password:
        password = pysecrets.token_urlsafe(18)
        print(f"Your app password is:  {password}   (write it down)")
    elif len(password) < 12:
        print("Please use at least 12 characters.")
        return 1

    lines = [f'app_password = "{password}"', "", "[letter_images]"]
    for path in images:
        text = base64.b64encode(path.read_bytes()).decode("ascii")
        lines.append(f'"{path.name}" = "{text}"')
        print(f"added {path.name}  ({path.stat().st_size // 1024} KB)")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWritten: {OUT}  ({OUT.stat().st_size // 1024} KB)")
    print("Copy the whole file into Streamlit Cloud > app > Settings > Secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
