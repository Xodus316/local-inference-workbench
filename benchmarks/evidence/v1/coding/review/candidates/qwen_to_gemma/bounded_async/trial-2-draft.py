import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    next_index = 0
    active = set()
    cancelled = False

    async def run_one(idx):
        nonlocal cancelled
        try:
            results[idx] = await worker(items[idx])
        except asyncio.CancelledError:
            if not cancelled:
                raise
            # If we were cancelled by bounded_map, swallow the cancellation
            # so that the task completes normally and doesn't interfere with
            # the outer cancellation handling.
            return
        except Exception as e:
            # Store the exception to be re-raised by bounded_map
            raise

    async def worker_loop():
        nonlocal next_index, cancelled
        while True:
            if next_index >= n:
                return
            idx = next_index
            next_index += 1
            task = asyncio.create_task(run_one(idx))
            active.add(task)
            try:
                await task
            except asyncio.CancelledError:
                if not cancelled:
                    raise
                # Swallow if we are in the process of being cancelled by bounded_map
                return
            except Exception as e:
                # A worker raised an ordinary exception
                cancelled = True
                # Cancel all other active tasks
                for t in active:
                    if t is not task and not t.done():
                        t.cancel()
                # Wait for all active tasks to finish
                if active:
                    await asyncio.gather(*active, return_exceptions=True)
                raise e

    # Create up to min(limit, n) worker loops
    num_loops = min(limit, n)
    loops = [asyncio.create_task(worker_loop()) for _ in range(num_loops)]

    try:
        # Wait for all loops to complete
        await asyncio.gather(*loops)
    except asyncio.CancelledError:
        # Caller cancelled bounded_map
        cancelled = True
        # Cancel all active worker tasks
        for t in active:
            if not t.done():
                t.cancel()
        # Wait for all active tasks to finish
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        # Also wait for the loops to finish
        for loop in loops:
            if not loop.done():
                loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        raise
    except Exception as e:
        # A worker exception propagated
        # Ensure all active tasks are cancelled and awaited
        for t in active:
            if not t.done():
                t.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        for loop in loops:
            if not loop.done():
                loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        raise e

    return results