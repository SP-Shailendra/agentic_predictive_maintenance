import json
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx
import jwt
from pypdf import PdfReader
from sqlalchemy import column
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from agents import diagnostic_agent
from core.config import Settings
from core import security
from core.security import Principal
from models import Asset
from core.config import settings
from core.observability import instrument_fastapi
from main import _rate_limit_buckets, app
from agents.workflow_graph import diagnose_node, dispatch_node
from routers.workflow import OPEN_INVESTIGATION_STATUSES
from schemas.asset import TelemetryBatch, TelemetryIngest
from schemas.chat import ChatRequest
from services import cmms_service
from services import outbox_worker
from services.notification_worker import build_webhook_payload
from services.simulator_scenarios import build_scenario_reading
from services.baseline_detector import train_and_evaluate
from services.edge_gateway import normalize_gateway_reading
from services.citation_verification import CitationCheck, validate_citation_checks
from services.audit_pack import build_work_order_audit_pack
from scripts.backfill_telemetry import load_historian_readings
from schemas.simulator import ScenarioName, ScenarioStart
from services.chat_service import READ_TOOL_NAMES
from services.telemetry_ingest import acknowledge_asset_alert, update_asset_condition


class PersistentAssetAlertTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.asset = Asset(
            status="operational", alert_active=False, alert_normal_readings=0,
            alert_last_reading_at=None, alert_acknowledged_at=None,
        )

    def sample(self, seconds: int, vibration: float = 2.0, temperature: float = 60.0) -> None:
        update_asset_condition(self.asset, vibration, temperature, self.now + timedelta(seconds=seconds))

    def test_normal_condition_does_not_clear_unacknowledged_alert(self) -> None:
        self.sample(0, temperature=93)
        for seconds in (5, 10, 15):
            self.sample(seconds)
        self.assertEqual(self.asset.status, "operational")
        self.assertTrue(self.asset.alert_active)
        acknowledge_asset_alert(self.asset, "operator-1", self.now + timedelta(seconds=16))
        self.assertFalse(self.asset.alert_active)
        self.assertEqual(self.asset.alert_acknowledged_by, "operator-1")

    def test_acknowledgement_during_breach_waits_for_recovery(self) -> None:
        self.sample(0, vibration=8)
        acknowledge_asset_alert(self.asset, "operator-1", self.now)
        for seconds in (5, 10):
            self.sample(seconds)
            self.assertTrue(self.asset.alert_active)
        self.sample(15)
        self.assertFalse(self.asset.alert_active)
        self.sample(20, temperature=93)
        self.assertTrue(self.asset.alert_active)
        self.assertIsNone(self.asset.alert_acknowledged_at)

    def test_breach_gap_duplicate_and_old_readings_do_not_confirm_recovery(self) -> None:
        self.sample(0, vibration=8)
        acknowledge_asset_alert(self.asset, "operator-1", self.now)
        self.sample(5)
        self.sample(5)
        self.sample(3)
        self.assertEqual(self.asset.alert_normal_readings, 1)
        self.sample(10, vibration=8)
        self.assertEqual(self.asset.alert_normal_readings, 0)
        self.sample(15)
        self.sample(80)
        self.assertEqual(self.asset.alert_normal_readings, 1)
        self.assertTrue(self.asset.alert_active)

    def test_stale_recovery_cannot_clear_at_acknowledgement(self) -> None:
        self.sample(0, vibration=8)
        for seconds in (5, 10, 15):
            self.sample(seconds)
        acknowledge_asset_alert(self.asset, "operator-1", self.now + timedelta(seconds=100))
        self.assertTrue(self.asset.alert_active)
        self.sample(100)
        self.assertEqual(self.asset.alert_normal_readings, 1)
        self.assertTrue(self.asset.alert_active)


