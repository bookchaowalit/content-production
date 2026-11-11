"""Moving-hazard timing on the GameIR grid, and the proof that a safe run exists.

Time is counted in ticks: one tick is the time the player needs to cross one
cell (``tile_size / player.speed`` seconds). A hazard walks its ``path``
forward and back (ping-pong) and needs ``step_ticks`` ticks per cell, moving in a
straight line between cell centres, as ``HazardPatrol`` does in Unity.

Safety is judged conservatively. During tick ``t`` a hazard is somewhere
between two path cells, so both cells count as dangerous for the whole tick.
The player spends tick ``t`` either waiting in a cell or moving to a
neighbour, and both cells it touches must be free of danger. A schedule found
under this rule also avoids every hazard in the continuous game, as long as the
hazard and player colliders are smaller than one cell.

Spawn, objectives and the exit may not lie on a hazard path, so the player is
always safe while waiting there. Which off-path cells can be reached safely
therefore does not depend on the phase, so collecting the objectives in a
greedy order (earliest safe arrival first) finds a safe run whenever one exists.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import Cell, GameIR, HazardIR

MAX_HAZARD_PERIOD_TICKS = 240
MAX_HAZARD_WORLD_SIDE = 32
_MOVES = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))


def hazard_sequence(hazard: HazardIR) -> tuple[Cell, ...]:
    """Cells visited in one ping-pong cycle: path forward, then back without repeating the ends."""

    path = hazard.path
    return (*path, *path[-2:0:-1])


def hazard_period_ticks(hazard: HazardIR) -> int:
    return len(hazard_sequence(hazard)) * hazard.step_ticks


def hazards_period_ticks(hazards: Sequence[HazardIR]) -> int:
    """Ticks after which every hazard is back where it started (1 when there are none)."""

    period = 1
    for hazard in hazards:
        period = math.lcm(period, hazard_period_ticks(hazard))
    return period


def hazard_cells_at(hazard: HazardIR, tick: int) -> tuple[Cell, Cell]:
    """The two cells the hazard moves between during ``tick``."""

    sequence = hazard_sequence(hazard)
    index = (tick // hazard.step_ticks) % len(sequence)
    return sequence[index], sequence[(index + 1) % len(sequence)]


def danger_table(hazards: Sequence[HazardIR], period: int) -> list[frozenset[Cell]]:
    return [frozenset(cell for hazard in hazards for cell in hazard_cells_at(hazard, tick)) for tick in range(period)]


@dataclass(frozen=True, slots=True)
class SafeLeg:
    start_tick: int
    arrival_tick: int
    cells: tuple[Cell, ...]  # one entry per tick, the start cell first


@dataclass(frozen=True, slots=True)
class SafeRun:
    order: tuple[int, ...]  # objective indexes in visiting order
    total_ticks: int
    legs: tuple[SafeLeg, ...]


class _Grid:
    def __init__(self, ir: GameIR) -> None:
        self.width = ir.world.width
        self.height = ir.world.height
        self.walls = frozenset(cell for wall in ir.walls for cell in wall.cells)
        self.period = hazards_period_ticks(ir.hazards)
        self.danger = danger_table(ir.hazards, self.period)

    def search(self, start: Cell, start_tick: int, goals: Iterable[Cell]) -> dict[Cell, SafeLeg]:
        """Earliest safe arrival from (start, start_tick) at each reachable goal cell."""

        wanted = set(goals)
        found: dict[Cell, SafeLeg] = {}
        phase = start_tick % self.period
        parents: dict[tuple[Cell, int], tuple[Cell, int] | None] = {(start, phase): None}
        ticks = {(start, phase): start_tick}
        pending = deque([(start, phase)])
        while pending and len(found) < len(wanted):
            cell, phase = pending.popleft()
            tick = ticks[(cell, phase)]
            danger = self.danger[phase]
            if cell in danger:
                continue
            for dx, dy in _MOVES:
                target = (cell[0] + dx, cell[1] + dy)
                if not (0 <= target[0] < self.width and 0 <= target[1] < self.height) or target in self.walls or target in danger:
                    continue
                state = (target, (phase + 1) % self.period)
                if state in parents:
                    continue
                parents[state] = (cell, phase)
                ticks[state] = tick + 1
                pending.append(state)
                if target in wanted and target not in found:
                    found[target] = self._leg(parents, ticks, state, start_tick)
        return found

    @staticmethod
    def _leg(
        parents: dict[tuple[Cell, int], tuple[Cell, int] | None],
        ticks: dict[tuple[Cell, int], int],
        state: tuple[Cell, int],
        start_tick: int,
    ) -> SafeLeg:
        cells: list[Cell] = []
        current: tuple[Cell, int] | None = state
        while current is not None:
            cells.append(current[0])
            current = parents[current]
        return SafeLeg(start_tick, ticks[state], tuple(cells[::-1]))


def safe_run(ir: GameIR, order: Sequence[int] | None = None) -> SafeRun | None:
    """A hazard-free run collecting every objective and then reaching the exit, or None.

    With ``order`` the objectives are visited in that order (used for metrics);
    without it the objective with the earliest safe arrival is taken next.
    """

    grid = _Grid(ir)
    cells = [objective.cell for objective in ir.objectives]
    position, tick = ir.player.spawn, 0
    remaining = list(range(len(cells)))
    chosen: list[int] = []
    legs: list[SafeLeg] = []
    while remaining:
        if order is not None:
            index = order[len(chosen)]
            arrivals = grid.search(position, tick, [cells[index]])
            if cells[index] not in arrivals:
                return None
        else:
            arrivals = grid.search(position, tick, [cells[item] for item in remaining])
            reachable = [item for item in remaining if cells[item] in arrivals]
            if not reachable:
                return None
            index = min(reachable, key=lambda item: (arrivals[cells[item]].arrival_tick, ir.objectives[item].id))
        leg = arrivals[cells[index]]
        legs.append(leg)
        chosen.append(index)
        remaining.remove(index)
        position, tick = cells[index], leg.arrival_tick
    arrivals = grid.search(position, tick, [ir.exit.cell])
    if ir.exit.cell not in arrivals:
        return None
    legs.append(arrivals[ir.exit.cell])
    return SafeRun(tuple(chosen), legs[-1].arrival_tick, tuple(legs))


__all__ = [
    "MAX_HAZARD_PERIOD_TICKS",
    "MAX_HAZARD_WORLD_SIDE",
    "SafeLeg",
    "SafeRun",
    "danger_table",
    "hazard_cells_at",
    "hazard_period_ticks",
    "hazard_sequence",
    "hazards_period_ticks",
    "safe_run",
]
