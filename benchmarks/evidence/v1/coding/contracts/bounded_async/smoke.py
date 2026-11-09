import asyncio
from candidate import bounded_map
async def main():
    async def worker(x):
        await asyncio.sleep(0)
        return x*x
    assert await bounded_map(worker,[3,1,2],2)==[9,1,4]
    assert await bounded_map(worker,[],1)==[]
asyncio.run(main())
