import sqlite3


class Runner:
    # the ONLY project method named "execute" — the collision that used to
    # swallow cursor.execute via unique-name fuzzy binding
    def execute(self):
        return 1


def query(db_path, user_id):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = " + user_id)
