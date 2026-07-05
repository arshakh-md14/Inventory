import json, csv, os, psycopg2
from collections import defaultdict

csv.field_size_limit(10000000)
PROJ = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend"
SHEET = r"C:\Users\Jogesh Behera\Code file\Inventory\bills_no_matching_po.csv"

# ---- collect unique MD refs from the sheet ----
rows = list(csv.DictReader(open(SHEET, encoding="utf-8-sig")))
refs = set()
for r in rows:
    for tok in r["MD Reference(s)"].split(";"):
        tok = tok.strip()
        if tok:
            refs.add(tok)
refs = sorted(refs)
print("unique MD refs to look up:", len(refs))

# ---- query DB (read-only) ----
d = json.load(open(os.path.join(PROJ, ".env.json")))
conn = psycopg2.connect(dbname=d["db_name"], user=d["db_user"], password=d["db_password"],
                        host=d["db_host"], port=5432, connect_timeout=15,
                        options="-c statement_timeout=120000")
cur = conn.cursor()

# PO rows
cur.execute("""
    SELECT po_number, po_delivery_status, is_deleted, is_decline, is_revised
    FROM po WHERE po_number = ANY(%s)
""", (refs,))
po_rows = defaultdict(list)
for pn, status, deleted, decline, revised in cur.fetchall():
    po_rows[pn].append({"status": status, "deleted": deleted,
                        "decline": decline, "revised": revised})

# POs that DO have a Zoho PO record created (purchase_order_id present)
cur.execute("""
    SELECT DISTINCT p.po_number
    FROM po p
    JOIN po_zoho_purchase_order z ON z.po_id = p.id
    WHERE p.po_number = ANY(%s)
      AND z.is_deleted = false
      AND z.purchase_order_id IS NOT NULL
      AND z.purchase_order_id <> ''
""", (refs,))
zoho_created = {row[0] for row in cur.fetchall()}
cur.close(); conn.close()


def status_for(ref):
    recs = po_rows.get(ref)
    if not recs:
        return "NOT IN DB"
    # prefer active (not deleted, not revised) record
    active = [r for r in recs if not r["deleted"] and not r["revised"]] or \
             [r for r in recs if not r["deleted"]] or recs
    r = active[0]
    s = r["status"] or "(blank)"
    if r["deleted"]:
        s += " [deleted]"
    if r["decline"]:
        s += " [declined]"
    return s


def zoho_for(ref):
    if ref not in po_rows:
        return "PO not in DB"
    return "Yes" if ref in zoho_created else "No (not created in Zoho)"


def deleted_for(ref):
    recs = po_rows.get(ref)
    if not recs:
        return ""
    return "Yes" if any(r["deleted"] for r in recs) else "No"


# ---- add columns to the sheet ----
for r in rows:
    rrefs = [t.strip() for t in r["MD Reference(s)"].split(";") if t.strip()]
    r["PO DB Status"] = "; ".join(status_for(x) for x in rrefs)
    r["Is Deleted"] = "; ".join(deleted_for(x) for x in rrefs)
    r["Zoho PO Created"] = "; ".join(zoho_for(x) for x in rrefs)

fields = list(rows[0].keys())
with open(SHEET, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

# ---- summary ----
from collections import Counter
notin = sum(1 for r in rows if "NOT IN DB" in r["PO DB Status"])
nozoho = sum(1 for r in rows if "No (not created" in r["Zoho PO Created"])
deleted = sum(1 for r in rows if "Yes" in r["Is Deleted"])
print("rows updated:", len(rows))
print("bills whose PO is NOT in DB:", notin)
print("bills whose PO exists in DB but NOT created in Zoho:", nozoho)
print("bills with at least one PO is_deleted=True:", deleted)
print("PO status distribution (single-ref bills):")
c = Counter(r["PO DB Status"] for r in rows if ";" not in r["MD Reference(s)"])
for k, v in c.most_common(15):
    print(f"  {k}: {v}")
