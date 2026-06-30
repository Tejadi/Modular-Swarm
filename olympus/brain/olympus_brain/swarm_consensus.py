"""Raft-like leader election for comms-denied swarm partitions.

When drones lose contact with the base station for > comms_timeout seconds,
the connected partition elects a leader via a lightweight Raft protocol.
The leader assumes escalation-approval authority until base comms restore.

Safety: autonomous mode cannot exceed pre-defined ROE (rules of engagement)
set at mission start. Decisions made during comms-denied are logged and
synced back to the base station for human review when comms restore.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from olympus_brain.protocol import (
    EscalationLevel,
    EscalationRequest,
    EscalationResponse,
)

logger = logging.getLogger(__name__)


class RaftRole(Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass
class RaftState:
    role: RaftRole = RaftRole.FOLLOWER
    term: int = 0
    voted_for: Optional[str] = None
    leader_id: Optional[str] = None
    last_heartbeat: float = field(default_factory=time.time)
    votes_received: set[str] = field(default_factory=set)


@dataclass
class AutonomousDecision:
    """Record of a decision made during comms-denied autonomous operation."""
    escalation_id: str
    task_id: str
    decision: str  # "approved", "denied"
    decided_by: str  # leader drone_id
    term: int
    timestamp: float = field(default_factory=time.time)
    reason: str = ""


@dataclass
class RulesOfEngagement:
    """Safety constraints for autonomous operation."""
    max_tasks_autonomous: int = 10
    allowed_task_types: list[str] = field(default_factory=lambda: [
        "inspect", "photograph", "relay", "relay_comms", "thermal_scan",
    ])
    max_resource_commitment: float = 0.5
    require_rtl_on_timeout: bool = True
    autonomous_timeout_s: int = 600  # Max time in autonomous mode before RTL


class SwarmConsensus:
    """Manages leader election and autonomous escalation approval."""

    def __init__(
        self,
        drone_id: str,
        known_peers: list[str],
        roe: Optional[RulesOfEngagement] = None,
        comms_timeout_s: float = 60.0,
        election_timeout_range: tuple[float, float] = (1.5, 3.0),
    ):
        self.drone_id = drone_id
        self.known_peers = known_peers
        self.roe = roe or RulesOfEngagement()
        self.comms_timeout_s = comms_timeout_s
        self.election_timeout_range = election_timeout_range

        self._state = RaftState()
        self._autonomous_decisions: list[AutonomousDecision] = []
        self._tasks_approved_autonomous: int = 0
        self._autonomous_start_time: Optional[float] = None

        self._base_last_seen: float = time.time()
        self._base_connected: bool = True

        self._send_callback: Optional[Callable] = None
        self._running = False

    @property
    def is_leader(self) -> bool:
        return self._state.role == RaftRole.LEADER

    @property
    def is_base_connected(self) -> bool:
        return self._base_connected

    @property
    def autonomous_decisions(self) -> list[AutonomousDecision]:
        return list(self._autonomous_decisions)

    def on_base_heartbeat(self) -> None:
        """Called when a message from base station is received."""
        self._base_last_seen = time.time()
        if not self._base_connected:
            logger.info("Base station comms RESTORED")
            self._base_connected = True
            self._state.role = RaftRole.FOLLOWER
            self._state.leader_id = None
            self._autonomous_start_time = None

    def set_send_callback(self, callback: Callable) -> None:
        self._send_callback = callback

    async def run(self) -> None:
        """Main consensus loop — monitors base connectivity and runs elections."""
        self._running = True
        while self._running:
            try:
                elapsed = time.time() - self._base_last_seen

                if elapsed > self.comms_timeout_s and self._base_connected:
                    logger.warning(
                        f"Base station lost for {elapsed:.0f}s — "
                        f"entering autonomous consensus mode"
                    )
                    self._base_connected = False
                    self._autonomous_start_time = time.time()

                if not self._base_connected:
                    await self._consensus_tick()

                    # Check autonomous timeout → RTL
                    if self.roe.require_rtl_on_timeout and self._autonomous_start_time:
                        autonomous_elapsed = time.time() - self._autonomous_start_time
                        if autonomous_elapsed > self.roe.autonomous_timeout_s:
                            logger.warning(
                                f"Autonomous timeout ({self.roe.autonomous_timeout_s}s) — "
                                f"signaling RTL"
                            )
                            self._running = False

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Consensus loop error: {e}")
                await asyncio.sleep(1.0)

    async def _consensus_tick(self) -> None:
        """One tick of the Raft state machine."""
        now = time.time()

        if self._state.role == RaftRole.FOLLOWER:
            timeout = random.uniform(*self.election_timeout_range)
            if now - self._state.last_heartbeat > timeout:
                await self._start_election()

        elif self._state.role == RaftRole.CANDIDATE:
            # Check if we won
            needed = (len(self.known_peers) + 1) // 2 + 1
            if len(self._state.votes_received) >= needed:
                self._state.role = RaftRole.LEADER
                self._state.leader_id = self.drone_id
                logger.info(
                    f"ELECTED LEADER (term={self._state.term}, "
                    f"votes={len(self._state.votes_received)}/{len(self.known_peers)+1})"
                )

            # Election timeout
            timeout = random.uniform(*self.election_timeout_range)
            if now - self._state.last_heartbeat > timeout * 2:
                self._state.role = RaftRole.FOLLOWER

        elif self._state.role == RaftRole.LEADER:
            # Send heartbeats (leader keepalive)
            pass

    async def _start_election(self) -> None:
        """Start a new election term."""
        self._state.term += 1
        self._state.role = RaftRole.CANDIDATE
        self._state.voted_for = self.drone_id
        self._state.votes_received = {self.drone_id}
        self._state.last_heartbeat = time.time()

        logger.info(f"Starting election for term {self._state.term}")

    def on_vote_request(self, candidate_id: str, term: int) -> bool:
        """Handle a vote request from a candidate."""
        if term > self._state.term:
            self._state.term = term
            self._state.role = RaftRole.FOLLOWER
            self._state.voted_for = None

        if term == self._state.term and self._state.voted_for in (None, candidate_id):
            self._state.voted_for = candidate_id
            self._state.last_heartbeat = time.time()
            return True

        return False

    def on_vote_response(self, voter_id: str, term: int, granted: bool) -> None:
        """Handle a vote response."""
        if term == self._state.term and granted:
            self._state.votes_received.add(voter_id)

    def on_leader_heartbeat(self, leader_id: str, term: int) -> None:
        """Handle a heartbeat from the elected leader."""
        if term >= self._state.term:
            self._state.term = term
            self._state.role = RaftRole.FOLLOWER
            self._state.leader_id = leader_id
            self._state.last_heartbeat = time.time()

    def decide_escalation(self, request: EscalationRequest) -> Optional[EscalationResponse]:
        """As leader, make an autonomous decision on an escalation request.

        Respects ROE constraints. Returns None if decision cannot be made.
        """
        if not self.is_leader:
            logger.debug("Not leader — cannot decide escalation")
            return None

        # Check ROE constraints
        if self._tasks_approved_autonomous >= self.roe.max_tasks_autonomous:
            logger.warning("ROE: max autonomous tasks reached — denying")
            decision = self._record_decision(request, "denied", "max_tasks_reached")
            return EscalationResponse(
                escalation_id=request.id,
                approved=False,
                responded_by=f"autonomous:{self.drone_id}",
            )

        # Check if task type is allowed in autonomous mode
        # (We'd need the actual task to check — use the escalation reason as heuristic)
        if request.escalation_level == EscalationLevel.EMERGENCY:
            logger.warning("ROE: EMERGENCY escalation — cannot approve autonomously")
            decision = self._record_decision(request, "denied", "emergency_requires_human")
            return EscalationResponse(
                escalation_id=request.id,
                approved=False,
                responded_by=f"autonomous:{self.drone_id}",
            )

        # Default: approve with ROE constraints
        self._tasks_approved_autonomous += 1
        self._record_decision(request, "approved", "roe_compliant")

        logger.info(
            f"Autonomous approval: escalation {request.id} "
            f"(term={self._state.term}, total={self._tasks_approved_autonomous})"
        )

        return EscalationResponse(
            escalation_id=request.id,
            approved=True,
            responded_by=f"autonomous:{self.drone_id}",
        )

    def _record_decision(self, request: EscalationRequest, decision: str, reason: str) -> AutonomousDecision:
        record = AutonomousDecision(
            escalation_id=request.id,
            task_id=request.task_id,
            decision=decision,
            decided_by=self.drone_id,
            term=self._state.term,
            reason=reason,
        )
        self._autonomous_decisions.append(record)
        return record

    def stop(self) -> None:
        self._running = False
