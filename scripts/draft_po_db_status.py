"""Step 1: for the draft-PO bucket bills, look up the DB PO's delivery status
(active row: is_deleted=false AND is_revised=false). Report distribution and write
draft_po_db_status.csv (bill, MD ref, DB delivery status, is_deleted/revised flags)."""
import json, csv, os, psycopg2
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
PROJ = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend"
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
RECON = os.path.join(INV, "bill_po_reconciliation.csv")
OUT = os.path.join(INV, "draft_po_db_status.csv")

rows = list(csv.DictReader(open(RECON, encoding="utf-8-sig")))
draft = [r for r in rows if r["Attach Status"] == "Not Attached" and r["Reason"] == "draft PO"]
refs = sorted({r["MD Reference(s)"].strip() for r in draft if r["MD Reference(s)"].strip()})
print("draft-PO bills:", len(draft), "| unique MD refs:", len(refs))

d = json.load(open(os.path.join(PROJ, ".env.json")))
conn = psycopg2.connect(dbname=d["db_name"], user=d["db_user"], password=d["db_password"],
                        host=d["db_host"], port=5432, connect_timeout=15,
                        options="-c statement_timeout=180000")
cur = conn.cursor()
cur.execute("""
    SELECT po_number, po_delivery_status, is_deleted, is_revised, is_decline, id
    FROM po WHERE po_number = ANY(%s)
""", (refs,))
db = defaultdict(list)
for pn, status, deleted, revised, decline, pid in cur.fetchall():
    db[pn].append({"status": status, "deleted": deleted, "revised": revised, "decline": decline, "id": pid})
cur.close(); conn.close()


def active(ref):
    recs = db.get(ref)
    if not recs:
        return None
    act = [r for r in recs if not r["deleted"] and not r["revised"]]
    return (act or [r for r in recs if not r["deleted"]] or recs)[0]


DELIVERED = {"delivered", "in_store"}
dist = Counter()
out = []
for r in draft:
    ref = r["MD Reference(s)"].strip()
    a = active(ref)
    if a is None:
        s, delivered = "NOT IN DB", "no"
    else:
        s = (a["status"] or "(blank)") + (" [deleted]" if a["deleted"] else "") + (" [declined]" if a["decline"] else "")
        delivered = "yes" if (a["status"] in DELIVERED and not a["deleted"]) else "no"
    dist[s] += 1
    out.append({"Bill Number": r["Bill Number"], "MD Reference": ref, "PO Number": r["PO Numbers"],
                "Bill Amount": r["Bill Amount"], "PO Amount": r["PO Amount"],
                "DB PO Delivery Status": s, "Delivered": delivered, "PO ID": a["id"] if a else ""})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["Bill Number", "MD Reference", "PO Number", "Bill Amount",
                                       "PO Amount", "DB PO Delivery Status", "Delivered", "PO ID"])
    w.writeheader(); w.writerows(out)

print("\nDB delivery status distribution (draft-PO bucket):")
for k, v in dist.most_common():
    print(f"  {v:5}  {k}")
print("\nDelivered (delivered/in_store, active):", sum(1 for r in out if r["Delivered"] == "yes"))
print("-> wrote", OUT)
