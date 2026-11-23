import time
import unittest
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QProgressBar, QFrame
from PySide6.QtCore import Qt

from codex_accounts_ui import AccountCard, CodexAccountsPane
from codex_account_manager import CodexAccountManager

app = QApplication.instance() or QApplication([])

class TestCodexAccountsUI(unittest.TestCase):
    def setUp(self):
        self.mock_acc_active = {
            'id': 'acc_test_1',
            'alias': '主力账户',
            'email': 'active@example.com',
            'plan_type': 'plus',
            'is_active': True,
            'expires_at': time.time() + 86400 * 5,
            'quota': {
                'windows': [
                    {'name': '5小时', 'remaining': 80.0, 'resetsAt': time.time() + 3600},
                    {'name': '一周', 'remaining': 95.0, 'resetsAt': time.time() + 86400}
                ]
            }
        }
        self.mock_acc_inactive = {
            'id': 'acc_test_2',
            'alias': '备用小号',
            'email': 'spare@example.com',
            'plan_type': 'free',
            'is_active': False,
            'expires_at': time.time() + 3600,
            'quota': {}
        }

    def test_account_card_layout_structure(self):
        # 1. Active card
        card_active = AccountCard(self.mock_acc_active)
        # Active card should show '当前账户' button with fixed size 64x24
        self.assertEqual(card_active.switch_btn.text(), '当前账户')
        self.assertEqual(card_active.switch_btn.width(), 64)
        self.assertEqual(card_active.switch_btn.height(), 24)
        self.assertFalse(card_active.switch_btn.isEnabled())

        # Details should not be hidden by default for active card
        self.assertFalse(card_active.details_widget.isHidden())
        self.assertIn('详情 ▴', card_active.details_btn.text())
        self.assertEqual(card_active.details_btn.width(), 54)
        self.assertEqual(card_active.details_btn.height(), 24)

        # 3 Progress bars in details
        progress_bars = card_active.details_widget.findChildren(QProgressBar)
        self.assertEqual(len(progress_bars), 3)
        # Token progress bar (index 0) with 5 days remaining should be 50% (on 10-day max scale)
        self.assertEqual(progress_bars[0].value(), 50)
        # 5-hour quota should be 80%
        self.assertEqual(progress_bars[2].value(), 80)
        # Week quota should be 95%
        self.assertEqual(progress_bars[1].value(), 95)

        # Verify the 3 title labels
        labels = [lbl.text() for lbl in card_active.details_widget.findChildren(QLabel)]
        self.assertTrue(any('凭证有效期：' in text for text in labels))
        self.assertTrue(any('本周周额度：' in text for text in labels))
        self.assertTrue(any('五小时额度：' in text for text in labels))

        # Ops button should exist and have fixed size 54x24
        self.assertEqual(card_active.ops_btn.width(), 54)
        self.assertEqual(card_active.ops_btn.height(), 24)
        self.assertEqual(card_active.ops_btn.cursor().shape(), Qt.CursorShape.PointingHandCursor)

        # 2. Inactive card
        card_inactive = AccountCard(self.mock_acc_inactive)
        # Inactive card should show '切换账户' button with same fixed size 64x24
        self.assertEqual(card_inactive.switch_btn.text(), '切换账户')
        self.assertEqual(card_inactive.switch_btn.width(), 64)
        self.assertEqual(card_inactive.switch_btn.height(), 24)
        self.assertEqual(card_inactive.switch_btn.cursor().shape(), Qt.CursorShape.PointingHandCursor)

        # Details should be hidden by default
        self.assertTrue(card_inactive.details_widget.isHidden())
        self.assertIn('详情 ▾', card_inactive.details_btn.text())

        # Test toggle details
        card_inactive.details_btn.click()
        self.assertFalse(card_inactive.details_widget.isHidden())
        self.assertIn('详情 ▴', card_inactive.details_btn.text())
        card_inactive.details_btn.click()
        self.assertTrue(card_inactive.details_widget.isHidden())
        self.assertIn('详情 ▾', card_inactive.details_btn.text())

    def test_pane_toolbar_and_status_timer(self):
        pane = CodexAccountsPane()
        # Verify 4 toolbar buttons
        self.assertEqual(pane.login_btn.text(), '新增账户')
        self.assertEqual(pane.import_btn.text(), '导入账户')
        self.assertEqual(pane.refresh_all_btn.text(), '全部刷新')
        self.assertEqual(pane.renew_all_btn.text(), '全部续期')

        # Verify PointingHandCursor on all toolbar buttons
        for btn in (pane.login_btn, pane.import_btn, pane.refresh_all_btn, pane.renew_all_btn):
            self.assertEqual(btn.cursor().shape(), Qt.CursorShape.PointingHandCursor)

        # Verify success banner auto-hide timer
        pane.set_busy(False, '切换成功测试', 'success')
        self.assertFalse(pane.status_banner.isHidden())
        self.assertIsNotNone(pane._status_timer)
        self.assertTrue(pane._status_timer.isSingleShot())
        self.assertEqual(pane._status_timer.interval(), 10000)

        # Verify busy/clear resets timer
        pane.set_busy(True, '切换中…', 'loading')
        self.assertIsNone(pane._status_timer)

    def test_pane_card_dividers(self):
        pane = CodexAccountsPane()
        # Mock 2 accounts in manager
        pane.manager.list_accounts = lambda: [self.mock_acc_active, self.mock_acc_inactive]
        pane.refresh_list()

        dividers = [w for w in pane.card_container.findChildren(QFrame) if w.objectName() == 'card_divider']
        self.assertEqual(len(dividers), 1)
        self.assertEqual(dividers[0].height(), 1)


if __name__ == '__main__':
    unittest.main()
