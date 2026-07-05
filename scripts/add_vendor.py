import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
RECON = r"C:\Users\Jogesh Behera\Code file\Inventory\bill_po_reconciliation.csv"


def norm(t):
    return re.sub(r'[^0-9A-Za-z]', '', t).upper()


def vnorm(s):
    """normalize vendor name for comparison: lowercase, collapse spaces, strip punctuation."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


# ---- PO file: normalized MD ref -> set of PO vendor names ----
ref_vendor = defaultdict(set)
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = norm(row["CF.PO Number"].strip())
            v = row["Vendor Name"].strip()
            if ref.startswith("MD") and v:
                ref_vendor[ref].add(v)

# ---- Update sheet ----
rows = list(csv.DictReader(open(RECON, encoding="utf-8-sig")))
from collections import Counter
counts = Counter()
for r in rows:
    bill_vendor = r["Vendor"].strip()
    po_vendors = set()
    for ref in (t.strip() for t in r["MD Reference(s)"].split(";")):
        if ref:
            po_vendors |= ref_vendor.get(ref, set())
    if not po_vendors:
        match = "No PO vendor"
        po_v_str = ""
    else:
        po_v_str = "; ".join(sorted(po_vendors))
        bn = vnorm(bill_vendor)
        if any(bn == vnorm(v) for v in po_vendors):
            match = "Yes"
        else:
            match = "No"
    counts[match] += 1
    r["PO Vendor"] = po_v_str
    r["Vendor Match"] = match

fields = list(rows[0].keys())
with open(RECON, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(rows)

print("rows:", len(rows))
print("Vendor Match breakdown:", dict(counts))
# show a few mismatches
print("\nsample mismatches:")
n = 0
for r in rows:
    if r["Vendor Match"] == "No":
        print(f"  {r['Bill Number']} | bill='{r['Vendor']}' | po='{r['PO Vendor']}'")
        n += 1
        if n >= 5:
            break
