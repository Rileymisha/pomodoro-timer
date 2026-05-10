# -*- coding: utf-8 -*-
"""
番茄钟（Pomodoro Timer）—— 基于 PyQt5 的桌面番茄钟应用

工作原理：
1. 一个番茄周期 = 25 分钟工作 + 5 分钟短休息
2. 每完成 4 个番茄，进行一次 15 分钟长休息
3. 通过系统托盘可后台运行，支持最小化到托盘
"""

import sys
import time
import json
import os
from PyQt5.QtCore import Qt, QTimer, QRectF, QPoint
from PyQt5.QtGui import QPainter, QColor, QFont, QPen, QBrush, QFontDatabase, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSystemTrayIcon, QMenu, QMessageBox
)
from PyQt5 import QtCore

# ====================== 常量配置 ======================
WORK_MIN = 25              # 工作时长（分钟）
SHORT_BREAK_MIN = 5        # 短休息时长（分钟）
LONG_BREAK_MIN = 15        # 长休息时长（分钟）
POMOS_BEFORE_LONG = 4      # 多少次番茄后触发长休息

# ====================== 全局样式表 ======================
STYLE = """
QMainWindow { background-color: #1a1a2e; }
QLabel { color: #eee; }
QPushButton {
    border: none; border-radius: 20px; padding: 10px 30px;
    font-size: 14px; font-weight: bold; color: white;
}
QPushButton:hover { opacity: 0.8; }
QPushButton:pressed { opacity: 0.6; }
"""


