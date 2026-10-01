"""終端機入口：orch add / ls / log。"""
import argparse

import taskqueue as tq

DEFAULT_DB = "queue.db"


def cmd_add(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    task_id = tq.add_task(conn, args.title, args.spec or args.title, cwd=args.cwd)
    print(task_id)


def cmd_ls(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    for t in tq.list_tasks(conn, status=args.status):
        print(f"{t['id']}  {t['status']:8s}  {t['title']}")


def cmd_log(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    t = tq.get_task(conn, args.id)
    if not t:
        print(f"找不到任務：{args.id}")
        return
    print(f"id     : {t['id']}")
    print(f"title  : {t['title']}")
    print(f"status : {t['status']}")
    if t["result"]:
        print("--- result ---")
        print(t["result"])
    if t["error"]:
        print("--- error ---")
        print(t["error"])


def build_parser():
    p = argparse.ArgumentParser(prog="orch", description="任務佇列")
    p.add_argument("--db", default=DEFAULT_DB, help="queue.db 路徑")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增任務")
    a.add_argument("title")
    a.add_argument("--spec", help="完整任務描述（預設同 title）")
    a.add_argument("--cwd", help="工作目錄")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("ls", help="列出任務")
    l.add_argument("--status", help="只看某個狀態")
    l.set_defaults(func=cmd_ls)

    g = sub.add_parser("log", help="看單一任務")
    g.add_argument("id")
    g.set_defaults(func=cmd_log)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
