"""macOS specific window and surface handling for desktop companion."""
import sys
from PySide6.QtCore import Qt


def _cocoa():
    if sys.platform != 'darwin':
        return None, None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app and app.platformName() != 'cocoa':
        return None, None
    try:
        import ctypes
        objc = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/Cocoa.framework/Cocoa')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        return objc, ctypes
    except Exception:
        return None, None


def get_nswindow(hwnd):
    if sys.platform != 'darwin' or not hwnd:
        return None
    try:
        val = int(hwnd)
        if val < 0x100000:
            return None
    except Exception:
        return None
    objc, ctypes = _cocoa()
    if not objc:
        return None
    try:
        sel_window = objc.sel_registerName(b'window')
        msgSend_ptr = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        return msgSend_ptr(ctypes.c_void_p(val), sel_window)
    except Exception:
        return None


def apply_macos_native_properties(widget):
    """Apply Cocoa-level hidesOnDeactivate and collectionBehavior to native window."""
    if sys.platform != 'darwin' or not widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        return
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and app.platformName() != 'cocoa':
            return
        objc, ctypes = _cocoa()
        if not objc:
            return
        sel_setHides = objc.sel_registerName(b'setHidesOnDeactivate:')
        sel_setBehavior = objc.sel_registerName(b'setCollectionBehavior:')
        
        msgSend_bool = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))
        msgSend_ulong = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
        
        win_id = int(widget.winId())
        nswindow = get_nswindow(win_id)
        if nswindow:
            # Explicitly force hidesOnDeactivate: NO so clicking other windows doesn't hide the pet
            msgSend_bool(nswindow, sel_setHides, False)
            # 1 = NSWindowCollectionBehaviorCanJoinAllSpaces
            # 16 = NSWindowCollectionBehaviorStationary
            # 256 = NSWindowCollectionBehaviorFullScreenAuxiliary
            msgSend_ulong(nswindow, sel_setBehavior, 1 | 16 | 256)
    except Exception:
        pass


def configure_macos_window(widget):
    """Ensure tool windows on macOS do not hide on application deactivate and stay across spaces."""
    if sys.platform != 'darwin':
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
    if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        apply_macos_native_properties(widget)


def is_macos_window_topmost(hwnd):
    if sys.platform != 'darwin' or not hwnd:
        return False
    objc, ctypes = _cocoa()
    if not objc:
        return False
    try:
        nswindow = get_nswindow(hwnd)
        if not nswindow:
            return False
        sel_level = objc.sel_registerName(b'level')
        msgSend_long = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p))
        level = msgSend_long(nswindow, sel_level)
        return level >= 3
    except Exception:
        return False


def set_macos_window_layer(hwnd, layer):
    if sys.platform != 'darwin' or not hwnd:
        return False
    objc, ctypes = _cocoa()
    if not objc:
        return False
    try:
        nswindow = get_nswindow(hwnd)
        if not nswindow:
            return False
        sel_setLevel = objc.sel_registerName(b'setLevel:')
        msgSend_setLong = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long))
        # Levels: top = 3 (NSFloatingWindowLevel), normal = 0 (NSNormalWindowLevel), bottom = -2147483603 (kCGDesktopWindowLevel)
        target = 3 if layer == 'top' else -2147483603 if layer == 'bottom' else 0
        msgSend_setLong(nswindow, sel_setLevel, target)
        return True
    except Exception:
        return False


def order_macos_window(top_hwnd, bottom_hwnd):
    if sys.platform != 'darwin' or not top_hwnd or not bottom_hwnd:
        return
    objc, ctypes = _cocoa()
    if not objc:
        return
    try:
        top_nw = get_nswindow(top_hwnd)
        bottom_nw = get_nswindow(bottom_hwnd)
        if not top_nw or not bottom_nw:
            return
        sel_winNumber = objc.sel_registerName(b'windowNumber')
        sel_order = objc.sel_registerName(b'orderWindow:relativeTo:')
        msgSend_long = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p))
        msgSend_order = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_long))
        rel_num = msgSend_long(bottom_nw, sel_winNumber)
        # 1 = NSWindowAbove
        msgSend_order(top_nw, sel_order, 1, rel_num)
    except Exception:
        pass
