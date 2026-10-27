"""Prototype data generator for banking_schema.sql.

Writes one SQL file of INSERT statements that fills all 20 tables with realistic, linked data
for a Pakistani retail bank: branches and staff, customers with CNICs and KYC documents,
accounts with IBANs, member cards, merchants, saved payees, loans and a month of transactions.

    python generate_bank_data.py                       # 1,000 customers -> bank_data.sql
    python generate_bank_data.py --customers 500000    # the full-size version (a large file)
    psql -d bank -f banking_schema.sql -f bank_data.sql

Every row respects the schema's UNIQUE and CHECK rules: ids come from one counter per table,
CNICs, mobiles, emails, IBANs and reference numbers are built from those ids so they can never
repeat, and each account's balance is the result of replaying its own transactions.
The same --seed always produces the same data.
"""

import argparse
import random
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

TODAY = date(2025, 10, 26)
HISTORY_START = TODAY - timedelta(days=29)   # one month of transactions
KIBOR = 11.0
HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------------------------
# Output: rows go to one temporary file per table, joined in foreign-key order at the end, so
# memory stays flat however many customers are generated.
# ---------------------------------------------------------------------------------------------
class Raw(str):
    """A value written into SQL as-is (numbers already formatted)."""


def money(paisa: int) -> Raw:
    sign = "-" if paisa < 0 else ""
    paisa = abs(paisa)
    return Raw(f"{sign}{paisa // 100}.{paisa % 100:02d}")


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, Raw):
        return v
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return f"'{v:%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def schema_columns(schema: Path) -> dict[str, list[str]]:
    """Read each table's column order straight from banking_schema.sql."""
    tables, current = {}, None
    for line in schema.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"CREATE TABLE (\w+) \(", line):
            current = tables.setdefault(m.group(1), [])
        elif current is not None and line.startswith(");"):
            current = None
        elif current is not None and (m := re.match(r"\s{4}(\w+)\s+[A-Z]", line)) and m.group(1) != "CONSTRAINT":
            current.append(m.group(1))
    return tables


LOAD_ORDER = ["designation", "customer_segment", "currency", "card_type", "channel", "transaction_type",
              "loan_product", "branch", "employee", "customer", "kyc_document", "account_type", "account",
              "account_holder", "card_product", "card", "merchant", "beneficiary", "loan", "bank_transaction"]
PK = {"designation": "designation_id", "customer_segment": "segment_id", "currency": "currency_id",
      "card_type": "card_type_id", "channel": "channel_id", "transaction_type": "txn_type_id",
      "loan_product": "loan_product_id", "branch": "branch_id", "employee": "employee_id",
      "customer": "customer_id", "kyc_document": "document_id", "account_type": "account_type_id",
      "account": "account_id", "account_holder": "holder_id", "card_product": "card_product_id",
      "card": "card_id", "merchant": "merchant_id", "beneficiary": "beneficiary_id", "loan": "loan_id",
      "bank_transaction": "transaction_id"}


class Writer:
    def __init__(self, columns):
        self.columns = columns
        self.tmp = Path(tempfile.mkdtemp(prefix="bankgen-"))
        self.files = {t: open(self.tmp / f"{t}.part", "w", encoding="utf-8") for t in LOAD_ORDER}
        self.next_id = {t: 1 for t in LOAD_ORDER}
        self.count = {t: 0 for t in LOAD_ORDER}

    def new_id(self, table):
        i = self.next_id[table]
        self.next_id[table] += 1
        return i

    def row(self, table, values: dict):
        cols = self.columns[table]
        missing = set(cols) - set(values)
        if missing:
            raise KeyError(f"{table}: missing {sorted(missing)}")
        self.files[table].write("(" + ", ".join(sql_literal(values[c]) for c in cols) + ")\n")
        self.count[table] += 1

    def finish(self, out: Path, header: str, batch: int = 1000):
        for f in self.files.values():
            f.close()
        with open(out, "w", encoding="utf-8", newline="\n") as o:
            o.write(header + "\nBEGIN;\n\n")
            for t in LOAD_ORDER:
                cols = ", ".join(self.columns[t])
                o.write(f"-- {t}: {self.count[t]:,} rows\n")
                with open(self.tmp / f"{t}.part", encoding="utf-8") as f:
                    chunk = []
                    for line in f:
                        chunk.append(line.rstrip("\n"))
                        if len(chunk) == batch:
                            o.write(f"INSERT INTO {t} ({cols}) OVERRIDING SYSTEM VALUE VALUES\n" + ",\n".join(chunk) + ";\n")
                            chunk = []
                    if chunk:
                        o.write(f"INSERT INTO {t} ({cols}) OVERRIDING SYSTEM VALUE VALUES\n" + ",\n".join(chunk) + ";\n")
                o.write("\n")
                if t == "employee":
                    # Branches were inserted without managers (employees didn't exist yet).
                    o.write("UPDATE branch b SET manager_employee_id = e.employee_id\n"
                            "FROM employee e JOIN designation d ON d.designation_id = e.designation_id\n"
                            "WHERE d.title = 'Branch Manager' AND e.branch_id = b.branch_id;\n\n")
            o.write("-- Move each id counter past the rows inserted with explicit ids.\n")
            for t in LOAD_ORDER:
                o.write(f"SELECT setval(pg_get_serial_sequence('{t}', '{PK[t]}'), GREATEST(1, (SELECT max({PK[t]}) FROM {t})));\n")
            o.write("\nCOMMIT;\nANALYZE;\n")
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------------------------
DESIGNATIONS = [  # title, grade, department, min, max salary, approval limit, approve loans, authorise txn
    ("Chief Executive Officer", "EVP", "Executive", 2_500_000, 6_000_000, 500_000_000, True, True),
    ("Branch Manager", "VP", "Branch Banking", 350_000, 750_000, 10_000_000, True, True),
    ("Operations Manager", "AVP", "Branch Banking", 220_000, 420_000, 2_000_000, False, True),
    ("Relationship Manager", "AVP", "Retail Banking", 180_000, 380_000, 0, False, False),
    ("Cash Officer (Teller)", "OG-II", "Branch Banking", 75_000, 140_000, 500_000, False, True),
    ("Customer Service Officer", "OG-II", "Branch Banking", 80_000, 150_000, 0, False, False),
    ("Loan Officer", "OG-I", "Consumer Finance", 120_000, 260_000, 0, False, False),
    ("Credit Analyst", "OG-I", "Risk Management", 150_000, 300_000, 0, False, False),
    ("Compliance Officer", "AVP", "Compliance", 200_000, 400_000, 0, False, False),
    ("Fraud & Risk Analyst", "OG-I", "Risk Management", 150_000, 300_000, 0, False, False),
    ("IT Support Officer", "OG-II", "Information Technology", 110_000, 220_000, 0, False, False),
]
SEGMENTS = [  # code, name, min age, max age, min income, min balance, priority, fee waiver %, share of customers
    ("RETAIL", "Mass Retail", 18, None, 0, 0, False, 0, 56),
    ("PRIORITY", "Priority Banking", 21, None, 300_000, 5_000_000, True, 50, 4),
    ("PREMIER", "Premier", 21, None, 800_000, 25_000_000, True, 100, 1),
    ("CORP", "Corporate", None, None, 0, 0, True, 0, 1),
    ("SME", "SME", None, None, 0, 0, False, 25, 3),
    ("STUDENT", "Student", 18, 25, 0, 0, False, 100, 8),
    ("TEENS", "Teens Club", 13, 17, 0, 0, False, 100, 5),
    ("KIDS", "Kids Club", 0, 12, 0, 0, False, 100, 4),
    ("SENIOR", "Senior Citizen", 60, None, 0, 0, False, 50, 9),
    ("RDA", "Roshan Digital", 18, None, 0, 0, False, 0, 9),
]
CURRENCIES = [("PKR", "Pakistani Rupee", "Rs", "Pakistan", 1, 1, True), ("USD", "US Dollar", "$", "United States", 280.10, 281.60, False),
              ("EUR", "Euro", "€", "Eurozone", 324.50, 327.90, False), ("GBP", "Pound Sterling", "£", "United Kingdom", 372.20, 376.10, False),
              ("AED", "UAE Dirham", "AED", "United Arab Emirates", 76.20, 76.80, False), ("SAR", "Saudi Riyal", "SAR", "Saudi Arabia", 74.60, 75.20, False),
              ("CNY", "Chinese Yuan", "CN¥", "China", 38.90, 39.60, False), ("JPY", "Japanese Yen", "¥", "Japan", 1.85, 1.89, False),
              ("CAD", "Canadian Dollar", "C$", "Canada", 199.80, 202.10, False), ("AUD", "Australian Dollar", "A$", "Australia", 182.40, 184.90, False)]
