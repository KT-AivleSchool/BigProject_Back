import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
DSN = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DSN)
cur = conn.cursor()
cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
)
for row in cur.fetchall():
    print(row[0])
