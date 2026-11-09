import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    
    # Use an index-based approach to avoid race conditions with Queue
    next_index = 0
    index_lock = asyncio.Lock()
    
    async def get_next_index():
        nonlocal next_index
        async with index_lock:
            if next_index < n:
                idx = next_index
                next_index += 1
                return idx
            return None
    
    workers = []
    num_workers = min(limit, n)
    
    async def worker_loop():
        while True:
            idx = await get_next_index()
            if idx is None:
                break
            try:
                res = await worker(items[idx])
                results[idx] = res
            except Exception:
                raise
    
    try:
        workers = [asyncio.create_task(worker_loop()) for _ in range(num_workers)]
        await asyncio.gather(*workers)
    except Exception as e:
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise e
    except asyncio.CancelledError:
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise

    return results