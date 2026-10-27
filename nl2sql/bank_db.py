"""The "bank" database: a retail bank for trying nl2sql on realistic data.

The schema is db/bank/schema.sql. This module generates the rows (deterministically, from a
fixed random seed) and either writes them to db/bank/seed.sql or loads both files into SQLite:

    python -m nl2sql.bank_db --sqlite bank.db        # build a local SQLite copy
    python -m nl2sql.bank_db --write-sql db/bank     # regenerate seed.sql

The same two .sql files load into PostgreSQL and MySQL; see docker-compose.yml.

The data is simulated rather than random noise: transactions from 2025-01-01 to TODAY are
replayed in date order, so every account balance equals the sum of its transactions, outgoing
payments never overdraw savings, transfers appear on both accounts, loan instalments debit the
borrower's checking account, and fraud alerts point at real card payments.
"""

import argparse
import random
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent / "db" / "bank"
TODAY = date(2025, 10, 26)        # fixed "now", so the data never changes between runs
HISTORY_START = date(2025, 1, 1)  # transactions are recorded from this date

BRANCHES = [("Downtown Seattle", "Seattle", "WA"), ("Bellevue", "Bellevue", "WA"),
            ("Portland Pearl", "Portland", "OR"), ("San Francisco Market St", "San Francisco", "CA"),
            ("Austin Congress Ave", "Austin", "TX"), ("Chicago Loop", "Chicago", "IL"),
            ("Brooklyn Heights", "New York", "NY"), ("Boston Back Bay", "Boston", "MA")]

FIRST_NAMES = ["Ayesha", "Lucas", "Mei", "Omar", "Sara", "James", "Emma", "Liam", "Olivia",
               "Noah", "Fatima", "Hiro", "Carlos", "Sofia", "Ahmed", "Zara", "Ali", "Hannah",
               "Mateo", "Isabel", "Arjun", "Priya", "Chloe", "Daniel", "Aisha", "Kenji", "Lucia",
               "Ethan", "Maya", "Bilal", "Nina", "Tom", "Grace", "Samuel", "Leah", "Victor"]
LAST_NAMES = ["Khan", "Martin", "Chen", "Haddad", "Lopez", "Smith", "Brown", "Miller", "Garcia",
              "Tanaka", "Silva", "Ahmed", "Malik", "Wilson", "Taylor", "Nguyen", "Kim", "Patel",
              "Rahman", "Johnson", "Hernandez", "Clark", "Lee", "Walker", "Young", "Wright"]

MERCHANTS = {  # category -> (names, typical amount range)
    "groceries": (["Whole Foods Market", "Trader Joe's", "Safeway", "Kroger", "H Mart", "Costco"], (18, 260)),
    "dining": (["Blue Bottle Coffee", "Chipotle", "Shake Shack", "Olive Garden", "Sweetgreen",
                "Nobu", "Local Diner"], (6, 180)),
    "travel": (["Delta Air Lines", "United Airlines", "Marriott", "Hilton", "Airbnb", "Uber",
                "Amtrak"], (15, 1400)),
    "fuel": (["Shell", "Chevron", "BP", "Exxon"], (25, 90)),
    "utilities": (["City Water & Power", "Comcast Xfinity", "Verizon Wireless", "PG&E"], (40, 260)),
    "shopping": (["Amazon", "Target", "Best Buy", "IKEA", "Apple Store", "Nordstrom", "Home Depot"], (12, 900)),
    "entertainment": (["Netflix", "Spotify", "AMC Theatres", "Ticketmaster", "Steam"], (9, 320)),
    "health": (["CVS Pharmacy", "Walgreens", "City Dental", "Equinox Gym"], (12, 400)),
}
CATEGORY_WEIGHTS = {"groceries": 26, "dining": 22, "travel": 6, "fuel": 10, "utilities": 8,
                    "shopping": 16, "entertainment": 7, "health": 5}
FRAUD_REASONS = ["Unusual amount", "Unusual location", "Rapid successive payments",
                 "High-value payment at new merchant"]

