#!/usr/bin/env python3
"""Platform registration CLI — register scouts, executors, partners, observers.

Usage:
    # Register own scout (auto-approved, full trust)
    olympus-register --id scout-01 --role scout --vertical athena \
        --trust-tier trusted --api-url http://localhost:8000 --api-key <key>

    # Register peer vehicle (requires operator approval)
    olympus-register --id partner-uav-01 --role scout --vertical athena \
        --trust-tier partner --provides-telemetry --provides-detections \
        --command-authority advisory \
        --accepted-commands "emergency_stop,recall_for_update" \
        --cbba-participant --ttl 7200 \
        --api-url http://localhost:8000 --api-key <partner-key>

    # Register observer (read-only, auto-approved)
    olympus-register --id observer-01 --role observer \
        --trust-tier observer --api-url http://localhost:8000 --api-key <key>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import aiohttp
import asyncio


async def register(args: argparse.Namespace) -> None:
    url = f"{args.api_url.rstrip('/')}/api/v1/vehicles/register"
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    body: dict = {
        "vehicle_id": args.id,
        "role": args.role,
        "trust_tier": args.trust_tier,
    }

    if args.capabilities:
        body["capabilities"] = [c.strip() for c in args.capabilities.split(",")]

    if args.home_lat is not None and args.home_lon is not None:
        body["position"] = {
            "latitude": args.home_lat,
            "longitude": args.home_lon,
            "altitude": args.home_alt,
            "heading": 0.0,
        }

    # Build capability manifest for partner/observer registrations
    if args.trust_tier in ("partner", "observer"):
        manifest: dict = {
            "provides_telemetry": args.provides_telemetry,
            "provides_detections": args.provides_detections,
            "provides_features": args.provides_features,
            "command_authority": args.command_authority,
            "participates_in_cbba": args.cbba_participant,
            "ttl_seconds": args.ttl,
            "data_encryption_required": False,
        }
        if args.accepted_commands:
            manifest["accepted_commands"] = [
                c.strip() for c in args.accepted_commands.split(",")
            ]
        else:
            manifest["accepted_commands"] = []
        body["capability_manifest"] = manifest

    print(f"Registering {args.role} '{args.id}' (vertical={args.vertical}, tier={args.trust_tier})...")
    print(f"  API: {url}")
    if body.get("capabilities"):
        print(f"  Capabilities: {body['capabilities']}")
    if body.get("capability_manifest"):
        m = body["capability_manifest"]
        print(f"  Trust tier: {args.trust_tier}")
        print(f"  Command authority: {m['command_authority']}")
        print(f"  Accepted commands: {m.get('accepted_commands', [])}")
        print(f"  CBBA participant: {m['participates_in_cbba']}")
        print(f"  TTL: {m['ttl_seconds']}s")
    if body.get("position"):
        pos = body["position"]
        print(f"  Home: ({pos['latitude']}, {pos['longitude']}, alt={pos['altitude']}m)")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                result = await resp.json()
                if resp.status in (200, 201, 202):
                    status = result.get("registration_status", "approved")
                    print(f"\nRegistered successfully! (status: {status})")
                    print(f"  Vehicle ID: {result.get('vehicle_id', args.id)}")
                    print(f"  Message: {result.get('message', 'OK')}")
                    if status == "pending":
                        print(f"\n  Awaiting operator approval.")
                else:
                    print(f"\nRegistration failed (HTTP {resp.status}):")
                    print(f"  {json.dumps(result, indent=2)}")
                    sys.exit(1)
    except aiohttp.ClientError as e:
        print(f"\nConnection error: {e}")
        print("Is the Vehicle API running?")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="olympus-register",
        description="Register a drone platform with the OLYMPUS Vehicle API",
    )
    parser.add_argument("--id", required=True, help="Vehicle ID (e.g. scout-01)")
    parser.add_argument(
        "--role",
        required=True,
        choices=["scout", "executor", "observer", "ground_vehicle", "aircraft"],
        help="Vehicle role",
    )
    parser.add_argument(
        "--vertical",
        default="athena",
        choices=["athena", "ceres", "vulcan", "hermes"],
        help="Mission vertical (default: athena)",
    )
    parser.add_argument(
        "--trust-tier",
        default="trusted",
        choices=["trusted", "partner", "observer"],
        help="Trust tier (default: trusted). Partner requires approval.",
    )
    parser.add_argument(
        "--capabilities",
        default=None,
        help="Comma-separated capabilities (e.g. investigate,photograph,mark,relay)",
    )
    parser.add_argument("--home-lat", type=float, default=None, help="Home latitude")
    parser.add_argument("--home-lon", type=float, default=None, help="Home longitude")
    parser.add_argument("--home-alt", type=float, default=0.0, help="Home altitude (m)")

    # Capability manifest flags (for partner/observer)
    parser.add_argument(
        "--provides-telemetry", action="store_true", default=True,
        help="Vehicle provides telemetry data (default: true)",
    )
    parser.add_argument(
        "--provides-detections", action="store_true", default=False,
        help="Vehicle provides detection data",
    )
    parser.add_argument(
        "--provides-features", action="store_true", default=False,
        help="Vehicle provides intermediate features for P2P learning",
    )
    parser.add_argument(
        "--command-authority",
        default="none",
        choices=["binding", "advisory", "none"],
        help="Command authority level (default: none)",
    )
    parser.add_argument(
        "--accepted-commands",
        default=None,
        help="Comma-separated commands this vehicle accepts (e.g. emergency_stop,recall_for_update)",
    )
    parser.add_argument(
        "--cbba-participant", action="store_true", default=False,
        help="Vehicle participates in CBBA task allocation",
    )
    parser.add_argument(
        "--ttl", type=int, default=3600,
        help="Registration TTL in seconds (default: 3600, 0=no expiry)",
    )

    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Vehicle API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (prefer OLYMPUS_API_KEY env var to avoid exposure in process listing)",
    )

    args = parser.parse_args()

    # Prefer env var for API key (CLI args are visible in `ps` output)
    if not args.api_key:
        args.api_key = os.environ.get("OLYMPUS_API_KEY")
    elif args.api_key:
        import warnings
        warnings.warn(
            "API key passed via --api-key is visible in process listings. "
            "Prefer setting the OLYMPUS_API_KEY environment variable.",
            stacklevel=2,
        )

    # Validate vehicle ID format
    import re
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", args.id):
        parser.error("Vehicle ID must be 1-64 chars: alphanumeric, hyphens, underscores only")

    asyncio.run(register(args))


if __name__ == "__main__":
    main()
