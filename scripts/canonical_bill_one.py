"""
Canonical bill form (per user's FD/26-27/4738 example), tested on ONE case (SM/4158):
  - bill line = PO's item details (rate = PO rate, item/po-link/receive, discount 0)
  - bill 'adjustment' = paid_total - PO_line_total  (Round Off)  -> bill total stays = paid
  - PO untouched (DB)
Item temporarily reactivated to set a line with an inactive item.
"""
import json, http.client, urllib.parse

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
BILL_ID = "2432338000006690391"   # SM/25-26/4158
PO_ID = "2432338000006341046"     # PO-05871
DRY_RUN = False

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None


def token():
    global _tok
    if _tok: return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}"); _tok = json.loads(c.getresponse().read())["access_token"]; return _tok


def call(m, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in"); h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"; c.request(m, f"/books/v3{path}?organization_id={ORG}", json.dumps(body), h)
    else: c.request(m, f"/books/v3{path}?organization_id={ORG}", headers=h)
    r = c.getresponse(); return r.status, json.loads(r.read().decode())


_, bd = call("GET", f"/bills/{BILL_ID}"); bill = bd["bill"]; bl = bill["line_items"][0]
_, pd = call("GET", f"/purchaseorders/{PO_ID}"); po = pd["purchaseorder"]; pl = po["line_items"][0]
paid_total = float(bill["total"])
po_line_total = round(float(po["total"]) - float(po.get("adjustment") or 0), 2)   # PO line+tax, excl PO adj
round_off = round(paid_total - po_line_total, 2)
item_id = pl["item_id"]
print(f"bill paid_total={paid_total} | PO line_total(excl adj)={po_line_total} | round_off={round_off}")

# receive link
receive = []
for rec in po.get("purchasereceives", []):
    if rec.get("billed_status") == "billed" and bill["bill_number"] not in [b.get("bill_number") for b in rec.get("bills", [])]:
        continue
    _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
    for rli in rdd.get("purchasereceive", {}).get("line_items", []):
        if rli.get("item_id") == item_id:
            receive.append({"receive_item_id": rli["line_item_id"], "quantity": float(pl["quantity"])})
            break

new_line = {"purchaseorder_item_id": pl["line_item_id"], "item_id": item_id,
            "account_id": pl.get("account_id") or bl.get("account_id"),
            "name": pl.get("name"), "description": "",
            "rate": float(pl["rate"]), "quantity": float(pl["quantity"]), "discount": 0,
            "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"),
            "tax_id": pl.get("tax_id") or bl.get("tax_id"), "item_order": 1, "is_billable": False}
if receive:
    new_line["receive_line_items"] = receive

ref = bill.get("reference_number") or ""
if po["purchaseorder_number"] not in ref:
    ref = (ref + "," + po["purchaseorder_number"]).strip(",")
PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms", "exchange_rate",
            "is_inclusive_tax", "is_item_level_tax_calc", "payment_terms", "payment_terms_label",
            "gst_treatment", "gst_no", "source_of_supply", "destination_of_supply",
            "template_id", "billing_address_id"]
bill_update = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line],
               "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
for k in PRESERVE:
    if bill.get(k) not in (None, ""):
        bill_update[k] = bill[k]
if bill.get("custom_fields"):
    bill_update["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]

print("\n--- BILL UPDATE payload ---")
print(json.dumps(bill_update, indent=2)[:700])
if DRY_RUN:
    raise SystemExit("\n[DRY]")

# reactivate item, PUT bill, deactivate
_, itd = call("GET", f"/items/{item_id}"); st_item = itd.get("item", {}).get("status")
if st_item != "active":
    call("POST", f"/items/{item_id}/active")
st, r = call("PUT", f"/bills/{BILL_ID}", bill_update)
print("\nPUT bill HTTP", st, r.get("message"))
if st_item != "active":
    call("POST", f"/items/{item_id}/inactive")

_, bd2 = call("GET", f"/bills/{BILL_ID}"); b2 = bd2["bill"]; bl2 = b2["line_items"][0]
_, pd2 = call("GET", f"/purchaseorders/{PO_ID}"); p2 = pd2["purchaseorder"]
print(f"VERIFY bill: total={b2['total']} (paid {paid_total}) | line rate={bl2['rate']} (PO {pl['rate']}) "
      f"disc={bl2.get('discount')} adj={b2.get('adjustment')} linked={b2.get('purchaseorder_ids')}")
print(f"VERIFY PO: total={p2['total']} adj={p2.get('adjustment')} billed={p2.get('billed_status')}")
ok = abs(float(b2["total"]) - paid_total) < 0.005 and abs(float(bl2["rate"]) - float(pl["rate"])) < 0.01 \
     and b2.get("purchaseorder_ids") and abs(float(p2["total"]) - po_line_total) < 0.05
print("RESULT:", "OK" if ok else "CHECK")