class ProductionSettingsTests(TestCase):
    def test_knowledge_upload_limit_has_safe_default_and_bounds(self) -> None:
        self.assertEqual(Settings(_env_file=None).knowledge_max_upload_mb, 10)
        for value in (0, 101):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(_env_file=None, knowledge_max_upload_mb=value)

    def test_approval_notifications_are_disabled_by_default(self) -> None:
        configured = Settings(_env_file=None)
        self.assertEqual(configured.approval_stale_after_minutes, 60)
        self.assertEqual(configured.approval_sla_minutes, 60)
        self.assertIsNone(configured.notification_email_to)
        self.assertIsNone(configured.notification_webhook_url)

    def test_partial_or_insecure_notification_settings_are_rejected(self) -> None:
        invalid = (
            {"smtp_host": "smtp.example.test"},
            {"notification_email_to": "ops@example.test", "smtp_host": "smtp.example.test", "smtp_from": "fieldnote@example.test", "smtp_username": "ops"},
            {"notification_webhook_url": "http://hooks.example.test/fieldnote"},
            {"notification_webhook_format": "teams-chat"},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(_env_file=None, **values)


    def test_slack_and_teams_payloads_contain_only_operational_link_context(self) -> None:
        notification = {"asset_tag": "PMP-001", "status": "approval_overdue", "url": "https://fieldnote.example/#workflow/order-1"}
        slack = build_webhook_payload(notification, "slack")
        teams = build_webhook_payload(notification, "teams")
        self.assertIn(notification["url"], slack["text"])
        self.assertEqual(teams["@type"], "MessageCard")
        self.assertEqual(teams["potentialAction"][0]["targets"][0]["uri"], notification["url"])
        for body in (slack, teams):
            serialized = str(body).lower()
            self.assertNotIn("diagnosis", serialized)
            self.assertNotIn("telemetry", serialized)

    def test_production_rejects_incomplete_configuration(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, environment="production")

    def test_production_accepts_secure_configuration(self) -> None:
        settings = Settings(
            _env_file=None,
            environment="production",
            oidc_issuer="https://login.example.com/tenant/v2.0",
            oidc_audience="fieldnote-api",
            oidc_jwks_url="https://login.example.com/tenant/discovery/v2.0/keys",
            oidc_roles_claim="roles",
            metrics_api_key="m" * 32,
            simulator_enabled=False,
            cors_origins=["https://maintenance.example.com"],
            cmms_api_url="https://cmms.example.com/work-orders",
            cmms_api_token="configured-token",
            database_url="postgresql+asyncpg://maintenance:strong-secret@db:5432/maintenance",
        )
        self.assertEqual(settings.environment, "production")

    def test_settings_can_read_secret_files(self) -> None:
        with TemporaryDirectory() as secret_dir:
            Path(secret_dir, "groq_api_key").write_text("mounted-secret", encoding="utf-8")
            settings = Settings(_env_file=None, _secrets_dir=secret_dir)
        self.assertEqual(settings.groq_api_key, "mounted-secret")


class OIDCIdentityTests(IsolatedAsyncioTestCase):
    async def test_verifies_oidc_subject_and_roles(self) -> None:
        configured = {
            "oidc_issuer": security.settings.oidc_issuer,
            "oidc_audience": security.settings.oidc_audience,
            "oidc_jwks_url": security.settings.oidc_jwks_url,
            "oidc_roles_claim": security.settings.oidc_roles_claim,
            "oidc_plants_claim": security.settings.oidc_plants_claim,
            "oidc_groups_claim": security.settings.oidc_groups_claim,
            "api_key": security.settings.api_key,
        }
        issuer = "https://identity.example.test/tenant"
        security.settings.oidc_issuer = issuer
        security.settings.oidc_audience = "fieldnote-api"
        security.settings.oidc_jwks_url = f"{issuer}/keys"
        security.settings.oidc_roles_claim = "realm.roles"
        security.settings.oidc_plants_claim = "plant_ids"
        security.settings.oidc_groups_claim = "groups"
        security.settings.api_key = None
        plant_id = uuid.uuid4()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(
            {
                "sub": "operator-17",
                "iss": issuer,
                "aud": "fieldnote-api",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=3),
                "realm": {"roles": ["operator", "approver"]},
                "plant_ids": [str(plant_id)],
                "groups": ["reliability-team"],
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private_key.public_key())
        try:
            with patch.object(security, "_jwks_client", return_value=jwks):
                principal = await security.get_current_principal(
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                    x_api_key=None,
                )
            self.assertEqual(principal.subject, "operator-17")
            self.assertEqual(principal.roles, frozenset({"operator", "approver"}))
            self.assertEqual(principal.plant_ids, frozenset({plant_id}))
            self.assertEqual(principal.groups, frozenset({"reliability-team"}))
            self.assertTrue(principal.verified)
        finally:
            for name, value in configured.items():
                setattr(security.settings, name, value)

    async def test_missing_plant_entitlements_fail_closed(self) -> None:
        configured = {
            "oidc_issuer": security.settings.oidc_issuer,
            "oidc_audience": security.settings.oidc_audience,
            "oidc_jwks_url": security.settings.oidc_jwks_url,
            "oidc_roles_claim": security.settings.oidc_roles_claim,
            "oidc_plants_claim": security.settings.oidc_plants_claim,
            "api_key": security.settings.api_key,
        }
        issuer = "https://identity.example.test/tenant"
        security.settings.oidc_issuer = issuer
        security.settings.oidc_audience = "fieldnote-api"
        security.settings.oidc_jwks_url = f"{issuer}/keys"
        security.settings.oidc_roles_claim = "roles"
        security.settings.oidc_plants_claim = "plant_ids"
        security.settings.api_key = None
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(
            {
                "sub": "operator-17",
                "iss": issuer,
                "aud": "fieldnote-api",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=3),
                "roles": ["operator"],
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private_key.public_key())
        try:
            with patch.object(security, "_jwks_client", return_value=jwks):
                principal = await security.get_current_principal(
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                    x_api_key=None,
                )
            self.assertEqual(principal.plant_ids, frozenset())
            with self.assertRaises(HTTPException) as raised:
                security.plant_scope_filter(principal, object())
            self.assertEqual(raised.exception.status_code, 403)
        finally:
            for name, value in configured.items():
                setattr(security.settings, name, value)

    async def test_role_guard_and_self_approval_policy(self) -> None:
        operator = Principal("operator-17", frozenset({"operator"}), True, "oidc")
        with self.assertRaises(HTTPException) as raised:
            await security.require_roles("approver")(operator)
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(HTTPException) as raised:
            security.reject_self_approval("operator-17", operator)
        self.assertEqual(raised.exception.status_code, 409)

    async def test_plant_scope_filter_rejects_unentitled_plant(self) -> None:
        allowed_plant = uuid.uuid4()
        principal = Principal("operator-17", frozenset({"operator"}), True, "oidc", frozenset({allowed_plant}))
        scope = security.plant_scope_filter(principal, column("plant_id"))
        self.assertIn("IN", str(scope))
        with self.assertRaises(HTTPException) as raised:
            security.plant_scope_filter(principal, column("plant_id"), uuid.uuid4())
        self.assertEqual(raised.exception.status_code, 403)


class TelemetryContractTests(TestCase):
    def setUp(self) -> None:
        self.reading = {
            "asset_id": uuid.uuid4(),
            "vibration_mm_s": 1.2,
            "temperature_c": 20.0,
            "pressure_bar": 2.0,
            "rpm": 1000.0,
        }

    def test_rejects_naive_and_future_timestamps(self) -> None:
        naive = {**self.reading, "timestamp": "2026-10-05T12:00:00"}
        future = {**self.reading, "timestamp": (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()}
        for payload in (naive, future, {**self.reading, "rpm": float("nan")}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    TelemetryIngest.model_validate(payload)

    def test_batch_size_is_bounded(self) -> None:
        TelemetryBatch.model_validate({"readings": [self.reading]})
        for readings in ([], [self.reading] * 501):
            with self.subTest(batch_size=len(readings)):
                with self.assertRaises(ValidationError):
                    TelemetryBatch.model_validate({"readings": readings})


class SimulatorScenarioTests(TestCase):
    def setUp(self) -> None:
        self.asset = Asset(
            id=uuid.uuid4(),
            name="Scenario test asset",
            asset_tag="TEST-001",
            asset_type="Pump",
            location="Test bay",
            status="operational",
            criticality="medium",
            vibration_threshold_mm_s=7.1,
            temperature_threshold_c=85.0,
        )

    def test_schema_limits_duration_and_severity(self) -> None:
        base = {"asset_id": self.asset.id, "scenario": "vibration_rise", "duration_readings": 3, "severity": 0.7}
        ScenarioStart.model_validate(base)
        for invalid in ({**base, "duration_readings": 121}, {**base, "severity": 1.1}):
            with self.subTest(payload=invalid), self.assertRaises(ValidationError):
                ScenarioStart.model_validate(invalid)

    def test_valid_scenarios_use_explicit_scenario_sources(self) -> None:
        names: tuple[ScenarioName, ...] = (
            "vibration_rise", "overheating", "sudden_spike", "sensor_flatline", "healthy_baseline",
        )
        for name in names:
            with self.subTest(scenario=name):
                reading = build_scenario_reading(self.asset, name, 0.8, 0, 5)
                self.assertIsNotNone(reading)
                self.assertEqual(reading.source, f"scenario:{name}")

    def test_dropout_emits_no_reading_and_invalid_reading_fails_validation(self) -> None:
        self.assertIsNone(build_scenario_reading(self.asset, "data_dropout", 0.5, 0, 3))
        with self.assertRaises(ValidationError):
            build_scenario_reading(self.asset, "invalid_reading", 0.9, 0, 3)

    def test_baseline_detector_reports_labeled_simulator_holdout_metrics(self) -> None:
        model, metrics = train_and_evaluate(samples_per_scenario=20)

        self.assertEqual(model.classes_.tolist(), [0, 1])
        self.assertEqual(metrics["evaluation_source"], "synthetic_simulator_scenarios")
        self.assertEqual(metrics["scenario_counts"], {name: 20 for name in ("healthy_baseline", "vibration_rise", "overheating", "sudden_spike")})
        self.assertGreaterEqual(metrics["recall"], 0.0)
        self.assertLessEqual(metrics["recall"], 1.0)


class EdgeGatewayTests(TestCase):
    def setUp(self) -> None:
        self.asset_id = uuid.uuid4()
        self.base_reading = {
            "asset_id": str(self.asset_id),
            "timestamp": "2026-10-15T10:00:00+00:00",
            "vibration_mm_s": 1.2,
            "temperature_c": 20,
            "pressure_bar": 3.4,
            "rpm": 1450,
        }

    def test_gateway_reading_has_stable_idempotency_key_and_source(self) -> None:
        first = normalize_gateway_reading(self.base_reading, source="mqtt")
        second = normalize_gateway_reading(self.base_reading, source="mqtt")
        self.assertEqual(first.source, "mqtt")
        self.assertEqual(first.source_event_id, second.source_event_id)
        opcua_first = normalize_gateway_reading(self.base_reading, source="opcua")
        opcua_retry = normalize_gateway_reading(self.base_reading, source="opcua")
        self.assertEqual(opcua_first.source_event_id, opcua_retry.source_event_id)

    def test_historian_backfill_requires_timestamps_and_sorts_per_asset(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "history.json"
            source_path.write_text(json.dumps([
                {**self.base_reading, "timestamp": "2026-10-15T10:05:00+00:00"},
                {**self.base_reading, "timestamp": "2026-10-15T10:00:00+00:00"},
            ]), encoding="utf-8")
            readings = load_historian_readings(source_path)
        self.assertEqual([item.timestamp.minute for item in readings], [0, 5])
        self.assertTrue(all(item.source == "historian" for item in readings))


class CMMSStatusMappingTests(TestCase):
    def test_external_status_maps_to_monotonic_work_order_states(self) -> None:
        self.assertEqual(cmms_service.map_cmms_status(" IN_PROGRESS "), "in_progress")
        self.assertEqual(cmms_service.map_cmms_status("closed"), "completed")
        self.assertIsNone(cmms_service.map_cmms_status("vendor_custom_state"))

    def test_cmms_status_configuration_requires_safe_transitions(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(cmms_status_url="https://cmms.example/api/status")
        with self.assertRaises(ValidationError):
            Settings(cmms_status_map={"done": "closed"})


class ObservabilityTests(TestCase):
    def test_tracing_is_disabled_without_an_otlp_endpoint(self) -> None:
        original_endpoint = settings.otel_exporter_otlp_endpoint
        settings.otel_exporter_otlp_endpoint = None
        try:
            self.assertFalse(instrument_fastapi(app))
        finally:
            settings.otel_exporter_otlp_endpoint = original_endpoint


class AuditPackTests(TestCase):
    def test_audit_pack_is_a_pdf_with_event_hash_chain(self) -> None:
        work_order = SimpleNamespace(
            id=uuid.uuid4(), status="dispatched", summary="Pump vibration alert",
            diagnosis="Possible imbalance", recommendation="Inspect coupling",
            cmms_reference="WO-123", workflow_state={"evidence_sources": [{"chunk_id": "chunk-1"}]},
        )
        event = SimpleNamespace(
            created_at=datetime.now(timezone.utc), event_type="assessment_completed",
            actor_id="operator-1", actor_type="oidc_user", from_status="processing",
            to_status="awaiting_approval", details={"evidence_status": "available"},
            event_hash="event-hash", previous_hash="previous-hash",
        )

        pdf = build_work_order_audit_pack(work_order, [event])

        self.assertTrue(pdf.startswith(b"%PDF-"))
        extracted = PdfReader(BytesIO(pdf)).pages[0].extract_text()
        self.assertIn("event-hash", extracted)


class CitationVerificationTests(TestCase):
    def test_claim_support_requires_a_retrieved_chunk_id(self) -> None:
        claims = ["Inspect bearing wear."]
        status, checks = validate_citation_checks(
            claims,
            [CitationCheck(claim=claims[0], verdict="supported", chunk_ids=["invented-chunk"])],
            {"retrieved-chunk"},
        )
        self.assertEqual(status, "issues")
        self.assertEqual(checks[0]["verdict"], "unsupported")
        self.assertEqual(checks[0]["chunk_ids"], [])

    def test_supported_claim_keeps_only_verified_retrieved_citations(self) -> None:
        claims = ["Inspect bearing wear."]
        status, checks = validate_citation_checks(
            claims,
            [CitationCheck(claim=claims[0], verdict="supported", chunk_ids=["chunk-1", "invented-chunk"])],
            {"chunk-1"},
        )
        self.assertEqual(status, "model_supported")
        self.assertEqual(checks[0]["chunk_ids"], ["chunk-1"])

    def test_verifier_results_must_match_the_claim_text(self) -> None:
        status, checks = validate_citation_checks(
            ["Inspect bearing wear."],
            [CitationCheck(claim="Inspect pump cooling.", verdict="supported", chunk_ids=["chunk-1"])],
            {"chunk-1"},
        )
        self.assertEqual(status, "invalid")
        self.assertEqual(checks, [])


class ChatContractTests(TestCase):
    def test_chat_input_and_history_are_bounded(self) -> None:
        ChatRequest.model_validate({"message": "Is the data fresh?", "history": [{"role": "user", "content": "Hello"}]})
        invalid_requests = [
            {"message": "x" * 1001},
            {"message": "question", "history": [{"role": "user", "content": "turn"}] * 11},
        ]
        for payload in invalid_requests:
            with self.subTest(payload_size=len(str(payload))), self.assertRaises(ValidationError):
                ChatRequest.model_validate(payload)

    def test_chat_tool_allowlist_contains_no_write_actions(self) -> None:
        expected = {
            "list_plants", "list_assets", "get_asset", "get_telemetry", "list_workflows",
            "get_workflow_events", "get_work_order_detail", "search_knowledge_notes", "get_data_freshness",
        }
        self.assertEqual(READ_TOOL_NAMES, expected)
        self.assertFalse(any(token in name for name in READ_TOOL_NAMES for token in ("create", "approve", "dispatch", "update", "delete", "stop")))


class CorsRateLimitTests(IsolatedAsyncioTestCase):
    async def test_rate_limit_response_keeps_cors_headers(self) -> None:
        original_limit = settings.rate_limit_requests_per_minute
        _rate_limit_buckets.clear()
        settings.rate_limit_requests_per_minute = 1
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Origin": "http://localhost:4173"},
            ) as client:
                await client.get("/route-that-does-not-exist")
                limited = await client.get("/another-unknown-route")
        finally:
            settings.rate_limit_requests_per_minute = original_limit
            _rate_limit_buckets.clear()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers.get("access-control-allow-origin"), "http://localhost:4173")

class CMMSDispatchTests(IsolatedAsyncioTestCase):
    async def test_dispatch_sends_bearer_and_idempotency_headers(self) -> None:
        captured: dict[str, str | None] = {}
        original_url = cmms_service.settings.cmms_api_url
        original_token = cmms_service.settings.cmms_api_token
        cmms_service.settings.cmms_api_url = "https://cmms.example.test/work-orders"
        cmms_service.settings.cmms_api_token = "test-token"
        real_client = httpx.AsyncClient

        def handler(request: httpx.Request) -> httpx.Response:
            captured["idempotency"] = request.headers.get("Idempotency-Key")
            captured["authorization"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"id": "WO-1"})

        def client_factory(*, timeout: httpx.Timeout) -> httpx.AsyncClient:
            return real_client(timeout=timeout, transport=httpx.MockTransport(handler))

        try:
            with patch.object(cmms_service.httpx, "AsyncClient", client_factory):
                reference = await cmms_service.dispatch_work_order({"work_order_id": "stable-order-id"})
        finally:
            cmms_service.settings.cmms_api_url = original_url
            cmms_service.settings.cmms_api_token = original_token

        self.assertEqual(reference, "WO-1")
        self.assertEqual(captured["idempotency"], "stable-order-id")
        self.assertEqual(captured["authorization"], "Bearer test-token")

    async def test_simulated_only_order_bypasses_configured_cmms(self) -> None:
        original_url = cmms_service.settings.cmms_api_url
        original_token = cmms_service.settings.cmms_api_token
        cmms_service.settings.cmms_api_url = "https://cmms.example.test/work-orders"
        cmms_service.settings.cmms_api_token = "test-token"
        client_factory = Mock()
        try:
            with patch.object(cmms_service.httpx, "AsyncClient", client_factory):
                reference = await cmms_service.dispatch_work_order({"work_order_id": "scenario-order", "simulated_only": True})
        finally:
            cmms_service.settings.cmms_api_url = original_url
            cmms_service.settings.cmms_api_token = original_token
        self.assertTrue(reference.startswith("SIM-"))
        client_factory.assert_not_called()


class WorkflowGateTests(IsolatedAsyncioTestCase):
    async def test_legacy_persisted_diagnosis_keeps_legacy_evidence_status(self) -> None:
        result = await diagnose_node({
            "diagnosis": "Persisted diagnosis",
            "recommendation": "Inspect using site procedure",
            "evidence": ["Legacy evidence text"],
            "activity": [],
        })
        self.assertEqual(result["evidence_status"], "legacy")
        self.assertEqual(result["evidence"], ["Legacy evidence text"])

    def test_duplicate_guard_covers_all_open_workflow_states(self) -> None:
        self.assertEqual(set(OPEN_INVESTIGATION_STATUSES), {
            "processing", "awaiting_approval", "dispatch_queued", "dispatching",
            "dispatch_retry", "dispatched", "in_progress",
        })

    async def test_noncompliant_workflow_is_blocked_before_approval_gate(self) -> None:
        result = await dispatch_node({
            "approved": False,
            "compliance_check": {"compliant": False},
            "activity": [],
        })

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["activity"][-1]["status"], "error")

    async def test_compliant_workflow_waits_for_approval_without_dispatch(self) -> None:
        result = await dispatch_node({
            "approved": False,
            "compliance_check": {"compliant": True},
            "activity": [],
        })

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["activity"][-1]["status"], "waiting")

    async def test_approved_compliant_workflow_queues_dispatch(self) -> None:
        result = await dispatch_node({
            "approved": True,
            "compliance_check": {"compliant": True},
            "activity": [],
        })

        self.assertEqual(result["status"], "dispatch_queued")
        self.assertIn("queued", result["activity"][-1]["message"])


