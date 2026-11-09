import asyncio

async def bounded_map(worker, items, limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if not items:
        return []

    n = len(items)
    results = [None] * n
    item_queue = asyncio.Queue()
    for i, item in enumerate(items):
        item_queue.put_nowait((i, item))

    workers = []
    num_workers = min(limit, n)

    async def worker_loop():
        try:
            while not item_queue.empty():
                try:
                    idx, item = item_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    res = await worker(item)
                    results[idx] = res
                finally:
                    item_queue.task_done()
        except Exception:
            raise

    try:
        # Create exactly 'limit' (or fewer) tasks
        workers = [asyncio.create_task(worker_loop()) for _ in range(num_workers)]
        await asyncio.gather(*workers)
    except Exception as e:
        # Cancel all other workers on any exception
        for w in workers:
            if not w.done():
                w.cancel()
        # Await all to ensure cleanup finishes before exiting
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