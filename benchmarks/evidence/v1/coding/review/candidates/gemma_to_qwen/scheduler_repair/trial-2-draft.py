import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by release time, then by id to ensure deterministic processing order
    # Use a sorted copy to avoid mutating the input list
    sorted_jobs = sorted(jobs, key=lambda x: (x["release"], x["id"]))
    
    # worker_free_times stores (finish_time, worker_id)
    # We use a heap to always find the worker who finishes earliest
    worker_free_times = [(0, i) for i in range(workers)]
    heapq.heapify(worker_free_times)
    
    # To handle the requirement: "choose the LOWEST worker index among all idle workers"
    # when multiple workers are idle at or before the job's release time,
    # we need to track which workers are actually idle.
    idle_workers = [] # Min-heap of worker indices currently idle
    
    # We maintain a separate heap of (finish_time, worker_id) for workers currently busy
    busy_workers = []
    
    # Initialize all workers as idle at time 0
    for i in range(workers):
        heapq.heappush(idle_workers, i)
        
    results = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        
        # Move all workers who finished by the job's release time to the idle_workers heap
        while busy_workers and busy_workers[0][0] <= release:
            f_time, w_id = heapq.heappop(busy_workers)
            heapq.heappush(idle_workers, w_id)
            
        if idle_workers:
            # A worker is available at release time; pick the one with the lowest index
            worker_id = heapq.heappop(idle_workers)
            start_time = release
        else:
            # All workers are busy; wait for the one who finishes earliest
            # If multiple finish at the same time, the heap (finish_time, worker_id) 
            # naturally picks the lowest worker index.
            finish_time, worker_id = heapq.heappop(busy_workers)
            start_time = finish_time
            
        end_time = start_time + duration
        results.append({
            "id": job["id"],
            "worker": worker_id,
            "start": start_time,
            "finish": end_time
        })
        
        # Mark worker as busy until end_time
        heapq.heappush(busy_workers, (end_time, worker_id))
        
    return results