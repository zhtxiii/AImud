"""
MUD 字节级收发层。
- drain 式读取：连续 recv 直到静默，避免战斗刷屏被切碎
- Telnet IAC 协商字节级剥离（跨块状态机）
- UTF-8 增量解码（不丢跨块的半个汉字）
- 分页自动续页合并（== 未完继续 N% ==）
"""
import codecs
import socket
import time

from mud import profile


class SocketLost(Exception):
    """连接断开/发送失败，例程应立即返回 reconnect。"""


# Telnet 协议常量
_IAC = 255
_SB = 250
_SE = 240
_WILL, _WONT, _DO, _DONT = 251, 252, 253, 254


class TelnetFilter:
    """跨块的 Telnet IAC 剥离状态机。"""

    def __init__(self):
        self._state = "data"

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if self._state == "data":
                if b == _IAC:
                    self._state = "iac"
                else:
                    out.append(b)
            elif self._state == "iac":
                if b == _IAC:
                    out.append(b)  # 转义的 0xFF
                    self._state = "data"
                elif b == _SB:
                    self._state = "sb"
                elif b in (_WILL, _WONT, _DO, _DONT):
                    self._state = "opt"
                else:
                    self._state = "data"
            elif self._state == "opt":
                self._state = "data"
            elif self._state == "sb":
                if b == _IAC:
                    self._state = "sb_iac"
            elif self._state == "sb_iac":
                self._state = "data" if b == _SE else "sb"
        return bytes(out)


class MudIO:
    """
    包装 SocketClient 的高层读写接口。
    所有返回文本均已剥离 ANSI 与 Telnet 噪声。
    """

    PAGER_MAX_PAGES = 30

    def __init__(self, sock_client, logger=None):
        """
        Args:
            sock_client: connection_manager.SocketClient（已连接）
            logger: 可选 callable(direction: str, text: str)，direction 为 ">>"/"<<"
        """
        self.client = sock_client
        self.logger = logger
        self._telnet = TelnetFilter()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    # ------------------------------------------------------------------
    def _raw_socket(self):
        sock = getattr(self.client, "socket", None)
        if sock is None or not self.client.connected:
            raise SocketLost("socket 未连接")
        return sock

    def _recv_chunk(self, timeout: float) -> str:
        """单次 recv → 剥 IAC → 增量解码。返回 ''=超时无数据。断开抛 SocketLost。"""
        sock = self._raw_socket()
        sock.settimeout(timeout)
        try:
            data = sock.recv(4096)
        except socket.timeout:
            return ""
        except OSError as e:
            self.client.disconnect()
            raise SocketLost(f"recv 失败: {e}")
        if not data:
            self.client.disconnect()
            raise SocketLost("服务器关闭了连接")
        return self._decoder.decode(self._telnet.feed(data))

    # ------------------------------------------------------------------
    def drain(self, quiet: float = 0.3, deadline: float = 8.0,
              auto_pager: bool = True) -> str:
        """
        持续读取直到：静默 quiet 秒（且已有数据）或 总时长达 deadline。
        自动处理分页提示（发空行续页并拼接，最多 PAGER_MAX_PAGES 页）。
        返回 ANSI 清理后的文本（可能为空串）。
        """
        chunks = []
        pages = 0
        start = time.time()
        last_data = start

        while True:
            now = time.time()
            if now - start >= deadline:
                break
            if chunks and (now - last_data) >= quiet:
                # 静默期到——检查是否停在分页提示上
                tail_clean = profile.strip_ansi("".join(chunks[-3:]))
                if auto_pager and pages < self.PAGER_MAX_PAGES and \
                        profile.has_event(profile.detect_events(tail_clean), "PAGER"):
                    pages += 1
                    self._send_raw("")
                    last_data = time.time()
                    start = min(start + 4.0, time.time())  # 续页适度延长预算
                    continue
                break
            text = self._recv_chunk(timeout=min(quiet, 0.2))
            if text:
                chunks.append(text)
                last_data = time.time()

        raw = "".join(chunks)
        clean = profile.strip_noise(profile.strip_ansi(raw))
        if auto_pager and pages:
            # 去掉中间的分页提示行
            clean = "\n".join(
                line for line in clean.splitlines()
                if "== 未完继续" not in line
            )
        clean = clean.strip("\n")
        if clean and self.logger:
            self.logger("<<", clean)
        return clean

    # ------------------------------------------------------------------
    def _send_raw(self, cmd_str: str):
        if not self.client.send(cmd_str):
            raise SocketLost(f"发送失败: {cmd_str!r}")

    def send(self, cmd_str: str):
        """发送一条命令（SocketClient 自动加换行）。失败抛 SocketLost。"""
        if self.logger:
            self.logger(">>", cmd_str if cmd_str else "<ENTER>")
        self._send_raw(cmd_str)

    # ------------------------------------------------------------------
    def request(self, cmd_str: str, quiet: float = 0.3, deadline: float = 8.0) -> str:
        """send + drain 一站式：返回该命令后的服务器输出。"""
        self.send(cmd_str)
        return self.drain(quiet=quiet, deadline=deadline)

    def request_events(self, cmd_str: str, quiet: float = 0.3,
                       deadline: float = 8.0) -> tuple[str, list[dict]]:
        """send + drain + 事件检测。"""
        text = self.request(cmd_str, quiet=quiet, deadline=deadline)
        return text, profile.detect_events(text)
