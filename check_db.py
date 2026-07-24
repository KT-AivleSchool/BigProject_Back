import os
import psycopg2

DSN = os.getenv(
    "DATABASE_URL",
    "host=localhost port=5432 dbname=omnisite user=postgres password=본인비번",
)
try:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    tables = ["parks", "cctv_locations", "national_properties"]
    for t in tables:
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"{t}: {cur.fetchone()[0]}")
    cur.close()
    conn.close()
except Exception as e:
    print("DB connection error:", e)
