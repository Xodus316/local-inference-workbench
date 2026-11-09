import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by (release, id) without mutating the input list
    sorted_jobs = sorted(jobs, key=lambda job: (job["release"], job["id"]))
    
    # Min-heap of (finish_time, worker_index)
    # Initialize all workers as free at time 0
    ready = [(0, worker) for worker in range(workers)]
    heapq.heapify(ready)
    
    answer = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Pop the worker with the earliest finish time
        # Ties are broken by worker index (lowest first) due to tuple comparison
        free_at, worker = heapq.heappop(ready)
        
        # Start time is max of when worker is free and job release time
        start = max(free_at, release)
        finish = start + duration
        
        answer.append({
            "id": job_id,
            "worker": worker,
            "start": start,
            "finish": finish
        })
        
        # Push back the updated finish time for this worker
        heapq.heappush(ready, (finish, worker))
    
    return answer