class OutboxWorkerTests(IsolatedAsyncioTestCase):
    async def test_successful_delivery_marks_outbox_complete(self) -> None:
        claimed = ("outbox-1", "work-order-1", {"work_order_id": "work-order-1"}, 1)
        with (
            patch.object(outbox_worker, "_claim_due_item", new_callable=AsyncMock, return_value=claimed),
            patch.object(outbox_worker, "dispatch_work_order", new_callable=AsyncMock, return_value="CMMS-17"),
            patch.object(outbox_worker, "_record_success", new_callable=AsyncMock) as record_success,
            patch.object(outbox_worker, "_record_failure", new_callable=AsyncMock) as record_failure,
        ):
            processed = await outbox_worker.process_one_outbox_item()

        self.assertTrue(processed)
        record_success.assert_awaited_once_with("outbox-1", "work-order-1", "CMMS-17")
        record_failure.assert_not_awaited()

    async def test_delivery_failure_is_scheduled_for_retry(self) -> None:
        claimed = ("outbox-2", "work-order-2", {"work_order_id": "work-order-2"}, 2)
        failure = httpx.ConnectError("connection unavailable")
        with (
            patch.object(outbox_worker, "_claim_due_item", new_callable=AsyncMock, return_value=claimed),
            patch.object(outbox_worker, "dispatch_work_order", new_callable=AsyncMock, side_effect=failure),
            patch.object(outbox_worker, "_record_success", new_callable=AsyncMock) as record_success,
            patch.object(outbox_worker, "_record_failure", new_callable=AsyncMock) as record_failure,
        ):
            processed = await outbox_worker.process_one_outbox_item()

        self.assertTrue(processed)
        record_failure.assert_awaited_once_with("outbox-2", "work-order-2", 2, failure)
        record_success.assert_not_awaited()


