"""READ-ONLY. For paid + inventory item-count-mismatch bills: match bill<->PO lines by
AMOUNT (bill line names are blank & item_ids differ), isolate the extra bill lines, and
resolve each extra line's item name via the Item master to test if it's a packaging/
transport charge. Output -> itemcount_classify.csv + summary."""
import importlib.util, csv, re, glob, os
from collections import Counter

spec = importlib.util.spec_from_file_location("m", r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call = m.call
csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()
INVSET = {"Inventory (active)", "Inventory (inactive)"}
SHIP_ID = "2432338000028755653"
CHARGE_KW = ("pack", "transport", "freight", "shipping", "loading", "unloading", "handling",
             "cartage", "courier", "forwarding", "insuranc", "labour", "labor", "octroi", "carriage",
             "misc", "charge", "delivery", "installation", "fitting", "adjustment", "round")

# item_id -> name from Item master
ITEM_NAME = {}
for f in glob.glob(os.path.join(INV, "Raw Files", "Item_*", "Item*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        i = row.get("Item ID", "").strip()
        if i:
            ITEM_NAME[i] = row.get("Item Name", "") or row.get("Item Name.1", "")


def line_name(li):
    # on these bills the label is usually in `description` (name & item_id blank)
    return (li.get("name") or li.get("description") or ITEM_NAME.get(li.get("item_id"), "") or "").strip()


def is_charge(li):
    if li.get("item_id") == SHIP_ID:
        return True
    text = " ".join([li.get("name") or "", li.get("description") or "",
                     ITEM_NAME.get(li.get("item_id"), "")]).lower()
    if "- purchase" in text:
        return True
    return any(k in text for k in CHARGE_KW)


def invonly(r):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    return bool(toks & INVSET)  # EXPANDED: inventory present (alone or mixed)


def amt(li):
    return round(float(li.get("item_total") or 0), 2)


poid, billid = {}, {}
for f in glob.glob(os.path.join(INV, "Raw Files", "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        r = norm(row["CF.PO Number"])
        if r.startswith("MD"):
            poid.setdefault(r, row["Purchase Order ID"])
for f in glob.glob(os.path.join(INV, "Raw Files", "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        for t in row["PurchaseOrder"].split(","):
            n = norm(t)
            if n.startswith("MD"):
                billid.setdefault((row["Bill Number"], n), row["Bill ID"])

rows = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
sel = [r for r in rows if r["Attach Status"] == "Not Attached" and r["Reason"].startswith("item-count")
       and r["Bill Status"] == "Paid" and invonly(r)]
print("paid+inventory item-count-mismatch bills:", len(sel))

cls, results, n = Counter(), [], 0
for r in sel:
    ref = norm(r["MD Reference(s)"].strip())
    pid = poid.get(ref); bid = billid.get((r["Bill Number"], ref))
    if not pid or not bid:
        cls["no-id"] += 1; continue
    try:
        _, bd = call("GET", f"/bills/{bid}"); bill = bd.get("bill")
        _, pd = call("GET", f"/purchaseorders/{pid}"); po = pd.get("purchaseorder")
    except Exception as e:
        cls["fetch-fail"] += 1; continue
    if not bill or not po:
        cls["fetch-fail"] += 1; continue
    bl, pl = bill["line_items"], po["line_items"]
    # greedy match bill<->PO lines by amount
    pool = list(pl); extra_bill = []
    for x in bl:
        hit = next((y for y in pool if amt(y) == amt(x)), None)
        if hit:
            pool.remove(hit)
        else:
            extra_bill.append(x)
    extra_po = pool
    eb_charge = [x for x in extra_bill if is_charge(x)]
    eb_prod = [x for x in extra_bill if not is_charge(x)]
    if not extra_po and extra_bill and not eb_prod:
        c = "charge-explained (bill extra = charges)"
    elif not extra_po and not extra_bill:
        c = "lines match by amount (false mismatch)"
    elif extra_po and not extra_bill:
        c = "PO has extra lines (bill covers subset)"
    elif eb_prod:
        c = "genuine extra product on bill"
    else:
        c = "other"
    cls[c] += 1
    results.append({"Bill Number": r["Bill Number"], "MD Reference(s)": r["MD Reference(s)"], "PO Numbers": r["PO Numbers"],
                    "bill_id": bid, "po_id": pid, "bill_amount": bill.get("total"),
                    "bill_lines": len(bl), "po_lines": len(pl),
                    "extra_bill_charge": len(eb_charge), "extra_bill_prod": len(eb_prod), "extra_po": len(extra_po),
                    "class": c,
                    "extra_bill_detail": " | ".join(f"{line_name(x)[:24]}={amt(x)}" for x in extra_bill)[:120]})
    n += 1
    if n % 60 == 0:
        print(f"  ...{n}")

with open(os.path.join(INV, "itemcount_classify.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    fields = ["Bill Number", "MD Reference(s)", "PO Numbers", "bill_id", "po_id", "bill_amount", "bill_lines", "po_lines",
              "extra_bill_charge", "extra_bill_prod", "extra_po", "class", "extra_bill_detail"]
    w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(results)
print("\nCLASSIFICATION:")
for k, v in cls.most_common():
    print(f"  {v:5}  {k}")
print("-> itemcount_classify.csv")