class CircularProgress(QWidget):
    """圆形进度条控件——画一个带圆弧倒计时的圆环"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self.setMaximumSize(420, 420)
        self._value = 1.0                     # 进度值：1.0（满）→ 0.0（空）
        self._color = QColor("#e74c3c")       # 圆弧颜色（红色，工作阶段）
        self._bg_color = QColor("#2d2d44")    # 背景圆环颜色

    def set_value(self, v):
        """设置当前进度值（0.0 ~ 1.0），触发重绘"""
        self._value = max(0.0, min(1.0, v))
        self.update()

    def set_color(self, color):
        """设置圆弧颜色（不同阶段切换颜色用）"""
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        """
        绘制事件：画出圆环 + 进度圆弧
        两层圆环：
        - 底部：完整的灰色背景圆环
        - 上层：彩色圆弧，从 12 点钟方向开始顺时针减少
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)  # 抗锯齿
        w, h = self.width(), self.height()
        side = min(w, h)                              # 取宽高较小值保证圆形
        rect = QRectF((w - side) // 2, (h - side) // 2, side, side)
        margin = side * 0.08
        draw_rect = rect.adjusted(margin, margin, -margin, -margin)

        # ---- 画背景灰色圆环 ----
        pen = QPen(self._bg_color, side * 0.04)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#1a1a2e")))
        painter.drawEllipse(draw_rect)

        # ---- 画彩色进度圆弧 ----
        if self._value < 1.0:
            pen = QPen(self._color, side * 0.04)
            pen.setCapStyle(Qt.RoundCap)              # 圆弧端点圆形
            painter.setPen(pen)
            span = 360 * (1 - self._value)            # 剩余角度 = 360° × 剩余比例
            painter.drawArc(draw_rect, 90 * 16, int(span * 16))
            # drawArc 参数说明：
            #   90*16 → 从 12 点方向开始（Qt 角度单位是 1/16 度）
            #   span  → 顺时针扫过的角度（正值 = 顺时针）


class PomodoroTimer(QMainWindow):
    """番茄钟主窗口——管理计时逻辑和 UI 交互"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("番茄钟")
        self.setFixedSize(420, 560)          # 固定窗口大小
        self.setStyleSheet(STYLE)

        # ---- 计时状态 ----
        self.running = False                 # 计时器是否在运行
        self.paused = False                  # 是否处于暂停状态
        self.phase = "work"                  # 当前阶段：work / short_break / long_break
        self.pomos_done = 0                  # 已完成的番茄数
        self.seconds_left = WORK_MIN * 60    # 当前阶段剩余秒数
        self.total_seconds = WORK_MIN * 60   # 当前阶段总秒数（用于计算百分比）

        # ---- Qt 定时器（每秒触发一次 _tick） ----
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)

        # ---- 系统托盘 ----
        self.tray_icon = None
        self._init_tray()

        self._build_ui()
        self._update_display()

    # ==================== 系统托盘 ====================

    def _init_tray(self):
        """初始化系统托盘图标，支持后台运行"""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self)
            self.tray_icon.setToolTip("番茄钟")
            menu = QMenu()
            show_action = menu.addAction("显示")
            show_action.triggered.connect(self.show)
            quit_action = menu.addAction("退出")
            quit_action.triggered.connect(self.close)
            self.tray_icon.setContextMenu(menu)
            # 双击托盘图标恢复窗口
            self.tray_icon.activated.connect(
                lambda reason: self.show() if reason == QSystemTrayIcon.DoubleClick else None
            )
            self.tray_icon.show()

    # ==================== 界面构建 ====================

    def _build_ui(self):
        """构建主界面所有控件"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        # ---- 阶段标签（"工作时间" / "短休息" / "长休息"） ----
        self.phase_label = QLabel("工作时间")
        self.phase_label.setAlignment(Qt.AlignCenter)
        self.phase_label.setStyleSheet("font-size: 18px; color: #e74c3c; font-weight: bold;")
        layout.addWidget(self.phase_label)

        # ---- 番茄计数 ----
        self.count_label = QLabel("已完成 0 个番茄")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(self.count_label)

        layout.addSpacing(10)

        # ---- 圆形进度条 ----
        self.progress = CircularProgress()
        layout.addWidget(self.progress, alignment=Qt.AlignCenter)

        # ---- 倒计时数字（叠加在进度条上方显示） ----
        self.time_label = QLabel("25:00")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet("font-size: 56px; font-weight: bold; color: #eee;")
        layout.addWidget(self.time_label)

        # ---- 按钮区域 ----
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignCenter)
        btn_layout.setSpacing(16)

        self.start_btn = QPushButton("开始")
        self.start_btn.setStyleSheet(
            "background-color: #e74c3c; min-width: 120px; min-height: 44px;")
        self.start_btn.clicked.connect(self._toggle)
        btn_layout.addWidget(self.start_btn)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.setStyleSheet(
            "background-color: #555; min-width: 80px; min-height: 44px;")
        self.reset_btn.clicked.connect(self._reset)
        btn_layout.addWidget(self.reset_btn)

        layout.addLayout(btn_layout)
        layout.addSpacing(10)

        # ---- 已用时间标签 ----
        self.elapsed_label = QLabel("")
        self.elapsed_label.setAlignment(Qt.AlignCenter)
        self.elapsed_label.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(self.elapsed_label)

        # ---- 查看记录按钮 ----
        self.record_btn = QPushButton("📋 今日记录")
        self.record_btn.setStyleSheet(
            "background-color: #2d2d44; min-width: 120px; min-height: 36px; font-size: 12px;")
        self.record_btn.clicked.connect(self._show_record)
        layout.addWidget(self.record_btn, alignment=Qt.AlignCenter)

    # ==================== 显示更新 ====================

    def _update_display(self):
        """
        刷新界面显示：
        - 更新时间数字（mm:ss）
        - 更新圆形进度条
        - 更新"已用时间"标签
        """
        m, s = divmod(self.seconds_left, 60)
        self.time_label.setText(f"{m:02d}:{s:02d}")
        ratio = self.seconds_left / self.total_seconds if self.total_seconds > 0 else 0
        self.progress.set_value(ratio)
        if self.running or self.paused:
            elapsed = self.total_seconds - self.seconds_left
            em, es = divmod(int(elapsed), 60)
            self.elapsed_label.setText(f"已用 {em:02d}:{es:02d}")

    # ==================== 开始 / 暂停 / 继续 ====================

    def _toggle(self):
        """
        切换按钮逻辑：
        - 未运行 → 开始计时
        - 运行中 → 暂停计时
        - 暂停中 → 继续计时
        """
        if not self.running:
            # 停止状态 → 开始
            self.running = True
            self.paused = False
            self.start_btn.setText("暂停")
            self.timer.start(1000)           # 每秒触发一次 _tick
        else:
            # 运行中 → 暂停
            self.running = False
            self.paused = True
            self.start_btn.setText("继续")
            self.timer.stop()

    # ==================== 核心计时逻辑 ====================

    def _tick(self):
        """
        每秒回调一次：
        - 剩余秒数减 1
        - 更新显示
        - 倒计时归零时触发阶段切换
        """
        if self.seconds_left > 0:
            self.seconds_left -= 1
            self._update_display()
        else:
            self.timer.stop()
            self._on_timer_done()

    def _on_timer_done(self):
        """
        计时结束时的处理：
        1. 发出蜂鸣声提示
        2. 激活窗口到前台
        3. 保存完成记录（工作阶段完成时）
        4. 自动切换到下一个阶段
        """
        self.running = False
        self.start_btn.setText("开始")

        # ---- 蜂鸣提示（Windows 系统蜂鸣） ----
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(800, 300)      # 800Hz, 300ms
                QApplication.processEvents()  # 保证蜂鸣时不阻塞 UI
                time.sleep(0.15)
        except Exception:
            pass

        # ---- 窗口闪动提示 ----
        self.activateWindow()
        self._flash_window()

        # ---- 阶段切换逻辑 ----
        if self.phase == "work":
            # 工作完成 → 增加番茄计数
            self.pomos_done += 1
            self.count_label.setText(f"已完成 {self.pomos_done} 个番茄")
            self._save_record()              # 保存完成记录到 JSON 文件
            # 判断是短休息还是长休息
            if self.pomos_done % POMOS_BEFORE_LONG == 0:
                self._switch_phase("long_break")
            else:
                self._switch_phase("short_break")
        else:
            # 休息结束 → 切回工作
            self._switch_phase("work")

        self._update_display()

    def _flash_window(self):
        """窗口闪动效果（保留接口，当前只是确保标签刷新）"""
        style = self.phase_label.styleSheet()
        self.phase_label.setStyleSheet(style + "font-size: 18px; font-weight: bold;")

    # ==================== 阶段切换 ====================

    def _switch_phase(self, phase):
        """
        切换到指定阶段，更新所有相关 UI 颜色和文本
        - work:       红色主题，25 分钟
        - short_break: 绿色主题，5 分钟
        - long_break:  蓝色主题，15 分钟
        """
        self.phase = phase
        if phase == "work":
            self.seconds_left = WORK_MIN * 60
            self.total_seconds = WORK_MIN * 60
            self.phase_label.setText("工作时间")
            self.phase_label.setStyleSheet("font-size: 18px; color: #e74c3c; font-weight: bold;")
            self.progress.set_color("#e74c3c")
            self.start_btn.setStyleSheet(
                "background-color: #e74c3c; min-width: 120px; min-height: 44px;")
        elif phase == "short_break":
            self.seconds_left = SHORT_BREAK_MIN * 60
            self.total_seconds = SHORT_BREAK_MIN * 60
            self.phase_label.setText("短休息")
            self.phase_label.setStyleSheet("font-size: 18px; color: #27ae60; font-weight: bold;")
            self.progress.set_color("#27ae60")
            self.start_btn.setStyleSheet(
                "background-color: #27ae60; min-width: 120px; min-height: 44px;")
        else:  # long_break
            self.seconds_left = LONG_BREAK_MIN * 60
            self.total_seconds = LONG_BREAK_MIN * 60
            self.phase_label.setText("长休息")
            self.phase_label.setStyleSheet("font-size: 18px; color: #2980b9; font-weight: bold;")
            self.progress.set_color("#2980b9")
            self.start_btn.setStyleSheet(
                "background-color: #2980b9; min-width: 120px; min-height: 44px;")

        self.elapsed_label.setText("")
        self.reset_btn.setStyleSheet("background-color: #555; min-width: 80px; min-height: 44px;")

    # ==================== 重置 ====================

    def _reset(self):
        """
        重置当前阶段：停止计时、恢复倒计时到初始值、刷新显示
        （不会重置番茄计数或切换阶段）
        """
        self.running = False
        self.paused = False
        self.timer.stop()
        self.start_btn.setText("开始")
        self._switch_phase(self.phase)       # 重新进入当前阶段（重置秒数）
        self._update_display()

    # ==================== 持久化存储 ====================

    def _save_record(self):
        """
        每次完成一个番茄时调用：
        将当前完成时间追加到 pomodoro_records.json
        数据结构：{ "2026-05-10": ["14:30", "15:00", ...], ... }
        """
        now = time.localtime()
        date = time.strftime("%Y-%m-%d", now)
        t = time.strftime("%H:%M", now)
        record_path = "D:/First_CC/pomodoro_records.json"
        try:
            records = {}
            if os.path.exists(record_path):
                with open(record_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            if date not in records:
                records[date] = []
            records[date].append(t)
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 保存失败静默处理，不影响主流程

    # ==================== 查看记录 ====================

    def _show_record(self):
        """
        弹出消息框显示最近的完成统计：
        - 总计完成的番茄数
        - 最近 7 天每天的数量
        """
        record_path = "D:/First_CC/pomodoro_records.json"
        try:
            records = {}
            if os.path.exists(record_path):
                with open(record_path, "r", encoding="utf-8") as f:
                    records = json.load(f)

            total = sum(len(v) for v in records.values())
            dates = sorted(records.keys(), reverse=True)[:7]

            lines = [f"总计完成: {total} 个番茄\n"]
            for d in dates:
                n = len(records[d])
                lines.append(f"  {d}: {n} 个")

            QMessageBox.information(self, "今日记录", "\n".join(lines) if lines else "暂无记录")
        except Exception as e:
            QMessageBox.information(self, "今日记录", f"暂无记录 ({e})")

    # ==================== 关闭事件（托盘隐藏） ====================

    def closeEvent(self, event):
        """
        关闭窗口时的行为：
        - 如果计时器在运行且有托盘 → 最小化到托盘（不退出）
        - 如果计时器在运行但无托盘 → 弹出确认对话框
        - 如果计时器未运行 → 直接退出
        """
        if self.running or self.paused:
            if self.tray_icon and self.tray_icon.isVisible():
                self.hide()
                self.tray_icon.showMessage(
                    "番茄钟", "已最小化到系统托盘，双击可恢复",
                    QSystemTrayIcon.Information, 2000
                )
                event.ignore()
                return
            reply = QMessageBox.question(
                self, "确认", "计时器正在运行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
        event.accept()


# ==================== 程序入口 ====================
if __name__ == "__main__":
    # 高 DPI 缩放策略：PassThrough 允许像素级精确保留
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")                   # 使用 Fusion 风格（跨平台统一外观）
    window = PomodoroTimer()
    window.show()
    sys.exit(app.exec_())                    # 进入 Qt 事件循环
