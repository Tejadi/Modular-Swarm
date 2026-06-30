"""OLYMPUS Advisor HTTP API — aiohttp server proxying to Ollama LLM.

Endpoints:
    POST /api/advisor/chat    — Chat with the advisor
    GET  /api/advisor/health  — Health check (Ollama connectivity)
"""

import asyncio
import json
import os
import re
import logging
from aiohttp import web

logger = logging.getLogger("olympus.advisor.api")

MAX_MESSAGE_LENGTH = 2000

_INJECTION_PATTERNS = re.compile(
    r"(ignore previous|system prompt|you are now|forget everything|disregard)",
    re.IGNORECASE,
)


def sanitize_message(message: str) -> str:
    message = message[:MAX_MESSAGE_LENGTH].strip()
    message = "".join(ch for ch in message if ch == "\n" or (ch.isprintable()))
    return message


def _create_advisor():
    from .advisor import OllamaClient, SwarmContextGatherer, RetrainingAssessor, OlympusAdvisor
    from .mission_profile import load_profile

    profile = load_profile()
    logger.info(f"Loaded mission profile: {profile.id} ({profile.name})")

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
    vehicle_api_url = os.environ.get("VEHICLE_API_URL_INTERNAL", "http://localhost:3001")

    ollama_client = OllamaClient(base_url=ollama_url, model=ollama_model)
    context_gatherer = SwarmContextGatherer(vehicle_api_url=vehicle_api_url)
    retraining_assessor = RetrainingAssessor()

    logger.info(f"Ollama endpoint: {ollama_url}, model: {ollama_model}")
    logger.info(f"Vehicle API for context: {vehicle_api_url}")

    return OlympusAdvisor(
        ollama_client=ollama_client,
        context_gatherer=context_gatherer,
        retraining_assessor=retraining_assessor,
        profile=profile,
    )


_advisor = None


def get_advisor():
    global _advisor
    if _advisor is None:
        _advisor = _create_advisor()
    return _advisor


async def handle_chat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": "Invalid JSON body"}, status=400
        )

    message = body.get("message", "").strip()
    if not message:
        return web.json_response(
            {"error": "Message is required"}, status=400
        )

    message = sanitize_message(message)

    if _INJECTION_PATTERNS.search(message):
        logger.warning(f"Potential injection attempt blocked: {message[:50]}...")
        return web.json_response(
            {"error": "Message contains disallowed patterns"}, status=400
        )

    advisor = get_advisor()

    try:
        response, sources = await advisor.chat(message)
        return web.json_response({
            "response": response,
            "sources": sources,
        })
    except Exception as e:
        logger.error(f"Advisor chat error: {e}")
        return web.json_response(
            {"error": "Internal advisor error"}, status=500
        )


async def handle_health(request: web.Request) -> web.Response:
    advisor = get_advisor()
    health = await advisor._ollama.health_check()
    return web.json_response({
        "status": "ok",
        "ollama_connected": health.get("connected", False),
        "model": health.get("model", "unknown"),
        "model_available": health.get("model_available", False),
    })


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/advisor/chat", handle_chat)
    app.router.add_get("/api/advisor/health", handle_health)
    return app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    port = int(os.environ.get("ADVISOR_PORT", "8080"))
    logger.info(f"Starting OLYMPUS Advisor API on port {port}")

    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
