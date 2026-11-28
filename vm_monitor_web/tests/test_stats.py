"""stats.py 解析與 SSH 收集的單元測試（TDD：先寫，預期 RED）。"""

import subprocess

import pytest

from vm_monitor_web import stats

VMSTAT = " 0  0      0 1906916 107788 15322388    0    0 3068 16 1592 1226  8  1 91  1  0  0"
FREE = """              total        used        free      shared  buff/cache   available
Mem:     25137188864  7720534016  1952681984     7708672 15800516608 17416654848
Swap:             0           0           0
"""
DF = """/dev/sda1       50884108288 13356081152 37511249920  27% /
/dev/sdb       108259958784 22684942336 80028471296  23% /mnt/data
"""
NET = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 100000000 1000 0 0 0 0 0 0 100000000 1000 0 0 0 0 0 0
enp0s6: 34653009491 3508907    0    0    0     0          0         0 1678689409 2820454 0 0 0 0 0 0
"""
PS = """    PID %CPU %MEM   RSS STAT COMMAND
 203190 53.5 72.8 17881948 Ssl python
 212115 39.1  0.1    32168 Sl   check-new-relea
"""
UP = " 02:26:06 up 8 days, 11:51,  4 users,  load average: 0.00, 0.00, 0.00"

SNAPSHOT = (
    "__VMSTAT__\n" + VMSTAT + "\n"
    "__MEM__\n" + FREE +
    "__DF__\n" + DF +
    "__NET__\n" + NET +
    "__PS__\n" + PS +
    "__UP__\n" + UP + "\n"
    "__END__\n"
)


def test_parse_cpu():
    assert stats.parse_cpu(VMSTAT) == 9


def test_parse_cpu_clamps_out_of_range():
    fields = VMSTAT.split()
    fields[14] = "200"
    assert stats.parse_cpu(" ".join(fields)) == 0
    fields[14] = "-5"
    assert stats.parse_cpu(" ".join(fields)) == 100


def test_parse_memory():
    result = stats.parse_memory(FREE)
    assert result["total"] == 25137188864
    assert result["used"] == 7720534016
    assert result["available"] == 17416654848


def test_parse_memory_missing_mem_line():
    assert stats.parse_memory("Swap: 0 0 0\n") == {}


def test_parse_disks():
    disks = stats.parse_disks(DF)
    assert disks["/"]["percent"] == 27
    assert disks["/mnt/data"]["size"] == 108259958784


def test_parse_disks_missing_mount():
    disks = stats.parse_disks("/dev/sda1 100 50 50 50% /\n")
    assert "/" in disks
    assert "/mnt/data" not in disks


def test_parse_net_excludes_loopback():
    net = stats.parse_net(NET)
    assert net["rx_bytes"] == 34653009491
    assert net["tx_bytes"] == 1678689409


def test_parse_processes():
    rows = stats.parse_processes(PS)
    assert rows[0]["pid"] == 203190
    assert rows[0]["cpu"] == 53.5
    assert rows[1]["comm"] == "check-new-relea"


def test_parse_uptime():
    result = stats.parse_uptime(UP)
    assert result["load"] == [0.0, 0.0, 0.0]
    assert "8 days" in result["uptime"]


def test_parse_snapshot():
    result = stats.parse_snapshot(SNAPSHOT)
    assert result["cpu"] == 9
    assert result["memory"]["total"] == 25137188864
    assert result["disks"]["/"]["percent"] == 27
    assert result["network"]["rx_bytes"] == 34653009491
    assert len(result["processes"]) == 2
    assert result["load"] == [0.0, 0.0, 0.0]


def test_parse_snapshot_requires_end_marker():
    with pytest.raises(stats.ParseError):
        stats.parse_snapshot(SNAPSHOT.replace("__END__\n", ""))


def test_collect_builds_expected_ssh_command(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=SNAPSHOT, stderr="")

    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    result = stats.collect()
    assert result["cpu"] == 9
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "-i" in argv
    assert "BatchMode=yes" in argv
    assert "ConnectTimeout=3" in argv
    assert "ubuntu@161.33.42.181" in argv
    assert "__VMSTAT__" in argv[-1]


def test_collect_propagates_timeout(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 4)

    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        stats.collect()
