import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    next_index = 0
    tasks = []
    first_error = None
    error_lock = asyncio.Lock()

    async def run_one(index):
        nonlocal first_error
        try:
            results[index] = await worker(items[index])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with error_lock:
                if first_error is None:
                    first_error = exc

    while next_index < n:
        batch_size = min(limit, n - next_index)
        batch = []
        for _ in range(batch_size):
            idx = next_index
            next_index += 1
            task = asyncio.create_task(run_one(idx))
            tasks.append(task)
            batch.append(task)

        done, _ = await asyncio.wait(batch, return_when=asyncio.FIRST_EXCEPTION)

        if done:
            async with error_lock:
                if first_error is not None:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise first_error

    return results
