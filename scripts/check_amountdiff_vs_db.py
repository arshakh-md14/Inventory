"""For the 'amount diff > 10' not-attached bills, compare the Zoho PO Amount against
the DB PO amount (po.total_po_raised_amount) of the ACTIVE record
(is_deleted=false AND is_revised=false). Output -> amount_diff_po_vs_db.csv."""
import json, csv, os, psycopg2
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
PROJ = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend"
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
RECON = os.path.join(INV, "bill_po_reconciliation.csv")
OUT = os.path.join(INV, "amount_diff_po_vs_db.csv")
TOL = 1.0  # rupees; within this = "equal"


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return None


rows = list(csv.DictReader(open(RECON, encoding="utf-8-sig")))
target = [r for r in rows if r["Attach Status"] == "Not Attached" and r["Reason"].startswith("amount diff")]
refs = sorted({r["MD Reference(s)"].strip() for r in target if r["MD Reference(s)"].strip()})
print("amount-diff bills:", len(target), "| unique MD refs:", len(refs))

d = json.load(open(os.path.join(PROJ, ".env.json")))
conn = psycopg2.connect(dbname=d["db_name"], user=d["db_user"], password=d["db_password"],
                        host=d["db_host"], port=5432, connect_timeout=15,
                        options="-c statement_timeout=180000")
cur = conn.cursor()
# ALL po records for these refs (so we can distinguish active vs revised/deleted)
cur.execute("""
    SELECT po_number, total_po_raised_amount, is_deleted, is_revised
    FROM po WHERE po_number = ANY(%s)
""", (refs,))
db = defaultdict(list)
for pn, amt, deleted, revised in cur.fetchall():
    db[pn].append({"amt": num(amt), "deleted": deleted, "revised": revised})
cur.close(); conn.close()


def db_active_amount(ref):
    recs = db.get(ref)
    if not recs:
        return None, "no PO in DB"
    active = [r for r in recs if not r["deleted"] and not r["revised"]]
    if not active:
        return None, "no active PO (all deleted/revised)"
    amts = sorted({r["amt"] for r in active})
    if len(amts) > 1:
        return amts, "multiple active PO amounts"
    return amts[0], "active"


verdicts = Counter()
out_rows = []
for r in target:
    ref = r["MD Reference(s)"].strip()
    po_amt = num(r["PO Amount"])
    bill_amt = num(r["Bill Amount"])
    db_amt, note = db_active_amount(ref)
    if isinstance(db_amt, list):
        verdict = "DB multiple active"
        db_disp = ";".join(str(x) for x in db_amt)
        diff = ""
    elif db_amt is None:
        verdict = note
        db_disp = ""
        diff = ""
    else:
        db_disp = db_amt
        diff = round(po_amt - db_amt, 2)
        if abs(diff) <= TOL:
            verdict = "PO = DB"
        else:
            verdict = "PO != DB"
    # does the BILL match DB? (bonus signal)
    bill_vs_db = ""
    if isinstance(db_amt, (int, float)):
        bill_vs_db = "Bill = DB" if abs(bill_amt - db_amt) <= TOL else "Bill != DB"
    verdicts[verdict] += 1
    out_rows.append({"Bill Number": r["Bill Number"], "MD Reference": ref, "PO Number": r["PO Numbers"],
                     "Bill Amount": bill_amt, "Zoho PO Amount": po_amt, "DB PO Amount": db_disp,
                     "PO vs DB": verdict, "PO-DB Diff": diff, "Bill vs DB": bill_vs_db,
                     "Reason": r["Reason"]})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["Bill Number", "MD Reference", "PO Number", "Bill Amount",
                                       "Zoho PO Amount", "DB PO Amount", "PO vs DB", "PO-DB Diff",
                                       "Bill vs DB", "Reason"])
    w.writeheader(); w.writerows(out_rows)

print("\nPO (Zoho) vs DB verdict:")
for k, v in verdicts.most_common():
    print(f"  {v:6}  {k}")
# cross-tab: for PO=DB and PO!=DB, how many bills also match DB?
print("\nBill vs DB (where DB amount known):")
for k, v in Counter(r["Bill vs DB"] for r in out_rows if r["Bill vs DB"]).most_common():
    print(f"  {v:6}  {k}")
print("\nCross: PO!=DB but Bill=DB (Zoho PO is the stale/wrong one):",
      sum(1 for r in out_rows if r["PO vs DB"] == "PO != DB" and r["Bill vs DB"] == "Bill = DB"))
print("Cross: PO=DB (so the >10 gap is a genuine bill-vs-PO mismatch):",
      sum(1 for r in out_rows if r["PO vs DB"] == "PO = DB"))
print("\n-> wrote", OUT)
