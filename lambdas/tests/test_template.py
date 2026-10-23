"""infra/flight-m1.yaml: guard rails that can't be checked without deploying."""

import pytest
import yaml

from conftest import LAMBDAS_DIR

TEMPLATE = LAMBDAS_DIR.parent / "infra" / "flight-m1.yaml"


class CfnLoader(yaml.SafeLoader):
    """SafeLoader that turns CloudFormation short-form tags (!Sub, !Ref, ...) into {"Fn::X": value}."""


def _cfn_tag(loader, tag_suffix, node):
    name = "Ref" if tag_suffix == "Ref" else f"Fn::{tag_suffix}"
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {name: value}


CfnLoader.add_multi_constructor("!", _cfn_tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)


def flight_data_statements(template):
    policies = template["Resources"]["FlightLambdaRole"]["Properties"]["Policies"]
    (policy,) = [p for p in policies if p["PolicyName"] == "flight-data"]
    return policy["PolicyDocument"]["Statement"]


def test_lambdas_may_only_read_the_routes_object_from_the_config_bucket(template):
    s3_statements = [
        s
        for s in flight_data_statements(template)
        if any(str(a).startswith("s3:") for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))
    ]

    assert len(s3_statements) == 1
    (statement,) = s3_statements
    assert statement["Action"] == "s3:GetObject"
    assert statement["Resource"] == {"Fn::Sub": "arn:aws:s3:::flight-config-${AWS::AccountId}/flight-routes.json"}


def test_stack_refuses_to_deploy_outside_us_east_1(template):
    assertions = [a for rule in template.get("Rules", {}).values() for a in rule["Assertions"]]

    assert {"Fn::Equals": [{"Ref": "AWS::Region"}, "us-east-1"]} in [a["Assert"] for a in assertions]
