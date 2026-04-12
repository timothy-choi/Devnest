#!/usr/bin/env python3
"""
Register an existing EC2 instance as a DevNest execution node (control plane).

Requires ``DATABASE_URL`` and AWS credentials (env, shared config, or instance role).
Run from the ``backend`` directory::

    PYTHONPATH=. python scripts/register_ec2_instance.py i-0123456789abcdef0

Optional: ``AWS_REGION``, ``DEVNEST_EC2_SSH_USER_DEFAULT`` (default ``ubuntu``).

TODO: Internal admin API + auth; sync job for periodic refresh.
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
    args = parser.parse_args()

    from sqlmodel import Session

    from app.libs.db.database import get_engine
    from app.services.providers.ec2_provider import register_ec2_instance

    engine = get_engine()
    with Session(engine) as session:
        node = register_ec2_instance(
            session,
            args.instance_id.strip(),
            node_key=args.node_key,
            ssh_user=args.ssh_user,
        )
        session.commit()
        print(
            f"Registered execution node id={node.id} node_key={node.node_key!r} "
            f"provider_instance_id={node.provider_instance_id!r} status={node.status!r} "
            f"schedulable={node.schedulable}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