CARD_TYPES = [  # code, name, credit, physical, reloadable, validity years, chip, contactless
    ("DEBIT", "Debit card", False, True, False, 5, True, True), ("CREDIT", "Credit card", True, True, False, 4, True, True),
    ("PREPAID", "Prepaid card", False, True, True, 3, True, True), ("VIRTUAL", "Virtual card", False, False, True, 3, False, False),
    ("CORP", "Corporate card", True, True, False, 3, True, True), ("FOREX", "Forex / travel card", False, True, True, 3, True, True),
    ("ATM", "ATM-only card", False, True, False, 5, True, False), ("ISLAMIC", "Islamic (Shariah) card", False, True, False, 5, True, True),
    ("GIFT", "Gift card", False, True, False, 1, True, True), ("WEAR", "Wearable (contactless band)", False, True, True, 3, True, True)]
CHANNELS = [  # code, name, operator, digital, 24x7, per txn limit, daily limit, otp, fee
    ("BRANCH", "Branch counter", "NBMS branches", False, False, 10_000_000, 50_000_000, False, 0),
    ("ATM", "ATM", "1LINK", False, True, 50_000, 100_000, False, 0),
    ("APP", "Mobile app", "NBMS", True, True, 1_000_000, 2_000_000, True, 0),
    ("WEB", "Internet banking", "NBMS", True, True, 1_000_000, 2_000_000, True, 0),
    ("POS", "POS terminal", "Visa / Mastercard / PayPak", False, True, 500_000, 1_000_000, False, 0),
    ("RAAST", "Raast (SBP instant payments)", "State Bank of Pakistan", True, True, 1_000_000, 2_000_000, True, 0),
    ("IBFT", "IBFT via 1LINK", "1LINK", True, True, 1_000_000, 2_000_000, True, 0),
    ("IVR", "Call centre (IVR)", "NBMS", False, True, 100_000, 200_000, True, 0),
    ("AGENT", "Branchless / agent banking", "Agent network", False, False, 25_000, 50_000, False, 0),
    ("ECOM", "E-commerce gateway", "Visa / Mastercard", True, True, 500_000, 1_000_000, True, 0)]
TXN_TYPES = {  # code: (name, direction, reversible, needs authorisation, withholding tax %)
    "CDEP": ("Cash deposit", "credit", True, False, 0), "CWDL": ("Cash withdrawal", "debit", False, False, 0.6),
    "RAAST": ("Raast transfer", "debit", False, False, 0), "IBFT": ("IBFT transfer", "debit", False, False, 0),
    "BILL": ("Utility bill payment", "debit", False, False, 0), "POS": ("POS purchase", "debit", True, False, 0),
    "ONLINE": ("Online purchase", "debit", True, False, 0), "CHQ": ("Cheque clearing", "credit", True, True, 0),
    "PROFIT": ("Profit credit", "credit", False, False, 15), "LOANI": ("Loan instalment", "debit", False, False, 0),
    "SALARY": ("Salary credit", "credit", False, False, 0), "TRFIN": ("Incoming transfer", "credit", True, False, 0),
    "LOAND": ("Loan disbursement", "credit", False, True, 0), "SVC": ("Service charges", "debit", False, False, 0)}
LOAN_PRODUCTS = [  # code, name, category, islamic, min, max, max tenure, rate type, spread, fee %
    ("PF", "Personal finance", "personal", False, 50_000, 3_000_000, 60, "kibor_linked", 9.5, 2.0),
    ("CAR", "Car finance", "auto", False, 500_000, 10_000_000, 84, "kibor_linked", 4.0, 1.0),
    ("HOME", "House finance", "housing", False, 1_000_000, 50_000_000, 300, "kibor_linked", 3.0, 1.0),
    ("IJARA", "Ijarah car finance (Islamic)", "auto", True, 500_000, 10_000_000, 84, "kibor_linked", 4.5, 1.0),
    ("SMERF", "SME running finance", "sme", False, 500_000, 75_000_000, 12, "kibor_linked", 3.5, 0.5),
    ("AGRI", "Agriculture finance", "agriculture", False, 100_000, 5_000_000, 36, "kibor_linked", 2.5, 0.5),
    ("GOLD", "Gold-backed finance", "gold", False, 25_000, 2_000_000, 12, "fixed", 0, 1.5),
    ("MPMG", "Mera Pakistan Mera Ghar", "government_scheme", False, 500_000, 10_000_000, 240, "fixed", 0, 0),
    ("STU", "Student finance", "student", False, 50_000, 2_000_000, 60, "fixed", 0, 0),
    ("SOLAR", "Solar panel finance", "green", False, 100_000, 3_000_000, 60, "kibor_linked", 2.0, 1.0)]
FIXED_RATES = {"GOLD": 18.0, "MPMG": 5.0, "STU": 7.0}
ACCOUNT_TYPES = [  # code, name, category, islamic, opening deposit, min balance, cap, profit %, monthly fee, cheque book, currency, segment
    ("ASAAN", "Asaan Account", "basic", False, 100, 0, 1_000_000, 0, 0, False, "PKR", None),
    ("ASAAND", "Asaan Digital Account", "basic", False, 0, 0, 1_000_000, 0, 0, False, "PKR", None),
    ("CURR", "Current Account", "current", False, 10_000, 25_000, None, 0, 150, True, "PKR", None),
    ("SAV", "Savings Account (PLS)", "savings", False, 10_000, 10_000, None, 10.5, 0, True, "PKR", None),
    ("KIDS", "Kids Club Account", "savings", False, 1_000, 0, 500_000, 10.0, 0, False, "PKR", "KIDS"),
    ("TEENS", "Teens Club Account", "savings", False, 1_000, 0, 500_000, 10.0, 0, False, "PKR", "TEENS"),
    ("SENIOR", "Senior Citizen Savings", "savings", False, 10_000, 0, None, 11.0, 0, True, "PKR", "SENIOR"),
    ("RDA", "Roshan Digital Account", "current", False, 0, 0, None, 0, 0, True, "PKR", "RDA"),
    ("FCY", "Foreign Currency Account", "foreign_currency", False, 100, 100, None, 1.5, 0, False, "USD", None),
    ("TD1Y", "Term Deposit (1 year)", "term_deposit", False, 100_000, 100_000, None, 11.25, 0, False, "PKR", None)]
