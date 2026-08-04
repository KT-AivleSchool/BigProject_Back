import json
import asyncio
import sys
from pathlib import Path

# Add the project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.session import AsyncSessionLocal
from app.db.models.audit import AuditRule
from sqlalchemy import text

async def main():
    json_path = Path(__file__).resolve().parent.parent / "dummy_audit.json"
    if not json_path.exists():
        print(f"File not found: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        audit_data = json.load(f)

    rules_to_insert = []
    for result in audit_data.get("results", []):
        dataset_id = result.get("dataset_id")
        for role in result.get("roles", []):
            facility_type = role.get("facility_type", result.get("summary", "")[:100])
            role_type = role.get("role")
            weight = role.get("weight")
            rationale = role.get("rationale")
            source = role.get("source")

            rule = AuditRule(
                dataset_id=dataset_id,
                facility_type=facility_type,
                role_type=role_type,
                weight=weight if weight is not None else 0.0,
                rationale=rationale,
                source=source
            )
            rules_to_insert.append(rule)

    async with AsyncSessionLocal() as session:
        # First, clear existing rules to prevent duplicates
        await session.execute(text("TRUNCATE TABLE audit_rules"))
        
        # Add new rules
        session.add_all(rules_to_insert)
        await session.commit()
    
    print(f"Successfully loaded {len(rules_to_insert)} audit rules into the database.")

if __name__ == "__main__":
    asyncio.run(main())
