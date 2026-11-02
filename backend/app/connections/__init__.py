"""Connecting a user's own AWS account so the agent can read their real alarms, metrics, logs and changes (Phase 16).

The user creates a read-only IAM role in their account from a template this package renders; AgentOps only ever
stores the role's ARN, the region, the log groups they chose and a random ExternalId. No access keys are involved.
"""