CARD_PRODUCTS = [  # name, card type, account type, segment, network, tier, annual fee, issuance fee, ATM, POS, credit limit, reward %
    ("Teens Club debit card", "DEBIT", "TEENS", "TEENS", "Visa", "Teens", 0, 0, 10_000, 20_000, None, 0),
    ("Kids Club card", "PREPAID", "KIDS", "KIDS", "Visa", "Kids", 0, 0, 5_000, 10_000, None, 0),
    ("Asaan Account debit card (PayPak)", "DEBIT", "ASAAN", None, "PayPak", "Basic", 0, 0, 50_000, 100_000, None, 0),
    ("Raast-linked PayPak card", "DEBIT", None, None, "PayPak", "Basic", 500, 0, 100_000, 200_000, None, 0),
    ("World Mastercard (credit)", "CREDIT", None, "PREMIER", "Mastercard", "World", 25_000, 0, 100_000, 2_000_000, 2_000_000, 1.5),
    ("Platinum Visa (credit)", "CREDIT", None, "PRIORITY", "Visa", "Platinum", 15_000, 0, 100_000, 1_000_000, 1_000_000, 1.0),
    ("Gold credit card", "CREDIT", None, None, "Mastercard", "Gold", 6_000, 0, 50_000, 500_000, 300_000, 0.5),
    ("Classic debit card", "DEBIT", None, None, "Visa", "Classic", 1_500, 500, 50_000, 100_000, None, 0),
    ("Titanium debit card", "DEBIT", None, None, "Mastercard", "Titanium", 3_500, 1_000, 100_000, 300_000, None, 0.25),
    ("Virtual prepaid card", "VIRTUAL", None, None, "Visa", "Virtual", 0, 0, 0, 100_000, None, 0)]
BIN = {"Visa": "4213", "Mastercard": "5412", "PayPak": "2205", "UnionPay": "6250"}

CITIES = {  # city: (province, phone code, weight, areas)
    "Karachi": ("Sindh", "021", 24, ["Clifton", "DHA Phase 6", "Gulshan-e-Iqbal", "Saddar", "North Nazimabad"]),
    "Lahore": ("Punjab", "042", 20, ["Gulberg", "Model Town", "Johar Town", "DHA Phase 5", "Mall Road"]),
    "Islamabad": ("Islamabad Capital Territory", "051", 9, ["Blue Area", "F-7 Markaz", "G-9 Markaz"]),
    "Rawalpindi": ("Punjab", "051", 7, ["Saddar", "Satellite Town"]),
    "Faisalabad": ("Punjab", "041", 8, ["Kohinoor City", "D Ground"]),
    "Multan": ("Punjab", "061", 5, ["Cantt", "Gulgasht"]),
    "Peshawar": ("Khyber Pakhtunkhwa", "091", 6, ["University Road", "Hayatabad"]),
    "Quetta": ("Balochistan", "081", 3, ["Jinnah Road"]),
    "Hyderabad": ("Sindh", "022", 4, ["Latifabad"]),
    "Sialkot": ("Punjab", "052", 3, ["Cantt"]),
    "Gilgit": ("Gilgit-Baltistan", "05811", 1, ["Airport Road"]),
    "Muzaffarabad": ("Azad Jammu and Kashmir", "05822", 1, ["Chattar"]),
}
DISCO = {"Karachi": "K-Electric", "Lahore": "LESCO", "Islamabad": "IESCO", "Rawalpindi": "IESCO", "Faisalabad": "FESCO",
         "Multan": "MEPCO", "Peshawar": "PESCO", "Quetta": "QESCO", "Hyderabad": "HESCO", "Sialkot": "GEPCO",
         "Gilgit": "GB Power", "Muzaffarabad": "AJK Electricity"}
MALE = ["Muhammad", "Ahmed", "Ali", "Hassan", "Usman", "Bilal", "Hamza", "Omar", "Faisal", "Imran", "Kamran", "Zeeshan",
        "Saad", "Asad", "Fahad", "Junaid", "Rizwan", "Tariq", "Waqas", "Shahid", "Adnan", "Haris", "Danish", "Arslan"]
FEMALE = ["Ayesha", "Fatima", "Sana", "Hira", "Maryam", "Zainab", "Mahnoor", "Amna", "Iqra", "Sadia", "Nadia", "Saima",
          "Rabia", "Mehwish", "Anum", "Kiran", "Sara", "Zara", "Areeba", "Hafsa", "Noor", "Laiba", "Komal", "Bushra"]
FAMILY = ["Khan", "Ahmed", "Malik", "Hussain", "Iqbal", "Butt", "Qureshi", "Sheikh", "Raza", "Siddiqui", "Chaudhry", "Abbasi",
          "Mirza", "Javed", "Shah", "Baig", "Akhtar", "Rana", "Bhatti", "Awan", "Farooq", "Haider", "Memon", "Baloch", "Yousafzai"]
BUSINESS_WORDS = ["Textiles", "Traders", "Enterprises", "Foods", "Pharma", "Steel", "Builders", "Motors", "Electronics",
                  "Logistics", "Rice Mills", "Leather", "Surgical", "Sports", "Technologies"]
OTHER_BANKS = ["HABB", "MUCB", "UNIL", "ABPA", "MEZN", "BAHL", "ALFH", "SCBL", "JSBL", "NBPA"]
BANK_NAMES = {"HABB": "Habib Bank", "MUCB": "MCB Bank", "UNIL": "United Bank", "ABPA": "Allied Bank", "MEZN": "Meezan Bank",
              "BAHL": "Bank Al Habib", "ALFH": "Bank Alfalah", "SCBL": "Standard Chartered", "JSBL": "JS Bank", "NBPA": "National Bank"}
MERCHANTS = [  # name, category, mcc, typical spend (Rs), national (one record) or per city
    ("Imtiaz Supermarket", "Groceries", "5411", 6_000, False), ("Al-Fatah", "Groceries", "5411", 5_000, False),
    ("Chase Up", "Groceries", "5411", 4_500, False), ("Metro Cash & Carry", "Groceries", "5411", 9_000, False),
    ("KFC", "Fast food", "5814", 2_200, False), ("Cheezious", "Fast food", "5814", 2_500, False),
    ("Savour Foods", "Restaurants", "5812", 1_500, False), ("Monal", "Restaurants", "5812", 7_000, False),
    ("PSO", "Fuel", "5541", 6_000, False), ("Shell", "Fuel", "5541", 6_500, False),
    ("D. Watson", "Pharmacy", "5912", 2_000, False), ("Servaid Pharmacy", "Pharmacy", "5912", 1_800, False),
    ("Khaadi", "Clothing", "5651", 8_000, False), ("Gul Ahmed", "Clothing", "5651", 7_500, False),
    ("Outfitters", "Clothing", "5651", 6_000, False), ("Shaukat Khanum Hospital", "Hospitals", "8062", 15_000, True),
    ("Daraz.pk", "Online shopping", "5399", 3_500, True), ("Foodpanda", "Online food", "5814", 1_800, True),
    ("Careem", "Ride hailing", "4121", 900, True), ("PIA", "Airlines", "4511", 45_000, True),
    ("Pearl Continental", "Hotels", "7011", 35_000, True), ("Netflix", "Subscriptions", "4899", 1_100, True)]
