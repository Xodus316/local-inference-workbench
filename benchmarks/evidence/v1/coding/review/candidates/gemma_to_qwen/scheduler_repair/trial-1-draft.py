import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by release time, then by id to ensure deterministic processing order
    # Use a sorted copy to avoid mutating the input list
    sorted_jobs = sorted(jobs, key=lambda x: (x["release"], x["id"]))
    
    # workers_free tracks when each worker becomes available. 
    # We need to find the lowest index worker among those idle at job['release'].
    # If none are idle, we need the worker who finishes earliest.
    
    # Priority queue for workers currently busy: (finish_time, worker_id)
    busy_workers = []
    # Priority queue for workers currently idle: (worker_id)
    idle_workers = list(range(workers))
    heapq.heapify(idle_workers)
    
    results = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Move all workers who finished by the job's release time to the idle pool
        while busy_workers and busy_workers[0][0] <= release:
            _, w_id = heapq.heappop(busy_workers)
            heapq.heappush(idle_workers, w_id)
            
        if idle_workers:
            # A worker is idle at or before release time. Pick the lowest index.
            worker_id = heapq.heappop(idle_workers)
            start_time = release
        else:
            # All workers are busy. Wait for the one who finishes earliest.
            finish_time, worker_id = heapq.heappop(busy_workers)
            start_time = finish_time
            
        end_time = start_time + duration
        results.append({"id": job_id, "worker": worker_id, "start": start_time, "finish": end_time})
        
        # Add the worker back to the busy pool
        heapq.heappush(busy_workers, (end_time, worker_id))
        
    return results