import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by release time, then by id to ensure deterministic order
    # Use a sorted copy to avoid mutating the input list
    sorted_jobs = sorted(jobs, key=lambda x: (x["release"], x["id"]))
    
    # workers_free tracks when each worker becomes available
    # We use a min-heap to find the worker who finishes earliest
    # Heap elements: (finish_time, worker_id)
    workers_free = [(0, i) for i in range(workers)]
    heapq.heapify(workers_free)
    
    # idle_workers tracks workers who are currently free at or before the current job's release
    # We use a min-heap to always pick the lowest worker index
    idle_workers = []
    
    results = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Move all workers who have finished by the job's release time into the idle pool
        while workers_free and workers_free[0][0] <= release:
            _, w_id = heapq.heappop(workers_free)
            heapq.heappush(idle_workers, w_id)
            
        if idle_workers:
            # If there are idle workers, pick the one with the lowest index
            worker_id = heapq.heappop(idle_workers)
            start_time = release
        else:
            # If no worker is idle, pick the one who finishes earliest
            # Tie-break by worker index (handled by the tuple in workers_free)
            finish_time, worker_id = heapq.heappop(workers_free)
            start_time = finish_time
            
        end_time = start_time + duration
        results.append({"id": job_id, "worker": worker_id, "start": start_time, "finish": end_time})
        
        # Put the worker back into the workers_free heap
        heapq.heappush(workers_free, (end_time, worker_id))
        
    return results