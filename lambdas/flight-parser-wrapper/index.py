"""flight-parser-wrapper: EventBridge (`flight-price-check`, rate(30 minutes)) fan-out.

Reads the admin-managed routes list `flight-routes.json` from the CONFIG_BUCKET S3
bucket and asynchronously (InvocationType=Event) invokes `flight-parser` once per
route with {origin, destination, route}. This Lambda needs no code change
to add a route (see infra/README.md for the places that do).
"""

import json
import os

import boto3

ROUTES_KEY = "flight-routes.json"
PARSER_FUNCTION = os.environ.get("PARSER_FUNCTION", "flight-parser")

s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")


def handler(event, context):
    bucket = os.environ["CONFIG_BUCKET"]
    routes = json.loads(s3.get_object(Bucket=bucket, Key=ROUTES_KEY)["Body"].read())

    invoked = []
    for r in routes:
        route = f"{r['origin']}-{r['destination']}"
        payload = {"origin": r["origin"], "destination": r["destination"], "route": route}
        lambda_client.invoke(
            FunctionName=PARSER_FUNCTION,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )
        invoked.append(route)

    print(json.dumps({"fn": "flight-parser-wrapper", "bucket": bucket, "routes": invoked, "invoked": len(invoked)}))
    return {"ok": True, "invoked": len(invoked), "routes": invoked}
