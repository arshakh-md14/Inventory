import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
SHEET = r"C:\Users\Jogesh Behera\Code file\Inventory\bills_no_matching_po.csv"
mdpat = re.compile(r'^MD', re.I)
NOT_MATCHED = {"Draft", "Issued"}


def q(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def norm(t):
    return re.sub(r'[^0-9A-Za-z]', '', t).upper()


def parse_refs(field):
    return [n for n in (norm(t) for t in field.split(',')) if n.startswith('MD')]


# ---- POs: ref -> qtys + statuses ----
po_map = defaultdict(lambda: {"qtys": [], "status": {}})
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = norm(row["CF.PO Number"].strip())
            if not ref.startswith("MD"):
                continue
            pn = row["Purchase Order Number"]
            po_map[ref]["status"][pn] = row["Purchase Order Status"]
            qv = q(row["QuantityOrdered"])
            if qv is not None:
                po_map[ref]["qtys"].append(qv)

# ---- Sep 2025+ bills grouped by Bill ID ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["Bill Date"] < "2025-09-01":
                continue
            bid = row["Bill ID"]
            b = bills.get(bid)
            if b is None:
                raw = row["PurchaseOrder"].strip()
                has_md = bool(raw and mdpat.match(raw))
                b = bills[bid] = {"has_md": has_md,
                                  "refs": parse_refs(raw) if has_md else [],
                                  "qtys": []}
            qv = q(row["Quantity"])
            if qv is not None:
                b["qtys"].append(qv)

total = len(bills)
no_md = sum(1 for b in bills.values() if not b["has_md"])
has_md = total - no_md

# reconcile MD-ref bills
po_found = no_po = 0
matched = qty_mm = count_mm = 0
unmatched_po = billed_po = 0
for b in bills.values():
    if not b["has_md"]:
        continue
    found = [r for r in b["refs"] if r in po_map]
    if not found:
        no_po += 1
        continue
    po_found += 1
    pqty, statuses = [], {}
    for r in found:
        pqty.extend(po_map[r]["qtys"])
        statuses.update(po_map[r]["status"])
    bqty = sorted(b["qtys"]); pqty = sorted(pqty)
    if len(bqty) != len(pqty):
        count_mm += 1
    elif bqty != pqty:
        qty_mm += 1
    else:
        matched += 1
    if any(s in NOT_MATCHED for s in statuses.values()):
        unmatched_po += 1
    else:
        billed_po += 1

# no-PO DB breakdown from the sheet
sheet = list(csv.DictReader(open(SHEET, encoding="utf-8-sig")))
db_no_zoho = sum(1 for r in sheet if r["MD Reference(s)"] and "No (not created" in r["Zoho PO Created"])
db_notin = sum(1 for r in sheet if r["MD Reference(s)"] and "NOT IN DB" in r["PO DB Status"])
db_deleted = sum(1 for r in sheet if r["MD Reference(s)"] and "Yes" in r["Is Deleted"])
db_other = no_po - db_no_zoho - db_notin

print(f"SEP 2025+ BILLS (Bill Date >= 2025-09-01): {total}")
print(f"|")
print(f"|-- No MD reference ........................ {no_md}")
print(f"|-- Has MD reference ....................... {has_md}")
print(f"     |-- No matching PO in files .......... {no_po}")
print(f"     |    |-- PO in DB, NOT created in Zoho  {db_no_zoho}")
print(f"     |    |-- PO reference not in DB ....... {db_notin}")
print(f"     |    |-- other (mixed multi-ref) ..... {db_other}")
print(f"     |    `-- (of above, PO is_deleted=True {db_deleted})")
print(f"     `-- Matching PO found ............... {po_found}")
print(f"          |-- Matched (item count + qty) .. {matched}")
print(f"          |-- Qty mismatch ............... {qty_mm}")
print(f"          |-- Item-count mismatch ........ {count_mm}")
print(f"          +-- PO not yet matched in Zoho .. {unmatched_po}")
print(f"          `-- PO already Billed in Zoho .. {billed_po}")
print()
print(f"check: {no_md}+{no_po}+{po_found} = {no_md+no_po+po_found} (== {total})")
