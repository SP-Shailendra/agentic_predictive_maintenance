import argparse
import asyncio
import json
import logging
import os
import ssl

import httpx

from core.config import settings
from services.edge_gateway import normalize_gateway_reading, post_reading_batch, utc_now

logger = logging.getLogger("fieldnote.edge_gateway")


async def run_mqtt(args: argparse.Namespace, client: httpx.AsyncClient) -> None:
    import aiomqtt

    tls_context = None
    if args.mqtt_tls_enabled:
        tls_context = ssl.create_default_context(cafile=args.mqtt_ca_file)
        if args.mqtt_client_cert:
            tls_context.load_cert_chain(args.mqtt_client_cert, args.mqtt_client_key)
    while True:
        try:
            async with aiomqtt.Client(
                hostname=args.mqtt_host,
                port=args.mqtt_port,
                username=args.mqtt_username,
                password=args.mqtt_password,
                tls_context=tls_context,
            ) as mqtt_client:
                await mqtt_client.subscribe(args.mqtt_topic)
                async for message in mqtt_client.messages:
                    payload = json.loads(message.payload.decode("utf-8"))
                    reading = normalize_gateway_reading(payload, source="mqtt")
                    await post_reading_batch(client, args.api_url, [reading])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("MQTT gateway disconnected (%s); retrying in five seconds", type(error).__name__)
            await asyncio.sleep(5)


async def run_opcua(args: argparse.Namespace, client: httpx.AsyncClient) -> None:
    from asyncua import Client

    node_map = json.loads(args.opcua_node_map)
    required = {"asset_id", "vibration_mm_s", "temperature_c", "pressure_bar", "rpm"}
    if not isinstance(node_map, list) or any(not isinstance(item, dict) or not required.issubset(item) for item in node_map):
        raise ValueError("OPC_UA_NODE_MAP must be a JSON array of asset_id and four signal node IDs.")
    while True:
        try:
            opcua_client = Client(url=args.opcua_endpoint)
            if args.opcua_security_string:
                await opcua_client.set_security_string(args.opcua_security_string)
            if args.opcua_username:
                opcua_client.set_user(args.opcua_username)
                opcua_client.set_password(args.opcua_password or "")
            async with opcua_client:
                while True:
                    readings = []
                    for mapping in node_map:
                        values = await asyncio.gather(*(
                            opcua_client.get_node(mapping[field]).read_value()
                            for field in ("vibration_mm_s", "temperature_c", "pressure_bar", "rpm")
                        ))
                        payload = {
                            "asset_id": mapping["asset_id"],
                            "timestamp": utc_now().isoformat(),
                            "vibration_mm_s": values[0],
                            "temperature_c": values[1],
                            "pressure_bar": values[2],
                            "rpm": values[3],
                        }
                        readings.append(normalize_gateway_reading(payload, source="opcua"))
                    await post_reading_batch(client, args.api_url, readings)
                    await asyncio.sleep(args.opcua_interval)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("OPC UA gateway disconnected (%s); retrying in five seconds", type(error).__name__)
            await asyncio.sleep(5)


async def main_async(args: argparse.Namespace) -> None:
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        tasks = []
        if args.mqtt_host:
            tasks.append(asyncio.create_task(run_mqtt(args, client)))
        if args.opcua_endpoint:
            if not args.opcua_node_map:
                raise ValueError("OPC_UA_NODE_MAP is required when OPC_UA_ENDPOINT is configured.")
            tasks.append(asyncio.create_task(run_opcua(args, client)))
        if not tasks:
            raise ValueError("Configure at least one of MQTT_BROKER_HOST or OPC_UA_ENDPOINT.")
        await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward MQTT or OPC UA readings to the Fieldnote telemetry ingest API.")
    parser.add_argument("--api-url", default=os.getenv("FIELDNOTE_API_URL", settings.fieldnote_api_url))
    parser.add_argument("--api-key", default=settings.api_key)
    parser.add_argument("--mqtt-host", default=settings.mqtt_broker_host)
    parser.add_argument("--mqtt-port", type=int, default=settings.mqtt_broker_port)
    mqtt_tls = parser.add_mutually_exclusive_group()
    mqtt_tls.add_argument("--mqtt-tls", dest="mqtt_tls_enabled", action="store_true")
    mqtt_tls.add_argument("--mqtt-no-tls", dest="mqtt_tls_enabled", action="store_false")
    parser.set_defaults(mqtt_tls_enabled=settings.mqtt_tls_enabled)
    parser.add_argument("--mqtt-topic", default=settings.mqtt_topic)
    parser.add_argument("--mqtt-username", default=settings.mqtt_username)
    parser.add_argument("--mqtt-password", default=settings.mqtt_password)
    parser.add_argument("--mqtt-ca-file", default=settings.mqtt_ca_file)
    parser.add_argument("--mqtt-client-cert", default=settings.mqtt_client_cert)
    parser.add_argument("--mqtt-client-key", default=settings.mqtt_client_key)
    parser.add_argument("--opcua-endpoint", default=settings.opc_ua_endpoint)
    parser.add_argument("--opcua-username", default=settings.opc_ua_username)
    parser.add_argument("--opcua-password", default=settings.opc_ua_password)
    parser.add_argument("--opcua-node-map", default=settings.opc_ua_node_map)
    parser.add_argument("--opcua-security-string", default=settings.opc_ua_security_string)
    parser.add_argument("--opcua-interval", type=float, default=settings.opc_ua_interval_seconds)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()