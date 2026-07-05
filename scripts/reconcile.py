import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
OUT = r"C:\Users\Jogesh Behera\Code file\Inventory\bill_po_reconciliation.csv"
mdpat = re.compile(r'^MD', re.I)


def q(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def norm(token):
    """Uppercase, strip all non-alphanumerics from a single MD ref token."""
    return re.sub(r'[^0-9A-Za-z]', '', token).upper()


def parse_refs(field):
    """Split a PurchaseOrder field on commas, normalize, keep MD-prefixed refs."""
    out = []
    for tok in field.split(','):
        n = norm(tok)
        if n.startswith('MD'):
            out.append(n)
    return out


# ---- POs: map normalized MD ref -> {po_nums, qtys, statuses} ----
NOT_MATCHED = {"Draft", "Issued"}  # not yet billed/matched in Zoho
po_map = defaultdict(lambda: {"po_nums": set(), "qtys": [], "po_status": {}})
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = norm(row["CF.PO Number"].strip())
            if not ref.startswith("MD"):
                continue
            pnum = row["Purchase Order Number"]
            po_map[ref]["po_nums"].add(pnum)
            po_map[ref]["po_status"][pnum] = row["Purchase Order Status"]
            qv = q(row["QuantityOrdered"])
            if qv is not None:
                po_map[ref]["qtys"].append(qv)

# ---- Bills: Sep 2025+ with MD ref, grouped by Bill ID ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = row["PurchaseOrder"].strip()
            if not (raw and mdpat.match(raw)):
                continue
            if row["Bill Date"] < "2025-09-01":
                continue
            bid = row["Bill ID"]
            b = bills.get(bid)
            if b is None:
                b = bills[bid] = {
                    "bill_num": row["Bill Number"], "date": row["Bill Date"],
                    "status": row["Bill Status"], "vendor": row["Vendor Name"],
                    "refs": parse_refs(raw), "qtys": [],
                }
            qv = q(row["Quantity"])
            if qv is not None:
                b["qtys"].append(qv)

# ---- ref -> how many bills cite it (partial-billing detection) ----
ref_bill_count = defaultdict(int)
for b in bills.values():
    for r in set(b["refs"]):
        ref_bill_count[r] += 1

# ---- Reconcile per bill ----
rows_out = []
status_counts = defaultdict(int)
for bid, b in bills.items():
    bqty = sorted(b["qtys"])
    refs = b["refs"]
    found_refs = [r for r in refs if r in po_map]
    po_nums, pqty, po_statuses = set(), [], {}
    for r in found_refs:
        po_nums |= po_map[r]["po_nums"]
        pqty.extend(po_map[r]["qtys"])
        po_statuses.update(po_map[r]["po_status"])
    pqty = sorted(pqty)
    shared = any(ref_bill_count[r] > 1 for r in refs)
    # "not yet matched" = at least one associated PO is Draft/Issued
    has_unmatched_po = any(s in NOT_MATCHED for s in po_statuses.values())

    if not found_refs:
        status = "No PO found"
    elif len(bqty) != len(pqty):
        status = "Item-count mismatch"
    elif bqty != pqty:
        status = "Qty mismatch"
    else:
        status = "Matched"

    # KEEP ONLY: a PO was found for this bill AND at least one of those POs
    # is not yet matched in Zoho (Draft/Issued).
    if not found_refs or not has_unmatched_po:
        continue
    status_counts[status] += 1

    rows_out.append({
        "Bill Number": b["bill_num"],
        "Bill Date": b["date"],
        "Status": status,
        "MD Reference(s)": "; ".join(refs),
        "PO Numbers": "; ".join(sorted(po_nums)),
        "PO Status(es)": "; ".join(f"{p}:{po_statuses[p]}" for p in sorted(po_statuses)),
        "Bill Item Count": len(bqty),
        "PO Item Count": len(pqty),
        "Bill Quantities": "; ".join(f"{x:g}" for x in bqty),
        "PO Quantities": "; ".join(f"{x:g}" for x in pqty),
        "Multi-PO Bill": "Yes" if len(refs) > 1 else "",
        "MD Ref Shared by Other Bills": "Yes" if shared else "",
        "Bill Status": b["status"],
        "Vendor": b["vendor"],
    })

order = {"Matched": 0, "Qty mismatch": 1, "Item-count mismatch": 2, "No PO found": 3}
rows_out.sort(key=lambda r: (order[r["Status"]], r["Bill Date"]))
fields = ["Bill Number", "Bill Date", "Status", "MD Reference(s)", "PO Numbers",
          "PO Status(es)", "Bill Item Count", "PO Item Count", "Bill Quantities",
          "PO Quantities", "Multi-PO Bill", "MD Ref Shared by Other Bills",
          "Bill Status", "Vendor"]
with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    w.writerows(rows_out)

print("Wrote", len(rows_out), "bills (unmatched PO + bill found) to", OUT)
print("Reconciliation breakdown among kept rows:")
for s in ["Matched", "Qty mismatch", "Item-count mismatch", "No PO found"]:
    print(f"  {s}: {status_counts[s]}")
