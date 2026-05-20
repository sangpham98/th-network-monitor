import asyncio


async def ping_host(host: str, timeout: int = 1, retry: int = 1) -> bool:
    if not host:
        return False

    proc = await asyncio.create_subprocess_exec(
        "ping",
        "-c",
        str(max(1, retry)),
        "-i",
        "0.2",
        "-W",
        str(timeout),
        host,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def check_wan(target: str, timeout: int = 1, retry: int = 1) -> bool:
    return await ping_host(target, timeout, retry)
