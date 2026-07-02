"""Data access; the SQL execute here is a sink but is not reachable from /reports."""

import sqlite3


def find_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()
