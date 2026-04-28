import asyncio
import socket


async def ping_host(host: str, timeout: int = 1) -> bool:
    if not host:
        return False

    proc = await asyncio.create_subprocess_exec(
        "ping",
        "-c",
        "1",
        "-W",
        str(timeout),
        host,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def resolve_dns(name: str) -> bool:
    if not name:
        return False
    try:
        await asyncio.to_thread(socket.gethostbyname, name)
        return True
    except OSError:
        return False


async def check_wan(target: str, timeout: int = 1) -> bool:
    if not target:
        return False

    resolved = await resolve_dns(target)
    if not resolved:
        # If target is already an IP, DNS resolve may fail on some systems; try ping anyway.
        return await ping_host(target, timeout)
    return await ping_host(target, timeout)
