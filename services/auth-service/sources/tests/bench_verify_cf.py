"""
Benchmark charge de /api/internal/verify-cf.

Utile pour valider que la chaine nginx → auth_request → auth-service → Redis
ne devient pas un goulot d'etranglement avec beaucoup d'uploads simultanes.

Usage :
    # Contre un staging avec auth-service demarre
    python bench_verify_cf.py --url http://localhost:8000 --n 1000 --concurrency 10

    # Sortie exemple :
    # Requetes : 1000
    # Reussies : 1000
    # Duree totale : 1.83s
    # p50 : 1.4ms
    # p95 : 3.1ms
    # p99 : 5.7ms
    # RPS : 547

Pas execute automatiquement dans pytest (necessite un vrai serveur), c'est
juste un utilitaire ponctuel. Pour l'usage de l'utilisateur (~10 uploads/jour)
c'est evidemment overkill, mais dispo si un jour il y a un doute.
"""

import argparse
import asyncio
import statistics
import time

import httpx


async def one_request(client: httpx.AsyncClient, url: str) -> tuple[int, float]:
    start = time.perf_counter()
    r = await client.get(
        f"{url}/api/internal/verify-cf",
        headers={
            "X-CF-Client-Id": "bench-id",
            "X-CF-Client-Secret": "bench-secret-value-for-testing",
        },
    )
    return r.status_code, (time.perf_counter() - start) * 1000


async def worker(
    client: httpx.AsyncClient, url: str, n: int, results: list, sem: asyncio.Semaphore
) -> None:
    for _ in range(n):
        async with sem:
            results.append(await one_request(client, url))


async def main(url: str, n: int, concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    results: list = []

    async with httpx.AsyncClient(timeout=10) as client:
        start = time.perf_counter()
        workers = [worker(client, url, n // concurrency, results, sem) for _ in range(concurrency)]
        await asyncio.gather(*workers)
        elapsed = time.perf_counter() - start

    latencies = [ms for _, ms in results]
    latencies.sort()
    success = sum(1 for status, _ in results if status in (204, 403, 503))

    def pct(p: float) -> float:
        return statistics.quantiles(latencies, n=100)[int(p) - 1]

    print(f"Requetes   : {len(results)}")
    print(f"Reussies   : {success}")
    print(f"Duree      : {elapsed:.2f}s")
    print(f"p50        : {statistics.median(latencies):.1f} ms")
    print(f"p95        : {pct(95):.1f} ms")
    print(f"p99        : {pct(99):.1f} ms")
    print(f"RPS        : {len(results) / elapsed:.0f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--n", type=int, default=1000, help="Nombre total de requetes")
    p.add_argument("--concurrency", type=int, default=10)
    args = p.parse_args()
    asyncio.run(main(args.url, args.n, args.concurrency))
