"""
Run the app for everyday use: on this PC, and on your phone through Tailscale.

    python serve.py          (or double-click start-app.bat)

What it does:
* Runs the app with Waitress (a proper server; `runserver` is only for
  development) on 127.0.0.1:8000, so nothing outside this PC can reach it
  directly. Your phone reaches it only through Tailscale, which adds HTTPS.
* Switches debug mode off, so error pages never show internal details.
* Reads this PC's Tailscale address (e.g. my-pc.tail1234.ts.net) and allows
  only that address and localhost.
* Prepares the styles and scripts, and warns if `python manage.py migrate` is needed.

Once, after installing Tailscale, run in an admin terminal:
    tailscale serve --bg 8000
Then open https://<your-pc>.<your-tailnet>.ts.net on your phone.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8000
TAILSCALE_PATHS = [r"C:\Program Files\Tailscale\tailscale.exe", "tailscale"]


def tailscale_exe() -> str | None:
    for candidate in TAILSCALE_PATHS:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def tailscale_name(exe: str | None) -> str | None:
    """This PC's Tailscale name, e.g. 'my-pc.tail1234.ts.net', or None."""
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15)
        name = json.loads(result.stdout or "{}").get("Self", {}).get("DNSName", "")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return name.rstrip(".") or None


def manage(*args: str) -> int:
    return subprocess.run([sys.executable, str(BASE_DIR / "manage.py"), *args], cwd=BASE_DIR).returncode


def main() -> None:
    # The Windows console can't show every character; never crash over one.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    os.chdir(BASE_DIR)
    exe = tailscale_exe()
    name = tailscale_name(exe)
    hosts = ["localhost", "127.0.0.1"] + ([name] if name else [])
    # Settings from here override .env (django-environ never overwrites what's already set).
    os.environ["DEBUG"] = "False"
    os.environ["ALLOWED_HOSTS"] = ",".join(hosts)
    # Forms posted from the phone come from the https Tailscale address; on this PC they're same-origin.
    os.environ["CSRF_TRUSTED_ORIGINS"] = f"https://{name}" if name else ""
    # Tailscale serves HTTPS and passes requests to this PC; the app itself listens on plain HTTP locally.
    os.environ["SECURE_SSL_REDIRECT"] = "False"
    os.environ["USE_X_FORWARDED_PROTO"] = "True"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    if not (BASE_DIR / "static" / "dist" / "main.js").is_file():
        sys.exit("The styles and scripts haven't been built. Run:  npm run build   then start again.")
    print("Preparing styles and scripts...")
    if manage("collectstatic", "--noinput", "--verbosity", "0"):
        sys.exit("Couldn't prepare the static files (see the message above).")
    if manage("migrate", "--check", "--verbosity", "0"):
        print("\n  Your database needs updating. Stop this window (Ctrl+C) and run:\n"
              "      python manage.py migrate\n  then start again.\n")

    print("\ninvoicing_io is running. Keep this window open; close it (or press Ctrl+C) to stop.\n")
    print(f"  On this PC:   http://localhost:{PORT}")
    if name:
        print(f"  On your phone: https://{name}")
        print("  (If the phone can't open it, run once in an admin terminal:  tailscale serve --bg 8000)")
    elif exe:
        print("  Tailscale is installed but not signed in. Open Tailscale, sign in, then restart this window.")
    else:
        print("  Tailscale isn't installed, so only this PC can open the app. See README, section 'Using it on your phone'.")
    print()

    from waitress import serve

    from config.wsgi import application

    serve(application, host=HOST, port=PORT, threads=8, url_scheme="http",
          trusted_proxy="127.0.0.1", trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for"},
          clear_untrusted_proxy_headers=True)


if __name__ == "__main__":
    main()
