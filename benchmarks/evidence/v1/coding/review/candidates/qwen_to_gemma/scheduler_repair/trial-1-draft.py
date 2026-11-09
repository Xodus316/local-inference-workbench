import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by (release, id) without mutating the input list
    sorted_jobs = sorted(jobs, key=lambda job: (job["release"], job["id"]))
    
    # Min-heap of (available_time, worker_index)
    # Initially all workers are available at time 0
    ready = [(0, worker) for worker in range(workers)]
    heapq.heapify(ready)
    
    answer = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Pop the worker with the earliest available time.
        # Ties are broken by worker index (lowest first) due to tuple comparison.
        free_at, worker = heapq.heappop(ready)
        
        # The job starts at max(free_at, release)
        start = max(free_at, release)
        finish = start + duration
        
        answer.append({
            "id": job_id,
            "worker": worker,
            "start": start,
            "finish": finish
        })
        
        # Push the worker back with its new available time
        heapq.heappush(ready, (finish, worker))
    
    return answer