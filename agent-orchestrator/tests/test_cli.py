import argparse
import subprocess
import sys
from pathlib import Path

import cli
import taskqueue as tq

HERE = Path(__file__).resolve().parent.parent


def test_cmd_add_creates_task(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    ns = argparse.Namespace(db=db, title="我的任務", spec="做點事", cwd=None)
    cli.cmd_add(ns)
    task_id = capsys.readouterr().out.strip()
    conn = tq.connect(db)
    assert tq.get_task(conn, task_id)["title"] == "我的任務"


def test_cmd_add_defaults_spec_to_title(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    cli.cmd_add(argparse.Namespace(db=db, title="只有標題", spec=None, cwd=None))
    conn = tq.connect(db)
    assert tq.list_tasks(conn)[0]["spec"] == "只有標題"


def test_cmd_ls_lists_tasks(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    cli.cmd_add(argparse.Namespace(db=db, title="A", spec="sa", cwd=None))
    capsys.readouterr()
    cli.cmd_ls(argparse.Namespace(db=db, status=None))
    assert "A" in capsys.readouterr().out


def test_cmd_log_shows_status_and_result(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)
    tid = tq.add_task(conn, "A", "sa")
    tq.set_status(conn, tid, "done", result="產出內容")
    cli.cmd_log(argparse.Namespace(db=db, id=tid))
    out = capsys.readouterr().out
    assert "done" in out and "產出內容" in out


def test_cli_runs_as_script(tmp_path):
    """從命令列真的叫得動（端到端的最小驗證）。"""
    db = str(tmp_path / "t.db")
    proc = subprocess.run(
        [sys.executable, str(HERE / "cli.py"), "--db", db, "add", "CLI 任務"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    task_id = proc.stdout.strip()
    ls = subprocess.run(
        [sys.executable, str(HERE / "cli.py"), "--db", db, "ls"],
        capture_output=True, text=True,
    )
    assert task_id in ls.stdout


def test_cmd_add_normalizes_relative_cwd(tmp_path, capsys, monkeypatch):
    """相對 --cwd 要用 add 當下的目錄解析成絕對路徑，否則 daemon 會解到錯的目錄。"""
    db = str(tmp_path / "t.db")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(tmp_path)
    cli.cmd_add(argparse.Namespace(db=db, title="r", spec="s", cwd="work"))
    tid = capsys.readouterr().out.strip()
    conn = tq.connect(db)
    assert tq.get_task(conn, tid)["cwd"] == str(work)


def test_cmd_plan_creates_orchestrator_task(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    work = tmp_path / "w"
    work.mkdir()
    cli.cmd_plan(argparse.Namespace(db=db, spec="研究並報告", cwd=str(work)))
    tid = capsys.readouterr().out.strip()
    conn = tq.connect(db)
    got = tq.get_task(conn, tid)
    assert got["kind"] == "orchestrator"
    assert got["cwd"] == str(work)


def test_cmd_confirm_releases_children(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)
    pid = tq.add_task(conn, "母", "x", kind="orchestrator")
    c1 = tq.add_task(conn, "子", "s")
    tq.set_status(conn, c1, "blocked", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    cli.cmd_confirm(argparse.Namespace(db=db, id=pid))
    assert tq.get_task(conn, c1)["status"] == "pending"
