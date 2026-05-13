# Day 10 Reliability Final Report

## 1. Architecture summary

The ReliabilityGateway implements a multi-layer reliability pattern:
1. **Cache Layer** (ResponseCache or SharedRedisCache): Returns cached responses for similar queries (similarity ≥ 0.92)
2. **Circuit Breaker Layer**: Controls access to each provider (primary, backup) with 3-failure threshold
3. **Provider Layer**: Two LLM providers with fallback chain (primary → backup)
4. **Fallback Layer**: Static message returned if all providers fail

```
User Request
    |
    v
[Gateway: complete()]
    |
    +---> [ResponseCache/SharedRedisCache]
    |     - Check similarity to cached queries
    |     - Return if score >= 0.92
    |     - Privacy guardrails applied
    |
    v (cache miss)
[CircuitBreaker: primary] ──(CLOSED)--> [FakeLLMProvider: primary]
    |                        (OPEN -> HALF_OPEN -> CLOSED)
    |
    +---> Failure? Move to next
    |
v (primary open/failed)
[CircuitBreaker: backup] ──(CLOSED)--> [FakeLLMProvider: backup]
    |
    +---> Failure? Move to static fallback
    |
v (all failed)
[Static Fallback Message]
```

**State Machine (Circuit Breaker):**
- CLOSED: Calls pass through; failures counted
- OPEN: After 3 failures, fail fast; wait 2 seconds
- HALF_OPEN: After timeout, allow 1 probe; close on success or re-open on failure


## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Detects failures quickly without triggering on transient glitches (allows 2 failures before opening) |
| reset_timeout_seconds | 2 | Allows fast recovery probes; 2 seconds is reasonable for detecting provider recovery |
| success_threshold | 1 | Single successful probe is sufficient to close circuit from HALF_OPEN state |
| cache TTL | 300 | 5-minute freshness window for FAQ-style queries; balances staleness vs hit rate |
| similarity_threshold | 0.92 | High threshold (92%) reduces false hits; tested to distinguish different years/dates |
| load_test requests | 100 | 100 requests per scenario for reasonable coverage and fast iteration |

## 3. SLO definitions

Define your target SLOs and whether your system meets them:

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 100% | Yes |
| Latency P95 | < 2500 ms | 317.01 ms | Yes |
| Fallback success rate | >= 95% | 100% | Yes |
| Cache hit rate | >= 10% | 81.73% | Yes |
| Recovery time | < 5000 ms | 3474.68 ms | Yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 1500 |
| availability | 1.0 |
| error_rate | 0.0 |
| latency_p50_ms | 0.19 |
| latency_p95_ms | 317.01 |
| latency_p99_ms | 525.33 |
| fallback_success_rate | 1.0 |
| cache_hit_rate | 0.8173 |
| circuit_open_count | 24 |
| recovery_time_ms | 3474.675854047139 |
| estimated_cost | 0.107436 |
| estimated_cost_saved | 1.226 |

## 5. Cache comparison

Run simulation with cache enabled vs disabled. Fill in both columns:

| latency_p50_ms | 219.57 ms | 0.19 ms | -219.37 ms (-99.9%) |
| latency_p95_ms | 511.57 ms | 280.19 ms | -231.38 ms (-45.2%) |
| estimated_cost | $0.052780 | $0.009718 | -$0.043062 (-81.6%) |
| cache_hit_rate | 0% | 79% | +79% |

## 6. Redis shared cache

Explain why shared cache matters for production:

- **Why in-memory cache is insufficient**: In-memory caches (ResponseCache) are local to each gateway instance. In a scaled deployment with multiple gateway replicas, each instance maintains separate cache state. This means:
  - Cache misses on one instance waste resources repeating work on another instance
  - Cache consistency breaks: different replicas see different cached data
  - Cold restarts lose all cached data, causing thundering herd of API calls
  
- **How SharedRedisCache solves this**: Redis provides a centralized, shared cache accessible to all gateway instances:
  - **Single source of truth**: All 5+ replicas access the same cache backend
  - **Cache efficiency**: 79% hit rate benefits all instances, not just one
  - **Distributed consistency**: Cache invalidations apply globally
  - **Resilience**: Redis persistence survives gateway restarts
  - **Horizontal scaling**: Add replicas without degrading cache effectiveness

## 7. Chaos Scenarios

| primary_timeout_100 | All traffic fallback to backup; circuit opens after 3 failures; 100% availability | All requests succeeded via fallback chain; circuit opened 24 times total across scenarios; fallback_success_rate = 100%; availability = 100% | pass |
| primary_flaky_50 | Circuit oscillates OPEN/CLOSED; mix of primary and fallback responses | Circuit opened multiple times; recovered within timeout; mix of responses; availability = 100% | pass |
| all_healthy | Both providers healthy; no circuit opens; latency ~200-300ms | No circuit opened in baseline; latency_p50 = 0.19ms (due to caching); availability = 100% | pass |

## 8. Analysis

**Remaining weakness:** Lack of per-request rate limiting and cost budgeting

**What could still go wrong:**
- A single client making thousands of expensive requests could deplete monthly API budget without throttling
- No user-level isolation: high-volume user gets same unlimited access as normal users
- DDoS or malicious clients could cause runaway API costs
- Circuit breaker protects availability but not cost

**How to fix before production:**
1. **Add token bucket rate limiter per user ID**: Limit requests/minute per user (e.g., 100 req/min for free tier, 1000 req/min for premium)
2. **Implement cost cap per user per day**: Track cumulative cost; when user hits daily limit, route to cache-only or reject with friendly error
3. **Add cost circuit breaker**: Monitor daily API spend; when aggregate spend hits 80% of budget, route expensive providers to backup; at 100%, route to cache-only
4. **Implement user-aware fallback routing**: Route expensive users preferentially to cheaper backup provider to minimize cost

## 9. Next steps

1. **Add per-user rate limiting and cost caps** (Section 8 detail): Implement token bucket rate limiter and daily cost budget tracking to prevent runaway API spend and ensure fair resource allocation across users.

2. **Promote circuit breaker state to Redis** (production readiness): Store circuit state in Redis so all gateway replicas share the same breaker state. Currently each replica has independent circuit state, causing cache misses when replicas disagree on provider health.

3. **Add request-level observability with Prometheus metrics** (operations): Export metrics like `agent_requests_total`, `agent_latency_seconds_bucket`, `cache_hits_total`, `circuit_state_gauge` to enable alerting on latency SLO violations, circuit flapping, or cache hit rate degradation.