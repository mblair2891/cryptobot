from __future__ import annotations

from aethergrid.domain.enums import BotStatus

ALLOWED: dict[BotStatus, set[BotStatus]] = {
    BotStatus.CREATED: {BotStatus.STARTING, BotStatus.ARCHIVED, BotStatus.ERROR},
    BotStatus.STARTING: {BotStatus.RUNNING, BotStatus.ERROR, BotStatus.STOPPING},
    BotStatus.RUNNING: {
        BotStatus.PAUSED,
        BotStatus.COOLDOWN,
        BotStatus.STOPPING,
        BotStatus.ERROR,
    },
    BotStatus.PAUSED: {BotStatus.RUNNING, BotStatus.STOPPING, BotStatus.COOLDOWN, BotStatus.ERROR},
    BotStatus.COOLDOWN: {BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.STOPPING, BotStatus.ERROR},
    BotStatus.STOPPING: {BotStatus.STOPPED, BotStatus.ARCHIVED, BotStatus.ERROR},
    BotStatus.STOPPED: {BotStatus.ARCHIVED, BotStatus.STARTING},
    BotStatus.ERROR: {BotStatus.COOLDOWN, BotStatus.STOPPING, BotStatus.ARCHIVED, BotStatus.STARTING},
    BotStatus.ARCHIVED: set(),
}


def legal_transition(current: BotStatus, target: BotStatus) -> bool:
    if current == target:
        return True
    return target in ALLOWED.get(current, set())


class IllegalTransition(RuntimeError):
    pass


def transition(current: BotStatus, target: BotStatus) -> BotStatus:
    if not legal_transition(current, target):
        raise IllegalTransition(f"{current} -> {target} is not allowed")
    return target