class DiagnosticFallbackTests(IsolatedAsyncioTestCase):
    async def test_llm_failure_returns_threshold_fallback_with_evidence(self) -> None:
        original_api_key = diagnostic_agent.settings.groq_api_key
        diagnostic_agent.settings.groq_api_key = "test-groq-key"
        model = Mock()
        model.ainvoke = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        retrieved = [{
            "chunk_id": "chunk-1", "document_id": "doc-1", "filename": "pump.pdf",
            "document_type": "manual", "revision": "A", "page_number": 4,
            "section_title": "Vibration", "source_uri": None,
            "content": "bearing maintenance note",
        }]
        try:
            with (
                patch.object(diagnostic_agent, "retrieve_documents", new_callable=AsyncMock, return_value=retrieved),
                patch.object(diagnostic_agent, "ChatGroq", return_value=model),
            ):
                result = await diagnostic_agent.diagnose_alert({
                    "alert_type": "High vibration",
                    "telemetry": {"vibration_mm_s": 8.0, "temperature_c": 60.0},
                })
        finally:
            diagnostic_agent.settings.groq_api_key = original_api_key

        self.assertEqual(result.method, "threshold_fallback")
        self.assertEqual(result.model_version, "threshold-fallback-v1")
        self.assertIn("bearing_wear", result.failure_modes)
        self.assertEqual(result.evidence, ["bearing maintenance note"])
        self.assertEqual(result.evidence_status, "available")
        self.assertEqual(result.evidence_sources[0]["chunk_id"], "chunk-1")

    async def test_scenario_can_force_threshold_fallback_with_groq_configured(self) -> None:
        original_api_key = diagnostic_agent.settings.groq_api_key
        diagnostic_agent.settings.groq_api_key = "test-groq-key"
        try:
            with (
                patch.object(diagnostic_agent, "retrieve_documents", new_callable=AsyncMock, return_value=[]),
                patch.object(diagnostic_agent, "ChatGroq") as groq,
            ):
                result = await diagnostic_agent.diagnose_alert({
                    "alert_type": "High vibration",
                    "telemetry": {"vibration_mm_s": 8.0, "temperature_c": 60.0},
                    "force_llm_fallback": True,
                })
        finally:
            diagnostic_agent.settings.groq_api_key = original_api_key
        self.assertEqual(result.method, "threshold_fallback")
        groq.assert_not_called()

    async def test_retrieval_failure_is_not_reported_as_no_matches(self) -> None:
        with (
            patch.object(diagnostic_agent, "retrieve_documents", new_callable=AsyncMock, side_effect=RuntimeError("db unavailable")),
            self.assertLogs(diagnostic_agent.logger, level="WARNING"),
        ):
            result = await diagnostic_agent.diagnose_alert({
                "asset_type": "pump",
                "alert_type": "High vibration",
                "telemetry": {"vibration_mm_s": 8.0},
            })
        self.assertEqual(result.evidence_status, "unavailable")
        self.assertEqual(result.evidence_error_code, "RuntimeError")
        self.assertEqual(result.evidence, [])

    async def test_successful_empty_retrieval_is_reported_as_no_matches(self) -> None:
        with patch.object(diagnostic_agent, "retrieve_documents", new_callable=AsyncMock, return_value=[]):
            result = await diagnostic_agent.diagnose_alert({"alert_type": "No matching maintenance note"})
        self.assertEqual(result.evidence_status, "no_matches")
        self.assertIsNone(result.evidence_error_code)

    def test_retrieval_query_uses_asset_and_limit_symptoms_not_raw_telemetry(self) -> None:
        query = diagnostic_agent.build_retrieval_query({
            "asset_type": "centrifugal pump",
            "alert_type": "Operator investigation",
            "message": "Vibration has increased.",
            "telemetry": {"vibration_mm_s": 8.0, "temperature_c": 60.0},
            "vibration_threshold_mm_s": 7.1,
            "temperature_threshold_c": 85.0,
        })
        self.assertIn("centrifugal pump", query)
        self.assertIn("vibration 8 mm/s exceeds threshold 7.1 mm/s", query)
        self.assertNotIn("Vibration has increased", query)
        self.assertNotIn("{", query)

    def test_diagnosis_failure_modes_are_closed_vocabulary(self) -> None:
        with self.assertRaises(ValidationError):
            diagnostic_agent.DiagnosisResult.model_validate({
                "diagnosis": "test",
                "confidence": 0.5,
                "recommendation": "inspect",
                "failure_modes": ["invented_failure_mode"],
            })