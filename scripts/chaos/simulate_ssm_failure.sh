#!/usr/bin/env bash
# Document / partially automate SSM SendCommand failure injection for SSM-backed execution nodes.
#
# DevNest path: SsmDockerRuntimeAdapter / SsmRemoteCommandRunner → boto3 SSM.
# There is no single global kill-switch; failures surface as orchestrator/workspace job errors.
#
# Safe simulation options (pick one):
#
# A) IAM denial (recommended for staging):
#    Attach an explicit deny on ssm:SendCommand for the instance role for the duration of the test.
#
# B) Network partition:
#    Security group: egress deny on HTTPS (443) from instance to prevent SSM data plane (VPC-dependent).
#
# C) Unit-level:
#    pytest failure_path tests mock SSM runner — see backend/tests for SSM adapter mocks.
#
# Logs to capture:
#   - workspace.job.failed / orchestrator.* failures referencing SSM / RemoteCommand / runner
#   - workspace.job.retry_scheduled when retries remain
#
# Usage: prints this guide (no destructive actions by default).
#
set -euo pipefail

cat <<'EOF'
[chaos] SSM failure injection — manual runbook (no changes performed)

1) IAM deny (time-bounded)
   - Snapshot current role policies.
   - Add explicitDeny for ssm:SendCommand on the EC2 instance profile used by execution nodes.
   - Trigger workspace START or orchestrator path that uses SSM.
   - Observe workspace/job failure + optional retry_scheduled.
   - Remove deny policy.

2) Network egress deny
   - On the execution subnet/SG, temporarily block outbound 443 from the instance (careful: breaks updates too).

3) Automated tests
   - backend/tests/unit/node_execution_service/test_orchestrator_ssm_wiring.py (mocked runner)
   - Extend failure_path integration if you add SSM-localstack harness.

Grep logs:
  rg 'workspace\\.job\\.(failed|retry_scheduled)|SSM|SendCommand|RemoteCommand' logs/

Revert: restore IAM/SG before leaving the environment inconsistent.
EOF
