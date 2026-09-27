"""The scoring math, on plain data. JUDGING.md explains the method."""

import math

import pytest

from app.services.scoring import Criterion, Sheet, normalize, rank_projects, sheet_score

CRIT = [Criterion(1, 1.0, 1, 5), Criterion(2, 1.0, 1, 5)]


def test_sheet_score_rescales_and_weights():
    assert sheet_score({1: 5, 2: 5}, CRIT) == 100
    assert sheet_score({1: 1, 2: 1}, CRIT) == 0
    weighted = [Criterion(1, 3.0, 1, 5), Criterion(2, 1.0, 1, 5)]
    assert sheet_score({1: 5, 2: 1}, weighted) == pytest.approx(75)


def test_sheet_score_mixed_scales_and_missing_criteria():
    crit = [Criterion(1, 1.0, 1, 5), Criterion(2, 1.0, 0, 10)]
    assert sheet_score({1: 3, 2: 5}, crit) == pytest.approx(50)
    assert sheet_score({1: 5}, crit) == 100          # criterion added after scoring is skipped
    assert sheet_score({}, crit) is None


def test_all_zero_weights_fall_back_to_equal():
    crit = [Criterion(1, 0.0, 1, 5), Criterion(2, 0.0, 1, 5)]
    assert sheet_score({1: 5, 2: 1}, crit) == pytest.approx(50)


def _sheets(judge, scores):
    return [Sheet(judge, pid, raw) for pid, raw in scores.items()]


def test_harsh_judge_is_corrected():
    # Both judges see projects 1-4 and agree on their order, but judge 2 scores
    # everything 20 points lower. Project 10 is seen only by the fair judge
    # (70, i.e. just below their top); project 11 only by the harsh judge
    # (55, i.e. near the top of *their* range). Raw averages put 10 first;
    # relative to each judge's own scale, 11 is the stronger project.
    fair = {1: 80, 2: 60, 3: 40, 4: 20}
    sheets = _sheets(1, {**fair, 10: 70}) + _sheets(2, {**{p: v - 20 for p, v in fair.items()}, 11: 55})
    norm = normalize(sheets, k=0)
    by_pid = {r.project_id: r for r in rank_projects([1, 2, 3, 4, 10, 11], sheets)}
    assert norm.judges[2].mean < norm.judges[1].mean
    assert by_pid[10].raw_mean > by_pid[11].raw_mean
    assert by_pid[11].normalized_mean > by_pid[10].normalized_mean
    assert by_pid[11].rank < by_pid[10].rank
    # Projects both judges saw keep their agreed order.
    assert [by_pid[p].rank for p in (1, 2, 3, 4)] == sorted(by_pid[p].rank for p in (1, 2, 3, 4))


def test_zero_spread_judge_does_not_divide_by_zero():
    sheets = _sheets(1, {1: 90, 2: 50, 3: 10}) + _sheets(2, {1: 70, 2: 70, 3: 70})
    norm = normalize(sheets, k=3)
    assert norm.judges[2].sd == 0
    assert norm.judges[2].shrunk_sd > 0
    flat = [s.normalized for s in sheets if s.judge_id == 2]
    assert all(math.isfinite(v) for v in flat)
    assert len(set(round(v, 9) for v in flat)) == 1   # carries no ranking information


def test_single_sheet_judge_is_mostly_pulled_to_the_mean():
    sheets = _sheets(1, {1: 80, 2: 60, 3: 40, 4: 20}) + [Sheet(2, 1, 95)]
    norm = normalize(sheets, k=3)
    j2 = norm.judges[2]
    assert j2.n == 1
    # 1 real sheet against a prior worth 3: the used mean moves only a quarter of the way.
    assert j2.shrunk_mean == pytest.approx((95 + 3 * norm.global_mean) / 4)


def test_identical_event_leaves_scores_untouched():
    sheets = _sheets(1, {1: 50, 2: 50}) + _sheets(2, {1: 50})
    normalize(sheets)
    assert all(s.normalized == 50 for s in sheets)


def test_ranking_orders_and_leaves_unscored_last():
    sheets = [Sheet(1, 1, 30), Sheet(1, 2, 90), Sheet(2, 2, 80)]
    for s in sheets:
        s.normalized = s.raw
    rows = rank_projects([1, 2, 3], sheets)
    assert [r.project_id for r in rows] == [2, 1, 3]
    assert rows[0].rank == 1 and rows[2].rank is None and rows[2].n == 0
    assert rows[0].stderr is not None and rows[1].stderr is None
