"""flight-version: GET /version -> {backend_version, env}.

BACKEND_VERSION is stamped by the deploy (infra/deploy.sh): the git tree ids of lambdas/
and infra/ that were deployed. The dev frontend compares it with the local checkout and
shows a banner when the deployed backend doesn't match, so a stale or failed deploy is
visible instead of a silently broken button.
"""

import json
import os


def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(
            {
                "backend_version": os.environ.get("BACKEND_VERSION") or "unknown",
                "env": os.environ.get("STACK_ENV") or "unknown",
            }
        ),
    }
