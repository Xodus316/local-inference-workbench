import heapq

def allocate_jobs(jobs, workers):
    # Sort jobs by (release, id) without mutating the input list
    sorted_jobs = sorted(jobs, key=lambda job: (job["release"], job["id"]))
    
    # Min-heap of (available_time, worker_index)
    # Initially all workers are available at time 0
    worker_heap = [(0, worker) for worker in range(workers)]
    heapq.heapify(worker_heap)
    
    # Min-heap of worker indices that are currently idle
    idle_workers = []
    
    answer = []
    
    for job in sorted_jobs:
        release = job["release"]
        duration = job["duration"]
        job_id = job["id"]
        
        # Move all workers who are free by the job's release time to the idle_workers heap
        while worker_heap and worker_heap[0][0] <= release:
            _, worker_idx = heapq.heappop(worker_heap)
            heapq.heappush(idle_workers, worker_idx)
        
        if idle_workers:
            # If any workers are idle, pick the one with the lowest index
            worker = heapq.heappop(idle_workers)
            start = release
        else:
            # If all workers are busy, pick the one who finishes earliest (tie-break by index)
            free_at, worker = heapq.heappop(worker_heap)
            start = free_at
            
        finish = start + duration
        answer.append({
            "id": job_id,
            "worker": worker,
            "start": start,
            "finish": finish
        })
        
        # The worker is now busy until 'finish'
        heapq.heappush(worker_heap, (finish, worker))
    
    return answer