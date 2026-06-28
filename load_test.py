import asyncio
import time
import aiohttp

async def send_request(session):
    start = time.perf_counter()
    async with session.post("http://100.54.68.234:8000/generate", json={"text": "hello"}) as resp:
        elapsed = time.perf_counter() - start
        return resp.status, elapsed


async def main():
    async with aiohttp.ClientSession() as session:
        tasks = []
        for _ in range(100):
            tasks.append(send_request(session))
        results = await asyncio.gather(*tasks)

        total = 0
        for status, elapsed in results:
            print(f"Status: {status}, Elapsed: {elapsed}")
            total += elapsed
        print(f"Average elapsed time: {total / len(results)}")

        latencies = sorted(e for _, e in results)
        p99 = latencies[int(len(latencies) * 0.99)]
        print(f"P99 latency: {p99}")

        success = sum(1 for status, _ in results if status == 200)
        limited = sum(1 for status, _ in results if status == 429)
        print(f"200s: {success}, 429s: {limited}")

asyncio.run(main())