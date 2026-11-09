import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    next_index = 0
    # Use a lock to protect access to next_index
    lock = asyncio.Lock()
    # Track active worker tasks to ensure they are cancelled and awaited
    active_tasks = set()

    async def worker_loop():
        nonlocal next_index
        while True:
            async with lock:
                if next_index >= n:
                    return
                idx = next_index
                next_index += 1
            
            # Execute the worker directly in the loop task to avoid 
            # creating one task per item.
            results[idx] = await worker(items[idx])

    # Create up to min(limit, n) worker tasks
    num_workers = min(limit, n)
    workers = []
    for _ in range(num_workers):
        t = asyncio.create_task(worker_loop())
        active_tasks.add(t)
        workers.append(t)

    try:
        # Wait for all worker loops to complete
        await asyncio.gather(*workers)
    except Exception as e:
        # Handle both asyncio.CancelledError and ordinary Exceptions
        # Cancel all worker tasks
        for t in active_tasks:
            if not t.done():
                t.cancel()
        # Await all worker tasks to ensure cleanup finishes before exiting
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        raise e
    finally:
        # Ensure tasks are cleaned up if gather finished normally
        # (though in this logic, they would already be done)
        pass

    return results