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
        # Create the worker tasks
        workers = [asyncio.create_task(worker_loop()) for _ in range(num_workers)]
        await asyncio.gather(*workers)
    except Exception as e:
        # Cancel all workers on any exception (including CancelledError)
        for w in workers:
            if not w.done():
                w.cancel()
        # Await all workers to ensure cleanup is finished
        await asyncio.gather(*workers, return_exceptions=True)
        raise e
    finally:
        # Ensure all tasks are cleaned up if not already handled by the except block
        for w in workers:
            if not w.done():
                w.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    return results