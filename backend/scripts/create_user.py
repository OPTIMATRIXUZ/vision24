import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.errors import Vision24Error
from app.models import ROLES, Tenant
from app.services import accounts


def read_password(confirm: bool) -> str:
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            raise SystemExit("No password on stdin.")
        return password
    password = getpass.getpass("Password: ")
    if confirm and password != getpass.getpass("Confirm password: "):
        raise SystemExit("The passwords did not match.")
    return password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None, help="the person's full name")
    parser.add_argument("--role", default=None, choices=list(ROLES))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--company", help="create a NEW tenant with this name, and own it")
    group.add_argument("--tenant", help="slug of an EXISTING tenant to add this user to")
    parser.add_argument("--no-confirm", action="store_true", help="skip the confirmation prompt")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.company and not args.tenant:
        parser.error("pass --company to create a tenant, or --tenant <slug> to join one")

    password = read_password(confirm=not args.no_confirm)

    with SessionLocal() as db:
        try:
            if args.company:
                user = accounts.register_tenant(
                    db,
                    email=args.email,
                    password=password,
                    company_name=args.company,
                    full_name=args.name,
                )
                print(f"Created tenant {args.company!r} with owner {user.email}")
            else:
                tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == args.tenant))
                if tenant_id is None:
                    slugs = db.scalars(select(Tenant.slug).order_by(Tenant.slug)).all()
                    known = ", ".join(slugs) or "(none — use --company)"
                    raise SystemExit(f"No tenant with slug {args.tenant!r}. Known slugs: {known}")
                user = accounts.create_user(
                    db,
                    tenant_id,
                    email=args.email,
                    password=password,
                    role=args.role or "admin",
                    full_name=args.name,
                )
                print(f"Added {user.email} to {args.tenant!r} as {user.role}")
        except Vision24Error as exc:
            raise SystemExit(str(exc)) from exc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
