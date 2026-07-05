"""Filter the draft-PO bucket to DELIVERED (DB) + INVENTORY-only item type.
Resolve Zoho bill_id/po_id and the DB amount (total_po_raised_amount) for the PO=DB target.
-> draft_inv_delivered_candidates.csv"""
import json, csv, os, glob, re, psycopg2
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
PROJ = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend"
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
OUT = os.path.join(INV, "draft_inv_delivered_candidates.csv")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def load(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig"))) if os.path.exists(f) else []


# delivered flag from step-1 output
delivered = {r["Bill Number"]: r for r in load(os.path.join(INV, "draft_po_db_status.csv")) if r["Delivered"] == "yes"}

# draft bucket rows, inventory-only + delivered
recon = load(os.path.join(INV, "bill_po_reconciliation.csv"))
picked = []
for r in recon:
    if not (r["Attach Status"] == "Not Attached" and r["Reason"] == "draft PO"):
        continue
    if r["Bill Number"] not in delivered:
        continue
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    if not toks or not (toks <= INVSET):
        continue
    picked.append(r)
print("delivered + inventory-only draft-PO bills:", len(picked))

refs = sorted({r["MD Reference(s)"].strip() for r in picked})
# DB amount (active row)
d = json.load(open(os.path.join(PROJ, ".env.json")))
conn = psycopg2.connect(dbname=d["db_name"], user=d["db_user"], password=d["db_password"],
                        host=d["db_host"], port=5432, connect_timeout=15,
                        options="-c statement_timeout=180000")
cur = conn.cursor()
cur.execute("""SELECT po_number, total_po_raised_amount, is_deleted, is_revised
               FROM po WHERE po_number = ANY(%s)""", (refs,))
dbamt = defaultdict(list)
for pn, amt, deleted, revised in cur.fetchall():
    dbamt[pn].append({"amt": amt, "deleted": deleted, "revised": revised})
cur.close(); conn.close()


def db_amount(ref):
    recs = dbamt.get(ref)
    if not recs:
        return None
    act = [r for r in recs if not r["deleted"] and not r["revised"]] or [r for r in recs if not r["deleted"]] or recs
    return act[0]["amt"]


# Zoho ids
poid = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            poid.setdefault(ref, row["Purchase Order ID"])
billid = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        for t in row["PurchaseOrder"].split(","):
            n = norm(t)
            if n.startswith("MD"):
                billid.setdefault((row["Bill Number"], n), row["Bill ID"])

out, miss = [], 0
for r in picked:
    ref = r["MD Reference(s)"].strip()
    bid = billid.get((r["Bill Number"], norm(ref)))
    pid = poid.get(norm(ref))
    amt = db_amount(ref)
    if not bid or not pid or amt is None:
        miss += 1
        continue
    out.append({"bill_id": bid, "po_id": pid, "md_ref": ref, "bill_number": r["Bill Number"],
                "po_number": r["PO Numbers"], "db_amount": round(float(amt), 2),
                "bill_amount": r["Bill Amount"], "n_lines": r["Bill Item Count"],
                "item_types": r["Item Type(s) (from PO)"]})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                       "db_amount", "bill_amount", "n_lines", "item_types"])
    w.writeheader(); w.writerows(out)
print("candidates written:", len(out), "| missing id/amt:", miss, "->", OUT)
print("line-count dist:", dict(Counter(r["n_lines"] for r in out)))