N_CUSTOMERS = 250
CENT = Decimal("0.01")


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def _day(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError


def _month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = _add_months(d, 1)


def _month_end(d: date) -> date:
    return _add_months(date(d.year, d.month, 1), 1) - timedelta(days=1)


def generate(seed: int = 42) -> dict[str, list[tuple]]:
    """Return {table: rows} in insertion order (parents before children)."""
    rng = random.Random(seed)
    data: dict[str, list[tuple]] = {}

    # --- branches and staff ------------------------------------------------------
    data["branches"] = [(i, name, city, state, _day(rng, date(1998, 1, 1), date(2018, 12, 31)))
                        for i, (name, city, state) in enumerate(BRANCHES, 1)]

    def person():
        return rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)

    employees = [(1, "Margaret", "Okafor", "Chief Executive Officer", None, date(2012, 5, 14), None),
                 (2, "David", "Lindqvist", "Chief Risk Officer", None, date(2015, 9, 1), 1),
                 (3, "Rosa", "Delgado", "Head of Fraud Operations", None, date(2017, 2, 20), 2)]
    fraud_analysts, loan_officers = [], defaultdict(list)
    for _ in range(3):
        eid = len(employees) + 1
        employees.append((eid, *person(), "Fraud Analyst", None, _day(rng, date(2019, 1, 1), date(2024, 3, 1)), 3))
        fraud_analysts.append(eid)
    for bid, *_ in data["branches"]:
        mgr = len(employees) + 1
        employees.append((mgr, *person(), "Branch Manager", bid, _day(rng, date(2014, 1, 1), date(2021, 1, 1)), 1))
        for title, n in (("Loan Officer", rng.randint(1, 2)), ("Teller", rng.randint(2, 3))):
            for _ in range(n):
                eid = len(employees) + 1
                employees.append((eid, *person(), title, bid, _day(rng, date(2016, 1, 1), date(2025, 3, 1)), mgr))
                if title == "Loan Officer":
                    loan_officers[bid].append(eid)
    data["employees"] = employees

    # --- customers ------------------------------------------------------------------
    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        first, last = person()
        bid, _, city, state, _ = rng.choice(data["branches"])
        joined = _day(rng, date(2012, 1, 1), date(2025, 9, 30))
        kyc = "pending" if joined > date(2025, 5, 1) and rng.random() < 0.7 else \
            rng.choices(["verified", "rejected"], [97, 3])[0]
        risk = rng.choices(["low", "medium", "high"], [74, 20, 6])[0]
        customers.append((cid, first, last, f"{first}.{last}{cid}@example.com".lower(),
                          _day(rng, date(1950, 1, 1), date(2005, 12, 31)), city, state, bid,
                          joined, kyc, risk))
    data["customers"] = customers

    # --- accounts and cards ------------------------------------------------------------
    accounts, cards = [], []  # accounts as mutable dicts until the end
    for c in customers:
        cid, joined, kyc, risk, bid = c[0], c[8], c[9], c[10], c[7]
        if kyc == "rejected":
            continue
        wanted = ["checking"] if rng.random() < 0.92 else []
        if rng.random() < 0.55:
            wanted.append("savings")
        if rng.random() < 0.08:
            wanted.append("business")
        for kind in wanted or ["savings"]:
            aid = len(accounts) + 1
            opened = _day(rng, joined, min(TODAY - timedelta(days=20), _add_months(joined, 36)))
            acct = {"id": aid, "number": f"{bid:02d}{aid:08d}", "customer": cid, "branch": bid,
                    "type": kind, "opened": opened, "status": "active", "closed": None,
                    "frozen": None, "balance": Decimal(0),
                    "rate": {"checking": Decimal("0.0005"), "business": Decimal("0.0010"),
                             "savings": _money(rng.uniform(0.03, 0.045) * 100) / 100}[kind],
                    "salary": _money(rng.randrange(2800, 11000, 50)) if kind == "checking" and rng.random() < 0.7 else None,
                    "card": None}
            if kyc == "pending":
                acct["status"], acct["frozen"] = "frozen", opened
            elif risk == "high" and rng.random() < 0.35:
                acct["status"], acct["frozen"] = "frozen", _day(rng, date(2025, 4, 1), date(2025, 10, 1))
            elif rng.random() < 0.04 and opened < date(2025, 6, 1):
                acct["status"], acct["closed"] = "closed", _day(rng, max(opened, HISTORY_START) + timedelta(days=30), TODAY - timedelta(days=5))
            accounts.append(acct)
            if kind in ("checking", "business"):
                issued = max(opened, _day(rng, date(2021, 6, 1), date(2024, 12, 31)))
                card = [len(cards) + 1, aid, f"{rng.randint(0, 9999):04d}", issued,
                        _add_months(issued, 48), "active"]
                if card[4] < TODAY:
                    card[5] = "expired"
                if acct["status"] != "active":
                    card[5] = "blocked"
                cards.append(card)
                acct["card"] = card
    checking_of = {}
    for a in accounts:
        if a["type"] == "checking":
            checking_of.setdefault(a["customer"], a)

    merchants, by_category = [], defaultdict(list)
    for category, (names, _) in MERCHANTS.items():
        for name in names:
            merchants.append((len(merchants) + 1, name, category, rng.choice(BRANCHES)[1]))
            by_category[category].append(len(merchants))
    data["merchants"] = merchants

    # --- loans ----------------------------------------------------------------------
    loans, schedule = [], []  # schedule: (due_on, loan_id, amount_due, behaviour, planned_paid_on)
    # Borrowers have a salaried checking account that instalments are collected from.
    borrowers = [c for c in customers if c[9] == "verified" and c[0] in checking_of
                 and checking_of[c[0]]["salary"] and checking_of[c[0]]["status"] == "active"]
    for c in rng.sample(borrowers, k=len(borrowers) // 3):
        kind = rng.choices(["mortgage", "auto", "personal", "business"], [25, 30, 35, 10])[0]
        principal, term, rate = {
            "mortgage": (rng.randrange(180_000, 900_000, 5000), rng.choice([180, 240, 360]), rng.uniform(0.055, 0.072)),
            "auto": (rng.randrange(12_000, 65_000, 500), rng.choice([36, 48, 60, 72]), rng.uniform(0.045, 0.09)),
            "personal": (rng.randrange(2_000, 30_000, 250), rng.choice([12, 24, 36]), rng.uniform(0.08, 0.17)),
            "business": (rng.randrange(25_000, 400_000, 1000), rng.choice([36, 60, 84]), rng.uniform(0.07, 0.11)),
        }[kind]
        rate = _money(rate * 100) / 100
        checking = checking_of[c[0]]
        start = _day(rng, max(c[8], checking["opened"], date(2022, 1, 1)), date(2025, 8, 31))
        lid = len(loans) + 1
        bid = c[7]
        status = "defaulted" if rng.random() < 0.12 and start < date(2025, 3, 1) else "active"
        r = float(rate) / 12
        annuity = (1 - (1 + r) ** -term) / r
        # Banks cap repayments at a share of income; keep instalments under 35% of salary.
        principal = min(principal, int(float(checking["salary"]) * 0.35 * annuity) // 500 * 500)
        instalment = _money(principal / annuity)
        dues = [_add_months(start, k) for k in range(1, term + 1)]
        dues = [d for d in dues if d <= TODAY]
        if len(dues) == term:
            status = "paid_off" if status == "active" else status
        default_from = len(dues) - rng.randint(2, 5) if status == "defaulted" else None
        for k, due in enumerate(dues):
            if default_from is not None and k >= default_from:
                behaviour = "missed"
            else:
                behaviour = rng.choices(["on_time", "late"], [91, 9])[0]
            planned = due + timedelta(days=rng.randint(3, 25)) if behaviour == "late" else due
            schedule.append((due, lid, instalment, behaviour, planned))
        loans.append((lid, c[0], bid, rng.choice(loan_officers[bid]), kind, _money(principal),
                      rate, term, start, status))
    data["loans"] = loans
    loan_customer = {l[0]: l[1] for l in loans}

    # --- transactions: replay events in date order -----------------------------------
    events = []  # (date, seq, kind, payload)
    seq = 0

    def add(day, kind, **payload):
        nonlocal seq
        seq += 1
        events.append((day, seq, kind, payload))

    payable = [a for a in accounts]
    for month in _month_starts(HISTORY_START, TODAY):
        m_end = min(_month_end(month), TODAY)
        for a in payable:
            first_day = max(month, a["opened"], HISTORY_START)
            last_day = min(m_end, a["closed"] or m_end)
            if a["frozen"] and a["frozen"] <= last_day:
                last_day = min(last_day, a["frozen"] - timedelta(days=1))
            if first_day > last_day:
                continue
            days = lambda: _day(rng, first_day, last_day)  # noqa: E731
            if first_day == max(a["opened"], HISTORY_START):
                desc = "Opening deposit" if a["opened"] >= HISTORY_START else "Balance brought forward"
                size = {"checking": (300, 9000), "savings": (500, 60000), "business": (5000, 150000)}[a["type"]]
                add(first_day, "deposit", acct=a, amount=_money(rng.uniform(*size)), desc=desc)
            if a["type"] == "checking":
                if a["salary"] and first_day <= month.replace(day=25) <= last_day:
                    add(month.replace(day=25), "salary", acct=a, amount=a["salary"], desc="Payroll deposit")
                for _ in range(rng.randint(2, 7)):
                    add(days(), "card", acct=a)
                if rng.random() < 0.45:
                    add(days(), "withdrawal", acct=a, amount=_money(rng.randrange(40, 400, 20)), desc="ATM withdrawal")
                if rng.random() < 0.3:
                    add(days(), "transfer", acct=a)
            elif a["type"] == "savings":
                if rng.random() < 0.35:
                    add(days(), "deposit", acct=a, amount=_money(rng.randrange(100, 2500, 50)), desc="Deposit")
                if rng.random() < 0.08:
                    add(days(), "withdrawal", acct=a, amount=_money(rng.randrange(200, 3000, 50)), desc="Withdrawal")
            else:  # business
                for _ in range(rng.randint(3, 8)):
                    add(days(), "deposit", acct=a, amount=_money(rng.uniform(400, 18000)), desc="Customer payment received")
                for _ in range(rng.randint(3, 9)):
                    add(days(), "card", acct=a)
                for _ in range(rng.randint(0, 2)):
                    add(days(), "transfer", acct=a)
            if last_day == m_end:
                add(m_end, "month_end", acct=a)
            if a["closed"] and month <= a["closed"] <= m_end:
                add(a["closed"], "close", acct=a)
    for due, lid, amount, behaviour, planned in schedule:
        if behaviour != "missed" and HISTORY_START <= planned <= TODAY:
            add(planned, "loan", acct=checking_of[loan_customer[lid]], amount=amount, lid=lid, due=due)
    events.sort(key=lambda e: (e[0], e[1]))

    txns, paid_loans = [], {}

    def post(a, day, kind, amount, desc, card=None, merchant=None, other=None):
        a["balance"] += amount
        txns.append([len(txns) + 1, a["id"], day, kind, amount, card, merchant, other, desc])
        return txns[-1]

    accounts_by_open = sorted(accounts, key=lambda a: a["opened"])
    for day, _, kind, p in events:
        a = p["acct"]
        if a["closed"] and day > a["closed"] or a["frozen"] and day >= a["frozen"]:
            continue
        if kind in ("deposit", "salary"):
            post(a, day, kind, p["amount"], p["desc"])
        elif kind == "withdrawal":
            if a["balance"] - p["amount"] >= 0:
                post(a, day, "withdrawal", -p["amount"], p["desc"])
        elif kind == "card":
            if a["card"] is None or a["card"][5] != "active" and a["card"][5] != "expired":
                continue
            weights = CATEGORY_WEIGHTS if a["type"] == "checking" else \
                {"shopping": 30, "travel": 25, "utilities": 20, "dining": 15, "fuel": 10}
            category = rng.choices(list(weights), list(weights.values()))[0]
            lo, hi = MERCHANTS[category][1]
            amount = _money(min(rng.lognormvariate(0, 0.8) * (lo + hi) / 4 + lo, hi * 1.6))
            if a["balance"] - amount < 0:
                continue
            mid = rng.choice(by_category[category])
            post(a, day, "card_payment", -amount, merchants[mid - 1][1], card=a["card"][0], merchant=mid)
        elif kind == "transfer":
            own = [b for b in accounts if b["customer"] == a["customer"] and b is not a]
            pool = own if own and rng.random() < 0.6 else accounts_by_open
            target = rng.choice(pool)
            if target is a or target["opened"] > day or (target["closed"] and target["closed"] <= day) \
                    or (target["frozen"] and target["frozen"] <= day):
                continue
            amount = _money(rng.randrange(50, 2500, 25) if a["type"] == "checking" else rng.uniform(1000, 20000))
            if a["balance"] - amount < 0:
                continue
            post(a, day, "transfer_out", -amount, f"Transfer to account {target['number']}", other=target["id"])
            post(target, day, "transfer_in", amount, f"Transfer from account {a['number']}", other=a["id"])
        elif kind == "month_end":
            if a["type"] == "savings" and a["balance"] > 0:
                interest = _money(a["balance"] * a["rate"] / 12)
                if interest > 0:
                    post(a, day, "interest", interest, "Monthly interest")
            # Fees are waived when the balance can't cover them.
            elif a["type"] == "checking" and Decimal(12) <= a["balance"] < 1500:
                post(a, day, "fee", Decimal("-12.00"), "Monthly maintenance fee")
            elif a["type"] == "business" and a["balance"] >= 25:
                post(a, day, "fee", Decimal("-25.00"), "Business account fee")
        elif kind == "loan":
            # Instalments are collected even if that overdraws the checking account.
            t = post(a, day, "loan_payment", -p["amount"], f"Loan #{p['lid']} instalment")
            paid_loans[(p["lid"], p["due"])] = t[2]
        elif kind == "close":
            if a["balance"] != 0:
                post(a, day, "withdrawal", -a["balance"], "Account closure payout")

    # --- loan payments, fraud alerts, final tables -------------------------------------
    loan_payments = []
    for due, lid, amount, behaviour, planned in sorted(schedule, key=lambda s: (s[1], s[0])):
        if behaviour == "missed":
            paid_on, paid = None, Decimal("0.00")
        elif planned < HISTORY_START:  # paid before transactions were recorded
            paid_on, paid = planned, amount
        elif (lid, due) in paid_loans:
            paid_on, paid = paid_loans[(lid, due)], amount
        else:  # a late payment that hasn't arrived yet
            paid_on, paid = None, Decimal("0.00")
        loan_payments.append((len(loan_payments) + 1, lid, due, amount, paid_on, paid))

    fraud = []
    card_txns = [t for t in txns if t[3] == "card_payment"]
    for t in card_txns:
        big = -t[4] > 900
        if (big and rng.random() < 0.35) or rng.random() < 0.004:
            raised = t[2]
            reviewed = (TODAY - raised).days > 7
            confirmed = (rng.random() < 0.25) if reviewed else None
            fraud.append((len(fraud) + 1, t[0], raised,
                          "Unusual amount" if big else rng.choice(FRAUD_REASONS),
                          rng.choice(fraud_analysts) if reviewed else None, confirmed))
            if confirmed:
                cards[t[5] - 1][5] = "blocked"

    data["accounts"] = [(a["id"], a["number"], a["customer"], a["branch"], a["type"], a["status"],
                         a["opened"], a["closed"], a["rate"], a["balance"]) for a in accounts]
    data["cards"] = [tuple(c) for c in cards]
    # Tables must load in foreign-key order: loans reference employees/customers (above),
    # transactions reference cards/merchants/accounts.
    order = ["branches", "employees", "customers", "accounts", "cards", "merchants",
             "transactions", "loans", "loan_payments", "fraud_alerts"]
    data["transactions"] = [tuple(t) for t in txns]
    data["loan_payments"] = loan_payments
    data["fraud_alerts"] = fraud
    return {name: data[name] for name in order}


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def seed_sql(data: dict[str, list[tuple]], batch: int = 250) -> str:
    out = ["-- Generated by `python -m nl2sql.bank_db --write-sql db/bank`; do not edit by hand.",
           "-- Plain multi-row INSERTs that load into PostgreSQL, MySQL 8 and SQLite.", ""]
    for table, rows in data.items():
        out.append(f"-- {table}: {len(rows)} rows")
        for i in range(0, len(rows), batch):
            values = ",\n".join("(" + ", ".join(_literal(v) for v in row) + ")"
                                for row in rows[i:i + batch])
            out.append(f"INSERT INTO {table} VALUES\n{values};")
        out.append("")
    return "\n".join(out)


def build_sqlite(path: str | Path, sql_dir: Path = SQL_DIR) -> Path:
    """Create a SQLite copy by running schema.sql and seed.sql exactly as written."""
    path = Path(path)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript((sql_dir / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((sql_dir / "seed.sql").read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="nl2sql.bank_db", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", metavar="FILE", help="build a SQLite database at FILE")
    parser.add_argument("--write-sql", metavar="DIR", help="write seed.sql into DIR")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not (args.sqlite or args.write_sql):
        parser.error("give --sqlite FILE and/or --write-sql DIR")

    if args.write_sql:
        data = generate(args.seed)
        out = Path(args.write_sql) / "seed.sql"
        out.write_text(seed_sql(data), encoding="utf-8", newline="\n")  # LF for Linux containers
        print(f"wrote {out} ({', '.join(f'{t} {len(r)}' for t, r in data.items())})")
    if args.sqlite:
        print(f"built {build_sqlite(args.sqlite)}")


if __name__ == "__main__":
    main()
