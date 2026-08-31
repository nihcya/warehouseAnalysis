"""在线状态卡片：总览页的连接状态/许可证/设备状态展示组件。

轻量级展示组件——不直接调用 API，所有数据通过槽函数传入。
风格克制，主色 #173B5F，与主窗口视觉一致。

数据来源：
- ``update_account_context``：GET /account/me 响应（AccountMeData）；
- ``update_snapshot``：SSE 快照 / 轮询快照（SnapshotData）；
- ``update_channel``：StatusStreamWorker 的 channel_changed 信号；
- ``set_offline``：控制平面不可达时由主窗口调用。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

#: 主色（对齐项目视觉）
_PRIMARY_COLOR = "#173B5F"

#: 在线绿色
_ONLINE_COLOR = "#27ae60"

#: 离线灰色
_OFFLINE_COLOR = "#7f8c8d"

#: 许可证状态 → 展示颜色
_LICENSE_COLORS: dict[str, str] = {
    "ACTIVE": "#27ae60",   # 绿色
    "GRACE": "#f39c12",    # 橙色（宽限期）
    "EXPIRED": "#c0392b",  # 红色
    "REVOKED": "#c0392b",  # 红色
    "MISSING": "#7f8c8d",  # 灰色
}

#: 通道 → 展示文案
_CHANNEL_LABELS: dict[str, str] = {
    "connecting": "连接中",
    "sse": "实时",
    "polling": "轮询降级",
    "offline": "离线",
}


class StatusCard(QWidget):
    """总览页"在线状态"卡片组件。

    展示内容：连接状态、通道、许可证状态、到期天数、设备数量、最后连接时间。
    所有数据通过槽函数传入，组件自身不发起任何网络请求。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # ---- 缓存值 ----
        self._max_devices: int | None = None
        self._registered_count: int | None = None

        # ---- 卡片布局 ----
        group = QGroupBox("在线状态")
        group.setStyleSheet(
            f"QGroupBox {{ color: {_PRIMARY_COLOR}; font-weight: bold; }}"
        )
        layout = QVBoxLayout(group)

        # 连接状态
        self._connection_label = QLabel("连接状态：离线")
        self._connection_label.setStyleSheet(f"color: {_OFFLINE_COLOR};")
        layout.addWidget(self._connection_label)

        # 通道
        self._channel_label = QLabel("通道：离线")
        layout.addWidget(self._channel_label)

        # 许可证状态
        self._license_label = QLabel("许可证：未知")
        layout.addWidget(self._license_label)

        # 到期天数
        self._days_label = QLabel("到期天数：—")
        layout.addWidget(self._days_label)

        # 设备数量
        self._devices_label = QLabel("设备：— / —")
        layout.addWidget(self._devices_label)

        # 最后连接时间
        self._last_seen_label = QLabel("最后连接：—")
        layout.addWidget(self._last_seen_label)

        outer = QVBoxLayout(self)
        outer.addWidget(group)
        outer.addStretch(1)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------

    def update_account_context(self, data: dict) -> None:
        """接收 account/me 响应，更新许可证与账号信息。

        :param data: AccountMeData（含 account / license / tenant）。
        """
        license_info = data.get("license") or {}
        self._apply_license(license_info)

    def update_snapshot(self, data: dict) -> None:
        """接收 SSE 快照，更新设备/任务状态。

        :param data: SnapshotData（含 devices / generated_at / license）。
        """
        devices = data.get("devices") or []
        self._registered_count = len(devices)

        generated_at = data.get("generated_at")
        if generated_at:
            self._last_seen_label.setText(f"最后连接：{generated_at}")

        # 快照中可能携带最新许可证信息
        license_info = data.get("license")
        if license_info:
            self._apply_license(license_info)

        self._refresh_devices_label()

    def update_channel(self, channel: str) -> None:
        """接收通道变更信号，更新连接状态显示。

        :param channel: "connecting" / "sse" / "polling" / "offline"。
        """
        label = _CHANNEL_LABELS.get(channel, channel)
        self._channel_label.setText(f"通道：{label}")

        if channel == "sse" or channel == "polling":
            self._connection_label.setText("连接状态：在线")
            self._connection_label.setStyleSheet(f"color: {_ONLINE_COLOR};")
        elif channel == "offline":
            self._connection_label.setText("连接状态：离线")
            self._connection_label.setStyleSheet(f"color: {_OFFLINE_COLOR};")
        elif channel == "connecting":
            self._connection_label.setText("连接状态：连接中")
            self._connection_label.setStyleSheet(f"color: {_OFFLINE_COLOR};")

    def set_offline(self) -> None:
        """控制平面不可达时设为离线显示。"""
        self._connection_label.setText("连接状态：离线")
        self._connection_label.setStyleSheet(f"color: {_OFFLINE_COLOR};")
        self._channel_label.setText("通道：离线")

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _apply_license(self, license_info: dict) -> None:
        """从 LicenseData 更新许可证状态、到期天数与设备上限。"""
        status = license_info.get("status", "未知")
        color = _LICENSE_COLORS.get(status, _OFFLINE_COLOR)
        self._license_label.setText(f"许可证：{status}")
        self._license_label.setStyleSheet(f"color: {color};")

        days = license_info.get("days_remaining")
        if days is not None:
            self._days_label.setText(f"到期天数：{days} 天")
        else:
            self._days_label.setText("到期天数：—")

        self._max_devices = license_info.get("max_devices")
        self._refresh_devices_label()

    def _refresh_devices_label(self) -> None:
        """刷新设备数量标签（已注册 / 上限）。"""
        registered = self._registered_count if self._registered_count is not None else "—"
        maximum = self._max_devices if self._max_devices is not None else "—"
        self._devices_label.setText(f"设备：{registered} / {maximum}")
