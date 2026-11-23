"""Codex Multi-Account Management Pane, Dialog & Card Components.

Matches Bailong desktop pet design system:
- Compact hierarchy, no middle-dot separators
- Hidden scrollbars with wheel scrolling preserved
- Distinct primary, danger, and disabled hover states
- Direct one-click login URL / browser authorization launch
- Non-blocking background worker threads for OAuth and quota operations
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
    QPushButton, QScrollArea, QMessageBox, QInputDialog, QProgressBar
)

from codex_account_manager import CodexAccountManager
from typography import FONT_CSS
from ui_theme import (
    SURFACE, INK, MUTED, ACCENT, TRACK, PANEL, BORDER,
    PRIMARY, PRIMARY_PRESSED, ON_PRIMARY, SELECTED, DANGER,
    DANGER_SURFACE, DISABLED, hide_scrollbars, editor_stylesheet
)
from theme_runtime import set_theme_style


class AccountWorker(QThread):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, target_fn, *args, parent=None):
        super().__init__(parent)
        self.target_fn = target_fn
        self.args = args

    def run(self):
        try:
            res = self.target_fn(*self.args)
            self.finished.emit(res)
        except Exception as error:
            self.failed.emit(str(error))


class AccountCard(QWidget):
    switchRequested = Signal(str)
    refreshRequested = Signal(str)
    renameRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, account_data: dict, parent=None):
        super().__init__(parent)
        self.account_data = account_data
        self.acc_id = account_data['id']
        self._init_ui()

    def _init_ui(self):
        is_active = self.account_data.get('is_active', False)
        bg = SURFACE if is_active else PANEL
        border_color = PRIMARY if is_active else BORDER

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        self.setObjectName('account_card')
        set_theme_style(
            self,
            f'QWidget#account_card{{background:{bg};border:1px solid {border_color};border-radius:8px;}}'
        )

        # Top row: Status dot + Alias + Plan Badge + Actions
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        dot = QLabel('●' if is_active else '○')
        set_theme_style(
            dot,
            f'{FONT_CSS}font-size:12px;color:{PRIMARY if is_active else MUTED};'
        )
        top_row.addWidget(dot)

        alias_text = self.account_data.get('alias') or '未命名账户'
        alias_lbl = QLabel(alias_text)
        set_theme_style(
            alias_lbl,
            f'{FONT_CSS}font-size:12px;font-weight:600;color:{INK};'
        )
        top_row.addWidget(alias_lbl)

        plan = (self.account_data.get('plan_type') or 'Free').upper()
        plan_badge = QLabel(f" {plan} ")
        set_theme_style(
            plan_badge,
            f'{FONT_CSS}font-size:10px;background:{SELECTED};color:{PRIMARY};border-radius:4px;padding:1px 4px;'
        )
        top_row.addWidget(plan_badge)

        if is_active:
            active_badge = QLabel(' 当前生效 ')
            set_theme_style(
                active_badge,
                f'{FONT_CSS}font-size:10px;background:{PRIMARY};color:{ON_PRIMARY};border-radius:4px;padding:1px 4px;'
            )
            top_row.addWidget(active_badge)

        top_row.addStretch()

        # Action buttons
        if not is_active:
            switch_btn = QPushButton('切换至此')
            switch_btn.setFixedHeight(24)
            set_theme_style(
                switch_btn,
                f'{FONT_CSS}font-size:11px;background:{PRIMARY};color:{ON_PRIMARY};border-radius:5px;padding:2px 8px;'
                f'QPushButton:hover{{background:{PRIMARY_PRESSED};}}'
            )
            switch_btn.clicked.connect(lambda: self.switchRequested.emit(self.acc_id))
            top_row.addWidget(switch_btn)

        refresh_card_btn = QPushButton('刷新')
        refresh_card_btn.setFixedHeight(24)
        set_theme_style(
            refresh_card_btn,
            f'{FONT_CSS}font-size:11px;background:transparent;color:{PRIMARY};border:1px solid {BORDER};border-radius:5px;padding:2px 6px;'
            f'QPushButton:hover{{background:{SELECTED};border-color:{PRIMARY};}}'
        )
        refresh_card_btn.setToolTip('刷新此账户凭据有效期并测算实时额度')
        refresh_card_btn.clicked.connect(lambda: self.refreshRequested.emit(self.acc_id))
        top_row.addWidget(refresh_card_btn)

        rename_btn = QPushButton('改名')
        rename_btn.setFixedHeight(24)
        set_theme_style(
            rename_btn,
            f'{FONT_CSS}font-size:11px;background:transparent;color:{MUTED};border:1px solid {BORDER};border-radius:5px;padding:2px 6px;'
            f'QPushButton:hover{{background:{SELECTED};color:{INK};}}'
        )
        rename_btn.clicked.connect(lambda: self.renameRequested.emit(self.acc_id))
        top_row.addWidget(rename_btn)

        del_btn = QPushButton('删除')
        del_btn.setFixedHeight(24)
        set_theme_style(
            del_btn,
            f'{FONT_CSS}font-size:11px;background:transparent;color:{DANGER};border:1px solid {BORDER};border-radius:5px;padding:2px 6px;'
            f'QPushButton:hover{{background:{DANGER_SURFACE};border-color:{DANGER};}}'
        )
        del_btn.clicked.connect(lambda: self.deleteRequested.emit(self.acc_id))
        top_row.addWidget(del_btn)

        layout.addLayout(top_row)

        # Meta row: Email + Token Expiry Info
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)

        email = self.account_data.get('email') or '无关联邮箱'
        email_lbl = QLabel(email)
        set_theme_style(email_lbl, f'{FONT_CSS}font-size:11px;color:{MUTED};')
        meta_row.addWidget(email_lbl)

        exp = self.account_data.get('expires_at')
        if exp:
            exp_sec = exp - time.time()
            days = int(exp_sec // 86400)
            hours = int((exp_sec % 86400) // 3600)
            mins = int((exp_sec % 3600) // 60)
            if days > 0:
                exp_str = f"凭证有效: {days} 天 {hours} 小时"
            elif hours > 0:
                exp_str = f"凭证有效: {hours} 小时 {mins} 分"
            elif exp_sec > 0:
                exp_str = f"凭证有效: {mins} 分 (即将到期)"
            else:
                exp_str = "凭证已到期 (需刷新)"
            exp_time = time.strftime('%m-%d %H:%M', time.localtime(exp))
            full_exp_str = f"{exp_str} (至 {exp_time})"
            exp_lbl = QLabel(full_exp_str)
            set_theme_style(
                exp_lbl,
                f'{FONT_CSS}font-size:11px;color:{PRIMARY if exp_sec > 3600 else DANGER};'
            )
            meta_row.addWidget(exp_lbl)

        meta_row.addStretch()
        layout.addLayout(meta_row)

        # Quota row
        quota = self.account_data.get('quota', {})
        windows = quota.get('windows', [])
        if windows:
            quota_row = QHBoxLayout()
            quota_row.setContentsMargins(0, 0, 0, 0)
            quota_row.setSpacing(8)
            for w in windows:
                name = w.get('name', '额度')
                rem = w.get('remaining')
                rem_str = f"{round(rem, 1)}%" if rem is not None else "未知"
                reset_ts = w.get('resetsAt')
                reset_info = ''
                if reset_ts and reset_ts > time.time():
                    diff = reset_ts - time.time()
                    if diff > 3600:
                        reset_info = f" ({int(diff//3600)}h{int((diff%3600)//60)}m重置)"
                    else:
                        reset_info = f" ({int(diff//60)}m重置)"
                q_lbl = QLabel(f"{name} 剩余 {rem_str}{reset_info}")
                set_theme_style(q_lbl, f'{FONT_CSS}font-size:10px;color:{PRIMARY};font-weight:500;')
                quota_row.addWidget(q_lbl)
            quota_row.addStretch()
            layout.addLayout(quota_row)
        else:
            quota_row = QHBoxLayout()
            quota_row.setContentsMargins(0, 0, 0, 0)
            quota_row.setSpacing(6)
            q_tip = QLabel('额度未获取（点击右上方「刷新」即可测算额度）')
            set_theme_style(q_tip, f'{FONT_CSS}font-size:10px;color:{MUTED};font-style:italic;')
            quota_row.addWidget(q_tip)
            quota_row.addStretch()
            layout.addLayout(quota_row)



class CodexAccountsPane(QWidget):
    """Main account management view that can be embedded directly in the compact bubble or shown in a window."""
    accountChanged = Signal(str)

    def __init__(self, manager: CodexAccountManager = None, parent=None):
        super().__init__(parent)
        self.manager = manager or CodexAccountManager()
        self.active_worker = None
        self._init_ui()
        self.refresh_list()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # Header description
        top_box = QVBoxLayout()
        top_box.setSpacing(2)
        desc = QLabel('凭据经系统本地物理隔离，多账户一键秒级切换与自动保活。')
        set_theme_style(desc, f'{FONT_CSS}font-size:11px;color:{MUTED};')
        top_box.addWidget(desc)
        main_layout.addLayout(top_box)

        # Action Toolbar
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 2, 0, 2)
        action_bar.setSpacing(6)

        self.login_btn = QPushButton('+ 新增账户 (直接登录)')
        self.login_btn.setFixedHeight(28)
        set_theme_style(
            self.login_btn,
            f'{FONT_CSS}font-size:11px;font-weight:600;background:{PRIMARY};color:{ON_PRIMARY};border-radius:6px;padding:3px 10px;'
            f'QPushButton:hover{{background:{PRIMARY_PRESSED};}}'
        )
        self.login_btn.setToolTip('直接拉起系统默认浏览器打开 Codex 官方登录授权页')
        self.login_btn.clicked.connect(self.login_new_direct)
        action_bar.addWidget(self.login_btn)

        self.refresh_all_btn = QPushButton('全部刷新')
        self.refresh_all_btn.setFixedHeight(28)
        set_theme_style(
            self.refresh_all_btn,
            f'{FONT_CSS}font-size:11px;font-weight:600;background:{SELECTED};color:{PRIMARY};border:1px solid {BORDER};border-radius:6px;padding:3px 8px;'
            f'QPushButton:hover{{background:{BORDER};color:{INK};}}'
        )
        self.refresh_all_btn.setToolTip('一键刷新所有账户的凭据有效期并测算实时额度')
        self.refresh_all_btn.clicked.connect(self.refresh_all)
        action_bar.addWidget(self.refresh_all_btn)

        self.import_btn = QPushButton('导入当前登录')
        self.import_btn.setFixedHeight(28)
        set_theme_style(
            self.import_btn,
            f'{FONT_CSS}font-size:11px;background:{SELECTED};color:{PRIMARY};border-radius:6px;padding:3px 8px;'
            f'QPushButton:hover{{background:{BORDER};}}'
        )
        self.import_btn.setToolTip('导入当前系统已生效的 Codex 登录凭据')
        self.import_btn.clicked.connect(self.import_current)
        action_bar.addWidget(self.import_btn)

        self.renew_all_btn = QPushButton('自动续期')
        self.renew_all_btn.setFixedHeight(28)
        set_theme_style(
            self.renew_all_btn,
            f'{FONT_CSS}font-size:11px;background:{PANEL};color:{MUTED};border-radius:6px;padding:3px 8px;'
            f'QPushButton:hover{{background:{BORDER};color:{INK};}}'
        )
        self.renew_all_btn.setToolTip('一键续期所有托管账户的凭据')
        self.renew_all_btn.clicked.connect(self.renew_all)
        action_bar.addWidget(self.renew_all_btn)

        action_bar.addStretch()
        main_layout.addLayout(action_bar)

        # Status and Progress Line
        self.status_line = QLabel('')
        set_theme_style(self.status_line, f'{FONT_CSS}font-size:11px;color:{PRIMARY};')
        main_layout.addWidget(self.status_line)

        # Accounts List Scroll Area
        self.scroll = hide_scrollbars(QScrollArea())
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        set_theme_style(self.scroll, f'QScrollArea{{background:transparent;border:0;}}')

        self.card_container = QWidget()
        set_theme_style(self.card_container, f'QWidget{{background:transparent;}}')
        self.card_layout = QVBoxLayout(self.card_container)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(6)
        self.card_layout.addStretch()

        self.scroll.setWidget(self.card_container)
        main_layout.addWidget(self.scroll, 1)

    def refresh_list(self):
        while self.card_layout.count() > 1:
            item = self.card_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        accounts = self.manager.list_accounts()
        if not accounts:
            empty = QLabel('当前尚未保存任何托管账户。\n点击上方“+ 新增账户 (直接登录)”拉起授权，或“导入当前登录”。')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_theme_style(empty, f'{FONT_CSS}font-size:11px;color:{MUTED};padding:30px 10px;')
            self.card_layout.insertWidget(0, empty)
            return

        for i, acc in enumerate(accounts):
            card = AccountCard(acc)
            card.switchRequested.connect(self.on_switch_requested)
            card.refreshRequested.connect(self.on_refresh_requested)
            card.renameRequested.connect(self.on_rename_requested)
            card.deleteRequested.connect(self.on_delete_requested)
            self.card_layout.insertWidget(i, card)

        self._check_auto_probe_missing_quotas(accounts)

    def _check_auto_probe_missing_quotas(self, accounts):
        missing = [a['id'] for a in accounts if not a.get('quota', {}).get('windows')]
        if missing and not getattr(self, '_probing_missing', False):
            self._probing_missing = True
            def probe_job():
                for aid in missing:
                    try:
                        self.manager.query_account_quota(aid)
                    except Exception:
                        pass
                return True
            worker = AccountWorker(probe_job, parent=self)
            def on_done(_):
                self._probing_missing = False
                self.refresh_list()
            worker.finished.connect(on_done)
            worker.failed.connect(lambda _: setattr(self, '_probing_missing', False))
            worker.start()

    def set_busy(self, busy: bool, message: str = ''):
        self.import_btn.setEnabled(not busy)
        self.login_btn.setEnabled(not busy)
        if hasattr(self, 'refresh_all_btn'):
            self.refresh_all_btn.setEnabled(not busy)
        self.renew_all_btn.setEnabled(not busy)
        self.status_line.setText(message)


    def import_current(self):
        try:
            alias, ok = QInputDialog.getText(
                self, '导入当前账户', '请输入此账户别名（如“个人主力”、“工作号”）：',
                text='个人主力'
            )
            if not ok:
                return
            acc = self.manager.import_current_account(alias.strip())
            self.set_busy(False, f'已成功导入账户：{acc.get("alias")}')
            self.refresh_list()
        except Exception as error:
            QMessageBox.warning(self, '导入失败', str(error))

    def login_new_direct(self):
        """Directly launch login in browser without extra confirmation dialogs."""
        self.set_busy(True, '正在拉起官方登录通道，请在浏览器中完成授权…')

        from codex_connection import find_executable, child_environment, cli_command

        temp_dir = tempfile.mkdtemp(prefix='bailong_new_login_')

        def worker_fn():
            exe = find_executable()
            env = child_environment()
            env['CODEX_HOME'] = temp_dir
            cmd = cli_command(exe, 'login')
            # Run official login: spawns browser login flow
            flags = 0x08000000 if sys.platform == 'win32' else 0
            proc = subprocess.Popen(cmd, env=env, creationflags=flags)
            proc.wait(timeout=240)
            target = Path(temp_dir) / 'auth.json'
            if not target.exists():
                raise TimeoutError('未在规定时间内检测到浏览器授权凭据，操作已超时取消。')
            # Read and parse
            auth_data = json.loads(target.read_text(encoding='utf-8'))
            return auth_data

        worker = AccountWorker(worker_fn, parent=self)
        self.active_worker = worker

        def on_done(auth_data):
            self.set_busy(False, '登录成功，正在保存档案…')
            try:
                from codex_account_manager import extract_account_metadata
                from codex_account_vault import vault_encrypt
                meta = extract_account_metadata(auth_data)
                acc_id = meta['account_id'] or f"acc_{int(time.time())}"
                cipher = vault_encrypt(json.dumps(auth_data, ensure_ascii=False), self.manager.vault_path.parent)
                record = {
                    'id': acc_id,
                    'account_id': meta['account_id'],
                    'user_id': meta['user_id'],
                    'email': meta['email'],
                    'name': meta['name'],
                    'plan_type': meta['plan_type'],
                    'alias': meta.get('name') or (meta.get('email', '').split('@')[0] if meta.get('email') else '') or '新账户',
                    'expires_at': meta['expires_at'],
                    'has_refresh_token': meta['has_refresh_token'],
                    'created_at': time.time(),
                    'last_used_at': time.time(),
                    'last_refreshed_at': time.time(),
                    'status': 'active',
                    'quota': {},
                    'tokens_cipher': cipher
                }
                with self.manager._lock:
                    self.manager._accounts[acc_id] = record
                    self.manager._save_vault()
                self.refresh_list()
                self.set_busy(False, f'新账户 [{record["alias"]}] 已成功加入托管！')
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        def on_fail(err):
            self.set_busy(False, '')
            shutil.rmtree(temp_dir, ignore_errors=True)
            QMessageBox.warning(self, '登录未完成', str(err))

        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.start()

    def on_switch_requested(self, account_id: str):
        acc = self.manager.get_account(account_id)
        alias = acc.get('alias', '') if acc else account_id
        self.set_busy(True, f'正在切换至账户 [{alias}] 并同步重启 Codex…')

        def switch_job():
            self.manager.switch_account(account_id, restart_codex=True)
            # Gentle quota probe for newly activated account if needed
            try:
                self.manager.query_account_quota(account_id)
            except Exception:
                pass
            return True

        worker = AccountWorker(switch_job, parent=self)
        self.active_worker = worker

        def on_done(res):
            self.set_busy(False, f'已成功切换至账户 [{alias}]，Codex 已同步重启！')
            self.refresh_list()
            self.accountChanged.emit(account_id)

        def on_fail(err):
            self.set_busy(False, '')
            QMessageBox.critical(self, '切换失败', f'切换账户出现错误：{err}')

        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.start()

    def on_refresh_requested(self, account_id: str):
        acc = self.manager.get_account(account_id)
        alias = acc.get('alias', '') if acc else account_id
        self.set_busy(True, f'正在刷新 [{alias}] 凭据有效期与实时额度…')

        worker = AccountWorker(self.manager.refresh_and_probe_account, account_id, parent=self)
        self.active_worker = worker

        def on_done(res):
            self.set_busy(False, f'[{alias}] 凭据有效期与实时额度已更新！')
            self.refresh_list()

        def on_fail(err):
            self.set_busy(False, '')
            QMessageBox.warning(self, '刷新失败', f'无法刷新该账户：{err}')

        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.start()

    def refresh_all(self):
        self.set_busy(True, '正在刷新所有账户的凭据有效期并测算实时额度…')
        worker = AccountWorker(lambda: self.manager.refresh_all_accounts(force=True), parent=self)
        self.active_worker = worker

        def on_done(results):
            self.set_busy(False, '所有账户凭据有效期与实时额度已更新！')
            self.refresh_list()

        def on_fail(err):
            self.set_busy(False, '')
            QMessageBox.warning(self, '刷新异常', str(err))

        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.start()


    def on_rename_requested(self, account_id: str):
        acc = self.manager.get_account(account_id)
        if not acc:
            return
        current_alias = acc.get('alias', '')
        new_alias, ok = QInputDialog.getText(
            self, '重命名账户', '修改账户显示别名：', text=current_alias
        )
        if ok and new_alias.strip():
            self.manager.update_alias(account_id, new_alias.strip())
            self.refresh_list()

    def on_delete_requested(self, account_id: str):
        acc = self.manager.get_account(account_id)
        alias = acc.get('alias', '') if acc else account_id
        msg = f'确定要删除账户 [{alias}] 的本地隔离档案吗？\n\n删除后该账户的本地凭据将被移除（不会注销官方账户）。'
        if QMessageBox.question(self, '确认删除', msg) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.manager.delete_account(account_id)
            self.refresh_list()
        except Exception as error:
            QMessageBox.warning(self, '删除失败', str(error))

    def renew_all(self):
        self.set_busy(True, '正在自动巡检并续期所有托管账户…')
        worker = AccountWorker(self.manager.refresh_all_accounts, parent=self)
        self.active_worker = worker

        def on_done(results):
            self.set_busy(False, '所有账户续期巡检完成！')
            self.refresh_list()
            QMessageBox.information(self, '续期完成', '所有账户凭据已完成续期巡检，保持有效。')

        def on_fail(err):
            self.set_busy(False, '')
            QMessageBox.warning(self, '续期出现异常', str(err))

        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.start()


class CodexAccountsDialog(QDialog):
    """Standalone window version wrapping the pane."""
    accountChanged = Signal(str)

    def __init__(self, manager: CodexAccountManager = None, parent=None):
        super().__init__(parent)
        self.manager = manager or CodexAccountManager()
        self.setWindowTitle('Codex 账户管理')
        self.resize(520, 540)
        set_theme_style(
            self,
            editor_stylesheet(FONT_CSS) +
            f'QDialog{{background:{SURFACE};}}'
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.pane = CodexAccountsPane(self.manager, self)
        self.pane.accountChanged.connect(self.accountChanged.emit)
        layout.addWidget(self.pane)
