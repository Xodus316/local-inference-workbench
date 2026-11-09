import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    n = len(items)
    if n == 0:
        return []

    results = [None] * n
    next_index = 0
    tasks = []
    first_error = None

    async def run_one(index):
        nonlocal first_error
        try:
            results[index] = await worker(items[index])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if first_error is None:
                first_error = exc

    try:
        while next_index < n:
            while len(tasks) < limit and next_index < n:
                idx = next_index
                next_index += 1
                tasks.append(asyncio.create_task(run_one(idx)))

            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                tasks.remove(t)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        if tasks:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        raise

    if first_error is not None:
        raise first_error

    return results