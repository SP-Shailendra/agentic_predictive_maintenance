import asyncio
import hashlib
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.workflow_graph import workflow_graph
from core.config import settings
from core.database import Base, get_db
from core.migrations import upgrade_database
from core.security import Principal, get_current_principal
from main import app
from models import Asset, AssetThresholdChange, CMMSOutbox, KnowledgeDocument, NotificationOutbox, Plant, SensorData, SimulatorScenarioEvent, WorkOrder, WorkflowEvent
from services import approval_sla_worker, notification_worker
from services import outbox_worker
from services import ingestion_service, rag_service
from services.document_loader import DocumentLoadError, DocumentSegment
from services.rag_evaluation import score_retrieval_suite
from services.workflow_notifications import enqueue_workflow_notification

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL/TimescaleDB database.")
class WorkflowPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert TEST_DATABASE_URL is not None
        self.engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade_database)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.original_overrides = app.dependency_overrides.copy()
        self.principal = Principal(
            subject="test-approver",
            roles=frozenset({"approver", "operator"}),
            verified=True,
            auth_method="oidc",
        )
        app.dependency_overrides[get_current_principal] = lambda: self.principal

        async def override_db():
            async with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        async with self.sessions() as session:
            async with session.begin():
                plant = Plant(
                    code=f"IT-{uuid.uuid4().hex[:8]}",
                    name="Integration plant",
                    location="Test location",
                    region="Test",
                )
                session.add(plant)
                await session.flush()
                asset = Asset(
                    plant_id=plant.id,
                    name="Integration asset",
                    asset_tag=f"IT-{uuid.uuid4().hex[:8]}",
                    asset_type="Test pump",
                    location="Test bay",
                    status="operational",
                    criticality="medium",
                )
                session.add(asset)
                await session.flush()
                self.asset_id = asset.id

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)
        await self.engine.dispose()

    async def _create_awaiting_order(self, created_by_sub: str | None = "requester") -> uuid.UUID:
        async with self.sessions() as session:
            async with session.begin():
                order = WorkOrder(
                    asset_id=self.asset_id,
                    status="awaiting_approval",
                    summary="Integration approval test",
                    diagnosis="Test diagnosis",
                    recommendation="Inspect the asset",
                    created_by_sub=created_by_sub,
                    workflow_state={
                        "alert_id": "pending",
                        "approved": False,
                        "diagnosis": "Test diagnosis",
                        "diagnosis_confidence": 0.5,
                        "recommendation": "Inspect the asset",
                        "compliance_check": {"compliant": True, "checks": []},
                        "activity": [],
                    },
                )
                session.add(order)
                await session.flush()
                order.workflow_state["alert_id"] = str(order.id)
                return order.id

    async def test_parallel_approvals_dispatch_once(self) -> None:
        work_order_id = await self._create_awaiting_order()
        first_entered_graph = asyncio.Event()
        release_first_graph = asyncio.Event()
        graph_calls = 0

        async def queue_graph(state):
            nonlocal graph_calls
            graph_calls += 1
            if graph_calls == 1:
                first_entered_graph.set()
                await release_first_graph.wait()
            return {
                **state,
                "status": "dispatch_queued",
                "approved": True,
                "compliance_check": {"compliant": True, "checks": []},
            }

        with patch.object(workflow_graph, "ainvoke", side_effect=queue_graph):
            first_request = asyncio.create_task(self.client.post(f"/api/workflows/{work_order_id}/approve", json={"comment": "first"}))
            await asyncio.wait_for(first_entered_graph.wait(), timeout=5)
            second_request = asyncio.create_task(self.client.post(f"/api/workflows/{work_order_id}/approve", json={"comment": "second"}))
            await asyncio.sleep(0.05)
            release_first_graph.set()
            responses = await asyncio.gather(first_request, second_request)

        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        async with self.sessions() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(CMMSOutbox).where(CMMSOutbox.work_order_id == work_order_id)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(WorkflowEvent).where(WorkflowEvent.work_order_id == work_order_id)), 2)

    async def test_verified_creator_cannot_approve_own_work_order(self) -> None:
        work_order_id = await self._create_awaiting_order(created_by_sub="test-approver")
        response = await self.client.post(f"/api/workflows/{work_order_id}/approve", json={"comment": "self approve"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot approve", response.json()["detail"])

    async def test_start_workflow_rejects_duplicate_open_investigation(self) -> None:
        existing_id = await self._create_awaiting_order()
        for status in ("processing", "awaiting_approval", "dispatch_queued", "dispatching", "dispatch_retry", "dispatched", "in_progress"):
            async with self.sessions() as session:
                await session.execute(update(WorkOrder).where(WorkOrder.id == existing_id).values(status=status))
                await session.commit()
            response = await self.client.post("/api/workflows", json={
                "asset_id": str(self.asset_id),
                "alert_type": "High vibration",
                "severity": "high",
                "message": "Retry duplicate investigation.",
                "telemetry": {"vibration_mm_s": 8.0},
            })
            self.assertEqual(response.status_code, 409, status)
            self.assertIn(str(existing_id), response.json()["detail"], status)

    async def test_terminal_workflows_do_not_block_new_investigation(self) -> None:
        previous_id = await self._create_awaiting_order()

        async def assess(state):
            return {**state, "status": "awaiting_approval", "diagnosis": "Test", "recommendation": "Inspect"}

        payload = {
            "asset_id": str(self.asset_id), "alert_type": "High vibration", "severity": "high",
            "message": "New investigation after terminal workflow.", "telemetry": {"vibration_mm_s": 8.0},
        }
        with patch.object(workflow_graph, "ainvoke", side_effect=assess):
            for terminal_status in ("rejected", "completed", "closed", "blocked"):
                async with self.sessions() as session:
                    await session.execute(update(WorkOrder).where(WorkOrder.id == previous_id).values(status=terminal_status))
                    await session.commit()
                response = await self.client.post("/api/workflows", json=payload)
                self.assertEqual(response.status_code, 201, terminal_status)
                previous_id = uuid.UUID(response.json()["id"])
                async with self.sessions() as session:
                    await session.execute(update(WorkOrder).where(WorkOrder.id == previous_id).values(status=terminal_status))
                    await session.commit()

    async def test_workflow_persists_only_retrieved_citations_to_state_and_audit(self) -> None:
        async def assess(state):
            return {
                **state,
                "diagnosis": "Possible bearing wear; inspect before replacing parts.",
                "recommendation": "Inspect bearing lubrication.",
                "evidence": ["Inspect bearing lubrication."],
                "evidence_sources": [{
                    "chunk_id": "retrieved-chunk-1",
                    "document_id": "retrieved-document-1",
                    "filename": "pump-guide.pdf",
                    "revision": "B",
                    "page_number": 12,
                    "section_title": "Bearing checks",
                }],
                "evidence_status": "available",
                "evidence_error_code": None,
                "status": "awaiting_approval",
            }

        with patch.object(workflow_graph, "ainvoke", side_effect=assess):
            response = await self.client.post("/api/workflows", json={
                "asset_id": str(self.asset_id),
                "alert_type": "High vibration",
                "severity": "high",
                "message": "Inspect the pump vibration condition.",
                "telemetry": {"vibration_mm_s": 8.0},
            })
        self.assertEqual(response.status_code, 201, response.text)
        state = response.json()["workflow_state"]
        self.assertEqual(state["evidence_status"], "available")
        self.assertEqual(state["evidence_sources"][0]["chunk_id"], "retrieved-chunk-1")

        async with self.sessions() as session:
            event = await session.scalar(select(WorkflowEvent).where(
                WorkflowEvent.work_order_id == response.json()["id"],
                WorkflowEvent.event_type == "assessment_completed",
            ))
            self.assertEqual(event.details["evidence_status"], "available")
            self.assertIsNone(event.details["evidence_error_code"])
            self.assertEqual(event.details["evidence_sources"][0]["revision"], "B")
            self.assertEqual(event.details["evidence_sources"][0]["chunk_id"], "retrieved-chunk-1")

    async def test_seeded_rag_fixture_reports_recall_and_mrr(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "rag_retrieval_eval.json"
        cases = json.loads(fixture_path.read_text(encoding="utf-8"))
        async with self.engine.begin() as connection:
            for case in cases:
                phrase = case["relevant_phrases"][0]
                embedding = rag_service._hash_embedding(phrase)
                vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"
                await connection.execute(
                    text("INSERT INTO knowledge_chunks (id, content, embedding) VALUES (:id, :content, CAST(:embedding AS vector))"),
                    {"id": uuid.uuid4(), "content": f"{phrase} Follow the approved site procedure.", "embedding": vector_literal},
                )
        ranked = []
        with patch.object(rag_service, "engine", self.engine):
            for case in cases:
                chunks = await rag_service.retrieve_documents(case["query"], limit=10, allowed_roles={"operator"})
                ranked.append([chunk["content"] for chunk in chunks])
        metrics = score_retrieval_suite(cases, ranked, k=5)
        self.assertEqual(metrics["case_count"], 4)
        self.assertGreaterEqual(metrics["mean_recall_at_k"], 0.0)
        self.assertLessEqual(metrics["mean_recall_at_k"], 1.0)
        self.assertGreaterEqual(metrics["mean_mrr_at_k"], 0.0)
        self.assertLessEqual(metrics["mean_mrr_at_k"], 1.0)

    async def test_knowledge_ingestion_persists_metadata_deduplicates_and_filters_acl(self) -> None:
        self.principal = Principal("test-reliability", frozenset({"reliability_engineer"}), True, "oidc")
        raw = ("# Bearing maintenance\n" + "Inspect bearing lubrication and coupling alignment before action. " * 110).encode()
        async with self.sessions() as session:
            asset = await session.get(Asset, self.asset_id)
            plant_id = asset.plant_id
        upload = await self.client.post(
            "/api/knowledge/documents",
            files={"file": ("pump-guide.md", raw, "text/markdown")},
            data={
                "document_type": "manual",
                "manufacturer": "Example Pumps",
                "asset_type": "pump",
                "revision": "A",
                "plant_id": str(plant_id),
                "allowed_roles": "technician",
            },
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        document = upload.json()
        self.assertEqual(document["ingestion_status"], "indexed")
        self.assertGreaterEqual(document["chunk_count"], 1)
        self.assertEqual(document["allowed_roles"], ["technician"])
        self.assertEqual(document["revision"], "A")

        duplicate = await self.client.post(
            "/api/knowledge/documents",
            files={"file": ("pump-guide.md", raw, "text/markdown")},
            data={"document_type": "manual", "allowed_roles": "technician"},
        )
        self.assertEqual(duplicate.status_code, 409)

        self.principal = Principal("test-manager", frozenset({"manager"}), True, "oidc")
        manager_documents = await self.client.get("/api/knowledge/documents")
        hidden_document = await self.client.get(f"/api/knowledge/documents/{document['id']}")
        self.assertEqual(manager_documents.json(), [])
        self.assertEqual(hidden_document.status_code, 404)

        self.principal = Principal("test-technician", frozenset({"technician"}), True, "oidc")
        visible_document = await self.client.get(f"/api/knowledge/documents/{document['id']}?plant_id={plant_id}")
        wrong_plant_document = await self.client.get(f"/api/knowledge/documents/{document['id']}?plant_id={uuid.uuid4()}")
        self.assertEqual(visible_document.status_code, 200, visible_document.text)
        self.assertEqual(wrong_plant_document.status_code, 404)

        with patch.object(rag_service, "engine", self.engine):
            technician_results = await rag_service.retrieve_documents(
                "bearing lubrication", allowed_roles={"technician"}, plant_id=plant_id,
            )
            manager_results = await rag_service.retrieve_documents(
                "bearing lubrication", allowed_roles={"manager"}, plant_id=plant_id,
            )
            other_plant_results = await rag_service.retrieve_documents(
                "bearing lubrication", allowed_roles={"technician"}, plant_id=uuid.uuid4(),
            )
        self.assertTrue(any(row["document_id"] == document["id"] for row in technician_results))
        self.assertFalse(any(row.get("document_id") == document["id"] for row in manager_results))
        self.assertFalse(any(row.get("document_id") == document["id"] for row in other_plant_results))
        source_chunk = next(row for row in technician_results if row["document_id"] == document["id"])
        passage = await self.client.get(f"/api/knowledge/chunks/{source_chunk['chunk_id']}?plant_id={plant_id}")
        self.assertEqual(passage.status_code, 200, passage.text)
        self.assertEqual(passage.json()["filename"], "pump-guide.md")
        self.assertIn("bearing lubrication", passage.json()["content"])
        first_page = await self.client.get(f"/api/knowledge/documents/{document['id']}/chunks?plant_id={plant_id}&limit=1")
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertEqual(first_page.json()["total"], document["chunk_count"])
        self.assertEqual(len(first_page.json()["items"]), 1)
        second_page = await self.client.get(f"/api/knowledge/documents/{document['id']}/chunks?plant_id={plant_id}&limit=1&offset=1")
        self.assertEqual(second_page.status_code, 200, second_page.text)
        self.assertNotEqual(first_page.json()["items"][0]["chunk_id"], second_page.json()["items"][0]["chunk_id"])
        self.principal = Principal("test-manager", frozenset({"manager"}), True, "oidc")
        hidden_passage = await self.client.get(f"/api/knowledge/chunks/{source_chunk['chunk_id']}?plant_id={plant_id}")
        self.assertEqual(hidden_passage.status_code, 404)
        hidden_preview = await self.client.get(f"/api/knowledge/documents/{document['id']}/chunks?plant_id={plant_id}")
        self.assertEqual(hidden_preview.status_code, 404)

    async def test_failed_knowledge_ingestion_can_retry_same_checksum(self) -> None:
        self.principal = Principal("test-reliability", frozenset({"reliability_engineer"}), True, "oidc")
        raw = b"Retryable source document"
        payload = {
            "files": {"file": ("retry.txt", raw, "text/plain")},
            "data": {"document_type": "procedure", "allowed_roles": "operator"},
        }
        with patch.object(ingestion_service, "load_document", side_effect=DocumentLoadError("extract_failed", "Safe failure.")):
            failed = await self.client.post("/api/knowledge/documents", **payload)
        self.assertEqual(failed.status_code, 422)
        checksum = hashlib.sha256(raw).hexdigest()
        async with self.sessions() as session:
            failed_record = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.sha256 == checksum))
            self.assertEqual(failed_record.ingestion_status, "failed")
            failed_id = failed_record.id

        with patch.object(ingestion_service, "load_document", return_value=[DocumentSegment("Verified retry content")]):
            retried = await self.client.post("/api/knowledge/documents", **payload)
        self.assertEqual(retried.status_code, 201, retried.text)
        self.assertEqual(retried.json()["id"], str(failed_id))
        self.assertEqual(retried.json()["ingestion_status"], "indexed")

    async def test_concurrent_workflow_starts_create_only_one_investigation(self) -> None:
        first_entered_graph = asyncio.Event()
        release_first_graph = asyncio.Event()
        graph_calls = 0

        async def assess(state):
            nonlocal graph_calls
            graph_calls += 1
            first_entered_graph.set()
            await release_first_graph.wait()
            return {**state, "status": "awaiting_approval", "diagnosis": "Test", "recommendation": "Inspect"}

        payload = {
            "asset_id": str(self.asset_id), "alert_type": "High vibration", "severity": "high",
            "message": "Concurrent investigation request.", "telemetry": {"vibration_mm_s": 8.0},
        }
        with patch.object(workflow_graph, "ainvoke", side_effect=assess):
            first = asyncio.create_task(self.client.post("/api/workflows", json=payload))
            await asyncio.wait_for(first_entered_graph.wait(), timeout=5)
            second = asyncio.create_task(self.client.post("/api/workflows", json=payload))
            await asyncio.sleep(0.05)
            release_first_graph.set()
            responses = await asyncio.gather(first, second)
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        self.assertEqual(graph_calls, 1)

    async def test_sla_worker_flags_overdue_once(self) -> None:
        work_order_id = await self._create_awaiting_order()
        async with self.sessions() as session:
            await session.execute(update(WorkOrder).where(WorkOrder.id == work_order_id).values(
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            ))
            await session.commit()
        original_sla = settings.approval_sla_minutes
        settings.approval_sla_minutes = 1
        try:
            with patch.object(approval_sla_worker, "SessionLocal", self.sessions):
                self.assertTrue(await approval_sla_worker.flag_one_overdue_approval())
                self.assertFalse(await approval_sla_worker.flag_one_overdue_approval())
        finally:
            settings.approval_sla_minutes = original_sla
        async with self.sessions() as session:
            order = await session.get(WorkOrder, work_order_id)
            count = await session.scalar(select(func.count()).select_from(WorkflowEvent).where(
                WorkflowEvent.work_order_id == work_order_id,
                WorkflowEvent.event_type == "approval_overdue",
            ))
            self.assertEqual(count, 1)
            self.assertTrue(order.workflow_state["approval_overdue"])
            self.assertEqual(order.status, "awaiting_approval")

    async def test_stale_approval_requires_and_records_acknowledgement(self) -> None:
        work_order_id = await self._create_awaiting_order()
        snapshot_at = datetime.now(timezone.utc) - timedelta(hours=2)
        async with self.sessions() as session:
            async with session.begin():
                order = await session.get(WorkOrder, work_order_id)
                order.workflow_state = {
                    **order.workflow_state,
                    "diagnosis_snapshot_at": snapshot_at.isoformat(),
                    "asset_status_at_diagnosis": "attention",
                    "telemetry": {"vibration_mm_s": 8.0, "temperature_c": 70.0},
                }
                session.add(SensorData(
                    asset_id=self.asset_id,
                    timestamp=datetime.now(timezone.utc),
                    vibration_mm_s=3.0,
                    temperature_c=55.0,
                    pressure_bar=2.0,
                    rpm=1000.0,
                    source="integration-test",
                ))
                asset = await session.get(Asset, self.asset_id)
                asset.status = "operational"
        detail = await self.client.get(f"/api/workflows/{work_order_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["approval_is_stale"])
        self.assertEqual(detail.json()["current_asset_status"], "operational")
        self.assertIn("asset_returned_to_normal", detail.json()["approval_stale_reasons"])

        self.principal = Principal("stale-approver", frozenset({"approver"}), True, "oidc")

        async def queue(state):
            return {**state, "status": "dispatch_queued", "approved": True}

        with patch.object(workflow_graph, "ainvoke", side_effect=queue):
            rejected = await self.client.post(f"/api/workflows/{work_order_id}/approve", json={"comment": "reviewed"})
            self.assertEqual(rejected.status_code, 409)
            self.assertTrue(rejected.json()["detail"]["requires_acknowledgement"])
            approved = await self.client.post(f"/api/workflows/{work_order_id}/approve", json={
                "comment": "reviewed current readings", "acknowledge_stale": True,
            })
        self.assertEqual(approved.status_code, 200, approved.text)
        async with self.sessions() as session:
            event = await session.scalar(select(WorkflowEvent).where(
                WorkflowEvent.work_order_id == work_order_id,
                WorkflowEvent.event_type == "approval_decision",
            ))
            self.assertTrue(event.details["stale_acknowledged"])
            self.assertIn("telemetry_changed", event.details["stale_reasons"])

    async def test_notification_outbox_deduplicates_delivery_and_limits_payload(self) -> None:
        work_order_id = await self._create_awaiting_order()
        original_webhook = settings.notification_webhook_url
        settings.notification_webhook_url = "https://hooks.example.test/fieldnote"
        try:
            async with self.sessions() as session:
                order = await session.get(WorkOrder, work_order_id)
                await enqueue_workflow_notification(session, order, "new_awaiting_approval", "IT-ASSET")
                await enqueue_workflow_notification(session, order, "new_awaiting_approval", "IT-ASSET")
                await session.commit()
            with (
                patch.object(notification_worker, "SessionLocal", self.sessions),
                patch.object(notification_worker, "_deliver", new_callable=AsyncMock, side_effect=[httpx.ConnectError("temporary outage"), None]) as deliver,
            ):
                self.assertTrue(await notification_worker.process_one_notification())
                async with self.sessions() as session:
                    item = await session.scalar(select(NotificationOutbox).where(NotificationOutbox.work_order_id == work_order_id))
                    self.assertEqual(item.status, "retry")
                    item.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    await session.commit()
                self.assertTrue(await notification_worker.process_one_notification())
                self.assertFalse(await notification_worker.process_one_notification())
            self.assertEqual(deliver.await_count, 2)
            async with self.sessions() as session:
                items = list((await session.scalars(select(NotificationOutbox).where(
                    NotificationOutbox.work_order_id == work_order_id,
                ))).all())
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].status, "sent")
                self.assertNotIn("diagnosis", items[0].payload)
                self.assertNotIn("telemetry", items[0].payload)
        finally:
            settings.notification_webhook_url = original_webhook

    async def test_terminal_dispatch_failure_enqueues_notification(self) -> None:
        original_webhook = settings.notification_webhook_url
        settings.notification_webhook_url = "https://hooks.example.test/fieldnote"
        async with self.sessions() as session:
            async with session.begin():
                await session.execute(update(CMMSOutbox).values(status="sent", locked_at=None))
                order = WorkOrder(
                    asset_id=self.asset_id,
                    status="dispatch_retry",
                    summary="Terminal dispatch failure",
                    workflow_state={"asset_tag": "IT-ASSET"},
                )
                session.add(order)
                await session.flush()
                work_order_id = order.id
                session.add(CMMSOutbox(
                    work_order_id=order.id,
                    idempotency_key=str(order.id),
                    payload={"work_order_id": str(order.id)},
                    status="retry",
                    attempts=outbox_worker.MAX_ATTEMPTS - 1,
                ))
        try:
            with (
                patch.object(outbox_worker, "SessionLocal", self.sessions),
                patch.object(outbox_worker, "dispatch_work_order", new_callable=AsyncMock, side_effect=httpx.ConnectError("test outage")),
            ):
                self.assertTrue(await outbox_worker.process_one_outbox_item())
            async with self.sessions() as session:
                order = await session.get(WorkOrder, work_order_id)
                notification = await session.scalar(select(NotificationOutbox).where(
                    NotificationOutbox.work_order_id == work_order_id,
                    NotificationOutbox.event_type == "dispatch_failed",
                ))
                self.assertEqual(order.status, "dispatch_failed")
                self.assertIsNotNone(notification)
        finally:
            settings.notification_webhook_url = original_webhook

    async def test_technician_assignment_and_asset_history(self) -> None:
        self.principal = Principal("test-manager", frozenset({"manager"}), True, "oidc")
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            async with session.begin():
                order = WorkOrder(
                    asset_id=self.asset_id,
                    status="dispatched",
                    summary="Assigned inspection",
                    workflow_state={"asset_tag": "IT-ASSET", "asset_name": "Integration asset"},
                )
                session.add(order)
                session.add_all([
                    SensorData(asset_id=self.asset_id, timestamp=now - timedelta(minutes=10), vibration_mm_s=1.0, temperature_c=20.0, pressure_bar=2.0, rpm=1000.0, source="external"),
                    SensorData(asset_id=self.asset_id, timestamp=now, vibration_mm_s=9.0, temperature_c=90.0, pressure_bar=2.0, rpm=1000.0, source="scenario:overheating"),
                    AssetThresholdChange(asset_id=self.asset_id, changed_by_sub="test-manager", vibration_old_mm_s=None, vibration_new_mm_s=6.0, temperature_old_c=None, temperature_new_c=75.0, reason="Integration history edit", created_at=now - timedelta(minutes=5)),
                    SimulatorScenarioEvent(asset_id=self.asset_id, scenario="overheating", event_type="started", actor_type="oidc_user", actor_id="test-manager", actor_roles=["manager"], actor_verified=True, details={}),
                ])
                await session.flush()
                work_order_id = order.id

        assigned = await self.client.patch(
            f"/api/workflows/{work_order_id}/assignment",
            json={"assigned_to_sub": "test-technician"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["assigned_to_sub"], "test-technician")

        self.principal = Principal("test-technician", frozenset({"technician"}), True, "oidc")
        my_work = await self.client.get("/api/workflows/my-work")
        self.assertEqual(my_work.status_code, 200, my_work.text)
        self.assertEqual([item["id"] for item in my_work.json()], [str(work_order_id)])

        history = await self.client.get(f"/api/assets/{self.asset_id}/history")
        self.assertEqual(history.status_code, 200, history.text)
        event_types = {event["event_type"] for event in history.json()}
        self.assertIn("status_change", event_types)
        self.assertIn("threshold_changed", event_types)
        self.assertIn("scenario_started", event_types)
        self.assertIn("technician_assigned", event_types)
        workflow_event = next(event for event in history.json() if event["event_type"] == "technician_assigned")
        self.assertEqual(workflow_event["work_order_id"], str(work_order_id))

    async def test_cmms_failure_persists_retry_then_dispatches_once(self) -> None:
        async with self.sessions() as session:
            async with session.begin():
                await session.execute(update(CMMSOutbox).values(status="sent", locked_at=None))
                work_order = WorkOrder(
                    asset_id=self.asset_id,
                    status="dispatch_queued",
                    summary="Outbox retry integration",
                    diagnosis="Test diagnosis",
                    recommendation="Inspect the asset",
                    workflow_state={"status": "dispatch_queued", "activity": []},
                )
                session.add(work_order)
                await session.flush()
                work_order_id = work_order.id
                session.add(CMMSOutbox(
                    work_order_id=work_order.id,
                    idempotency_key=str(work_order.id),
                    payload={"work_order_id": str(work_order.id)},
                    status="queued",
                    attempts=0,
                ))

        with (
            patch.object(outbox_worker, "SessionLocal", self.sessions),
            patch.object(outbox_worker, "dispatch_work_order", new_callable=AsyncMock, side_effect=httpx.ConnectError("test outage")) as dispatch,
        ):
            self.assertTrue(await outbox_worker.process_one_outbox_item())
            async with self.sessions() as session:
                outbox = await session.scalar(select(CMMSOutbox).where(CMMSOutbox.work_order_id == work_order_id))
                order = await session.get(WorkOrder, work_order_id)
                self.assertEqual(outbox.status, "retry")
                self.assertEqual(outbox.attempts, 1)
                self.assertEqual(order.status, "dispatch_retry")
                outbox.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

            dispatch.side_effect = None
            dispatch.return_value = "CMMS-IT-1"
            self.assertTrue(await outbox_worker.process_one_outbox_item())

        async with self.sessions() as session:
            outbox = await session.scalar(select(CMMSOutbox).where(CMMSOutbox.work_order_id == work_order_id))
            order = await session.get(WorkOrder, work_order_id)
            self.assertEqual(outbox.status, "sent")
            self.assertEqual(outbox.attempts, 2)
            self.assertEqual(order.status, "dispatched")
            self.assertEqual(order.cmms_reference, "CMMS-IT-1")
            event_count = await session.scalar(
                select(func.count()).select_from(WorkflowEvent).where(WorkflowEvent.work_order_id == work_order_id)
            )
            self.assertEqual(event_count, 4)

    async def test_closeout_records_outcome_and_manager_metrics(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            async with session.begin():
                order = WorkOrder(
                    asset_id=self.asset_id,
                    status="in_progress",
                    summary="Close-out metrics integration",
                    diagnosis="Elevated vibration",
                    recommendation="Inspect bearings",
                    approved_at=now - timedelta(minutes=4),
                    dispatched_at=now - timedelta(minutes=2),
                    in_progress_at=now - timedelta(minutes=1),
                    workflow_state={"status": "in_progress", "failure_modes": ["bearing_wear", "imbalance"]},
                )
                session.add(order)
                await session.flush()
                work_order_id = order.id

        self.principal = Principal("test-technician", frozenset({"technician"}), True, "oidc")
        progress_response = await self.client.post(
            f"/api/workflows/{work_order_id}/lifecycle",
            json={"status": "completed"},
        )
        self.assertEqual(progress_response.status_code, 200)

        closeout_response = await self.client.post(
            f"/api/workflows/{work_order_id}/close-out",
            json={"confirmed_root_cause": "bearing_wear", "alert_was_useful": True, "comment": "Confirmed during inspection."},
        )
        self.assertEqual(closeout_response.status_code, 200)
        self.assertEqual(closeout_response.json()["status"], "closed")

        self.principal = Principal("test-manager", frozenset({"manager"}), True, "oidc")
        metrics_response = await self.client.get("/api/workflows/metrics/maintenance")
        self.assertEqual(metrics_response.status_code, 200)
        metrics = metrics_response.json()
        self.assertEqual(metrics["useful_alert_rate"], 1.0)
        self.assertEqual(metrics["top_three_root_cause_accuracy"], 1.0)
        self.assertGreater(metrics["approval_to_dispatch_p95_seconds"], 0)

    async def test_manager_metrics_exclude_simulated_orders_and_report_sample_sizes(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            async with session.begin():
                asset = await session.get(Asset, self.asset_id)
                plant_id = asset.plant_id
                session.add_all([
                    WorkOrder(
                        asset_id=self.asset_id, status="closed", summary="Real pilot close-out",
                        diagnosis="Bearing wear", recommendation="Inspect bearings",
                        cmms_reference="CMMS-REAL-1",
                        approved_at=now - timedelta(minutes=3), dispatched_at=now - timedelta(minutes=1),
                        confirmed_root_cause="bearing_wear", alert_was_useful=True,
                        workflow_state={"failure_modes": ["bearing_wear"]},
                    ),
                    WorkOrder(
                        asset_id=self.asset_id, status="closed", summary="Simulated close-out",
                        diagnosis="Bearing wear", recommendation="Inspect bearings",
                        cmms_reference="SIM-TEST-1",
                        approved_at=now - timedelta(minutes=5), dispatched_at=now - timedelta(minutes=4),
                        confirmed_root_cause="bearing_wear", alert_was_useful=False,
                        workflow_state={"failure_modes": ["other"], "simulated_only": True},
                    ),
                ])
        self.principal = Principal("test-manager", frozenset({"manager"}), True, "oidc")
        response = await self.client.get(f"/api/workflows/metrics/maintenance?plant_id={plant_id}")
        self.assertEqual(response.status_code, 200)
        metrics = response.json()
        self.assertEqual(metrics["useful_alert_rate"], 1.0)
        self.assertEqual(metrics["usefulness_sample_count"], 1)
        self.assertEqual(metrics["root_cause_sample_count"], 1)
        self.assertEqual(metrics["top_three_root_cause_accuracy"], 1.0)
        self.assertEqual(metrics["dispatch_sample_count"], 1)
        self.assertEqual(metrics["simulated_dispatch_count"], 1)
        self.assertEqual(metrics["status_counts"].get("closed"), 1)
        self.assertAlmostEqual(metrics["approval_to_dispatch_p95_seconds"], 120, delta=1)

    async def test_alert_remains_after_live_recovery_until_operator_acknowledges(self) -> None:
        now = datetime.now(timezone.utc) - timedelta(seconds=20)
        payload = {
            "asset_id": str(self.asset_id), "vibration_mm_s": 2.0,
            "temperature_c": 60.0, "pressure_bar": 3.0, "rpm": 1450,
            "source": "integration-test",
        }
        for seconds, temperature in ((0, 93.0), (5, 60.0), (10, 60.0), (15, 60.0)):
            response = await self.client.post("/api/telemetry/ingest", json={
                **payload, "temperature_c": temperature,
                "timestamp": (now + timedelta(seconds=seconds)).isoformat(),
            })
            self.assertEqual(response.status_code, 201, response.text)
        asset = (await self.client.get(f"/api/assets/{self.asset_id}")).json()
        self.assertEqual(asset["status"], "operational")
        self.assertTrue(asset["alert_active"])
        self.assertEqual(asset["alert_normal_readings"], 3)
        plants = (await self.client.get("/api/plants")).json()
        plant = next(plant for plant in plants if plant["id"] == asset["plant_id"])
        self.assertEqual(plant["attention_count"], 1)
        self.principal = Principal("read-only", frozenset({"technician"}), True, "oidc")
        denied = await self.client.post(f"/api/assets/{self.asset_id}/alert/acknowledge")
        self.assertEqual(denied.status_code, 403)
        self.principal = Principal("test-operator", frozenset({"operator"}), True, "oidc")
        acknowledged = await self.client.post(f"/api/assets/{self.asset_id}/alert/acknowledge")
        self.assertEqual(acknowledged.status_code, 200, acknowledged.text)
        self.assertEqual(acknowledged.json()["status"], "cleared")
        async with self.sessions() as session:
            persisted = await session.get(Asset, self.asset_id)
            self.assertFalse(persisted.alert_active)
            self.assertEqual(persisted.alert_acknowledged_by, "test-operator")
            self.assertEqual(await session.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.asset_id == self.asset_id)), 0)

    async def test_acknowledged_alert_requires_three_new_normal_readings_in_batch(self) -> None:
        now = datetime.now(timezone.utc) - timedelta(seconds=30)
        payload = {
            "asset_id": str(self.asset_id), "vibration_mm_s": 2.0,
            "temperature_c": 60.0, "pressure_bar": 3.0, "rpm": 1450,
            "source": "integration-test",
        }
        breached = {**payload, "vibration_mm_s": 8.0, "timestamp": now.isoformat(), "source_event_id": "breach"}
        self.assertEqual((await self.client.post("/api/telemetry/ingest", json=breached)).status_code, 201)
        acknowledged = await self.client.post(f"/api/assets/{self.asset_id}/alert/acknowledge")
        self.assertEqual(acknowledged.json()["status"], "acknowledged")
        repeated = await self.client.post(f"/api/assets/{self.asset_id}/alert/acknowledge")
        self.assertEqual(repeated.json()["alert_acknowledged_at"], acknowledged.json()["alert_acknowledged_at"])
        first_normal = {**payload, "timestamp": (now + timedelta(seconds=5)).isoformat(), "source_event_id": "normal-1"}
        batch = await self.client.post("/api/telemetry/ingest/batch", json={"readings": [first_normal, first_normal, breached]})
        self.assertEqual(batch.status_code, 201, batch.text)
        self.assertEqual(batch.json()["accepted"], 1)
        asset = (await self.client.get(f"/api/assets/{self.asset_id}")).json()
        self.assertTrue(asset["alert_active"])
        self.assertEqual(asset["alert_normal_readings"], 1)
        batch = await self.client.post("/api/telemetry/ingest/batch", json={"readings": [
            {**payload, "timestamp": (now + timedelta(seconds=15)).isoformat()},
            {**payload, "timestamp": (now + timedelta(seconds=10)).isoformat()},
        ]})
        self.assertEqual(batch.status_code, 201, batch.text)
        asset = (await self.client.get(f"/api/assets/{self.asset_id}")).json()
        self.assertFalse(asset["alert_active"])
        self.assertEqual(asset["status"], "operational")
        self.assertEqual((await self.client.post(f"/api/assets/{self.asset_id}/alert/acknowledge")).status_code, 409)
        self.assertEqual((await self.client.post(f"/api/assets/{uuid.uuid4()}/alert/acknowledge")).status_code, 404)

    async def test_batch_breach_is_latched_even_when_latest_batch_reading_is_normal(self) -> None:
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        payload = {
            "asset_id": str(self.asset_id), "vibration_mm_s": 2.0,
            "temperature_c": 60.0, "pressure_bar": 3.0, "rpm": 1450,
            "source": "integration-test",
        }
        response = await self.client.post("/api/telemetry/ingest/batch", json={"readings": [
            {**payload, "timestamp": (now + timedelta(seconds=5)).isoformat()},
            {**payload, "timestamp": now.isoformat(), "temperature_c": 93.0},
        ]})
        self.assertEqual(response.status_code, 201, response.text)
        asset = (await self.client.get(f"/api/assets/{self.asset_id}")).json()
        self.assertEqual(asset["status"], "operational")
        self.assertTrue(asset["alert_active"])
        self.assertIsNone(asset["alert_acknowledged_at"])

    async def test_asset_threshold_change_is_audited_and_used_for_ingest_status(self) -> None:
        self.principal = Principal("test-reliability", frozenset({"reliability_engineer"}), True, "oidc")
        threshold_response = await self.client.patch(
            f"/api/assets/{self.asset_id}/thresholds",
            json={
                "vibration_threshold_mm_s": 2.0,
                "temperature_threshold_c": 40.0,
                "reason": "Commissioning baseline approved by reliability engineering.",
            },
        )
        self.assertEqual(threshold_response.status_code, 200)
        self.assertEqual(threshold_response.json()["vibration_threshold_source"], "asset")
        self.assertEqual(threshold_response.json()["temperature_threshold_c"], 40.0)

        history_response = await self.client.get(f"/api/assets/{self.asset_id}/threshold-history")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()[0]["changed_by_sub"], "test-reliability")
        self.assertIn("Commissioning baseline", history_response.json()[0]["reason"])

        telemetry_response = await self.client.post("/api/telemetry/ingest", json={
            "asset_id": str(self.asset_id),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vibration_mm_s": 2.5,
            "temperature_c": 25.0,
            "pressure_bar": 2.0,
            "rpm": 1000,
            "source": "integration-test",
        })
        self.assertEqual(telemetry_response.status_code, 201)
        async with self.sessions() as session:
            asset = await session.get(Asset, self.asset_id)
            self.assertEqual(asset.status, "attention")

    async def test_telemetry_event_ids_deduplicate_and_old_backfill_does_not_change_status(self) -> None:
        newest_time = datetime.now(timezone.utc)
        payload = {
            "asset_id": str(self.asset_id),
            "timestamp": newest_time.isoformat(),
            "vibration_mm_s": 1.0,
            "temperature_c": 20.0,
            "pressure_bar": 2.0,
            "rpm": 1000,
            "source": "edge-bridge",
            "source_event_id": "device-42",
        }
        first = await self.client.post("/api/telemetry/ingest", json=payload)
        duplicate = await self.client.post("/api/telemetry/ingest", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(duplicate.json()["id"], first.json()["id"])

        old_payload = {
            **payload,
            "timestamp": (newest_time - timedelta(hours=1)).isoformat(),
            "vibration_mm_s": 25.0,
            "source_event_id": "device-older-41",
        }
        old_result = await self.client.post("/api/telemetry/ingest", json=old_payload)
        self.assertEqual(old_result.status_code, 201)
        async with self.sessions() as session:
            asset = await session.get(Asset, self.asset_id)
            readings = await session.scalar(
                select(func.count()).select_from(SensorData).where(SensorData.asset_id == self.asset_id)
            )
            self.assertEqual(asset.status, "operational")
            self.assertEqual(readings, 2)

        batch_payload = {**payload, "timestamp": (newest_time + timedelta(minutes=1)).isoformat(), "source_event_id": "batch-50"}
        batch_result = await self.client.post("/api/telemetry/ingest/batch", json={"readings": [batch_payload, batch_payload]})
        self.assertEqual(batch_result.status_code, 201)
        self.assertEqual(batch_result.json()["accepted"], 1)
        self.assertEqual(batch_result.json()["duplicates"], 1)

    async def test_scenario_routes_are_disabled_with_simulator(self) -> None:
        original = settings.simulator_enabled
        settings.simulator_enabled = False
        try:
            payload = {
                "asset_id": str(self.asset_id),
                "scenario": "healthy_baseline",
                "duration_readings": 3,
                "severity": 0.5,
            }
            listed = await self.client.get("/api/simulator/scenarios")
            started = await self.client.post("/api/simulator/scenarios", json=payload)
            stopped = await self.client.delete(f"/api/simulator/scenarios/{self.asset_id}")
        finally:
            settings.simulator_enabled = original
        self.assertEqual([listed.status_code, started.status_code, stopped.status_code], [403, 403, 403])

    async def test_scenario_ingest_is_tagged_audited_and_never_starts_workflow(self) -> None:
        original = settings.simulator_enabled
        settings.simulator_enabled = True
        self.principal = Principal("scenario-operator", frozenset({"operator"}), True, "oidc")
        try:
            response = await self.client.post("/api/simulator/scenarios", json={
                "asset_id": str(self.asset_id),
                "scenario": "vibration_rise",
                "duration_readings": 3,
                "severity": 0.9,
            })
        finally:
            settings.simulator_enabled = original
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["active"])
        self.assertEqual(response.json()["reading"]["status"], "accepted")
        async with self.sessions() as session:
            reading = await session.scalar(select(SensorData).where(SensorData.asset_id == self.asset_id))
            event = await session.scalar(select(SimulatorScenarioEvent).where(SimulatorScenarioEvent.asset_id == self.asset_id))
            workflows = await session.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.asset_id == self.asset_id))
            self.assertEqual(reading.source, "scenario:vibration_rise")
            self.assertEqual(event.event_type, "started")
            self.assertEqual(event.actor_id, "scenario-operator")
            self.assertEqual(workflows, 0)

    async def test_scenario_workflow_uses_fallback_and_simulated_only_outbox(self) -> None:
        original = settings.simulator_enabled
        settings.simulator_enabled = True
        self.principal = Principal("scenario-operator", frozenset({"operator"}), True, "oidc")
        try:
            scenario_response = await self.client.post("/api/simulator/scenarios", json={
                "asset_id": str(self.asset_id),
                "scenario": "healthy_baseline",
                "duration_readings": 10,
                "severity": 0.5,
                "force_llm_fallback": True,
            })
            self.assertEqual(scenario_response.status_code, 200, scenario_response.text)
            initial_state = {}

            async def assess(state):
                initial_state.update(state)
                return {
                    **state,
                    "diagnosis": "Scenario diagnosis",
                    "recommendation": "Inspect using site procedure",
                    "compliance_check": {"compliant": True, "checks": ["human approval required"]},
                    "status": "awaiting_approval",
                }

            with patch.object(workflow_graph, "ainvoke", side_effect=assess):
                started = await self.client.post("/api/workflows", json={
                    "asset_id": str(self.asset_id),
                    "alert_type": "Scenario investigation",
                    "severity": "medium",
                    "message": "Operator requested scenario workflow review.",
                    "telemetry": {"vibration_mm_s": 1.0, "temperature_c": 30.0},
                })
            self.assertEqual(started.status_code, 201, started.text)
            self.assertTrue(initial_state["force_llm_fallback"])
            self.assertTrue(initial_state["simulated_only"])
            self.assertEqual(initial_state["simulation_scenario"], "healthy_baseline")

            self.principal = Principal("scenario-approver", frozenset({"approver"}), True, "oidc")

            async def queue(state):
                return {**state, "status": "dispatch_queued", "approved": True}

            with patch.object(workflow_graph, "ainvoke", side_effect=queue):
                approved = await self.client.post(f"/api/workflows/{started.json()['id']}/approve", json={"comment": "test approval"})
            self.assertEqual(approved.status_code, 200, approved.text)
            async with self.sessions() as session:
                outbox = await session.scalar(select(CMMSOutbox).where(CMMSOutbox.work_order_id == started.json()["id"]))
                self.assertTrue(outbox.payload["simulated_only"])
        finally:
            settings.simulator_enabled = original

    async def test_invalid_scenario_sample_is_rejected_not_persisted(self) -> None:
        original = settings.simulator_enabled
        settings.simulator_enabled = True
        try:
            response = await self.client.post("/api/simulator/scenarios", json={
                "asset_id": str(self.asset_id),
                "scenario": "invalid_reading",
                "duration_readings": 3,
                "severity": 1.0,
            })
        finally:
            settings.simulator_enabled = original
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["reading_rejected"])
        async with self.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(SensorData).where(SensorData.asset_id == self.asset_id))
            event = await session.scalar(select(SimulatorScenarioEvent).where(SimulatorScenarioEvent.asset_id == self.asset_id))
            self.assertEqual(count, 0)
            self.assertEqual(event.event_type, "start_rejected")

    async def test_chat_uses_live_data_in_rule_based_mode_and_cites_scenario_source(self) -> None:
        original_key = settings.groq_api_key
        settings.groq_api_key = None
        self.principal = Principal("chat-operator", frozenset({"operator"}), True, "oidc")
        try:
            ingest = await self.client.post("/api/telemetry/ingest", json={
                "asset_id": str(self.asset_id),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "vibration_mm_s": 8.0,
                "temperature_c": 60.0,
                "pressure_bar": 3.0,
                "rpm": 1200,
                "source": "scenario:vibration_rise",
            })
            response = await self.client.post("/api/chat", json={
                "message": "Why is this asset in attention?",
                "asset_id": str(self.asset_id),
            })
            unsupported = await self.client.post("/api/chat", json={"message": "What is its remaining useful life?"})
        finally:
            settings.groq_api_key = original_key
        self.assertEqual(ingest.status_code, 201)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["used_llm"])
        self.assertIn("8.0 mm/s", response.json()["answer"])
        self.assertIn("scenario-sourced demo data", response.json()["answer"])
        self.assertTrue(any(source["type"] == "telemetry" for source in response.json()["sources"]))
        self.assertFalse(unsupported.json()["used_llm"])
        self.assertIn("I don't have that data", unsupported.json()["answer"])