UTILITIES = sorted(set(DISCO.values())) + ["SNGPL", "SSGC", "PTCL", "StormFiber", "Jazz", "Zong", "Telenor"]
NICKNAMES = ["Ammi", "Abbu", "Bhai", "Baji", "Landlord", "Driver", "Maid", "School fee", "Tuition", "Committee",
             "Brother", "Sister", "Friend", "Office", "Mamu", "Khala", "Chacha", "Plumber"]


# ---------------------------------------------------------------------------------------------
# Helpers: identifiers that can never repeat, built from per-table ids
# ---------------------------------------------------------------------------------------------
def perm7(i: int) -> int:
    """A one-to-one shuffle of 0..9,999,999, so ids become unique but random-looking numbers."""
    return (i * 7_654_321 + 1_234_567) % 10_000_000


def cnic(i: int, rng, female: bool) -> str:
    last = rng.choice([2, 4, 6, 8]) if female else rng.choice([1, 3, 5, 7, 9])  # NADRA: even = female
    return f"{rng.choice([3, 3, 3, 4, 4, 6, 1, 5, 7])}{rng.randint(1000, 9999)}-{perm7(i):07d}-{last}"


def mobile(i: int, rng) -> str:
    return f"03{rng.choice(['00', '01', '03', '05', '06', '13', '15', '21', '33', '35', '45', '47'])}{perm7(i):07d}"


def iban(bank: str, account_no: str) -> str:
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in bank + account_no + "PK00")
    return f"PK{98 - int(digits) % 97:02d}{bank}{account_no}"


