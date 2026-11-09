import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    n = len(items)
    if n == 0:
        return []

    results = [None] * n
    next_index = 0
    tasks = set()
    first_error = None

    async def run_one(index):
        nonlocal first_error
        try:
            res = await worker(items[index])
            results[index] = res
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if first_error is None:
                first_error = exc
            raise

    try:
        while next_index < n or tasks:
            while len(tasks) < limit and next_index < n:
                idx = next_index
                next_index += 1
                t = asyncio.create_task(run_one(idx))
                tasks.add(t)

            if not tasks:
                break

            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            for t in done:
                tasks.remove(t)
                try:
                    await t
                except Exception as exc:
                    if not isinstance(exc, asyncio.CancelledError):
                        if first_error is None:
                            first_error = exc
                        # Trigger immediate cancellation of others
                        for remaining in tasks:
                            remaining.cancel()
                        # Wait for all to finish before raising
                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)
                        raise first_error from None

    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception as exc:
        # This handles the re-raised first_error
        raise

    return results