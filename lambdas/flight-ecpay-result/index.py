"""flight-ecpay-result: ANY /ecpay-result (ECPay's OrderResultURL).

After paying, ECPay sends the *browser* back with a form POST. A static SPA answers a
POST to a page route with 405, so this Lambda turns it into a 302 to the dashboard:
<SITE_URL>/app?purchase=success, or ?purchase=failed when RtnCode isn't "1".

UX only: it changes no state and trusts nothing. Activation is the ReturnURL callback's job.
"""

import base64
import json
import os
import urllib.parse


def handler(event, context):
    rtn_code = read_fields(event).get("RtnCode")
    outcome = "success" if rtn_code == "1" else "failed"
    site = os.environ["SITE_URL"].strip().rstrip("/")
    print(json.dumps({"result": outcome, "rtn_code": rtn_code}))
    return {
        "statusCode": 302,
        "headers": {"location": f"{site}/app?purchase={outcome}", "cache-control": "no-store"},
        "body": "",
    }


def read_fields(event):
    """The form-urlencoded POST body, or the query string for a GET."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if raw:
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    return event.get("queryStringParameters") or {}
