import asyncio,json
from candidate import bounded_map
class IntSubclass(int): pass
async def worker(x): return x
async def main():
    rows=[]
    for items in ([], [1]):
        try:
            value=await bounded_map(worker,items,IntSubclass(1))
        except ValueError:
            rows.append({'items':items,'passed':True,'outcome':'ValueError'})
        except Exception as e:
            rows.append({'items':items,'passed':False,'outcome':type(e).__name__})
        else:
            rows.append({'items':items,'passed':False,'outcome':'accepted int subclass','returned':value})
    print(json.dumps(rows))
asyncio.run(main())
