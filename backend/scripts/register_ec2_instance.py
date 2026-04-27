#!/usr/bin/env python3
"""
Register an existing EC2 instance as a DevNest execution node (control plane).

Requires ``DATABASE_URL`` and AWS credentials (env, shared config, or instance role).
Run from the ``backend`` directory::

    PYTHONPATH=. python scripts/register_ec2_instance.py i-0123456789abcdef0

Optional env: ``AWS_REGION``, ``DEVNEST_EC2_SSH_USER_DEFAULT`` (for ``ssh_docker``),
``DEVNEST_EC2_DEFAULT_EXECUTION_MODE`` (``ssm_docker`` default, or ``ssh_docker``),
``DEVNEST_EXECUTION_MODE`` (worker override; see settings).

See also: ``scripts/provision_ec2_node.py`` (new instances) and
``POST /internal/execution-nodes/register-existing`` (``X-Internal-API-Key``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add backend root so ``app`` resolves when run as ``python scripts/register_ec2_instance.py``.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Register an EC2 instance as a DevNest execution node.")
    parser.add_argument("instance_id", help="EC2 instance id (e.g. i-0123456789abcdef0)")
    parser.add_argument("--node-key", default=None, help="Override ExecutionNode.node_key (default ec2-<instance_id>)")
    parser.add_argument("--ssh-user", default=None, help="SSH user for ssh_docker (default from settings)")
    parser.add_argument(
        "--execution-mode",
        default=None,
        choices=["ssm_docker", "ssh_docker"],
        help="ExecutionNode.execution_mode (default from DEVNEST_EC2_DEFAULT_EXECUTION_MODE)",
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help=(
            "Phase 3b Step 4: register EC2 metadata + capacity but force schedulable=false "
            "(scheduler will not place new workspaces; no routing changes)."
        ),
    )
    args = parser.parse_args()

    from sqlmodel import Session

    from app.libs.db.database import get_engine
    from app.services.providers.errors import Ec2ProviderError
    from app.services.providers.ec2_provider import register_ec2_instance

    engine = get_engine()
    try:
        with Session(engine) as session:
            node = register_ec2_instance(
                session,
                args.instance_id.strip(),
                node_key=args.node_key,
                ssh_user=args.ssh_user,
                execution_mode=args.execution_mode,
                catalog_only=bool(args.catalog_only),
            )
            session.commit()
            suffix = " catalog_only=schedulable_forced_false" if args.catalog_only else ""
            print(
                f"Registered execution node id={node.id} node_key={node.node_key!r} "
                f"provider_instance_id={node.provider_instance_id!r} execution_mode={node.execution_mode!r} "
                f"status={node.status!r} schedulable={node.schedulable}{suffix}",
            )
    except Ec2ProviderError as e:
        print(f"EC2 registration failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
