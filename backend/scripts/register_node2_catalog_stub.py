#!/usr/bin/env python3
"""
Insert/update execution_node for node-2 as EC2 catalog-only (Phase 3b Step 4).

No AWS calls. Always schedulable=false. Replace provider_instance_id / IPs when the real instance exists,
then use register_ec2_instance.py with --catalog-only or POST /register-existing + catalog_only.

Run from backend/::

    PYTHONPATH=. python scripts/register_node2_catalog_stub.py \\
      --private-ip 10.0.2.10 \\
      --public-ip 1.2.3.4 \\
      --provider-instance-id i-0123456789abcdef0

Omit optional flags to use placeholders suitable for an empty fleet row before EC2 exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="Register node-2 EC2 catalog stub (no AWS, schedulable=false).")
    p.add_argument("--node-key", default="node-2", help="ExecutionNode.node_key")
    p.add_argument("--name", default="node 2 (catalog)", help="Display name")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--private-ip", default="", help="Node 2 private IP (empty = omit)")
    p.add_argument("--public-ip", default="", help="Node 2 public IP if applicable (empty = omit)")
    p.add_argument(
        "--provider-instance-id",
        default="",
        help="Real EC2 instance id, or leave empty for catalog-pending:<node_key>",
    )
    p.add_argument("--execution-mode", default="ssm_docker", choices=["ssm_docker", "ssh_docker"])
    p.add_argument("--status", default="NOT_READY", choices=["NOT_READY", "READY"])
    p.add_argument(
        "--align-heartbeat-status",
        action="store_true",
        help="Set READY from last_heartbeat_at age instead of --status",
    )
    args = p.parse_args()

    from sqlmodel import Session

    from app.libs.db.database import get_engine
    from app.services.infrastructure_service.errors import NodeLifecycleError
    from app.services.infrastructure_service.lifecycle import register_catalog_ec2_stub

    engine = get_engine()
    try:
        with Session(engine) as session:
            node = register_catalog_ec2_stub(
                session,
                node_key=args.node_key.strip(),
                name=args.name.strip() or None,
                provider_instance_id=args.provider_instance_id.strip() or None,
                private_ip=args.private_ip.strip() or None,
                public_ip=args.public_ip.strip() or None,
                region=args.region.strip() or None,
                execution_mode=args.execution_mode,
                status=None if args.align_heartbeat_status else args.status,
                align_status_with_heartbeat=bool(args.align_heartbeat_status),
            )
            session.commit()
            print(
                f"Catalog EC2 node id={node.id} node_key={node.node_key!r} "
                f"provider_instance_id={node.provider_instance_id!r} "
                f"status={node.status!r} schedulable={node.schedulable}",
            )
    except NodeLifecycleError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
