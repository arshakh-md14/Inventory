"""
Revert committed CHECK cases to PRE-MATCH state:
  - restore the bill's ORIGINAL account line (original rate + original discount amount)
  - unlink it from the PO (no purchaseorder_item_id / no receive link)
  - restore the PO (original rate, adjustment 0) -> back to to_be_billed
Reads originals from the CSV exports. Logs to attach_reverted.csv and REMOVES the row
from attach_results.csv so the corrected attach can re-process the valid ones later.

DRY_RUN=True prints intended changes only.  LIMIT caps how many to process.
"""
import json, http.client, urllib.parse, csv, glob, re, os, time

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
REVERTED = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_reverted.csv"
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"

DRY_RUN = False
LIMIT = 50
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def f(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_tok = None


def token():
    global _tok
    if _tok:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _tok = json.loads(c.getresponse().read())["access_token"]
    return _tok


def call(m, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"
        c.request(m, f"/books/v3{path}?organization_id={ORG}", json.dumps(body), h)
    else:
        c.request(m, f"/books/v3{path}?organization_id={ORG}", headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read().decode())


# ---- CHECK rows ----
rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
checks = [r for r in rows if r["result"] == "CHECK"]
check_bids = {r["bill_id"] for r in checks}

# ---- export originals: bill_id -> orig line; po ref -> orig rate ----
bill_orig = {}
for fn in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        if row["Bill ID"] in check_bids and row["Bill ID"] not in bill_orig:
            bill_orig[row["Bill ID"]] = {"rate": f(row["Rate"]), "disc": f(row["Discount Amount"]),
                                         "qty": f(row["Quantity"]), "desc": row["Description"],
                                         "total": f(row["Total"]),
                                         "before_tax": (row.get("Is Discount Before Tax", "true").lower() != "false")}
po_orig = {}
for fn in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            po_orig[ref] = {"rate": f(row["Item Price"]), "adj": f(row["Adjustment"])}

reverted_ids = set()
done = 0
log = []
for r in checks:
    if done >= LIMIT:
        break
    bid, pid = r["bill_id"], r["po_id"]
    bo = bill_orig.get(bid)
    if not bo:
        print(f"  {r['bill_number']}: no export original -> skip"); continue
    _, bd = call("GET", f"/bills/{bid}")
    bill = bd["bill"]; bl = bill["line_items"][0]

    line = {"account_id": bl.get("account_id"), "name": bo["desc"] or bl.get("name"),
            "description": bo["desc"], "rate": bo["rate"], "quantity": bo["qty"],
            "tax_id": bl.get("tax_id"), "item_order": 1}
    if bo["disc"] > 0:
        line["discount"] = bo["disc"]
    PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms", "adjustment",
                "adjustment_description", "exchange_rate", "is_inclusive_tax", "is_item_level_tax_calc",
                "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bill_revert = {"bill_number": bill["bill_number"], "line_items": [line],
                   "discount_type": "item_level", "is_discount_before_tax": bo["before_tax"]}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bill_revert[k] = bill[k]
    ref_str = (bill.get("reference_number") or "")
    bill_revert["reference_number"] = ref_str.replace(r["po_number"], "").strip(", ")

    done += 1
    if DRY_RUN:
        print(f"[{done}] {r['bill_number']}/{r['po_number']}: restore rate={bo['rate']} disc={bo['disc']} qty={bo['qty']} -> expect bill total {bo['total']}; unmatch PO")
        continue

    # 1. revert bill (unlinks PO)
    st, rr = call("PUT", f"/bills/{bid}", bill_revert)
    _, bd2 = call("GET", f"/bills/{bid}"); b2 = bd2["bill"]
    # 2. restore PO
    _, pd = call("GET", f"/purchaseorders/{pid}"); po = pd["purchaseorder"]; pl = po["line_items"][0]
    poref = next((x for x in (norm(t) for t in (bill.get('reference_number') or '').split(',')) if x in po_orig), None)
    porate = po_orig.get(poref, {}).get("rate", float(pl["rate"]))
    poadj = po_orig.get(poref, {}).get("adj", 0.0)
    po_restore = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                                  "rate": porate, "quantity": float(pl["quantity"]), "tax_id": pl.get("tax_id")}],
                  "adjustment": poadj, "adjustment_description": "Round Off"}
    call("PUT", f"/purchaseorders/{pid}", po_restore)
    _, pd2 = call("GET", f"/purchaseorders/{pid}"); p2 = pd2["purchaseorder"]
    ok = abs(float(b2["total"]) - bo["total"]) < 0.1 and not b2.get("purchaseorder_ids") and p2.get("billed_status") != "billed"
    print(f"[{done}] {r['bill_number']}/{r['po_number']}: bill {b2['total']} (orig {bo['total']}) linked={bool(b2.get('purchaseorder_ids'))} PO billed={p2.get('billed_status')} -> {'OK' if ok else 'CHECK'}")
    if ok:
        reverted_ids.add(bid)
        log.append({**r, "result": "REVERTED", "reason": "restored to pre-match + PO unmatched"})
    time.sleep(0.5)

if not DRY_RUN and reverted_ids:
    # append to reverted log, remove from results
    newlog = os.path.exists(REVERTED) is False
    with open(REVERTED, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if newlog:
            w.writeheader()
        w.writerows(log)
    remaining = [x for x in rows if x["bill_id"] not in reverted_ids]
    with open(RESULTS, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(remaining)
    print(f"\nreverted {len(reverted_ids)} | removed from results, logged to attach_reverted.csv")
else:
    print(f"\n[DRY] would process {done}")
