import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by (release, id) without mutating the input list
    sorted_jobs = sorted(jobs, key=lambda job: (job["release"], job["id"]))
    
    # Min-heap of (finish_time, worker_index)
    # Initialize all workers as free at time 0
    busy_workers = [] # Stores (finish_time, worker_index)
    idle_workers = list(range(workers))
    heapq.heapify(idle_workers)
    
    answer = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Move workers who have finished by the job's release time to the idle heap
        while busy_workers and busy_workers[0][0] <= release:
            _, worker_idx = heapq.heappop(busy_workers)
            heapq.heappush(idle_workers, worker_idx)
            
        if idle_workers:
            # Use the lowest index among all idle workers
            worker = heapq.heappop(idle_workers)
            start = release
        else:
            # No worker is idle at release time, pick the one finishing earliest
            free_at, worker = heapq.heappop(busy_workers)
            start = free_at
            
        finish = start + duration
        answer.append({
            "id": job_id,
            "worker": worker,
            "start": start,
            "finish": finish
        })
        
        heapq.heappush(busy_workers, (finish, worker))
    
    return answer