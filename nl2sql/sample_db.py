"""Build a small demo store database: customers, products, orders, order_items."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL  -- ISO date
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,  -- ISO date
    status TEXT NOT NULL CHECK (status IN ('placed', 'shipped', 'cancelled'))
);
CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
"""

CUSTOMERS = [
    (1, "Ayesha Khan", "Pakistan", "2025-01-14"),
    (2, "Lucas Martin", "France", "2025-02-03"),
    (3, "Mei Chen", "Singapore", "2025-03-22"),
    (4, "Omar Haddad", "UAE", "2025-05-09"),
    (5, "Sara Lopez", "Spain", "2025-07-30"),
]
PRODUCTS = [
    (1, "Mechanical Keyboard", "Electronics", 8900),
    (2, "USB-C Hub", "Electronics", 3500),
    (3, "Desk Lamp", "Home", 2400),
    (4, "Notebook (A5)", "Stationery", 600),
    (5, "Ergonomic Chair", "Furniture", 24900),
]
ORDERS = [
    (1, 1, "2025-08-01", "shipped"),
    (2, 2, "2025-08-03", "shipped"),
    (3, 1, "2025-09-12", "placed"),
    (4, 3, "2025-09-15", "cancelled"),
    (5, 4, "2025-10-02", "shipped"),
    (6, 5, "2025-10-05", "placed"),
]
ORDER_ITEMS = [
    (1, 1, 1), (1, 4, 3),
    (2, 5, 1),
    (3, 2, 2), (3, 3, 1),
    (4, 1, 1),
    (5, 4, 10), (5, 3, 2),
    (6, 2, 1),
]


def build(path: str | Path) -> Path:
    path = Path(path)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", CUSTOMERS)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", ORDERS)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?)", ORDER_ITEMS)
    conn.close()
    return path


if __name__ == "__main__":
    print(f"wrote {build('sample.db')}")
