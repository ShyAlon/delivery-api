"""Capacity memory comes from the container limit when there is one, because /proc/meminfo inside Docker shows the whole machine."""
import pytest

from delivery_api import run_slots

MB = 1048576


def _files(tmp_path, monkeypatch, pairs):
    paths = []
    for index, (limit, used) in enumerate(pairs):
        limit_file, used_file = tmp_path / ("limit%d" % index), tmp_path / ("used%d" % index)
        if limit is not None:
            limit_file.write_text(str(limit) + "\n")
        if used is not None:
            used_file.write_text(str(used) + "\n")
        paths.append((str(limit_file), str(used_file)))
    monkeypatch.setattr(run_slots, "CGROUP_MEMORY_FILES", tuple(paths))
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       8388608 kB\nMemFree:        1000000 kB\nMemAvailable:   6291456 kB\n")
    monkeypatch.setattr(run_slots, "MEMINFO_FILE", str(meminfo))


def test_a_container_limit_is_read_in_mb(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, [(3072 * MB, 1000 * MB)])
    assert run_slots._container_memory_mb() == (3072, 1000)


@pytest.mark.parametrize("limit", ["max", str(1 << 62), "0", "not a number"])
def test_no_limit_or_an_unreadable_one_is_not_a_limit(tmp_path, monkeypatch, limit):
    _files(tmp_path, monkeypatch, [(limit, 5 * MB)])
    assert run_slots._container_memory_mb() is None


def test_a_missing_file_falls_through_to_the_next_cgroup_layout(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, [(None, None), (2048 * MB, 512 * MB)])
    assert run_slots._container_memory_mb() == (2048, 512)


def test_the_container_limit_replaces_the_machine_figure_when_it_is_smaller(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, [(3072 * MB, 1000 * MB)])
    host = run_slots.host_capacity()
    assert host["mem_total_mb"] == 3072 and host["mem_available_mb"] == 2072 and host["limited_by"] == "container"


def test_a_limit_larger_than_the_machine_is_ignored(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, [(10 ** 6 * MB, 0)])
    host = run_slots.host_capacity()
    assert "limited_by" not in host and host["mem_total_mb"] == 8192 and host["mem_available_mb"] == 6144


def test_the_text_says_when_the_figure_is_the_container_limit_and_sizes_runs_from_it():
    snap = {"isolation": "process", "max_concurrent_runs": 2, "time_limit_s": 1800, "running": [], "waiting": 0,
            "recent_peaks": [{"task_id": "t1", "peak_rss_mb": 800}],
            "host": {"cpus": 2, "mem_total_mb": 3072, "mem_available_mb": 2072, "limited_by": "container"}}
    text = run_slots.snapshot_text(snap)
    assert "3072 MB (container limit), 2072 MB available" in text and "Room for about 2 more runs" in text
