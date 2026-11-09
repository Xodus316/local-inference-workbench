import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    next_index = 0
    active_tasks = set()
    first_exception = None

    async def wrapper(idx):
        nonlocal first_exception
        try:
            res = await worker(items[idx])
            results[idx] = res
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if first_exception is None:
                first_exception = e
            raise e

    try:
        while next_index < n or active_tasks:
            # Fill the pool up to limit
            while next_index < n and len(active_tasks) < limit:
                task = asyncio.create_task(wrapper(next_index))
                active_tasks.add(task)
                next_index += 1

            if not active_tasks:
                break

            done, _ = await asyncio.wait(
                active_tasks, 
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                active_tasks.remove(task)
                try:
                    await task
                except Exception as e:
                    if not isinstance(e, asyncio.CancelledError):
                        # Capture the first exception to re-raise later
                        if first_exception is None:
                            first_exception = e
                        raise e
                    else:
                        raise e
    except Exception:
        # Cancel all remaining tasks on any exception (including CancelledError)
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        raise
    finally:
        # Ensure all tasks are cleaned up if loop exited normally but tasks remained
        if active_tasks:
            for task in active_tasks:
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)

    return results