from datetime import time

from sqlalchemy import select

from app import storage
from app.db import SessionLocal
from app.models import Site, Tenant


def run() -> None:
    storage.ensure_bucket()

    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant).where(Tenant.slug == "demo")).first()
        if tenant is None:
            tenant = Tenant(name="demo", slug="demo")
            db.add(tenant)
            db.flush()

        site = db.scalars(select(Site).where(Site.tenant_id == tenant.id)).first()
        if site is None:
            site = Site(
                tenant_id=tenant.id,
                name="Demo Store",
                timezone="Asia/Tashkent",
                closing_time=time(21, 0),
            )
            db.add(site)

        db.commit()
        print(f"Seeded tenant={tenant.name} site={site.name}")


if __name__ == "__main__":
    run()
