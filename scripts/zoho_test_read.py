"""
READ-ONLY test: authenticate to Zoho Books (Haut Luxe org) via OAuth and fetch
one matched Bill + Purchase Order so we can inspect the live data before any write.

Test case (from bill_po_reconciliation.csv, Status=Matched):
  Bill 3267  -> Bill ID 2432338000006302002 (status Paid)
  PO-05744   -> PO   ID 2432338000006216041 (status Issued, QtyBilled 0)
  MD9202531774 | amounts 1534.00 vs 1533.90
"""
import json, http.client, urllib.parse

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
BILL_ID = "2432338000006302002"
PO_ID = "2432338000006216041"

d = json.load(open(ENV))
CLIENT_ID = d["zoho_client_id_haut_luxe"]
CLIENT_SECRET = d["zoho_client_secret_haut_luxe"]
REFRESH_TOKEN = d["zoho_refresh_token_haut_luxe"]
ORG_ID = str(d["zoho_org_id_haut_luxe"])


def get_token():
    conn = http.client.HTTPSConnection("accounts.zoho.in")
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN,
    })
    conn.request("POST", f"/oauth/v2/token?{params}")
    r = conn.getresponse(); body = r.read().decode()
    conn.close()
    tok = json.loads(body).get("access_token")
    if not tok:
        raise SystemExit(f"Token refresh failed: {body}")
    return tok


def api_get(token, path):
    conn = http.client.HTTPSConnection("www.zohoapis.in")
    sep = "&" if "?" in path else "?"
    conn.request("GET", f"/books/v3{path}{sep}organization_id={ORG_ID}",
                 headers={"Authorization": f"Zoho-oauthtoken {token}"})
    r = conn.getresponse(); body = r.read().decode()
    conn.close()
    return r.status, json.loads(body)


token = get_token()
print("OAuth: access token obtained OK\n")

# ---- Bill ----
st, data = api_get(token, f"/bills/{BILL_ID}")
print(f"GET /bills/{BILL_ID} -> HTTP {st}")
b = data.get("bill", {})
if b:
    print(f"  bill_number = {b.get('bill_number')} | status = {b.get('status')}")
    print(f"  vendor = {b.get('vendor_name')}")
    print(f"  total = {b.get('total')} | sub_total = {b.get('sub_total')}")
    print(f"  purchaseorder linked? purchaseorder_ids = {b.get('purchaseorder_ids')}")
    print(f"  line_items ({len(b.get('line_items', []))}):")
    for li in b.get("line_items", []):
        print(f"    - line_item_id={li.get('line_item_id')} | name={li.get('name')!r} | "
              f"item_id={li.get('item_id')} | qty={li.get('quantity')} | rate={li.get('rate')} | "
              f"total={li.get('item_total')} | po_id={li.get('purchaseorder_id')}")
else:
    print("  ", json.dumps(data)[:400])

print()

# ---- Purchase Order ----
st, data = api_get(token, f"/purchaseorders/{PO_ID}")
print(f"GET /purchaseorders/{PO_ID} -> HTTP {st}")
p = data.get("purchaseorder", {})
if p:
    print(f"  po_number = {p.get('purchaseorder_number')} | status = {p.get('status')} | "
          f"billed_status = {p.get('billed_status')}")
    print(f"  vendor = {p.get('vendor_name')} | total = {p.get('total')}")
    print(f"  line_items ({len(p.get('line_items', []))}):")
    for li in p.get("line_items", []):
        print(f"    - line_item_id={li.get('line_item_id')} | name={li.get('name')!r} | "
              f"item_id={li.get('item_id')} | qty={li.get('quantity')} | "
              f"billed_qty={li.get('quantity_billed')} | rate={li.get('rate')} | total={li.get('item_total')}")
else:
    print("  ", json.dumps(data)[:400])
