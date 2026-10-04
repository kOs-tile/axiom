import pytest

from benchmark.resolver_corpus import build_corpus, run_benchmark_async


def test_resolver_corpus_shape_is_stable():
    skills, tasks = build_corpus()

    assert len(skills) == 240
    assert len(tasks) == 100
    assert sum(task.kind == "exact" for task in tasks) == 70
    assert sum(task.kind == "ambiguous" for task in tasks) == 20
    assert sum(task.kind == "no_solution" for task in tasks) == 10


@pytest.mark.asyncio
async def test_resolver_benchmark_meets_v0_contract():
    result = await run_benchmark_async()

    assert result["skills"] == 240
    assert result["tasks"] == 100
    assert result["top1_target_hits"] == 90
    assert result["top1_target_recall"] == 1.0
    assert result["no_solution_correct"] == 10
    assert result["no_solution_precision"] == 1.0
    assert result["selected_surface_total"] == 90
    assert result["bundle_valid_count"] == 90
    assert result["authority_leak_count"] == 0
    assert result["authority_leak_rate"] == 0.0
