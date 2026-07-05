"""DB delivery status for every MD ref in the multi-PO candidate set (for draft-PO handling).
-> multipo_db_delivery.csv (ref, delivered yes/no)."""
import json, csv, os, psycopg2, re
from collections import defaultdict

csv.field_size_limit(10000000)
PROJ = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend"
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()

refs = set()
for c in csv.DictReader(open(os.path.join(INV, "multipo_candidates.csv"), encoding="utf-8-sig")):
    for t in c["md_refs"].split(";"):
        t = t.strip()
        if t:
            refs.add(t)
refs = sorted(refs)
print("refs to look up:", len(refs))

d = json.load(open(os.path.join(PROJ, ".env.json")))
conn = psycopg2.connect(dbname=d["db_name"], user=d["db_user"], password=d["db_password"],
                        host=d["db_host"], port=5432, connect_timeout=15,
                        options="-c statement_timeout=180000")
cur = conn.cursor()
cur.execute("""SELECT po_number, po_delivery_status, is_deleted, is_revised
               FROM po WHERE po_number = ANY(%s)""", (refs,))
db = defaultdict(list)
for pn, status, deleted, revised in cur.fetchall():
    db[pn].append({"status": status, "deleted": deleted, "revised": revised})
cur.close(); conn.close()

DELIVERED = {"delivered", "in_store"}
out = []
for ref in refs:
    recs = db.get(ref)
    delivered = "no"
    st = "NOT IN DB"
    if recs:
        act = [r for r in recs if not r["deleted"] and not r["revised"]] or [r for r in recs if not r["deleted"]] or recs
        st = act[0]["status"] or "(blank)"
        delivered = "yes" if (act[0]["status"] in DELIVERED and not act[0]["deleted"]) else "no"
    out.append({"ref": ref, "db_status": st, "delivered": delivered})

with open(os.path.join(INV, "multipo_db_delivery.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["ref", "db_status", "delivered"]); w.writeheader(); w.writerows(out)
from collections import Counter
print("delivered:", dict(Counter(r["delivered"] for r in out)))
print("-> multipo_db_delivery.csv")
