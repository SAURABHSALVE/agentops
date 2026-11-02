"""The CloudFormation template a user deploys in their own account to create the read-only role.

It is built as a data structure and emitted as JSON (which CloudFormation accepts), never by pasting strings into
YAML, so a log group name cannot add a statement. Every input has already been checked against a closed pattern in
models.py; the shape of the output is checked again in the tests.
"""

from typing import Any

from app.connections.models import ROLE_NAME, AwsConnection
from app.connections.permissions import PERMISSIONS, Permission

TEMPLATE_FILE = "agentops-read-only-role.template.json"
SESSION_SECONDS = 3600  # the shortest an IAM role may allow is 1 hour; the app asks for 15 minutes at a time


def _resources(permission: Permission, connection: AwsConnection) -> Any:
    if permission.applies_to == "account":
        return "*"
    # The account id comes from the stack itself, so the template works in whichever account it is deployed to.
    return [
        {"Fn::Sub": f"arn:aws:logs:{connection.region}:${{AWS::AccountId}}:log-group:{name}:*"}
        for name in connection.log_groups
    ]


def render_template(connection: AwsConnection, *, principal_arn: str) -> dict[str, Any]:
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": (
            "AgentOps AI: a read-only role so AgentOps can look at alarms, metrics, recent changes and the log groups "
            "you chose. It cannot change anything. Delete this stack to remove all of its access."
        ),
        "Resources": {
            "AgentOpsReadOnlyRole": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "RoleName": ROLE_NAME,
                    "Description": "Read-only access for AgentOps AI. Delete the stack to revoke.",
                    "MaxSessionDuration": SESSION_SECONDS,
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": principal_arn},
                                "Action": "sts:AssumeRole",
                                # Only a caller that presents this value may use the role, so another AgentOps user
                                # cannot point AgentOps at your role.
                                "Condition": {"StringEquals": {"sts:ExternalId": connection.external_id}},
                            }
                        ],
                    },
                    "Policies": [
                        {
                            "PolicyName": "ReadOnlyForAgentOps",
                            "PolicyDocument": {
                                "Version": "2012-10-17",
                                "Statement": [
                                    {
                                        "Sid": p.sid,
                                        "Effect": "Allow",
                                        "Action": list(p.actions),
                                        "Resource": _resources(p, connection),
                                    }
                                    for p in PERMISSIONS
                                ],
                            },
                        }
                    ],
                    "Tags": [{"Key": "Purpose", "Value": "AgentOps AI read-only access"}],
                },
            }
        },
        "Outputs": {
            "RoleArn": {
                "Description": "Copy this into AgentOps, step 3.",
                "Value": {"Fn::GetAtt": ["AgentOpsReadOnlyRole", "Arn"]},
            }
        },
    }
