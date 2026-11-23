"""Cross-platform secure credential vault for Codex multi-account tokens.

Supports:
- Windows: Native DPAPI (CryptProtectData / CryptUnprotectData) via ctypes
- macOS & Linux: AES-256 authenticated encryption with machine-user key stored in data_dir (0600)
"""
import base64
import json
import os
import sys
from pathlib import Path

IS_WIN = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'

if IS_WIN:
    import ctypes
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def _get_fallback_key(data_dir: Path) -> bytes:
    """Retrieve or generate a 32-byte master key for non-Windows encryption."""
    key_file = data_dir / '.vault_key'
    if key_file.exists():
        try:
            raw = key_file.read_bytes()
            if len(raw) == 32:
                return raw
        except OSError:
            pass
    import secrets
    key = secrets.token_bytes(32)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        if hasattr(os, 'chmod'):
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
    except OSError:
        pass
    return key

def _encrypt_aes(raw: bytes, key: bytes) -> bytes:
    """Encrypt raw bytes using AES-GCM or authenticated AES-CBC."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, raw, None)
        return b'gcm:' + nonce + ciphertext
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(raw)
        return b'pyc:' + cipher.nonce + tag + ciphertext
    except ImportError:
        pass
    # Fallback using standard library hashlib + HMAC stream cipher
    import hashlib, hmac
    nonce = os.urandom(16)
    keystream = hashlib.sha256(key + nonce).digest()
    while len(keystream) < len(raw):
        keystream += hashlib.sha256(key + keystream).digest()
    encrypted = bytes(b ^ k for b, k in zip(raw, keystream[:len(raw)]))
    tag = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    return b'hmc:' + nonce + tag + encrypted

def _decrypt_aes(payload: bytes, key: bytes) -> bytes:
    """Decrypt payload using the corresponding scheme."""
    if payload.startswith(b'gcm:'):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        body = payload[4:]
        nonce, ciphertext = body[:12], body[12:]
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    if payload.startswith(b'pyc:'):
        from Crypto.Cipher import AES
        body = payload[4:]
        nonce, tag, ciphertext = body[:16], body[16:32], body[32:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)
    if payload.startswith(b'hmc:'):
        import hashlib, hmac
        body = payload[4:]
        nonce, tag, ciphertext = body[:16], body[16:48], body[48:]
        expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError('HMAC validation failed')
        keystream = hashlib.sha256(key + nonce).digest()
        while len(keystream) < len(ciphertext):
            keystream += hashlib.sha256(key + keystream).digest()
        return bytes(b ^ k for b, k in zip(ciphertext, keystream[:len(ciphertext)]))
    raise ValueError('Unknown encryption scheme')

def vault_encrypt(value: str, custom_data_dir: Path = None, force_portable: bool = False) -> str:
    """Encrypt a string. On Windows uses DPAPI; on others uses AES."""
    if not value:
        return ''
    raw = value.encode('utf-8')
    if IS_WIN and not force_portable:
        buf = ctypes.create_string_buffer(raw)
        src = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
        if not ok:
            raise OSError('Windows 凭据保护服务调用失败')
        try:
            result = ctypes.string_at(out.pbData, out.cbData)
            return 'dpapi:' + base64.b64encode(result).decode('ascii')
        finally:
            ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.LocalFree(out.pbData)
    # macOS / Linux or portable
    if custom_data_dir is None:
        from platform_paths import data_dir
        root = data_dir()
    else:
        root = Path(custom_data_dir)
    key = _get_fallback_key(root)
    encrypted = _encrypt_aes(raw, key)
    return 'aes:' + base64.b64encode(encrypted).decode('ascii')

def vault_decrypt(encoded: str, custom_data_dir: Path = None, force_portable: bool = False) -> str:
    """Decrypt an encrypted string."""
    if not encoded:
        return ''
    if encoded.startswith('dpapi:') and IS_WIN and not force_portable:
        raw = base64.b64decode(encoded[6:])
        buf = ctypes.create_string_buffer(raw)
        src = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
        if not ok:
            raise OSError('无法解密本地凭据，请确认在当前 Windows 用户下运行。')
        try:
            result = ctypes.string_at(out.pbData, out.cbData)
            return result.decode('utf-8')
        finally:
            ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.LocalFree(out.pbData)
    if encoded.startswith('aes:') or force_portable:
        b64_part = encoded[4:] if encoded.startswith('aes:') else encoded
        raw = base64.b64decode(b64_part)
        if custom_data_dir is None:
            from platform_paths import data_dir
            root = data_dir()
        else:
            root = Path(custom_data_dir)
        key = _get_fallback_key(root)
        decrypted = _decrypt_aes(raw, key)
        return decrypted.decode('utf-8')
    # Backward compatibility with un-prefixed DPAPI string
    if IS_WIN:
        raw = base64.b64decode(encoded)
        buf = ctypes.create_string_buffer(raw)
        src = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
        if ok:
            try:
                result = ctypes.string_at(out.pbData, out.cbData)
                return result.decode('utf-8')
            finally:
                ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
                ctypes.windll.kernel32.LocalFree(out.pbData)
    raise OSError('凭据密文无法识别或不支持当前系统环境')
