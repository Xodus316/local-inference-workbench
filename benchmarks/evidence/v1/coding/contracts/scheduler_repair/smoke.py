from candidate import allocate_jobs
assert allocate_jobs([{"id":"a","release":0,"duration":3},{"id":"b","release":1,"duration":1}],1)==[{"id":"a","worker":0,"start":0,"finish":3},{"id":"b","worker":0,"start":3,"finish":4}]
assert allocate_jobs([],2)==[]
