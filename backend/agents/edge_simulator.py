import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy import select

from core.config import settings
from core.database import SessionLocal
from models import Asset, Plant, SensorData
from services.simulator_scenarios import process_scenario_tick
from services.telemetry_ingest import update_asset_condition


async def seed_demo_assets() -> None:
    demo_plants = [
        {"code": "PLT-01", "name": "Plant 01", "location": "North Works", "region": "North"},
        {"code": "PLT-02", "name": "Plant 02", "location": "Riverbend Process", "region": "West"},
        {"code": "PLT-03", "name": "Plant 03", "location": "East Utilities", "region": "East"},
        {"code": "PLT-04", "name": "Plant 04", "location": "Central Production", "region": "Central"},
    ]
    demo_assets = [
        {"name": "Induction Pump 01", "asset_tag": "PMP-001", "asset_type": "Centrifugal pump", "location": "Line A / Bay 2", "criticality": "high", "plant_code": "PLT-01"},
        {"name": "Transfer Pump 05", "asset_tag": "PMP-005", "asset_type": "Process pump", "location": "Line A / Bay 4", "criticality": "medium", "plant_code": "PLT-01"},
        {"name": "Compressor 02", "asset_tag": "CMP-002", "asset_type": "Air compressor", "location": "Utilities / North", "criticality": "critical", "plant_code": "PLT-02"},
        {"name": "Boiler Feed Pump 06", "asset_tag": "PMP-006", "asset_type": "Boiler feed pump", "location": "Boiler House / Bay 1", "criticality": "high", "plant_code": "PLT-02"},
        {"name": "Conveyor Drive 03", "asset_tag": "DRV-003", "asset_type": "Gear motor", "location": "Line B / Bay 1", "criticality": "medium", "plant_code": "PLT-03"},
        {"name": "Cooling Water Pump 07", "asset_tag": "PMP-007", "asset_type": "Cooling water pump", "location": "Cooling Loop / East", "criticality": "high", "plant_code": "PLT-03"},
        {"name": "Cooling Tower Fan 04", "asset_tag": "FAN-004", "asset_type": "Cooling fan", "location": "Cooling plant / Roof", "criticality": "medium", "plant_code": "PLT-04"},
        {"name": "Chiller Compressor 08", "asset_tag": "CHL-008", "asset_type": "Chiller compressor", "location": "HVAC / Plant Room", "criticality": "critical", "plant_code": "PLT-04"},
    ]
    async with SessionLocal() as session:
        plants_by_code = {plant.code: plant for plant in (await session.scalars(select(Plant))).all()}
        for plant_data in demo_plants:
            if plant_data["code"] not in plants_by_code:
                plant = Plant(**plant_data)
                session.add(plant)
                plants_by_code[plant.code] = plant
        await session.flush()

        existing_assets = {asset.asset_tag: asset for asset in (await session.scalars(select(Asset))).all()}
        for asset_data in demo_assets:
            plant_id = plants_by_code[asset_data["plant_code"]].id
            asset = existing_assets.get(asset_data["asset_tag"])
            if asset is None:
                values = {key: value for key, value in asset_data.items() if key != "plant_code"}
                session.add(Asset(**values, plant_id=plant_id))
            elif asset.plant_id is None:
                asset.plant_id = plant_id
        await session.commit()


def sample_reading() -> tuple[float, float, float, float]:
    anomaly = random.random() < 0.07
    vibration = random.uniform(5.0, 10.5) if anomaly else random.uniform(1.1, 4.6)
    temperature = random.uniform(78, 96) if anomaly else random.uniform(48, 73)
    return vibration, temperature, random.uniform(2.8, 4.1), random.uniform(1420, 1490)


async def simulator_loop(interval_seconds: float) -> None:
    while True:
        async with SessionLocal() as session:
            assets = (await session.scalars(select(Asset).order_by(Asset.id).with_for_update())).all()
            now = datetime.now(timezone.utc)
            for asset in assets:
                if await process_scenario_tick(session, asset, now):
                    continue
                vibration, temperature, pressure, rpm = sample_reading()
                session.add(SensorData(
                    asset_id=asset.id,
                    timestamp=now,
                    vibration_mm_s=vibration,
                    temperature_c=temperature,
                    pressure_bar=pressure,
                    rpm=rpm,
                    source="edge-simulator",
                ))
                update_asset_condition(asset, vibration, temperature, now)
            await session.commit()
        await asyncio.sleep(max(1.0, interval_seconds))
