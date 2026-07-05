"""
Attach a matched Bill to its Purchase Order in Zoho Books (Haut Luxe) via OAuth.

Per matched pair:
  1. GET bill + PO; validate (single line; bill not linked; PO open & not billed).
  2. Item must be billable: if inactive -> temporarily reactivate (REACTIVATE_INACTIVE).
  3. Make PO total == bill total by matching PRE-TAX subtotal:
     PO line rate = bill.sub_total / qty   (robust to inclusive/exclusive tax).
  4. PUT bill: swap line items to the PO's catalog line (link purchaseorder_item_id
     + receive_line_items), PRESERVING the bill's header/tax fields so the PAID
     bill's total is unchanged. Old line dropped (array replacement).
  5. Re-deactivate the item if we reactivated it.
  6. Verify: bill total unchanged, one linked line, PO billed, totals equal.

DRY_RUN=True -> prints intended writes only.
"""
import json, http.client, urllib.parse

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"

DRY_RUN = False
REACTIVATE_INACTIVE = True
BILL_ID = "2432338000006391059"   # SM/25-26/4146  (inactive item -> reactivation path)
PO_ID = "2432338000006114038"     # PO-05623

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_token = None


def token():
    global _token
    if _token:
        return _token
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC,
                                "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _token = json.loads(c.getresponse().read()).get("access_token")
    if not _token:
        raise SystemExit("token refresh failed")
    return _token


def call(method, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    sep = "&" if "?" in path else "?"
    full = f"/books/v3{path}{sep}organization_id={ORG}"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"
        c.request(method, full, json.dumps(body), h)
    else:
        c.request(method, full, headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read().decode())


def money_eq(a, b):
    return abs(float(a) - float(b)) < 0.005


# ---------- fetch ----------
_, bd = call("GET", f"/bills/{BILL_ID}")
bill = bd["bill"]
_, pd = call("GET", f"/purchaseorders/{PO_ID}")
po = pd["purchaseorder"]
orig_bill_total = float(bill["total"])
print(f"BILL {bill['bill_number']} status={bill['status']} total={orig_bill_total} "
      f"sub_total={bill['sub_total']} inclusive_tax={bill.get('is_inclusive_tax')} linked={bill.get('purchaseorder_ids')}")
print(f"PO   {po['purchaseorder_number']} status={po['status']} billed={po.get('billed_status')} "
      f"received={po.get('received_status')} total={po['total']}")

# ---------- validate ----------
problems = []
if len(bill["line_items"]) != 1 or len(po["line_items"]) != 1:
    problems.append("not single-line on both sides")
if bill.get("purchaseorder_ids"):
    problems.append("bill already linked")
if po.get("billed_status") == "billed":
    problems.append("PO already billed")
if po.get("status") not in ("open",):
    problems.append(f"PO status is {po.get('status')} (need open)")
b_li, p_li = bill["line_items"][0], po["line_items"][0]
if b_li.get("quantity") != p_li.get("quantity"):
    problems.append(f"qty differs bill={b_li.get('quantity')} po={p_li.get('quantity')}")
if problems:
    raise SystemExit("ABORT: " + "; ".join(problems))

qty = float(b_li["quantity"])
item_id = p_li["item_id"]

# ---------- item status ----------
_, itd = call("GET", f"/items/{item_id}")
item_status = itd.get("item", {}).get("status")
need_reactivate = item_status != "active"
print(f"item status={item_status} need_reactivate={need_reactivate}")
if need_reactivate and not REACTIVATE_INACTIVE:
    raise SystemExit("ABORT: item inactive and REACTIVATE_INACTIVE=False")

# ---------- PO target rate: match pre-tax subtotal ----------
po_target_rate = round(float(bill["sub_total"]) / qty, 6)
po_update = {"line_items": [{"line_item_id": p_li["line_item_id"], "item_id": item_id,
                            "rate": po_target_rate, "quantity": float(p_li["quantity"])}],
             "adjustment": float(bill.get("adjustment") or 0),
             "adjustment_description": bill.get("adjustment_description") or "Round Off"}
print(f"PO rate {p_li['rate']} -> {po_target_rate} (so PO subtotal == bill subtotal {bill['sub_total']})")

# ---------- receive linkage ----------
receive_line_items = []
for rec in po.get("purchasereceives", []):
    if rec.get("billed_status") == "billed":
        continue
    _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
    for rli in rdd.get("purchasereceive", {}).get("line_items", []):
        if rli.get("item_id") == item_id:
            receive_line_items.append({"receive_item_id": rli["line_item_id"], "quantity": qty})
            break
print(f"receive_line_items: {receive_line_items}")

# ---------- bill PUT (preserve header/tax; swap line) ----------
new_line = {
    "purchaseorder_item_id": p_li["line_item_id"], "item_id": item_id,
    "account_id": p_li.get("account_id") or b_li.get("account_id"),
    "name": p_li.get("name"), "description": b_li.get("description", ""),
    "rate": float(b_li["rate"]), "quantity": qty,            # keep BILL's rate -> total unchanged
    "unit": p_li.get("unit") or b_li.get("unit"),
    "hsn_or_sac": p_li.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
    "tax_id": b_li.get("tax_id") or p_li.get("tax_id"),
    "item_order": 1, "is_billable": False,
}
if receive_line_items:
    new_line["receive_line_items"] = receive_line_items

ref = bill.get("reference_number") or ""
if po["purchaseorder_number"] not in ref:
    ref = (ref + "," + po["purchaseorder_number"]).strip(",")

PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms",
            "adjustment", "adjustment_description", "exchange_rate", "is_inclusive_tax",
            "is_item_level_tax_calc", "discount", "discount_type", "is_discount_before_tax",
            "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
            "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
bill_update = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line]}
for k in PRESERVE:
    if bill.get(k) not in (None, ""):
        bill_update[k] = bill[k]
