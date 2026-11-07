import json
from news_engine.storage import Store
from treasury_gateway.news_progress import news_progress


def test_progress_survives_reader_restart_and_detects_interruption(tmp_path):
    path = tmp_path / "news.sqlite"
    assert news_progress(path)["status"] == "idle"
    with Store(path) as store:
        store.start_run("Rtest")
        store.progress("Rtest", "classification", 2, 5)
        started = news_progress(path)["started_at"]
        with store._file_lock():
            status = news_progress(path)
            assert status["running"] and status["completed"] == 2 and status["total"] == 5
            assert status["stage"] == "classification"
        store.progress("Rtest", "analysis", 1, 3)
        assert news_progress(path)["started_at"] == started
        assert news_progress(path)["status"] == "interrupted"


def test_existing_task_without_progress_is_visible(tmp_path):
    path = tmp_path / "news.sqlite"
    with Store(path) as store:
        store.start_run("Rold")
        with store._file_lock():
            status = news_progress(path)
            assert status["running"] and status["stage"] == "processing"
            assert "total" not in status and "started_at" not in status
            assert status["database_totals"] == {"articles": 0, "events": 0}
        store.db.execute("UPDATE runs SET status='partial'")
        store.db.commit()
        assert news_progress(path)["status"] == "partial"
        assert not news_progress(path)["running"]
