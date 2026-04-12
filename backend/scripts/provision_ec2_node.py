#!/usr/bin/env python3
"""
Provision one EC2 instance and insert a PROVISIONING :class:`~app.services.placement_service.models.ExecutionNode`.

Uses ``DEVNEST_EC2_*`` and ``AWS_REGION`` unless CLI flags override. After AWS reports ``running``,
call ``sync`` (CLI ``--sync`` or ``POST /internal/execution-nodes/sync``) to refresh metadata and
promote to ``READY`` when SSM is online (``ssm_docker``).

Run from the ``backend`` directory::

    PYTHONPATH=. python scripts/provision_ec2_node.py
    PYTHONPATH=. python scripts/provision_ec2_node.py --sync

Requires ``DATABASE_URL`` and IAM permission for ``ec2:RunInstances``, ``ec2:CreateTags``, etc.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision an EC2-backed DevNest execution node.")
    parser.add_argument("--ami-id", default=None, help="Override DEVNEST_EC2_AMI_ID")
    parser.add_argument("--instance-type", default=None, help="Override DEVNEST_EC2_INSTANCE_TYPE")
    parser.add_argument("--subnet-id", default=None, help="Override DEVNEST_EC2_SUBNET_ID")
    parser.add_argument(
        "--security-group-ids",
        default=None,
        help="Comma-separated SG ids (override DEVNEST_EC2_SECURITY_GROUP_IDS)",
    )
    parser.add_argument("--iam-instance-profile", default=None, help="Override DEVNEST_EC2_INSTANCE_PROFILE")
    parser.add_argument("--key-name", default=None, help="Override DEVNEST_EC2_KEY_NAME")
    parser.add_argument("--region", default=None, help="Override AWS_REGION")
    parser.add_argument("--node-key", default=None, help="Explicit ExecutionNode.node_key")
    parser.add_argument("--name-tag", default=None, help="EC2 Name tag")
    parser.add_argument(
        "--execution-mode",
        default=None,
        choices=["ssm_docker", "ssh_docker"],
        help="Execution mode for the new row",
    )
    parser.add_argument(
        "--no-wait-running",
        action="store_true",
        help="Do not block on EC2 instance_running waiter",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="After provision, run sync_node_state (SSM readiness for ssm_docker)",
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="With --sync, only refresh EC2 fields; do not promote PROVISIONING → READY",
    )
    args = parser.parse_args()

    from dataclasses import replace

    from sqlmodel import Session

    from app.libs.db.database import get_engine
    from app.services.infrastructure_service import (
        Ec2ProvisionConfigurationError,
        Ec2ProvisionRequest,
        NodeLifecycleError,
        provision_ec2_node,
        sync_node_state,
    )
    from app.services.providers.errors import Ec2ProviderError

    req = Ec2ProvisionRequest.from_settings()
    if args.ami_id:
        req = replace(req, ami_id=args.ami_id.strip())
    if args.instance_type:
        req = replace(req, instance_type=args.instance_type.strip())
    if args.subnet_id:
        req = replace(req, subnet_id=args.subnet_id.strip())
    if args.security_group_ids:
        sg = [x.strip() for x in args.security_group_ids.split(",") if x.strip()]
        req = replace(req, security_group_ids=sg)
    if args.iam_instance_profile:
        v = args.iam_instance_profile.strip()
        req = replace(req, iam_instance_profile_name=v or None)
    if args.key_name:
        v = args.key_name.strip()
        req = replace(req, key_name=v or None)
    if args.region:
        v = args.region.strip()
        req = replace(req, region=v or None)
    if args.node_key:
        req = replace(req, node_key=args.node_key.strip())
    if args.name_tag:
        req = replace(req, name_tag=args.name_tag.strip())
    if args.execution_mode:
        req = replace(req, execution_mode=args.execution_mode.strip())

    engine = get_engine()
    try:
        with Session(engine) as session:
            node = provision_ec2_node(
                session,
                req,
                wait_until_running=not args.no_wait_running,
            )
            session.commit()
            session.refresh(node)
            print(
                f"Provisioned execution node id={node.id} node_key={node.node_key!r} "
                f"instance_id={node.provider_instance_id!r} status={node.status!r}",
            )
            if args.sync:
                sync_node_state(
                    session,
                    node_id=node.id,
                    promote_provisioning_when_ready=not args.no_promote,
                )
                session.commit()
                session.refresh(node)
                print(
                    f"After sync: status={node.status!r} schedulable={node.schedulable} "
                    f"private_ip={node.private_ip!r}",
                )
    except Ec2ProvisionConfigurationError as e:
        print(f"Invalid provisioning configuration: {e}", file=sys.stderr)
        return 1
    except (Ec2ProviderError, NodeLifecycleError) as e:
        print(f"Provisioning failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
