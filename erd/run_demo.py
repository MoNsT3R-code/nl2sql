"""Hackathon demo: ask the 20-table bank questions in plain English, free and offline.

    python erd/run_demo.py

One command does everything:
  1. starts a private PostgreSQL 16 on this computer (port 55432) and, the first time, loads
     banking_schema.sql and bank_data.sql (1,000 customers)
  2. connects the free local AI (Ollama with qwen2.5-coder:3b) if it is running
  3. opens the nl2sql web app at http://127.0.0.1:8000

Rehearsed questions (demo_questions.json) answer instantly. Anything else goes to the local AI,
which runs on this laptop: no API key, no cost, and no data leaves the machine.
Press Ctrl+C to stop; PostgreSQL is shut down too.

Needs:  pip install pgserver sqlalchemy "psycopg[binary]" pydantic
and, for questions beyond the rehearsed ones:  ollama pull qwen2.5-coder:3b
"""

import argparse
import os
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so `nl2sql` imports from the repository

from nl2sql import NL2SQL, open_database  # noqa: E402
from nl2sql.local import DEFAULT_LOCAL_MODEL, OllamaClient, RehearsedClient, load_rehearsed  # noqa: E402
from nl2sql.web import serve  # noqa: E402

PG_PORT = 55432
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "nl2sql-demo" / "pgdata"


def pg_bin() -> Path:
    try:
        import pgserver
    except ImportError:
        sys.exit("PostgreSQL isn't installed for Python. Run:  pip install pgserver")
    return Path(pgserver.__file__).parent / "pginstall" / "bin"


def run(cmd, **kw):
    # Output goes to a file, not a pipe: PostgreSQL's server process would otherwise hold the
    # pipe open and the command would never return.
    return subprocess.run([str(c) for c in cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def psql(bin_dir, *args, db="postgres"):
    return subprocess.run([str(bin_dir / "psql"), "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres", "-d", db,
                           "-v", "ON_ERROR_STOP=1", "-q", "-At", *map(str, args)],
                          capture_output=True, text=True)


def start_postgres() -> Path:
    bin_dir = pg_bin()
    if not (DATA_DIR / "PG_VERSION").exists():
        print("Setting up a private PostgreSQL (first run only)...")
        DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        if run([bin_dir / "initdb", "-D", DATA_DIR, "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C"]).returncode:
            sys.exit("Couldn't create the PostgreSQL data folder.")
    if run([bin_dir / "pg_isready", "-h", "127.0.0.1", "-p", PG_PORT]).returncode != 0:
        log = DATA_DIR.parent / "postgres.log"
        if run([bin_dir / "pg_ctl", "-D", DATA_DIR, "-o", f"-p {PG_PORT}", "-l", log, "-w", "start"]).returncode:
            sys.exit(f"PostgreSQL didn't start. See {log}")
    exists = psql(bin_dir, "-c", "SELECT 1 FROM pg_database WHERE datname = 'bank'").stdout.strip()
    if not exists:
        print("Loading the bank: 20 tables, 1,000 customers...")
        psql(bin_dir, "-c", "CREATE DATABASE bank")
        for f in ("banking_schema.sql", "bank_data.sql"):
            r = psql(bin_dir, "-f", HERE / f, db="bank")
            if r.returncode:
                psql(bin_dir, "-c", "DROP DATABASE bank")
                sys.exit(f"Loading {f} failed:\n{r.stderr[-800:]}")
    return bin_dir


def stop_postgres(bin_dir: Path):
    run([bin_dir / "pg_ctl", "-D", DATA_DIR, "-m", "fast", "stop"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default=DEFAULT_LOCAL_MODEL, help=f"Ollama model (default {DEFAULT_LOCAL_MODEL})")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    bin_dir = start_postgres()
    try:
        db = open_database(f"postgresql+psycopg://postgres@127.0.0.1:{PG_PORT}/bank")
        local = OllamaClient(args.model)
        ready, why = local.available()
        client = RehearsedClient(load_rehearsed(HERE / "demo_questions.json"), fallback=local if ready else None)
        label = f"{args.model} (local, offline)" if ready else "rehearsed questions only"
        engine = NL2SQL(db, client=client, model=label)
        if ready:
            print(f"Local AI: {args.model}. Warming it up (the first time can take a minute)...")
            try:
                local.warm_up(engine._full_system)
            except Exception as exc:  # the demo still works with rehearsed questions
                print(f"  warm-up skipped: {exc}")
        else:
            print(f"Local AI not available ({why}) Rehearsed questions still work.")

        url = f"http://127.0.0.1:{args.port}"
        server = serve(engine, label, port=args.port)
        print(f"\nReady: {url}   (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        print("Stopping PostgreSQL...")
        stop_postgres(bin_dir)


if __name__ == "__main__":
    main()
