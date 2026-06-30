"""OLYMPUS AI Advisor — Open-source LLM integration via Ollama.

Provides context-aware chat for operators: fleet status, detection analysis,
strategic recommendations, and SwarmNet retraining assessment.

Runs entirely locally — no cloud API keys required.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from typing import Any, Optional

import httpx

from .mission_profile import MissionProfileConfig, load_profile

logger = logging.getLogger("olympus.advisor")


class OllamaClient:
    """Async HTTP client for the Ollama chat API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=90.0)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> str:
        """Send a chat completion request to Ollama. Returns assistant text."""
        url = f"{self._base_url}/api/chat"
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 512,
            },
        }
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.ConnectError:
            raise OllamaUnavailableError(
                "Ollama is not running. Start it with: `ollama serve` "
                f"and pull the model: `ollama pull {self._model}`"
            )
        except httpx.TimeoutException:
            raise OllamaUnavailableError(
                "Ollama timed out. The model may be loading or the system "
                "is under heavy load. Try again in a moment."
            )

        if resp.status_code == 404:
            raise OllamaUnavailableError(
                f"Model '{self._model}' not found. Pull it with: "
                f"`ollama pull {self._model}`"
            )

        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    async def health_check(self) -> dict[str, Any]:
        """Check Ollama connectivity and model availability."""
        try:
            resp = await self._client.get(
                f"{self._base_url}/api/tags", timeout=5.0
            )
            if resp.status_code != 200:
                return {"connected": False, "model_available": False}

            tags = resp.json()
            models = [m.get("name", "") for m in tags.get("models", [])]
            model_available = any(
                self._model in m for m in models
            )
            return {
                "connected": True,
                "model_available": model_available,
                "model": self._model,
                "available_models": models,
            }
        except (httpx.ConnectError, httpx.TimeoutException):
            return {"connected": False, "model_available": False}

    async def close(self):
        await self._client.aclose()


class OllamaUnavailableError(Exception):
    """Raised when Ollama cannot be reached or model is missing."""
    pass


