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
    assert statement["Resource"] == {"Fn::Sub": "arn:aws:s3:::${FlightConfigBucket}/flight-routes.json"}


def test_stack_refuses_to_deploy_outside_us_east_1(template):
    assertions = [a for rule in template.get("Rules", {}).values() for a in rule["Assertions"]]

    assert {"Fn::Equals": [{"Ref": "AWS::Region"}, "us-east-1"]} in [a["Assert"] for a in assertions]


def resources_of_type(template, type_):
    return {name: r for name, r in template["Resources"].items() if r["Type"] == type_}


def resolve(value, env="prod"):
    """Evaluate `!If [IsDev, dev, prod]` for one environment (the template's only condition)."""
    if isinstance(value, dict) and set(value) == {"Fn::If"}:
        condition, if_dev, if_prod = value["Fn::If"]
        assert condition == "IsDev"
        return resolve(if_dev if env == "dev" else if_prod, env)
    if isinstance(value, dict):
        return {k: resolve(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, env) for v in value]
    return value


def function_named(template, function_name):
    """A Lambda resource by its prod FunctionName."""
    (fn,) = [
        r for r in resources_of_type(template, "AWS::Lambda::Function").values()
        if resolve(r["Properties"]["FunctionName"]) == function_name
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


def test_save_subscription_can_start_a_price_check_on_its_own_stacks_parser(template):
    env = function_named(template, "flight-save-subscription")["Properties"]["Environment"]["Variables"]

    assert env["PARSER_FUNCTION"] == {"Ref": "ParserFunction"}


def test_save_subscription_billing_period_is_configured_in_the_template(template):
    env = function_named(template, "flight-save-subscription")["Properties"]["Environment"]["Variables"]

    # Strings: Lambda env values are strings. Switch to D/1/2 to test renewals, then back.
    assert (env["PERIOD_TYPE"], env["FREQUENCY"], env["EXEC_TIMES"]) == ("M", "1", "999")


def test_ecpay_result_redirect_lambda_is_on_any_ecpay_result(template):
    fn_name = api_route_function(template, "ANY /ecpay-result")
    fn = template["Resources"][fn_name]["Properties"]

    assert resolve(fn["FunctionName"]) == "flight-ecpay-result"
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

    assert resolve(fn["FunctionName"]) == "flight-ecpay-return"
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

    assert resolve(fn["FunctionName"]) == "flight-ecpay-period"
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


# ---------------------------------------------- cancel (#21)


def test_cancel_lambda_is_on_post_cancel_and_told_the_status_queue_name(template):
    fn_name = api_route_function(template, "POST /cancel")
    fn = template["Resources"][fn_name]["Properties"]

    assert resolve(fn["FunctionName"]) == "flight-cancel-subscription"
    assert fn["Code"] == "../lambdas/flight-cancel-subscription/"
    assert fn["Role"] == {"Fn::GetAtt": "FlightLambdaRole.Arn"}
    assert fn["Environment"]["Variables"]["STATUS_QUEUE_NAME"] == {"Fn::GetAtt": "FlightStatusQueue.QueueName"}

    permissions = [
        p["Properties"] for p in resources_of_type(template, "AWS::Lambda::Permission").values()
        if p["Properties"]["FunctionName"] == {"Ref": fn_name}
    ]
    assert [p["SourceArn"] for p in permissions] == [
        {"Fn::Sub": "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${FlightApi}/*/*/cancel"}
    ]


def test_browsers_may_post_json_to_the_api_cross_origin(template):
    cors = template["Resources"]["FlightApi"]["Properties"]["CorsConfiguration"]

    assert "POST" in cors["AllowMethods"]  # POST /cancel from the dashboard
    assert "content-type" in [h.lower() for h in cors["AllowHeaders"]]


# ---------------------------------------------- status notifications (#20)


def statement(template, sid):
    (s,) = [s for s in flight_data_statements(template) if s.get("Sid") == sid]
    return s


def actions(s):
    return sorted([s["Action"]] if isinstance(s["Action"], str) else s["Action"])


def test_status_queue_visibility_timeout_is_about_six_times_the_consumer_timeout(template):
    (queue_name,) = [
        name for name, r in resources_of_type(template, "AWS::SQS::Queue").items()
        if resolve(r["Properties"]["QueueName"]) == "flight-status-queue"
    ]
    consumer = function_named(template, "flight-status-notification")["Properties"]

    assert consumer["Code"] == "../lambdas/flight-status-notification/"
    assert consumer["Role"] == {"Fn::GetAtt": "FlightLambdaRole.Arn"}
    visibility = template["Resources"][queue_name]["Properties"]["VisibilityTimeout"]
    assert 5 * consumer["Timeout"] <= visibility <= 7 * consumer["Timeout"]


def test_status_queue_feeds_the_one_status_consumer_one_message_at_a_time(template):
    mappings = [
        m["Properties"] for m in resources_of_type(template, "AWS::Lambda::EventSourceMapping").values()
        if m["Properties"]["EventSourceArn"] == {"Fn::GetAtt": "FlightStatusQueue.Arn"}
    ]

    assert len(mappings) == 1
    (mapping,) = mappings
    consumer = template["Resources"][mapping["FunctionName"]["Ref"]]["Properties"]
    assert resolve(consumer["FunctionName"]) == "flight-status-notification"
    assert mapping["BatchSize"] == 1


def test_status_queue_permissions_are_least_privilege_on_its_own_arn(template):
    consume = statement(template, "FlightStatusQueueConsume")
    send = statement(template, "FlightStatusQueueSend")

    assert actions(consume) == ["sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ReceiveMessage"]
    assert actions(send) == ["sqs:GetQueueUrl", "sqs:SendMessage"]
    for s in (consume, send):
        assert s["Effect"] == "Allow"
        assert s["Resource"] == {"Fn::GetAtt": "FlightStatusQueue.Arn"}
    # No other statement touches the status queue, and the fare-queue grants are unchanged.
    others = [s for s in flight_data_statements(template) if s["Sid"] not in {consume["Sid"], send["Sid"]}]
    assert all("FlightStatusQueue" not in str(s["Resource"]) for s in others)
    assert statement(template, "FlightFareQueueSend")["Resource"] == {"Fn::GetAtt": "FlightFareQueue.Arn"}
    assert statement(template, "FlightFareQueueConsume")["Resource"] == {"Fn::GetAtt": "FlightFareQueue.Arn"}


def test_status_notifier_can_read_and_mark_the_subscription_row_without_a_wider_grant(template):
    # flight-status-notification GetItems the row and UpdateItems its sent-email marker. The
    # shared role's FlightTables statement already covers both; no other statement grants DynamoDB.
    dynamodb = [s for s in flight_data_statements(template) if "dynamodb:" in str(s["Action"])]

    assert [s["Sid"] for s in dynamodb] == ["FlightTables"]
    (tables,) = dynamodb
    assert {"dynamodb:GetItem", "dynamodb:UpdateItem"} <= set(actions(tables))
    assert {"Fn::GetAtt": "SubscriptionsTable.Arn"} in tables["Resource"]
    assert "*" not in str(tables["Resource"])


def test_ecpay_return_is_told_the_status_queue_name(template):
    env = function_named(template, "flight-ecpay-return")["Properties"]["Environment"]["Variables"]

    assert env["STATUS_QUEUE_NAME"] == {"Fn::GetAtt": "FlightStatusQueue.QueueName"}


# ------------------------------------------- environments: prod + an isolated dev stack
# One template, two stacks: `flight-m1` (Env=prod) and `flight-dev` (Env=dev). Prod's names
# must resolve to exactly what is deployed today: renaming a table, queue, bucket or role in
# CloudFormation REPLACES it (a new, empty table).

NAMED = {
    "AWS::DynamoDB::Table": "TableName",
    "AWS::SQS::Queue": "QueueName",
    "AWS::IAM::Role": "RoleName",
    "AWS::Lambda::Function": "FunctionName",
    "AWS::S3::Bucket": "BucketName",
    "AWS::Events::Rule": "Name",
    "AWS::ApiGatewayV2::Api": "Name",
}

PROD_NAMES = {
    "SubscriptionsTable": "subscriptions",
    "NotificationHistoryTable": "notification_history",
    "FlightFareQueue": "flight-fare-queue",
    "FlightStatusQueue": "flight-status-queue",
    "FlightLambdaRole": "flight-lambda-role",
    "FlightConfigBucket": {"Fn::Sub": "flight-config-${AWS::AccountId}"},
    "FlightPriceCheckRule": "flight-price-check",
    "FlightApi": "flight-api",
    "SaveSubscriptionFunction": "flight-save-subscription",
    "ParserFunction": "flight-parser",
    "ParserWrapperFunction": "flight-parser-wrapper",
    "FareNotificationFunction": "flight-fare-notification",
    "ListSubscriptionsFunction": "flight-list-subscriptions",
}


def named_resources(template):
    return {
        name: r["Properties"][NAMED[r["Type"]]]
        for name, r in template["Resources"].items()
        if r["Type"] in NAMED
    }


def test_env_parameter_defaults_to_prod(template):
    env = template["Parameters"]["Env"]

    assert env["AllowedValues"] == ["prod", "dev"]
    assert env["Default"] == "prod"
    assert template["Conditions"]["IsDev"] == {"Fn::Equals": [{"Ref": "Env"}, "dev"]}


def test_prod_keeps_every_already_deployed_name(template):
    names = named_resources(template)

    assert {k: resolve(names[k], "prod") for k in PROD_NAMES} == PROD_NAMES


def test_every_dev_name_differs_from_prod(template):
    for name, value in named_resources(template).items():
        assert resolve(value, "dev") != resolve(value, "prod"), name
        assert "dev" in str(resolve(value, "dev")), name


def test_each_environment_reads_only_its_own_secrets(template):
    secrets = statement(template, "FlightSecrets")["Resource"]
    arn = "arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:"

    assert resolve(secrets, "prod") == {"Fn::Sub": arn + "flight/*"}
    assert resolve(secrets, "dev") == {"Fn::Sub": arn + "flight-dev/*"}  # flight/* can't match it


def test_parser_wrapper_may_only_invoke_its_own_environments_parser(template):
    invoke = statement(template, "FlightParserInvoke")["Resource"]
    arn = "arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${Name}"

    assert resolve(invoke, "prod") == {"Fn::Sub": [arn, {"Name": "flight-parser"}]}
    assert resolve(invoke, "dev") == {"Fn::Sub": [arn, {"Name": "flight-parser-dev"}]}


DATA_LAMBDAS = [
    "flight-save-subscription", "flight-parser", "flight-fare-notification", "flight-list-subscriptions",
    "flight-cancel-subscription", "flight-ecpay-return", "flight-ecpay-period", "flight-status-notification",
]


@pytest.mark.parametrize("function_name", DATA_LAMBDAS)
def test_data_lambdas_are_told_their_environments_tables_and_secrets(template, function_name):
    env = function_named(template, function_name)["Properties"]["Environment"]["Variables"]

    assert env["SUBSCRIPTIONS_TABLE"] == {"Ref": "SubscriptionsTable"}
    assert env["HISTORY_TABLE"] == {"Ref": "NotificationHistoryTable"}
    assert resolve(env["SECRETS_PREFIX"], "prod") == "flight/"
    assert resolve(env["SECRETS_PREFIX"], "dev") == "flight-dev/"


def test_the_scheduled_price_check_only_runs_in_prod(template):
    state = template["Resources"]["FlightPriceCheckRule"]["Properties"]["State"]

    assert resolve(state, "prod") == "ENABLED"
    assert resolve(state, "dev") == "DISABLED"  # invoke flight-parser-wrapper-dev by hand


def test_site_url_may_be_a_local_dev_server(template):
    import re

    pattern = template["Parameters"]["SiteUrl"]["AllowedPattern"]
    assert re.match(pattern, "http://localhost:8080")
    assert re.match(pattern, "https://flight.example.vercel.app")


# ------------------------------------------- /checkout and /version


def test_checkout_and_subscribe_both_go_to_save_subscription(template):
    assert api_route_function(template, "POST /subscribe") == "SaveSubscriptionFunction"
    assert api_route_function(template, "POST /checkout") == "SaveSubscriptionFunction"

    source_arns = [
        p["Properties"]["SourceArn"] for p in resources_of_type(template, "AWS::Lambda::Permission").values()
        if p["Properties"]["FunctionName"] == {"Ref": "SaveSubscriptionFunction"}
    ]
    prefix = "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${FlightApi}/*/*/"
    assert sorted(a["Fn::Sub"] for a in source_arns) == [prefix + "checkout", prefix + "subscribe"]


def test_version_endpoint_reports_the_stamped_backend_version(template):
    fn_name = api_route_function(template, "GET /version")
    fn = template["Resources"][fn_name]["Properties"]

    assert resolve(fn["FunctionName"]) == "flight-version"
    assert fn["Code"] == "../lambdas/flight-version/"
    assert fn["Environment"]["Variables"] == {
        "BACKEND_VERSION": {"Ref": "BackendVersion"},
        "STACK_ENV": {"Ref": "Env"},
    }
    assert template["Parameters"]["BackendVersion"]["Default"] == "unknown"