if bill.get("custom_fields"):
    bill_update["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")}
                                    for c in bill["custom_fields"]]

print("\n--- PO UPDATE PUT /purchaseorders/%s ---" % PO_ID)
print(json.dumps(po_update))
print("--- BILL UPDATE PUT /bills/%s ---" % BILL_ID)
print(json.dumps(bill_update, indent=2))

if DRY_RUN:
    print("\n[DRY_RUN] no writes.")
    raise SystemExit

# ---------- execute ----------
if need_reactivate:
    st, r = call("POST", f"/items/{item_id}/active")
    print("reactivate item HTTP", st, r.get("message"))
st, r = call("PUT", f"/purchaseorders/{PO_ID}", po_update)
print("PO update HTTP", st, r.get("message"))
st, r = call("PUT", f"/bills/{BILL_ID}", bill_update)
print("Bill update HTTP", st, r.get("message"))
if need_reactivate:
    st, r = call("POST", f"/items/{item_id}/inactive")
    print("re-deactivate item HTTP", st, r.get("message"))

# ---------- verify ----------
_, bd2 = call("GET", f"/bills/{BILL_ID}")
b2 = bd2["bill"]
_, pd2 = call("GET", f"/purchaseorders/{PO_ID}")
p2 = pd2["purchaseorder"]
old_ids = {li["line_item_id"] for li in bill["line_items"]}
leftover = [li["line_item_id"] for li in b2["line_items"] if li["line_item_id"] in old_ids]
print(f"\nVERIFY bill: total={b2['total']} (orig {orig_bill_total}) lines={len(b2['line_items'])} "
      f"linked={b2.get('purchaseorder_ids')} leftover={leftover}")
print(f"VERIFY PO: billed_status={p2.get('billed_status')} total={p2['total']}")
ok = (money_eq(b2["total"], orig_bill_total) and len(b2["line_items"]) == 1 and not leftover
      and b2.get("purchaseorder_ids") and p2.get("billed_status") == "billed"
      and money_eq(b2["total"], p2["total"]))
print("RESULT:", "OK" if ok else "CHECK MANUALLY")
