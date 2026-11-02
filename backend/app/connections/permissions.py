"""Everything the role in the user's account is allowed to do, in one place.

The CloudFormation template is built from this list and so is the "what will you be able to see?" screen, so the two
cannot disagree. Every action here is a read. A test fails if an action outside this list, or any non-read verb,
reaches the template, and if the list grows without the template growing with it.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Permission:
    id: str
    sid: str  # the statement id in the policy (letters and digits only)
    title: str
    why: str
    actions: tuple[str, ...]
    # "account": the API has no per-resource form, so AWS only accepts `*`. "log_groups": limited to the log groups
    # the user listed.
    applies_to: Literal["account", "log_groups"]
    reads: str
    never: str


PERMISSIONS: tuple[Permission, ...] = (
    Permission(
        id="alarms",
        sid="Alarms",
        title="See which alarms are firing",
        why="The alarms that are in trouble are the incidents to investigate.",
        actions=("cloudwatch:DescribeAlarms", "cloudwatch:DescribeAlarmHistory"),
        applies_to="account",
        reads="Alarm names, state, threshold, the reason text and when each changed state.",
        never="Does not create, change, mute or delete an alarm.",
    ),
    Permission(
        id="metrics",
        sid="Metrics",
        title="Read metric graphs",
        why="A metric that stayed high for a while is the strongest evidence of a real problem.",
        actions=("cloudwatch:GetMetricData", "cloudwatch:ListMetrics"),
        applies_to="account",
        reads="Numeric CloudWatch metrics (CPU, errors, latency, queue depth) for the last few hours.",
        never="Does not publish metrics. Metrics are numbers; they hold no log text or customer data.",
    ),
    Permission(
        id="logs_search",
        sid="LogsSearch",
        title="Search the log groups you list",
        why="Error messages in the logs usually name the cause.",
        actions=("logs:StartQuery",),
        applies_to="log_groups",
        reads="Log lines from the groups you listed, over a short time window, a limited number at a time.",
        never="Cannot read any other log group, and cannot write, delete or export logs.",
    ),
    Permission(
        id="logs_results",
        sid="LogsResults",
        title="Collect the results of those searches",
        why="A log search runs in the background; this fetches its answer, or cancels it.",
        actions=("logs:GetQueryResults", "logs:StopQuery"),
        applies_to="account",
        reads="The answer to a search AgentOps itself started (a search can only be started on your listed groups).",
        never="Does not give access to any log group by itself.",
    ),
    Permission(
        id="changes",
        sid="Changes",
        title="See what changed recently",
        why="A deploy or configuration change shortly before an incident is a classic cause.",
        actions=("cloudtrail:LookupEvents",),
        applies_to="account",
        reads="When something changed, which service, and who or what changed it (management events, last 90 days).",
        never="AgentOps keeps only the time, event name, service and actor. It does not pass on the request details.",
    ),
)


def all_actions() -> frozenset[str]:
    return frozenset(a for p in PERMISSIONS for a in p.actions)
