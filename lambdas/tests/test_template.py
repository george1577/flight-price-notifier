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


def resources_of_type(template, type_):
    return {name: r for name, r in template["Resources"].items() if r["Type"] == type_}


def function_named(template, function_name):
    (fn,) = [
        r for r in resources_of_type(template, "AWS::Lambda::Function").values()
        if r["Properties"]["FunctionName"] == function_name
    ]
    return fn


def api_route_function(template, route_key):
    """The Lambda resource name an HTTP API route proxies to."""
    (route,) = [
        r for r in resources_of_type(template, "AWS::ApiGatewayV2::Route").values()
        if r["Properties"]["RouteKey"] == route_key
    ]
    integration_name = route["Properties"]["Target"]["Fn::Sub"].removeprefix("integrations/${").removesuffix("}")
    integration = template["Resources"][integration_name]["Properties"]
    return integration["IntegrationUri"]["Fn::GetAtt"].split(".")[0]  # !GetAtt Name.Arn


def test_save_subscription_gets_the_api_base_url_for_the_ecpay_callbacks(template):
    env = function_named(template, "flight-save-subscription")["Properties"]["Environment"]["Variables"]

    assert env["API_BASE_URL"] == {"Fn::Sub": "https://${FlightApi}.execute-api.${AWS::Region}.amazonaws.com"}


def test_save_subscription_billing_period_is_configured_in_the_template(template):
    env = function_named(template, "flight-save-subscription")["Properties"]["Environment"]["Variables"]

    # Strings: Lambda env values are strings. Switch to D/1/2 to test renewals, then back.
    assert (env["PERIOD_TYPE"], env["FREQUENCY"], env["EXEC_TIMES"]) == ("M", "1", "999")


def test_ecpay_result_redirect_lambda_is_on_any_ecpay_result(template):
    fn_name = api_route_function(template, "ANY /ecpay-result")
    fn = template["Resources"][fn_name]["Properties"]

    assert fn["FunctionName"] == "flight-ecpay-result"
    assert fn["Code"] == "../lambdas/flight-ecpay-result/"
    assert fn["Role"] == {"Fn::GetAtt": "FlightLambdaRole.Arn"}
    assert fn["Environment"]["Variables"]["SITE_URL"] == {"Ref": "SiteUrl"}
    assert "SiteUrl" in template["Parameters"]

    permissions = [
        p["Properties"] for p in resources_of_type(template, "AWS::Lambda::Permission").values()
        if p["Properties"]["FunctionName"] == {"Ref": fn_name}
    ]
    assert [p["SourceArn"] for p in permissions] == [
        {"Fn::Sub": "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${FlightApi}/*/*/ecpay-result"}
    ]


def test_ecpay_return_callback_lambda_is_on_post_ecpay_return(template):
    fn_name = api_route_function(template, "POST /ecpay-return")
    fn = template["Resources"][fn_name]["Properties"]

    assert fn["FunctionName"] == "flight-ecpay-return"
    assert fn["Code"] == "../lambdas/flight-ecpay-return/"
    assert fn["Role"] == {"Fn::GetAtt": "FlightLambdaRole.Arn"}

    permissions = [
        p["Properties"] for p in resources_of_type(template, "AWS::Lambda::Permission").values()
        if p["Properties"]["FunctionName"] == {"Ref": fn_name}
    ]
    assert [p["SourceArn"] for p in permissions] == [
        {"Fn::Sub": "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${FlightApi}/*/*/ecpay-return"}
    ]


def test_ecpay_period_callback_lambda_is_on_post_ecpay_period(template):
    fn_name = api_route_function(template, "POST /ecpay-period")
    fn = template["Resources"][fn_name]["Properties"]

    assert fn["FunctionName"] == "flight-ecpay-period"
    assert fn["Code"] == "../lambdas/flight-ecpay-period/"
    assert fn["Role"] == {"Fn::GetAtt": "FlightLambdaRole.Arn"}

    permissions = [
        p["Properties"] for p in resources_of_type(template, "AWS::Lambda::Permission").values()
        if p["Properties"]["FunctionName"] == {"Ref": fn_name}
    ]
    assert [p["SourceArn"] for p in permissions] == [
        {"Fn::Sub": "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${FlightApi}/*/*/ecpay-period"}
    ]


def test_role_policy_keeps_every_m1_statement(template):
    sids = {s["Sid"] for s in flight_data_statements(template)}

    assert {
        "FlightSecrets", "FlightTables", "FlightFareQueueSend", "FlightConfigObjects",
        "FlightParserInvoke", "FlightFareQueueConsume",
    } <= sids