def rand_date(rng, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return date(d.year + y, m + 1, day)
        except ValueError:
            pass


def at(day: date, rng, hours=(8, 23)) -> datetime:
    return datetime(day.year, day.month, day.day, rng.randint(*hours) if hours[0] < hours[1] else hours[0], rng.randint(0, 59), rng.randint(0, 59))


def rupees(rng, typical: float, spread: float = 0.6) -> int:
    """A right-skewed amount around `typical` rupees, returned in paisa."""
    return max(100, int(rng.lognormvariate(0, spread) * typical) * 100)


# ---------------------------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------------------------
def generate(n_customers: int, seed: int, out: Path):
    rng = random.Random(seed)
    w = Writer(schema_columns(HERE / "banking_schema.sql"))

    # Reference tables ---------------------------------------------------------------------
    desig = {}
    for t, grade, dept, lo, hi, limit, loans, auth in DESIGNATIONS:
        desig[t] = w.new_id("designation")
        w.row("designation", dict(designation_id=desig[t], title=t, grade=grade, department=dept, min_salary=lo,
                                  max_salary=hi, approval_limit_pkr=limit, can_approve_loans=loans,
                                  can_authorise_txn=auth, description=None, status="active"))
    seg = {}
    for code, name, amin, amax, inc, bal, prio, waiver, _ in SEGMENTS:
        seg[code] = w.new_id("customer_segment")
        w.row("customer_segment", dict(segment_id=seg[code], segment_code=code, segment_name=name, min_age=amin,
                                       max_age=amax, min_monthly_income=inc, min_relationship_balance=bal,
                                       priority_service=prio, fee_waiver_pct=waiver, description=None, status="active"))
    cur = {}
    for iso, name, sym, country, buy, sell, base in CURRENCIES:
        cur[iso] = w.new_id("currency")
        w.row("currency", dict(currency_id=cur[iso], iso_code=iso, currency_name=name, symbol=sym, country=country,
                               decimal_places=0 if iso == "JPY" else 2, buy_rate_pkr=buy, sell_rate_pkr=sell,
                               is_base_currency=base, rate_updated_at=datetime(2025, 10, 26, 9, 0), status="active"))
    ctype = {}
    for code, name, credit, phys, reload, years, chip, contactless in CARD_TYPES:
        ctype[code] = w.new_id("card_type")
        w.row("card_type", dict(card_type_id=ctype[code], type_code=code, type_name=name, is_credit=credit,
                                is_physical=phys, is_reloadable=reload, default_validity_years=years,
                                chip_enabled=chip, contactless_enabled=contactless, description=None, status="active"))
    chan = {}
    for code, name, op, digital, always, per, daily, otp, fee in CHANNELS:
        chan[code] = w.new_id("channel")
        w.row("channel", dict(channel_id=chan[code], channel_code=code, channel_name=name, operator=op,
                              is_digital=digital, available_24x7=always, per_txn_limit=per, daily_limit=daily,
                              requires_otp=otp, fee_per_txn=fee, status="active"))
    ttype = {}
    for code, (name, direction, rev, auth, wht) in TXN_TYPES.items():
        ttype[code] = w.new_id("transaction_type")
        w.row("transaction_type", dict(txn_type_id=ttype[code], type_code=code, type_name=name, direction=direction,
                                       is_reversible=rev, requires_authorisation=auth, service_charge=0,
                                       withholding_tax_pct=wht, fed_pct=0, daily_limit=None, status="active"))
    lprod = {}
    for code, name, cat, isl, lo, hi, tenure, rtype, spread, fee in LOAN_PRODUCTS:
        lprod[code] = (w.new_id("loan_product"), lo, hi, tenure, FIXED_RATES.get(code, KIBOR + spread))
        w.row("loan_product", dict(loan_product_id=lprod[code][0], product_code=code, product_name=name, category=cat,
                                   is_islamic=isl, min_amount=lo, max_amount=hi, max_tenure_months=tenure,
                                   rate_type=rtype, spread_over_kibor_pct=spread, processing_fee_pct=fee))
    atype = {}
    for code, name, cat, isl, opening, minbal, cap, rate, fee, chq, ccy, sg in ACCOUNT_TYPES:
        atype[code] = dict(id=w.new_id("account_type"), cap=cap, rate=rate, ccy=ccy, cat=cat)
        w.row("account_type", dict(account_type_id=atype[code]["id"], segment_id=seg.get(sg), currency_id=cur[ccy],
                                   product_code=code, product_name=name, category=cat, is_islamic=isl,
                                   min_opening_deposit=opening, min_balance=minbal, max_balance_limit=cap,
                                   profit_rate_pct=rate, monthly_service_fee=fee, cheque_book_allowed=chq))
    cprod = {}
    for name, ct, at_, sg, net, tier, annual, issue, atm, pos, limit, reward in CARD_PRODUCTS:
        cprod[name] = dict(id=w.new_id("card_product"), network=net, limit=limit, years=next(c[5] for c in CARD_TYPES if c[0] == ct))
        w.row("card_product", dict(card_product_id=cprod[name]["id"], card_type_id=ctype[ct],
                                   account_type_id=atype[at_]["id"] if at_ else None, currency_id=cur["PKR"],
                                   segment_id=seg.get(sg), product_name=name, network=net, tier=tier, annual_fee=annual,
                                   issuance_fee=issue, daily_atm_limit=atm, daily_pos_limit=pos,
                                   default_credit_limit=limit, reward_rate_pct=reward, status="active"))

    # Branches and staff --------------------------------------------------------------------
    branches, city_weights = [], []
    for city, (prov, code, weight, areas) in CITIES.items():
        hub = None
        for area in areas:
            bid = w.new_id("branch")
            branches.append(dict(id=bid, city=city, code=f"{bid:04d}", area=area))
            city_weights.append(weight / len(areas))
            w.row("branch", dict(branch_id=bid, manager_employee_id=None, parent_branch_id=hub, branch_code=f"{bid:04d}",
                                 branch_name=f"{area}, {city}", city=city, province=prov,
                                 address=f"Plot {rng.randint(1, 250)}, Main Boulevard, {area}, {city}",
                                 phone=f"{code}-{rng.randint(3_000_000, 3_999_999)}", opened_on=rand_date(rng, date(1995, 1, 1), date(2022, 12, 31)),
                                 is_islamic=rng.random() < 0.15, has_lockers=rng.random() < 0.5, status="active"))
            hub = hub or bid

    def employee(title, branch, manager):
        eid = w.new_id("employee")
        female = rng.random() < 0.35
        first, last = rng.choice(FEMALE if female else MALE), rng.choice(FAMILY)
        lo, hi = next((d[3], d[4]) for d in DESIGNATIONS if d[0] == title)
        w.row("employee", dict(employee_id=eid, branch_id=branch, designation_id=desig[title], manager_id=manager,
                               employee_no=f"E{eid:06d}", first_name=first, last_name=last,
                               cnic=cnic(2_000_000 + eid, rng, female), gender="F" if female else "M",
                               email=f"{first}.{last}.{eid}@nbms.com.pk".lower(), mobile=mobile(2_000_000 + eid, rng),
                               hire_date=rand_date(rng, date(2005, 1, 1), date(2025, 6, 30)),
                               monthly_salary=money(rng.randint(lo, hi) * 100), status="active"))
        return eid

    ceo = employee("Chief Executive Officer", None, None)
    head_office = [employee(t, None, ceo) for t in ["Compliance Officer", "Fraud & Risk Analyst", "Credit Analyst", "IT Support Officer"]]
    for b in branches:
        b["manager"] = employee("Branch Manager", b["id"], ceo)
        b["ops"] = employee("Operations Manager", b["id"], b["manager"])
        b["rm"] = employee("Relationship Manager", b["id"], b["manager"])
        b["teller"] = employee("Cash Officer (Teller)", b["id"], b["manager"])
        b["cso"] = employee("Customer Service Officer", b["id"], b["manager"])
        b["loan_officer"] = employee("Loan Officer", b["id"], b["manager"])

    # Customers, with everything that belongs to them ----------------------------------------
    seg_codes = [s[0] for s in SEGMENTS]
    seg_weights = [s[8] for s in SEGMENTS]
    adults = []          # ids of adult individual customers, for guardians and nominees
    merchants = []       # (merchant id, national, city, spend, category)
    merchant_owners = [] # (account id, branch) of business current accounts
    utility_ids = {}
    n_business_first = max(12, n_customers // 50)

    def add_account(c, b, code, opened, owner_name, balance_target):
        at_ = atype[code]
        aid = w.new_id("account")
        number = f"{b['code']}{aid:012d}"
        status, closed = "active", None
        roll = rng.random()
        if roll < 0.03 and opened < HISTORY_START - timedelta(days=60):
            status, closed = "closed", rand_date(rng, opened + timedelta(days=30), HISTORY_START - timedelta(days=1))
        elif roll < 0.07:
            status = "dormant"
        elif roll < 0.075:
            status = "frozen"
        ccy = rng.choice(["USD", "USD", "EUR", "GBP"]) if code == "FCY" else at_["ccy"]
        if at_["cap"]:
            balance_target = min(balance_target, at_["cap"] * 100 // 2)
        return dict(id=aid, customer=c["id"], branch=b, code=code, type_id=at_["id"], ccy=ccy, number=number,
                    iban=iban("NBMS", number), title=owner_name, opened=opened, closed=closed, status=status,
                    target=0 if status == "closed" else balance_target, rate=at_["rate"], card=None, raast=None,
                    opened_by=b["cso"] if rng.random() < 0.8 else None)

    for i in range(n_customers):
        if i < n_business_first:
            code = "CORP" if i % 4 == 0 else "SME"
        else:
            code = rng.choices(seg_codes, seg_weights)[0]
            if code in ("KIDS", "TEENS") and not adults:
                code = "RETAIL"
        business = code in ("CORP", "SME")
        b = branches[rng.choices(range(len(branches)), city_weights)[0]]
        cid = w.new_id("customer")
        female = rng.random() < 0.48
        if business:
            name = f"{rng.choice(FAMILY)} {rng.choice(BUSINESS_WORDS)}{' (Pvt) Ltd' if code == 'CORP' else ''}"
            dob = gender = person_cnic = None
            email = f"accounts.{cid}@{re.sub(r'[^a-z]', '', name.lower())[:20]}.com.pk"
        else:
            name = f"{rng.choice(FEMALE if female else MALE)} {rng.choice(FAMILY)}"
            age_lo, age_hi = {"KIDS": (2, 12), "TEENS": (13, 17), "STUDENT": (18, 25), "SENIOR": (60, 85)}.get(code, (22, 70))
            dob = rand_date(rng, date(TODAY.year - age_hi - 1, TODAY.month, TODAY.day) + timedelta(days=1),
                            date(TODAY.year - age_lo, TODAY.month, TODAY.day))
            gender = "F" if female else "M"
            person_cnic = cnic(cid, rng, female)
            email = None if code == "KIDS" or rng.random() < 0.3 else \
                f"{name.split()[0]}.{name.split()[1]}{cid}@{rng.choice(['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com'])}".lower()
        kyc_status = "pending" if rng.random() < 0.03 else "verified"
        c = dict(id=cid, code=code, business=business, branch=b)
        w.row("customer", dict(customer_id=cid, segment_id=seg[code], home_branch_id=b["id"],
                               relationship_manager_id=b["rm"] if code in ("PRIORITY", "PREMIER", "CORP", "SME") else None,
                               referred_by_customer_id=rng.randint(1, cid - 1) if cid > 1 and rng.random() < 0.1 else None,
                               onboarded_by_employee_id=b["cso"], cif_number=f"{cid:010d}", full_name=name,
                               cnic=person_cnic, date_of_birth=dob, gender=gender, mobile=mobile(cid, rng), email=email,
                               address=f"House {rng.randint(1, 900)}, Street {rng.randint(1, 40)}, {rng.choice(CITIES[b['city']][3])}, {b['city']}",
                               kyc_status=kyc_status, risk_rating=rng.choices(["low", "medium", "high"], [80, 16, 4])[0]))
        if not business and code not in ("KIDS", "TEENS"):
            adults.append(cid)

        # KYC documents: an identity document, plus proof of income or business registration.
        docs = []
        if business:
            docs = [("NTN certificate", f"{perm7(cid):07d}-{cid % 10}", "Federal Board of Revenue"),
                    ("SECP registration", f"SECP-{cid:08d}", "SECP")] if code == "CORP" else \
                   [("NTN certificate", f"{perm7(cid):07d}-{cid % 10}", "Federal Board of Revenue")]
        else:
            idtype = "B-Form" if code in ("KIDS", "TEENS") else "NICOP" if code == "RDA" else rng.choice(["CNIC", "SNIC"])
            docs.append((idtype, person_cnic, "NADRA"))
            if code == "RDA":
                docs.append(("Passport", f"A{perm7(cid):07d}", "Directorate General of Immigration & Passports"))
            elif code == "STUDENT":
                docs.append(("Student ID", f"STU-{cid:08d}", "University registrar"))
            elif code not in ("KIDS", "TEENS") and rng.random() < 0.4:
                docs.append(("Salary slip", f"SS-{cid:08d}", "Employer"))
        for dtype, number, authority in docs:
            issued = rand_date(rng, date(2016, 1, 1), date(2025, 6, 1))
            verified = kyc_status == "verified"
            w.row("kyc_document", dict(document_id=w.new_id("kyc_document"), customer_id=cid,
                                       verified_by_employee_id=b["ops"] if verified else None, document_type=dtype,
                                       document_number=number, issuing_authority=authority, issue_date=issued,
                                       expiry_date=add_months(issued, 120) if dtype in ("CNIC", "SNIC", "NICOP", "Passport") else None,
                                       file_reference=f"dms://kyc/{cid}/{dtype.lower().replace(' ', '-')}.pdf",
                                       verification_status="verified" if verified else "pending",
                                       verified_at=at(issued + timedelta(days=rng.randint(1, 30)), rng, (9, 17)) if verified else None,
                                       biometric_verified=verified and dtype in ("CNIC", "SNIC", "NICOP", "B-Form"), remarks=None))

        # Accounts: the product depends on the segment.
        income = {"PRIORITY": 450_000, "PREMIER": 1_200_000, "STUDENT": 0, "SENIOR": 60_000,
                  "RDA": 0, "KIDS": 0, "TEENS": 0}.get(code, 80_000)
        income = rupees(rng, income, 0.5) if income else 0
        plan = {"KIDS": ["KIDS"], "TEENS": ["TEENS"], "STUDENT": [rng.choice(["ASAAND", "SAV"])], "SENIOR": ["SENIOR"],
                "RDA": ["RDA"] + (["FCY"] if rng.random() < 0.4 else []), "CORP": ["CURR", "TD1Y"], "SME": ["CURR"],
                "PRIORITY": ["CURR", "SAV"] + (["FCY"] if rng.random() < 0.3 else []),
                "PREMIER": ["CURR", "SAV", "TD1Y"] + (["FCY"] if rng.random() < 0.5 else [])}.get(
            code, [rng.choices(["ASAAN", "ASAAND", "CURR", "SAV"], [30, 15, 35, 20])[0]] + (["SAV"] if rng.random() < 0.2 else []))
        plan = list(dict.fromkeys(plan))
        first_open = rand_date(rng, max(date(2008, 1, 1), (dob or date(2008, 1, 1)) + timedelta(days=30)), TODAY - timedelta(days=3))
        accts = []
        for k, acode in enumerate(plan):
            opened = first_open if k == 0 else rand_date(rng, first_open, TODAY - timedelta(days=1))
            target = rupees(rng, {"CORP": 8_000_000, "SME": 900_000, "PREMIER": 6_000_000, "PRIORITY": 1_500_000,
                                  "KIDS": 15_000, "TEENS": 25_000, "STUDENT": 20_000}.get(code, 120_000), 0.9)
            if acode == "FCY":
                target = rupees(rng, 3_000, 0.8)
            accts.append(add_account(c, b, acode, opened, name + (" (Minor)" if code in ("KIDS", "TEENS") else ""), target))
        live = [a for a in accts if a["status"] in ("active", "dormant") and a["ccy"] == "PKR" and a["code"] != "TD1Y"]
        if live and not business and code not in ("KIDS", "TEENS") and rng.random() < 0.5:
            live[0]["raast"] = None  # set below from the customer's own mobile
        if business and len(merchant_owners) < n_business_first and live:
            merchant_owners.append((live[0]["id"], b))

        # Holders: the owner, plus a guardian for minors and sometimes a nominee.
        for a in accts:
            w.row("account_holder", dict(holder_id=w.new_id("account_holder"), account_id=a["id"], customer_id=cid,
                                         added_by_employee_id=a["opened_by"], holder_role="primary", ownership_pct=100,
                                         signing_authority="singly", is_primary=True, relationship_to_owner=None,
                                         added_on=a["opened"], removed_on=None, mandate_reference=None,
                                         notify_by_sms=True, status="active"))
            other = rng.choice(adults) if adults else None
            if other and other != cid:
                if code in ("KIDS", "TEENS"):
                    role, rel, signing = "guardian", rng.choice(["Father", "Mother"]), "singly"
                elif rng.random() < 0.12:
                    role, rel, signing = "nominee", rng.choice(["Spouse", "Son", "Daughter", "Brother", "Sister"]), "none"
                else:
                    role = None
                if role:
                    w.row("account_holder", dict(holder_id=w.new_id("account_holder"), account_id=a["id"], customer_id=other,
                                                 added_by_employee_id=a["opened_by"], holder_role=role, ownership_pct=None,
                                                 signing_authority=signing, is_primary=False, relationship_to_owner=rel,
                                                 added_on=a["opened"], removed_on=None, mandate_reference=None,
                                                 notify_by_sms=True, status="active"))

        # Cards: member cards follow the account and segment.
        for a in accts:
            product = {"KIDS": "Kids Club card", "TEENS": "Teens Club debit card", "ASAAN": "Asaan Account debit card (PayPak)",
                       "ASAAND": "Raast-linked PayPak card"}.get(a["code"])
            if not product and a["code"] in ("CURR", "SAV", "SENIOR", "RDA"):
                product = {"PRIORITY": "Titanium debit card", "PREMIER": "Titanium debit card"}.get(code, "Classic debit card")
            if not product or a["status"] == "closed" or rng.random() > 0.85:
                continue
            cards = [product] + ([{"PREMIER": "World Mastercard (credit)", "PRIORITY": "Platinum Visa (credit)"}.get(code, "Gold credit card")]
                                 if a["code"] == "CURR" and not business and (code in ("PRIORITY", "PREMIER") or rng.random() < 0.08) else [])
            for pname in cards:
                p = cprod[pname]
                issued = rand_date(rng, max(a["opened"], date(2020, 1, 1)), TODAY - timedelta(days=1))
                expiry = add_months(issued, 12 * p["years"])
                status = "expired" if expiry <= TODAY else rng.choices(["active", "inactive", "blocked", "hot_listed"], [92, 4, 3, 1])[0]
                if a["status"] in ("frozen", "dormant") and status == "active":
                    status = "blocked"
                card_id = w.new_id("card")
                limit = p["limit"]
                w.row("card", dict(card_id=card_id, account_id=a["id"], card_product_id=p["id"], customer_id=cid,
                                   issuing_branch_id=b["id"], masked_pan=f"{BIN[p['network']]} **** **** {rng.randint(0, 9999):04d}",
                                   name_on_card=name.upper()[:26], issued_on=issued, expiry_date=expiry,
                                   credit_limit=limit, outstanding_balance=money(rng.randint(0, limit * 60)) if limit else 0,
                                   pin_status="not_set" if status == "inactive" else "set",
                                   contactless_enabled=True, ecommerce_enabled=rng.random() < 0.6, status=status))
                if status == "active" and not limit and a["card"] is None:
                    a["card"] = card_id

        # Merchants are created once enough business accounts exist to settle into.
        if i == n_business_first - 1:
            for mname, cat, mcc, spend, national in MERCHANTS + [(u, "Utilities", "4900", 0, True) for u in UTILITIES]:
                for city in ([None] if national else CITIES):
                    acc, ob = rng.choice(merchant_owners)
                    mid = w.new_id("merchant")
                    w.row("merchant", dict(merchant_id=mid, settlement_account_id=acc, acquiring_branch_id=ob["id"],
                                           merchant_code=f"MID{mid:012d}", merchant_name=f"{mname}{f' – {city}' if city else ''}",
                                           mcc=mcc, category=cat, city=city or "Online", terminal_count=0 if national else rng.randint(1, 15),
                                           accepts_raast_qr=rng.random() < 0.6, mdr_pct=0 if cat == "Utilities" else round(rng.uniform(0.5, 2.5), 2),
                                           onboarded_on=rand_date(rng, date(2015, 1, 1), date(2025, 1, 1)), status="active"))
                    if cat == "Utilities":
                        utility_ids[mname] = mid
                    else:
                        merchants.append((mid, national, city, spend, cat, mname))

        # Saved payees.
        payees = []
        if not business and code not in ("KIDS", "TEENS") and rng.random() < 0.6:
            for nick in rng.sample(NICKNAMES, rng.randint(1, 3)):
                kind = rng.choices(["raast", "ibft"], [60, 40])[0]
                bank = rng.choice(OTHER_BANKS)
                bid_ = w.new_id("beneficiary")
                added = rand_date(rng, date(2022, 1, 1), TODAY - timedelta(days=2))
                w.row("beneficiary", dict(beneficiary_id=bid_, customer_id=cid, beneficiary_account_id=None, nickname=nick,
                                          beneficiary_name=f"{rng.choice(MALE + FEMALE)} {rng.choice(FAMILY)}",
                                          bank_name=BANK_NAMES[bank],
                                          iban=iban(bank, f"{rng.randint(0, 10**16 - 1):016d}") if kind == "ibft" else None,
                                          raast_id=mobile(3_000_000 + bid_, rng) if kind == "raast" else None,
                                          beneficiary_type=kind, mobile=None, added_on=added,
                                          cooling_off_until=datetime(added.year, added.month, added.day, 23, 59) + timedelta(days=1),
                                          status="active"))
                payees.append((bid_, kind, nick))

        # A loan for some customers with a regular income.
        loan = None
        pkr_live = [a for a in live if a["status"] == "active"]
        if pkr_live and (income and rng.random() < 0.09 or business and rng.random() < 0.25):
            pcode = "SMERF" if business else rng.choices(["PF", "CAR", "IJARA", "SOLAR", "GOLD", "MPMG", "HOME"],
                                                         [40, 20, 8, 10, 10, 7, 5])[0]
            lid_, lo, hi, max_tenure, rate = lprod[pcode]
            tenure = min(max_tenure, rng.choice([12, 24, 36, 48, 60, 84, 120, 180, 240]))
            r = rate / 1200
            cap = income * 0.4 * (1 - (1 + r) ** -tenure) / r / 100 if income else hi
            principal = max(lo, min(hi, int(min(cap, rng.uniform(lo, hi))) // 1000 * 1000))
            emi = int(principal * r / (1 - (1 + r) ** -tenure) * 100)
            status = rng.choices(["active", "overdue", "closed", "written_off", "applied", "rejected"], [72, 8, 12, 2, 3, 3])[0]
            if status in ("applied", "rejected"):
                disbursed = maturity = None
                outstanding, dpd = principal * 100, 0
            else:
                if status == "closed":
                    tenure = min(tenure, 24)
                    disbursed = rand_date(rng, TODAY - timedelta(days=30 * tenure + 700), TODAY - timedelta(days=30 * tenure + 5))
                else:
                    disbursed = rand_date(rng, max(pkr_live[0]["opened"], TODAY - timedelta(days=30 * tenure - 40)), TODAY - timedelta(days=5))
                maturity = add_months(disbursed, tenure)
                paid = 0 if status == "closed" else max(0, ((TODAY - disbursed).days // 30))
                bal = principal
                for _ in range(min(paid, tenure)):
                    bal -= emi / 100 - bal * r
                outstanding = 0 if status == "closed" else max(0, int(bal * 100))
                dpd = {"overdue": rng.randint(30, 180), "written_off": rng.randint(181, 720)}.get(status, 0)
            lid = w.new_id("loan")
            w.row("loan", dict(loan_id=lid, customer_id=cid, loan_product_id=lid_, branch_id=b["id"],
                               loan_officer_id=b["loan_officer"],
                               approved_by_employee_id=None if status in ("applied", "rejected") else b["manager"],
                               disbursement_account_id=pkr_live[0]["id"], loan_number=f"LN-{(disbursed or TODAY).year % 100:02d}-{lid:07d}",
                               principal_amount=principal, interest_rate_pct=round(rate, 2), tenure_months=tenure,
                               emi_amount=money(emi), disbursed_on=disbursed, maturity_date=maturity,
                               outstanding_principal=money(outstanding), days_past_due=dpd, status=status))
            if status in ("active", "overdue"):
                loan = dict(id=lid, account=pkr_live[0]["id"], emi=emi, day=disbursed.day, disbursed=disbursed,
                            principal=principal * 100, missed=dpd // 30)

        # Raast alias: the customer's own mobile, on one account.
        if live and not business and code not in ("KIDS", "TEENS") and rng.random() < 0.5:
            live[0]["raast"] = mobile(cid, rng)  # same digits as the customer's mobile number
        # Transactions for the last month, then the account rows with the resulting balance.
        for a in accts:
            post_transactions(w, rng, a, c, b, income, payees, merchants, utility_ids, loan, chan, ttype, cur)
            hold = 0 if rng.random() < 0.95 else int(a["balance"] * rng.uniform(0, 0.05))
            w.row("account", dict(account_id=a["id"], customer_id=cid, account_type_id=a["type_id"], branch_id=b["id"],
                                  currency_id=cur[a["ccy"]], opened_by_employee_id=a["opened_by"], account_number=a["number"],
                                  iban=a["iban"], account_title=a["title"], opened_on=a["opened"], closed_on=a["closed"],
                                  ledger_balance=money(a["balance"]), available_balance=money(a["balance"] - hold),
                                  raast_alias=a["raast"], is_dormant=a["status"] == "dormant", status=a["status"]))

    header = (f"-- Banking Management System: generated data for {n_customers:,} customers (seed {seed}).\n"
              f"-- Load after banking_schema.sql:  psql -d bank -v ON_ERROR_STOP=1 -f {out.name}\n"
              f"-- Rows: " + ", ".join(f"{t} {w.count[t]:,}" for t in LOAD_ORDER))
    w.finish(out, header)
    return w.count


def post_transactions(w, rng, a, c, b, income, payees, merchants, utility_ids, loan, chan, ttype, cur):
    """Build a month of activity for one account, replay it in time order, and record the balance."""
    a["balance"] = 0
    if a["status"] in ("closed", "frozen"):
        a["balance"] = a["target"]
        return
    start = max(HISTORY_START, a["opened"])
    if start > TODAY:
        a["balance"] = a["target"]
        return
    ev = []  # (time, type, channel, amount paisa, card, merchant, beneficiary, loan, teller, narration)

    def add(day, ttype_, channel, amount, narration, card=None, merchant=None, payee=None, loan_id=None, teller=None, hours=(8, 23)):
        if start <= day <= TODAY:
            ev.append((at(day, rng, hours), ttype_, channel, amount, card, merchant, payee, loan_id, teller, narration))

    def day_in_window():
        return rand_date(rng, start, TODAY)

    if a["opened"] >= HISTORY_START:
        add(a["opened"], "CDEP", "BRANCH", rupees(rng, 20_000), "Opening deposit", teller=b["teller"], hours=(10, 16))
    active = a["status"] == "active"
    pkr = a["ccy"] == "PKR"
    if active and a["code"] in ("CURR", "SAV", "ASAAN", "ASAAND", "SENIOR", "RDA") and pkr:
        if income and not c["business"]:
            add(date(2025, 10, 1), "SALARY", "IBFT", income, "Salary credit", hours=(6, 10))
        if a["code"] == "RDA":
            add(day_in_window(), "TRFIN", "IBFT", rupees(rng, 150_000), "Home remittance via Roshan Digital Account")
        if not income and not c["business"]:
            add(day_in_window(), "CDEP", "AGENT" if rng.random() < 0.3 else "BRANCH", rupees(rng, 15_000), "Cash deposit", teller=b["teller"])
        scale = max(income, 4_000_000) / 8_000_000
        if a["card"] and merchants:  # the first business customers are created before any merchant exists
            local = [m for m in merchants if not m[1] and m[2] == b["city"]] or merchants
            for _ in range(rng.randint(2, 7)):
                m = rng.choice(local)
                add(day_in_window(), "POS", "POS", rupees(rng, m[3] * scale), f"POS purchase at {m[5]}",
                    card=a["card"], merchant=m[0])
            for _ in range(rng.randint(0, 2)):
                m = rng.choice([m for m in merchants if m[1] and m[3] < 20_000])
                add(day_in_window(), "ONLINE", "ECOM", rupees(rng, m[3]), f"Online purchase at {m[5]}", card=a["card"], merchant=m[0])
            for _ in range(rng.randint(0, 2)):
                add(day_in_window(), "CWDL", "ATM", rng.choice([5_000, 10_000, 15_000, 20_000, 25_000]) * 100, "ATM cash withdrawal", card=a["card"])
        elif rng.random() < 0.5:
            add(day_in_window(), "CWDL", "BRANCH", rng.choice([10_000, 20_000, 50_000]) * 100, "Cash withdrawal", teller=b["teller"], hours=(10, 16))
        if utility_ids:
            gas = "SSGC" if b["city"] in ("Karachi", "Hyderabad", "Quetta") else "SNGPL"
            for util in rng.sample([DISCO[b["city"]], gas, "PTCL", "Jazz"], rng.randint(1, 3)):
                add(day_in_window(), "BILL", rng.choice(["APP", "WEB"]), rupees(rng, 4_000 if util in DISCO.values() else 2_000),
                    f"{util} bill payment", merchant=utility_ids[util])
        for pid, kind, nick in rng.sample(payees, min(len(payees), rng.randint(0, 2))):
            add(day_in_window(), "RAAST" if kind == "raast" else "IBFT", "RAAST" if kind == "raast" else "IBFT",
                rupees(rng, 12_000), f"Transfer to {nick}", payee=pid)
        if c["business"]:
            for _ in range(rng.randint(3, 8)):
                add(day_in_window(), rng.choice(["TRFIN", "CHQ", "CDEP"]), "BRANCH", rupees(rng, 250_000),
                    "Customer payment received", teller=b["teller"], hours=(10, 16))
            for _ in range(rng.randint(1, 4)):
                add(day_in_window(), "IBFT", "WEB", rupees(rng, 180_000), "Supplier payment")
        if loan and loan["account"] == a["id"]:
            due = date(2025, 10, min(loan["day"], 28))
            if loan["disbursed"] >= HISTORY_START:
                add(loan["disbursed"], "LOAND", "BRANCH", loan["principal"], "Loan disbursement", loan_id=loan["id"], hours=(11, 15))
            elif loan["missed"] == 0:
                add(due if due >= start else start, "LOANI", "BRANCH", loan["emi"], "Loan instalment", loan_id=loan["id"], hours=(1, 3))
        add(date(2025, 9, 30), "SVC", "BRANCH", 20_000 if a["code"] == "CURR" else 0, "SMS alert charges", hours=(23, 23))
    elif active and a["code"] == "FCY" and rng.random() < 0.5:
        add(day_in_window(), "TRFIN", "IBFT", rupees(rng, 800), "Foreign remittance received")
    if a["code"] in ("SAV", "SENIOR", "KIDS", "TEENS", "TD1Y") and a["status"] in ("active", "dormant"):
        profit = int(a["target"] * a["rate"] / 1200)
        add(date(2025, 9, 30), "PROFIT", "BRANCH", profit, "Monthly profit", hours=(23, 23))
    if a["code"] in ("KIDS", "TEENS") and active:
        add(day_in_window(), "CDEP", "BRANCH", rupees(rng, 3_000), "Pocket money deposit by guardian", teller=b["teller"], hours=(10, 16))
        if a["card"] and merchants:
            for _ in range(rng.randint(0, 3)):
                m = rng.choice(merchants)
                add(day_in_window(), "POS", "POS", rupees(rng, 600), f"POS purchase at {m[5]}", card=a["card"], merchant=m[0])

    ev = [e for e in ev if e[3] > 0]
    ev.sort(key=lambda e: e[0])
    # Replay in time order. A small share of digital payments fail or get reversed (no effect on
    # the balance); everything else posts. The opening balance is chosen so the month ends near the
    # account's target balance without ever going below zero.
    effects, statuses = [], []
    for e in ev:
        sign = 1 if TXN_TYPES[e[1]][1] == "credit" else -1
        tax = e[3] * 15 // 100 if e[1] == "PROFIT" else 0
        status = "posted"
        if e[2] in ("APP", "WEB", "RAAST", "IBFT", "ECOM", "ATM") and sign < 0 and rng.random() < 0.01:
            status = "failed"
        elif e[1] in ("POS", "ONLINE") and rng.random() < 0.004:
            status = "reversed"
        statuses.append(status)
        effects.append(sign * e[3] - tax if status == "posted" else 0)
    running, lowest = 0, 0
    for effect in effects:
        running += effect
        lowest = min(lowest, running)
    balance = max(a["target"] - running, -lowest)
    for e, effect, status in zip(ev, effects, statuses):
        tid = w.new_id("bank_transaction")
        if status == "posted":
            balance += effect
        tax = e[3] * 15 // 100 if e[1] == "PROFIT" else 0
        w.row("bank_transaction", dict(
            transaction_id=tid, account_id=a["id"], txn_type_id=ttype[e[1]], channel_id=chan[e[2]], currency_id=cur[a["ccy"]],
            card_id=e[4], merchant_id=e[5], beneficiary_id=e[6], counterparty_account_id=None, loan_id=e[7],
            processed_by_employee_id=e[8], reference_no=f"NB{e[0]:%Y%m%d}{tid:010d}",
            rrn=f"{(tid * 982_451_653) % 10**12:012d}" if e[2] in ("POS", "ATM", "RAAST", "IBFT", "ECOM") else None,
            amount=money(e[3]), fee_amount=0, tax_amount=money(tax), balance_after=money(balance),
            txn_datetime=e[0], value_date=e[0].date(), narration=e[9][:140], status=status))
    a["balance"] = balance


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customers", type=int, default=1000, help="number of customers (default 1,000)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=HERE / "bank_data.sql")
    args = ap.parse_args()
    counts = generate(args.customers, args.seed, args.out)
    print(f"wrote {args.out}")
    for t in LOAD_ORDER:
        print(f"  {t:<18} {counts[t]:>10,}")


if __name__ == "__main__":
    main()
