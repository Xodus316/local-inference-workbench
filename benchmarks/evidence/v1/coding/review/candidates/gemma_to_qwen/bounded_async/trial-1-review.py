import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    
    # Use an index-based approach to avoid queue race conditions
    next_index = 0
    lock = asyncio.Lock()
    
    async def worker_loop():
        nonlocal next_index
        while True:
            async with lock:
                if next_index >= n:
                    break
                idx = next_index
                next_index += 1
            
            try:
                res = await worker(items[idx])
                results[idx] = res
            except Exception:
                raise
            except asyncio.CancelledError:
                raise
    
    workers = []
    num_workers = min(limit, n)
    
    try:
        workers = [asyncio.create_task(worker_loop()) for _ in range(num_workers)]
        await asyncio.gather(*workers)
    except Exception as e:
        # Cancel all other workers
        for w in workers:
            if not w.done():
                w.cancel()
        # Await all to ensure cleanup finishes
        await asyncio.gather(*workers, return_exceptions=True)
        raise e
    except asyncio.CancelledError:
        # Handle caller cancellation
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    
    return results