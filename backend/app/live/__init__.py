"""Reading a user's own AWS account (Phase 16B), read-only, through the role they created.

The same four diagnostic tools the demo uses, backed by their CloudWatch alarms, metrics, log groups and CloudTrail
change events instead of fixtures. Everything the agent may do is still decided above this package: it can supply
different data, never a wider set of tools, and nothing here can write to the account.
"""
