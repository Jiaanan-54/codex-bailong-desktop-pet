"""Codex Multi-Account Manager & Credential Isolation Service.

Provides:
- Secure isolated storage for multiple OpenAI/Codex accounts (Windows DPAPI / macOS AES)
- Auto-refresh mechanism via OAuth token endpoint (app_EMoamEEZ73f0CkXaXp7hrann)
- Atomic 1-click active account switching with backup and rollback
- Independent isolated quota probing without switching the global account
- Account lifecycle: import, add, rename, delete, status tracking
"""
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from codex_account_vault import vault_encrypt, vault_decrypt
from desktop_core import atomic_json, VERSION

OPENAI_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
TOKEN_ENDPOINT = 'https://auth.openai.com/oauth/token'

def decode_jwt_payload(jwt_token: str) -> dict:
    """Safely decode the JSON payload part of a JWT without external libraries."""
    if not isinstance(jwt_token, str) or '.' not in jwt_token:
        return {}
    parts = jwt_token.split('.')
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    padded = payload_b64 + '=' * (-len(payload_b64) % 4)
    try:
        data = base64.urlsafe_b64decode(padded)
        return json.loads(data)
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}

def extract_account_metadata(auth_dict: dict) -> dict:
    """Extract profile metadata from auth.json dictionary."""
    tokens = auth_dict.get('tokens') or {}
    id_token = tokens.get('id_token') or ''
    access_token = tokens.get('access_token') or ''
    refresh_token = tokens.get('refresh_token') or ''
    account_id = tokens.get('account_id') or ''

    id_payload = decode_jwt_payload(id_token)
    at_payload = decode_jwt_payload(access_token)

    auth_claims = id_payload.get('https://api.openai.com/auth') or {}
    email = id_payload.get('email') or ''
    name = id_payload.get('name') or ''
    user_id = id_payload.get('sub') or auth_claims.get('user_id') or ''
    plan_type = (auth_claims.get('chatgpt_plan_type') or 'free').lower()
    chatgpt_account_id = account_id or auth_claims.get('chatgpt_account_id') or user_id

    exp = at_payload.get('exp') or id_payload.get('exp') or (time.time() + 3600)

    return {
        'account_id': chatgpt_account_id,
        'user_id': user_id,
        'email': email,
        'name': name,
        'plan_type': plan_type,
        'expires_at': float(exp),
        'has_refresh_token': bool(refresh_token)
    }

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def http_refresh_token(refresh_token: str, client_id: str = OPENAI_CLIENT_ID, timeout: int = 15) -> dict:
    """Exchange refresh_token for fresh access_token & id_token via OpenAI OAuth."""
    if not refresh_token:
        raise ValueError('缺少刷新凭证 (refresh_token)')
    form = {
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'refresh_token': refresh_token
    }
    data = urlencode(form).encode('ascii')
    req = Request(
        TOKEN_ENDPOINT,
        data=data,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )
    try:
        with build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            body = resp.read(128 * 1024)
            return json.loads(body.decode('utf-8'))
    except HTTPError as error:
        code = None
        detail = ''
        try:
            err_body = error.read(8192)
            parsed = json.loads(err_body.decode('utf-8'))
            code = parsed.get('error')
            detail = parsed.get('error_description') or ''
        except Exception:
            pass
        finally:
            error.close()
        if code == 'invalid_grant' or error.code in (400, 401):
            raise OSError(f'账户授权已过期或失效，请重新登录（{detail or "invalid_grant"}）。') from None
        if error.code == 429:
            raise OSError('OpenAI 授权服务请求频繁，请稍后再试。') from None
        raise OSError(f'刷新 Token 失败 (HTTP {error.code})：{detail or "未知错误"}') from None
    except (URLError, TimeoutError) as error:
        raise OSError('无法连接 OpenAI 认证服务，请检查网络或代理设置。') from error