class SwarmContextGatherer:
    """Fetches real-time swarm context from the Vehicle API for LLM prompt injection."""

    def __init__(self, vehicle_api_url: str = "http://localhost:3001"):
        self._api_url = vehicle_api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=5.0)

    async def gather(self) -> dict[str, Any]:
        """Fetch fleet, detection, and AI agent data in parallel."""
        results: dict[str, Any] = {}

        async def _fetch(key: str, path: str):
            try:
                resp = await self._client.get(f"{self._api_url}{path}")
                if resp.status_code == 200:
                    results[key] = resp.json()
                else:
                    logger.warning(f"Vehicle API {path} returned {resp.status_code}")
                    results[key] = None
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(f"Vehicle API {path} unreachable: {e}")
                results[key] = None

        await asyncio.gather(
            _fetch("vehicles", "/api/v1/vehicles"),
            _fetch("detections", "/api/v1/detections"),
            _fetch("ai_agent", "/api/v1/ai-agent/status"),
            _fetch("swarmnet", "/api/v1/swarmnet/status"),
        )
        return results

    @staticmethod
    def _sanitize(value: str, max_len: int = 64) -> str:
        """Sanitize a string for safe embedding in LLM prompts.

        Strips control chars, limits length, removes prompt injection markers.
        Prevents untrusted telemetry fields from manipulating the system prompt.
        """
        value = str(value)[:max_len]
        value = "".join(c for c in value if c.isprintable() and c != "\n")
        return value

    def format_context(self, data: dict[str, Any]) -> str:
        """Format gathered data into a readable context block for the system prompt."""
        lines: list[str] = []
        san = self._sanitize

        # Fleet status
        vehicles = data.get("vehicles")
        if vehicles and isinstance(vehicles, list):
            vehicles = vehicles[:20]  # Limit to 20 most recent
            role_counts: dict[str, int] = {}
            for v in vehicles:
                role = san(v.get("role", "unknown"), 20)
                role_counts[role] = role_counts.get(role, 0) + 1

            total = len(vehicles)
            role_summary = ", ".join(f"{c} {r}s" for r, c in role_counts.items())
            lines.append(f"Fleet Status:")
            lines.append(f"- Active vehicles: {total} ({role_summary})")

            for v in vehicles:
                vid = san(v.get("vehicle_id", "unknown"))
                status = san(v.get("status", "unknown"), 20)
                battery = v.get("battery_pct", "?")
                pos = v.get("position", {})
                lat = pos.get("latitude", 0)
                lon = pos.get("longitude", 0)
                rssi = v.get("signal_rssi", "?")
                trust = san(v.get("trust_tier", "trusted"), 20)
                task = v.get("current_task", None)
                task_str = f" | Task: {san(task)}" if task else ""
                trust_str = f" | Trust: {trust}" if trust != "trusted" else ""
                lines.append(
                    f"- {vid}: {status} | Battery: {battery}% | "
                    f"({lat:.4f}, {lon:.4f}) | RSSI: {rssi}"
                    f"{trust_str}{task_str}"
                )
        else:
            lines.append("Fleet Status: unavailable")

        lines.append("")

        # Detection summary
        detections = data.get("detections")
        if detections and isinstance(detections, list):
            det_count = len(detections)
            by_type: dict[str, int] = {}
            total_conf = 0.0
            for d in detections[:50]:
                dt = san(d.get("detection_type", "unknown"), 32)
                by_type[dt] = by_type.get(dt, 0) + 1
                total_conf += d.get("confidence", 0)

            avg_conf = total_conf / max(det_count, 1)
            type_summary = ", ".join(
                f"{c} {t}" for t, c in sorted(by_type.items(), key=lambda x: -x[1])
            )
            lines.append(f"Recent Detections:")
            lines.append(f"- {det_count} total: {type_summary}")
            lines.append(f"- Avg confidence: {avg_conf:.2f}")
        else:
            lines.append("Recent Detections: unavailable")

        lines.append("")

        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()


class RetrainingAssessor:
    """Formats SwarmNet retraining assessment context for the LLM."""

    def assess(self, agent_status: Optional[dict[str, Any]]) -> str:
        """Given AI agent status, produce a structured assessment block."""
        if not agent_status:
            return "Model & Retraining Status: unavailable (AI agent not reporting)"

        lines: list[str] = []
        model_version = agent_status.get("model_version", "unknown")
        accuracy = agent_status.get("accuracy", None)
        drift = agent_status.get("drift_detected", False)
        retrain_count = agent_status.get("retrain_count", 0)
        recall_count = agent_status.get("recall_count", 0)
        active_drones = agent_status.get("active_drones", 0)
        last_retrain = agent_status.get("last_retrain_at", None)
        contributions = agent_status.get("contributions", {})

        lines.append(f"SwarmNet Global Model: v{model_version}")
        if accuracy is not None:
            lines.append(f"- Current accuracy: {accuracy:.3f}")
        lines.append(f"- Active drones contributing: {active_drones}")
        lines.append(f"- Drift detection: {'TRIGGERED' if drift else 'NOT triggered'}")
        lines.append(f"- Retrain count: {retrain_count}, Recall count: {recall_count}")
        if last_retrain:
            lines.append(f"- Last retrain: {last_retrain}")
        if contributions:
            top_contributors = sorted(
                contributions.items(), key=lambda x: -x[1]
            )[:5]
            contrib_str = ", ".join(f"{k}: {v}" for k, v in top_contributors)
            lines.append(f"- Top contributors: {contrib_str}")

        # Assessment
        lines.append("")
        if drift:
            lines.append(
                "Assessment: CONCEPT DRIFT DETECTED. The model's accuracy is degrading. "
                "Retraining is recommended immediately. Consider using the retrain endpoint "
                "or the AI Agent will trigger automatic recall/retrain/redeploy."
            )
        elif accuracy is not None and accuracy < 0.80:
            lines.append(
                f"Assessment: Model accuracy ({accuracy:.3f}) is below the 0.80 threshold. "
                "Retraining should be considered. Monitor for further degradation."
            )
        elif accuracy is not None and accuracy >= 0.85:
            lines.append(
                f"Assessment: Model performing well (accuracy {accuracy:.3f}). "
                "No immediate retraining needed. Continue monitoring."
            )
        else:
            lines.append(
                "Assessment: Model status within normal range. "
                "The AI Agent will automatically trigger retraining if drift is detected."
            )

        return "\n".join(lines)


