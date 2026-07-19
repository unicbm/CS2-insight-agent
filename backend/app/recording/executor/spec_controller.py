import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from ...win_cs2_console import inject_console_batch, inject_console_sequence
except ImportError:
    def inject_console_sequence(lines): pass
    def inject_console_batch(lines): pass

async def spec_player(player_name: str, mode: int = 5, *, include_mode: bool = True) -> bool:
    """
    Send spec_player, optionally including spec_mode, to CS2.

    Player names are deliberately kept on the existing per-line path: unlike a
    numeric slot, a demo-provided name can contain quoting or separator
    characters and must not be folded into a semicolon batch.
    mode: 5 = first-person (POV), 4 = chase/third-person, 1 = free
    """
    cmds = []
    if include_mode:
        cmds.append(f"spec_mode {mode}")
    cmds.append(f"spec_player {player_name}")
    sent = False
    try:
        sent = await asyncio.to_thread(inject_console_sequence, cmds) is not False
    except Exception as e:
        logger.warning("spec_player %s failed: %s", player_name, e)
    # Wait for spec switch to settle
    await asyncio.sleep(0.8)
    return sent

async def spec_by_slot(
    slot: int,
    mode: int = 5,
    settle: float = 0.8,
    *,
    include_mode: bool = True,
) -> bool:
    """Send spec_player by numeric slot, batching spec_mode when it is needed."""
    cmds = []
    if include_mode:
        cmds.append(f"spec_mode {mode}")
    cmds.append(f"spec_player {int(slot)}")
    sent = False
    try:
        injector = inject_console_batch if include_mode else inject_console_sequence
        sent = await asyncio.to_thread(injector, cmds) is not False
    except Exception as e:
        logger.warning("spec_player slot %s failed: %s", slot, e)
    if settle > 0:
        await asyncio.sleep(settle)
    return sent