class CodexAccountManager:
    def __init__(self, data_home_override: Path = None, vault_file_override: Path = None):
        if data_home_override is not None:
            self.codex_home = Path(data_home_override)
        else:
            from codex_connection import data_home
            self.codex_home = data_home()

        if vault_file_override is not None:
            self.vault_path = Path(vault_file_override)
        else:
            from platform_paths import data_dir
            self.vault_path = data_dir() / 'codex-accounts.vault.json'

        self._lock = threading.RLock()
        self._accounts = {}
        self._load_vault()

    @property
    def auth_json_path(self) -> Path:
        return self.codex_home / 'auth.json'

    def _load_vault(self):
        """Read account metadata index from vault file."""
        if not self.vault_path.exists():
            self._accounts = {}
            return
        try:
            raw = json.loads(self.vault_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict) and 'accounts' in raw and isinstance(raw['accounts'], dict):
                self._accounts = raw['accounts']
            else:
                self._accounts = {}
        except (OSError, ValueError):
            self._accounts = {}

    def _save_vault(self):
        """Atomically persist account index."""
        payload = {
            'version': 1,
            'updated_at': time.time(),
            'accounts': self._accounts
        }
        atomic_json(self.vault_path, payload)

    def list_accounts(self) -> list:
        """Return list of all managed accounts sorted by active status and last_used."""
        active_id = self.get_active_account_id()
        result = []
        with self._lock:
            for acc_id, acc in self._accounts.items():
                item = dict(acc)
                item.pop('tokens_cipher', None)
                item['is_active'] = (acc_id == active_id)
                result.append(item)
        result.sort(key=lambda a: (not a['is_active'], -a.get('last_used_at', 0)))
        return result

    def get_account(self, account_id: str) -> dict:
        """Get an account metadata by ID."""
        with self._lock:
            acc = self._accounts.get(account_id)
            if not acc:
                return None
            item = dict(acc)
            item['is_active'] = (account_id == self.get_active_account_id())
            return item

    def get_account_tokens(self, account_id: str) -> dict:
        """Decrypt and return account's auth tokens."""
        with self._lock:
            acc = self._accounts.get(account_id)
            if not acc:
                raise ValueError(f'找不到账户：{account_id}')
            cipher = acc.get('tokens_cipher')
            if not cipher:
                raise ValueError(f'账户凭证已损坏：{account_id}')
        decrypted = vault_decrypt(cipher, self.vault_path.parent)
        return json.loads(decrypted)

    def get_active_account_id(self) -> str:
        """Identify which managed account matches current auth.json on disk."""
        auth_file = self.auth_json_path
        if not auth_file.exists():
            return ''
        try:
            current_auth = json.loads(auth_file.read_text(encoding='utf-8'))
            meta = extract_account_metadata(current_auth)
            current_acc_id = meta.get('account_id')
            current_email = meta.get('email')
            with self._lock:
                for acc_id, acc in self._accounts.items():
                    if acc.get('account_id') and acc['account_id'] == current_acc_id:
                        return acc_id
                    if current_email and acc.get('email') == current_email:
                        return acc_id
        except (OSError, ValueError):
            pass
        return ''

    def import_current_account(self, alias: str = None) -> dict:
        """Capture active auth.json into managed accounts."""
        auth_file = self.auth_json_path
        if not auth_file.exists():
            raise FileNotFoundError(f'未找到 Codex 登录凭据（{auth_file}），请先在 Codex 中登录。')
        try:
            content = auth_file.read_text(encoding='utf-8')
            auth_dict = json.loads(content)
        except (OSError, ValueError) as err:
            raise ValueError(f'无法解析当前 auth.json：{err}') from err

        meta = extract_account_metadata(auth_dict)
        if not meta.get('email') and not meta.get('account_id'):
            raise ValueError('当前凭据未包含有效的 Codex / ChatGPT 账户信息。')

        acc_id = meta['account_id'] or f"acc_{int(time.time())}"
        cipher = vault_encrypt(json.dumps(auth_dict, ensure_ascii=False), self.vault_path.parent)

        with self._lock:
            existing = self._accounts.get(acc_id, {})
            name_display = meta.get('name') or meta.get('email', '').split('@')[0]
            default_alias = alias or existing.get('alias') or name_display or 'Codex 账户'
            record = {
                'id': acc_id,
                'account_id': meta['account_id'],
                'user_id': meta['user_id'],
                'email': meta['email'],
                'name': meta['name'],
                'plan_type': meta['plan_type'],
                'alias': default_alias,
                'expires_at': meta['expires_at'],
                'has_refresh_token': meta['has_refresh_token'],
                'created_at': existing.get('created_at', time.time()),
                'last_used_at': time.time(),
                'last_refreshed_at': time.time(),
                'status': 'active',
                'quota': existing.get('quota', {}),
                'tokens_cipher': cipher
            }
            self._accounts[acc_id] = record
            self._save_vault()
        return record

    def switch_account(self, account_id: str, auto_refresh: bool = True, restart_codex: bool = False) -> bool:
        """Atomically switch active Codex credentials to specified account and optionally restart Codex."""
        acc = self.get_account(account_id)
        if not acc:
            raise ValueError(f'指定账户不存在：{account_id}')

        tokens_dict = self.get_account_tokens(account_id)

        # Proactively refresh token if expired or about to expire in 5 minutes
        if auto_refresh and acc.get('has_refresh_token'):
            expires_at = acc.get('expires_at', 0)
            if expires_at <= time.time() + 300:
                try:
                    refreshed = self.refresh_account_token(account_id)
                    tokens_dict = self.get_account_tokens(account_id)
                except Exception:
                    # Proceed with existing tokens if refresh fails due to network
                    pass

        # Backup existing auth.json
        auth_file = self.auth_json_path
        bak_file = auth_file.with_name(auth_file.name + '.bak')
        if auth_file.exists():
            try:
                shutil.copy2(auth_file, bak_file)
            except OSError:
                pass

        # Atomic replacement
        try:
            atomic_json(auth_file, tokens_dict)
        except Exception as error:
            if bak_file.exists():
                try:
                    shutil.copy2(bak_file, auth_file)
                except OSError:
                    pass
            raise OSError(f'切换账户失败，已恢复原凭据：{error}') from error

        with self._lock:
            if account_id in self._accounts:
                self._accounts[account_id]['last_used_at'] = time.time()
                self._save_vault()

        if restart_codex:
            try:
                from desktop_services import restart_codex_desktop
                restart_codex_desktop()
            except Exception:
                pass
        return True

    def refresh_account_token(self, account_id: str) -> dict:
        """Perform OAuth token refresh using refresh_token."""
        tokens_dict = self.get_account_tokens(account_id)
        tokens_sub = tokens_dict.get('tokens') or {}
        refresh_token = tokens_sub.get('refresh_token')
        if not refresh_token:
            raise ValueError('此账户未包含续期凭证 (refresh_token)。')

        resp = http_refresh_token(refresh_token)

        new_access_token = resp.get('access_token')
        new_refresh_token = resp.get('refresh_token') or refresh_token
        new_id_token = resp.get('id_token') or tokens_sub.get('id_token')
        expires_in = int(resp.get('expires_in', 3600))

        tokens_sub['access_token'] = new_access_token
        tokens_sub['refresh_token'] = new_refresh_token
        if new_id_token:
            tokens_sub['id_token'] = new_id_token
        tokens_dict['tokens'] = tokens_sub
        tokens_dict['last_refresh'] = datetime.now(timezone.utc).isoformat()

        # Update metadata
        meta = extract_account_metadata(tokens_dict)
        cipher = vault_encrypt(json.dumps(tokens_dict, ensure_ascii=False), self.vault_path.parent)

        with self._lock:
            if account_id in self._accounts:
                self._accounts[account_id].update({
                    'expires_at': time.time() + expires_in,
                    'last_refreshed_at': time.time(),
                    'status': 'active',
                    'plan_type': meta.get('plan_type') or self._accounts[account_id].get('plan_type'),
                    'tokens_cipher': cipher
                })
                self._save_vault()

        # If currently active, sync to disk
        if self.get_active_account_id() == account_id:
            try:
                atomic_json(self.auth_json_path, tokens_dict)
            except OSError:
                pass

        return self.get_account(account_id)

    def refresh_and_probe_account(self, account_id: str) -> dict:
        """Force refresh token (if supported) and query live quota for a single account."""
        acc = self.get_account(account_id)
        if not acc:
            raise ValueError(f'指定账户不存在：{account_id}')

        if acc.get('has_refresh_token'):
            try:
                self.refresh_account_token(account_id)
            except Exception:
                pass

        try:
            self.query_account_quota(account_id)
        except Exception:
            pass

        return self.get_account(account_id)

    def refresh_all_accounts(self, force: bool = True) -> dict:
        """Inspect and refresh all stored accounts.
        If force=True, refreshes tokens regardless of expiration time.
        Also queries quota for all accounts so all accounts display updated rate limits.
        """
        results = {}
        with self._lock:
            ids = list(self._accounts.keys())
        for acc_id in ids:
            acc = self.get_account(acc_id)
            if not acc:
                continue
            item_res = {'token': 'skipped', 'quota': 'skipped'}
            if acc.get('has_refresh_token'):
                # Force refresh or refresh if within 7 days
                if force or acc.get('expires_at', 0) <= time.time() + 7 * 86400:
                    try:
                        self.refresh_account_token(acc_id)
                        item_res['token'] = 'refreshed'
                    except Exception as err:
                        item_res['token'] = 'failed'
                        item_res['token_error'] = str(err)
                else:
                    item_res['token'] = 'valid'
            try:
                self.query_account_quota(acc_id)
                item_res['quota'] = 'ok'
            except Exception as q_err:
                item_res['quota'] = 'failed'
                item_res['quota_error'] = str(q_err)
            results[acc_id] = item_res
        return results


    def query_account_quota(self, account_id: str, timeout: int = 15) -> list:
        """Query rate limits for any stored account in an isolated sandbox without switching global login."""
        tokens_dict = self.get_account_tokens(account_id)
        from codex_connection import find_executable, child_environment, cli_command
        from quota import quota_windows

        executable = find_executable()
        temp_dir = tempfile.mkdtemp(prefix='bailong_quota_')
        temp_path = Path(temp_dir)
        try:
            # Stage isolated auth.json
            atomic_json(temp_path / 'auth.json', tokens_dict)
            env = child_environment()
            env['CODEX_HOME'] = str(temp_path)

            command = cli_command(executable, 'app-server')
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0

            from process_launch import clean_dll_search
            with clean_dll_search():
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding='utf-8',
                    env=env,
                    creationflags=flags
                )

            messages = queue.Queue()
            def receive():
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        pass
                messages.put(None)

            threading.Thread(target=receive, daemon=True).start()

            def send(msg):
                process.stdin.write(json.dumps(msg) + '\n')
                process.stdin.flush()

            def wait_res(req_id):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        msg = messages.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                    except queue.Empty:
                        continue
                    if msg is None:
                        break
                    if msg.get('id') == req_id:
                        if 'error' in msg:
                            raise RuntimeError(msg['error'].get('message', '额度查询失败'))
                        return msg['result']
                raise TimeoutError('额度查询超时')

            try:
                send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'bailong_probe', 'version': VERSION}}})
                wait_res(1)
                send({'method': 'initialized'})
                send({'id': 2, 'method': 'account/rateLimits/read'})
                raw_limits = wait_res(2)
                windows = quota_windows(raw_limits, allow_empty=True)
                with self._lock:
                    if account_id in self._accounts:
                        self._accounts[account_id]['quota'] = {
                            'windows': windows,
                            'checked_at': time.time()
                        }
                        self._save_vault()
                return windows
            finally:
                from quota import close_cli
                try:
                    close_cli(process)
                except Exception:
                    pass
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def update_alias(self, account_id: str, new_alias: str):
        """Update display alias for an account."""
        clean_alias = str(new_alias).strip()
        if not clean_alias:
            raise ValueError('别名不能为空')
        with self._lock:
            if account_id not in self._accounts:
                raise ValueError('账户不存在')
            self._accounts[account_id]['alias'] = clean_alias[:50]
            self._save_vault()

    def delete_account(self, account_id: str):
        """Delete an account from store."""
        if account_id == self.get_active_account_id():
            raise ValueError('不能删除当前正在生效的活跃账户，请先切换到其他账户后再删除。')
        with self._lock:
            if account_id in self._accounts:
                del self._accounts[account_id]
                self._save_vault()
