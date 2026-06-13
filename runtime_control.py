"""
运行时控制模块
进程级停止标志：SIGTERM/SIGINT 处理器置位，节点与例程在循环中轮询，
实现优雅退出（保存 checkpoint 后返回）。
"""
import threading

_stop_event = threading.Event()


def request_stop():
    _stop_event.set()


def stop_requested() -> bool:
    return _stop_event.is_set()


def reset():
    _stop_event.clear()