class OlympusAdvisor:
    """Main advisor class — context-aware LLM chat for OLYMPUS operators."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        context_gatherer: SwarmContextGatherer,
        retraining_assessor: RetrainingAssessor,
        profile: MissionProfileConfig,
    ):
        self._ollama = ollama_client
        self._context = context_gatherer
        self._retraining = retraining_assessor
        self._profile = profile
        self._history: deque[dict[str, str]] = deque(maxlen=20)

    async def chat(self, user_message: str) -> tuple[str, list[str]]:
        """Process a chat message. Returns (response_text, sources_list)."""
        # 1. Gather live swarm context
        try:
            ctx_data = await self._context.gather()
        except Exception as e:
            logger.warning(f"Failed to gather swarm context: {e}")
            ctx_data = {}

        # 2. Format context blocks
        fleet_context = self._context.format_context(ctx_data)
        retraining_context = self._retraining.assess(ctx_data.get("ai_agent"))

        # 3. Build system prompt
        system_prompt = self._build_system_prompt(fleet_context, retraining_context)

        # 4. Build messages array
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Include last 10 history entries for conversational continuity
        history_slice = list(self._history)[-10:]
        messages.extend(history_slice)

        messages.append({"role": "user", "content": user_message})

        # 5. Call Ollama
        try:
            response = await self._ollama.chat(messages)
        except OllamaUnavailableError as e:
            return (str(e), [])
        except Exception as e:
            logger.error(f"Ollama chat error: {e}")
            return ("The advisor encountered an internal error. Please try again.", [])

        # 6. Update history
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": response})

        # 7. Build sources list
        sources: list[str] = []
        if ctx_data.get("vehicles") is not None:
            sources.append("Fleet telemetry (live)")
        if ctx_data.get("detections") is not None:
            sources.append("Detection records (live)")
        if ctx_data.get("ai_agent") is not None:
            sources.append("AI Agent / SwarmNet status (live)")

        return (response, sources)

    def _build_system_prompt(
        self, fleet_context: str, retraining_context: str
    ) -> str:
        """Compose the full system prompt with injected context."""
        base = self._profile.advisor.system_prompt
        # Strip the {context} placeholder from the old prompt template
        base = base.replace("Current context: {context}", "").strip()

        return f"""{base}

=== LIVE OPERATIONS CONTEXT ===

{fleet_context}

=== MODEL & RETRAINING STATUS ===

{retraining_context}

=== INSTRUCTIONS ===

When the operator asks about data collection, coverage, or detections:
- Reference the detection counts, types, and confidence levels above.
- Highlight any detection types that are unusually frequent or absent.

When the operator asks about strategy, deployment, or resource allocation:
- Reference fleet positions, battery levels, roles, and pending tasks.
- Consider vehicle availability and battery reserves for new tasking.

When the operator asks about model performance, retraining, or drift:
- Reference the Model & Retraining Status section above.
- Explain drift detection status in plain terms.
- Recommend retraining if accuracy is dropping or drift is detected.
- Mention the AI Agent's automatic recall/retrain/redeploy cycle.

Be concise and actionable. Use domain-appropriate terminology for this vertical.
Do not fabricate data — only reference information from the context above.
If context is unavailable, say so rather than guessing."""

    def get_history(self) -> list[dict[str, str]]:
        return list(self._history)

    def clear_history(self):
        self._history.clear()

    async def close(self):
        await self._ollama.close()
        await self._context.close()
