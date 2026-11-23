"""Unit tests for Codex Multi-Account Manager & Credential Isolation."""
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_account_manager import (
    CodexAccountManager,
    decode_jwt_payload,
    extract_account_metadata
)
from codex_account_vault import vault_encrypt, vault_decrypt

def make_dummy_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b'=').decode('ascii')
    claims = base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).rstrip(b'=').decode('ascii')
    sig = base64.urlsafe_b64encode(b'dummysignaturebytes').rstrip(b'=').decode('ascii')
    return f"{header}.{claims}.{sig}"

class TestCodexAccountManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix='test_acc_mgr_'))
        self.codex_home = self.test_dir / '.codex'
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.vault_file = self.test_dir / 'accounts.vault.json'

        self.account_a_tokens = {
            'auth_mode': 'chatgpt',
            'tokens': {
                'id_token': make_dummy_jwt({
                    'email': 'user_a@example.com',
                    'name': 'User Alpha',
                    'sub': 'user-alpha-id',
                    'exp': time.time() + 3600,
                    'https://api.openai.com/auth': {
                        'chatgpt_account_id': 'acc-alpha-111',
                        'chatgpt_plan_type': 'plus',
                        'user_id': 'user-alpha-id'
                    }
                }),
                'access_token': make_dummy_jwt({
                    'exp': time.time() + 3600,
                    'sub': 'user-alpha-id'
                }),
                'refresh_token': 'rt_alpha_token_123',
                'account_id': 'acc-alpha-111'
            },
            'last_refresh': '2026-11-23T00:00:00Z'
        }

        self.account_b_tokens = {
            'auth_mode': 'chatgpt',
            'tokens': {
                'id_token': make_dummy_jwt({
                    'email': 'user_b@company.com',
                    'name': 'User Beta',
                    'sub': 'user-beta-id',
                    'exp': time.time() + 7200,
                    'https://api.openai.com/auth': {
                        'chatgpt_account_id': 'acc-beta-222',
                        'chatgpt_plan_type': 'team',
                        'user_id': 'user-beta-id'
                    }
                }),
                'access_token': make_dummy_jwt({
                    'exp': time.time() + 7200,
                    'sub': 'user-beta-id'
                }),
                'refresh_token': 'rt_beta_token_456',
                'account_id': 'acc-beta-222'
            },
            'last_refresh': '2026-11-23T00:00:00Z'
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_jwt_decode(self):
        token = make_dummy_jwt({'email': 'test@example.com', 'exp': 12345})
        payload = decode_jwt_payload(token)
        self.assertEqual(payload.get('email'), 'test@example.com')
        self.assertEqual(payload.get('exp'), 12345)

    def test_extract_metadata(self):
        meta = extract_account_metadata(self.account_a_tokens)
        self.assertEqual(meta['email'], 'user_a@example.com')
        self.assertEqual(meta['plan_type'], 'plus')
        self.assertEqual(meta['account_id'], 'acc-alpha-111')
        self.assertTrue(meta['has_refresh_token'])

    def test_import_and_list_accounts(self):
        # Stage Account A in active codex_home
        auth_file = self.codex_home / 'auth.json'
        auth_file.write_text(json.dumps(self.account_a_tokens), encoding='utf-8')

        mgr = CodexAccountManager(self.codex_home, self.vault_file)
        acc = mgr.import_current_account(alias='个人主力号')
        self.assertEqual(acc['alias'], '个人主力号')
        self.assertEqual(acc['email'], 'user_a@example.com')
        self.assertEqual(acc['plan_type'], 'plus')

        accounts = mgr.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertTrue(accounts[0]['is_active'])

    def test_switch_accounts(self):
        # Stage Account A
        auth_file = self.codex_home / 'auth.json'
        auth_file.write_text(json.dumps(self.account_a_tokens), encoding='utf-8')

        mgr = CodexAccountManager(self.codex_home, self.vault_file)
        mgr.import_current_account(alias='Alpha')

        # Now stage and import Account B
        auth_file.write_text(json.dumps(self.account_b_tokens), encoding='utf-8')
        mgr.import_current_account(alias='Beta')

        # Currently Account B is on disk
        self.assertEqual(mgr.get_active_account_id(), 'acc-beta-222')

        # Switch back to Account A
        ok = mgr.switch_account('acc-alpha-111')
        self.assertTrue(ok)

        # Check auth.json on disk
        current_disk = json.loads(auth_file.read_text(encoding='utf-8'))
        meta = extract_account_metadata(current_disk)
        self.assertEqual(meta['account_id'], 'acc-alpha-111')
        self.assertEqual(meta['email'], 'user_a@example.com')
        self.assertEqual(mgr.get_active_account_id(), 'acc-alpha-111')

        # Check backup exists
        self.assertTrue((self.codex_home / 'auth.json.bak').exists())

    def test_delete_prevented_for_active(self):
        auth_file = self.codex_home / 'auth.json'
        auth_file.write_text(json.dumps(self.account_a_tokens), encoding='utf-8')

        mgr = CodexAccountManager(self.codex_home, self.vault_file)
        mgr.import_current_account(alias='Alpha')

        with self.assertRaises(ValueError):
            mgr.delete_account('acc-alpha-111')

    def test_alias_update(self):
        auth_file = self.codex_home / 'auth.json'
        auth_file.write_text(json.dumps(self.account_a_tokens), encoding='utf-8')

        mgr = CodexAccountManager(self.codex_home, self.vault_file)
        mgr.import_current_account(alias='旧名字')
        mgr.update_alias('acc-alpha-111', '新别名')

        acc = mgr.get_account('acc-alpha-111')
        self.assertEqual(acc['alias'], '新别名')

    @patch('codex_account_manager.http_refresh_token')
    def test_token_refresh(self, mock_http):
        mock_http.return_value = {
            'access_token': make_dummy_jwt({'exp': time.time() + 9999}),
            'refresh_token': 'new_rt_token_999',
            'expires_in': 9999
        }
        auth_file = self.codex_home / 'auth.json'
        auth_file.write_text(json.dumps(self.account_a_tokens), encoding='utf-8')

        mgr = CodexAccountManager(self.codex_home, self.vault_file)
        mgr.import_current_account(alias='Alpha')

        mgr.refresh_account_token('acc-alpha-111')
        tokens = mgr.get_account_tokens('acc-alpha-111')
        self.assertEqual(tokens['tokens']['refresh_token'], 'new_rt_token_999')

    def test_vault_portable_and_native(self):
        secret = "secret_credentials_data_123"
        # Test portable AES encryption (macOS path)
        enc_portable = vault_encrypt(secret, self.test_dir, force_portable=True)
        dec_portable = vault_decrypt(enc_portable, self.test_dir, force_portable=True)
        self.assertEqual(dec_portable, secret)

        # Test native (Windows DPAPI)
        if sys.platform == 'win32':
            enc_native = vault_encrypt(secret, self.test_dir)
            dec_native = vault_decrypt(enc_native, self.test_dir)
            self.assertEqual(dec_native, secret)

if __name__ == '__main__':
    unittest.main()
