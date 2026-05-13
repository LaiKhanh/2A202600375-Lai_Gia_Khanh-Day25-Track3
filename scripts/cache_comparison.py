"""Generate cache vs no-cache comparison metrics."""
from __future__ import annotations

import json
from pathlib import Path

from reliability_lab.chaos import load_queries, run_scenario
from reliability_lab.config import load_config, ScenarioConfig


def main() -> None:
    config = load_config("configs/default.yaml")
    queries = load_queries()
    
    # Run baseline scenario (all_healthy) with cache enabled
    scenario_with_cache = ScenarioConfig(
        name="all_healthy",
        description="Baseline with cache",
        provider_overrides={}
    )
    metrics_with_cache = run_scenario(config, queries, scenario_with_cache, enable_cache=True)
    
    # Run same scenario with cache disabled
    metrics_without_cache = run_scenario(config, queries, scenario_with_cache, enable_cache=False)
    
    # Create comparison
    comparison = {
        "with_cache": metrics_with_cache.to_report_dict(),
        "without_cache": metrics_without_cache.to_report_dict(),
        "comparison": {
            "latency_p50_ms": {
                "without_cache": round(metrics_without_cache.percentile(50), 2),
                "with_cache": round(metrics_with_cache.percentile(50), 2),
                "delta": round(metrics_without_cache.percentile(50) - metrics_with_cache.percentile(50), 2),
                "improvement_pct": round((1 - metrics_with_cache.percentile(50) / metrics_without_cache.percentile(50)) * 100, 1) if metrics_without_cache.percentile(50) > 0 else 0,
            },
            "latency_p95_ms": {
                "without_cache": round(metrics_without_cache.percentile(95), 2),
                "with_cache": round(metrics_with_cache.percentile(95), 2),
                "delta": round(metrics_without_cache.percentile(95) - metrics_with_cache.percentile(95), 2),
                "improvement_pct": round((1 - metrics_with_cache.percentile(95) / metrics_without_cache.percentile(95)) * 100, 1) if metrics_without_cache.percentile(95) > 0 else 0,
            },
            "estimated_cost": {
                "without_cache": round(metrics_without_cache.estimated_cost, 6),
                "with_cache": round(metrics_with_cache.estimated_cost, 6),
                "delta": round(metrics_without_cache.estimated_cost - metrics_with_cache.estimated_cost, 6),
                "improvement_pct": round((1 - metrics_with_cache.estimated_cost / metrics_without_cache.estimated_cost) * 100, 1) if metrics_without_cache.estimated_cost > 0 else 0,
            },
            "cache_hit_rate": {
                "without_cache": 0,
                "with_cache": round(metrics_with_cache.cache_hit_rate, 4),
            },
        }
    }
    
    # Write comparison to file
    Path("reports/cache_comparison.json").write_text(json.dumps(comparison, indent=2))
    
    # Print summary
    print("Cache Comparison Results:")
    print("=" * 80)
    comp = comparison["comparison"]
    print(f"\nLatency P50 (ms):")
    print(f"  Without cache: {comp['latency_p50_ms']['without_cache']}")
    print(f"  With cache:    {comp['latency_p50_ms']['with_cache']}")
    print(f"  Improvement:   {comp['latency_p50_ms']['improvement_pct']}%")
    
    print(f"\nLatency P95 (ms):")
    print(f"  Without cache: {comp['latency_p95_ms']['without_cache']}")
    print(f"  With cache:    {comp['latency_p95_ms']['with_cache']}")
    print(f"  Improvement:   {comp['latency_p95_ms']['improvement_pct']}%")
    
    print(f"\nEstimated Cost:")
    print(f"  Without cache: {comp['estimated_cost']['without_cache']}")
    print(f"  With cache:    {comp['estimated_cost']['with_cache']}")
    print(f"  Improvement:   {comp['estimated_cost']['improvement_pct']}%")
    
    print(f"\nCache Hit Rate:")
    print(f"  With cache:    {comp['cache_hit_rate']['with_cache']}")
    
    print("\n" + "=" * 80)
    print("Detailed comparison saved to: reports/cache_comparison.json")


if __name__ == "__main__":
    main()
