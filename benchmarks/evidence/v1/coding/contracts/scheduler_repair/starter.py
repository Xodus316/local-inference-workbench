import heapq

def allocate_jobs(jobs, workers):
    jobs.sort(key=lambda job: (job["release"], job["id"]))
    ready = [(0, worker) for worker in range(workers)]
    heapq.heapify(ready)
    answer = []
    for job in jobs:
        free_at, worker = heapq.heappop(ready)
        start = max(free_at, job["release"])
        finish = start + job["duration"]
        answer.append({"id": job["id"], "worker": worker, "start": start, "finish": finish})
        heapq.heappush(ready, (finish, worker))
    return answer
