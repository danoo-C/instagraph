from instagraph.store.cache import ResponseCache, make_key


def test_lru_eviction_and_compute() -> None:
    cache = ResponseCache(maxsize=2)
    calls: list[int] = []

    def compute(v: int) -> int:
        calls.append(v)
        return v

    k1, k2, k3 = (make_key(1, "e", extra=i) for i in range(3))
    assert cache.get_or_compute(k1, lambda: compute(1)) == 1
    assert cache.get_or_compute(k1, lambda: compute(1)) == 1
    cache.get_or_compute(k2, lambda: compute(2))
    cache.get_or_compute(k3, lambda: compute(3))  # evicts k1
    assert cache.get(k1) is None and cache.get(k3) == 3
    assert calls == [1, 2, 3]
