"""CheckMacValue parity: every Lambda's copy of the CMV helper must pass ECPay's vectors.

The CMV helper is deliberately duplicated into each Lambda that signs or verifies ECPay
requests (see infra/README.md), so this is the one place a module-level function is
tested directly: it keeps the copies in step, the way test_route_tables.py keeps the
route tables in step. When a new Lambda gets a copy, add its directory to CMV_LAMBDAS.

fixtures/checkmacvalue.json is ECPay's official known-answer file, copied verbatim from
the ecpay skill's test-vectors/. Only the AIO SHA256 vectors apply to our helper; the
MD5 (logistics, e-invoice) and E-Ticket vectors use other formulas.
"""

import json
from pathlib import Path

import pytest

from conftest import ecpay_cmv, load_lambda

CMV_LAMBDAS = [
    "flight-cancel-subscription",
    "flight-save-subscription",
    "flight-ecpay-return",
]

VECTORS = [
    v
    for v in json.loads((Path(__file__).parent / "fixtures" / "checkmacvalue.json").read_text())["vectors"]
    if v["method"] == "SHA256" and "params" in v
]


def test_fixture_includes_the_tricky_encoding_cases():
    names = " ".join(v["name"] for v in VECTORS)
    assert "~" in names and "'" in names and "空格" in names


@pytest.mark.parametrize("function_name", CMV_LAMBDAS)
@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_cmv_helper_matches_ecpay_known_answers(aws, function_name, vector):
    module = load_lambda(function_name)

    assert module.check_mac_value(vector["params"], vector["hashKey"], vector["hashIV"]) == vector["expected"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_the_tests_own_signer_matches_ecpay_known_answers(vector):
    assert ecpay_cmv(vector["params"], vector["hashKey"], vector["hashIV"]) == vector["expected"]
