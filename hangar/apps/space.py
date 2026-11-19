"""One space's live desktop in its own window; on the Hangar OS, on its own
KDE virtual desktop ("✦ <space>"), full screen.

  hangar-app space NAME
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType

from hangar import kde
from hangar.apps import common
from hangar.apps.home import Bridge, LiveDesktop, Picture, Thumbs

QML = Path(__file__).parent / "qml"


def main() -> None:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtQuickControls2 import QQuickStyle
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.exit("usage: hangar-app space NAME")
    name = args[-1].removeprefix("hangar-space:")
    app = common.make_app("spaces")
    app.setDesktopFileName("dev.hangar.space")
    sock_name = f"hangar-space-{name}"
    sock = QLocalSocket()
    sock.connectToServer(sock_name)
    if sock.waitForConnected(300):          # already open: bring it (and its desktop) forward
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(sock_name)
    server = QLocalServer()
    server.listen(sock_name)

    QQuickStyle.setStyle("Basic")
    qmlRegisterType(LiveDesktop, "Hangar", 1, 0, "LiveDesktop")
    qmlRegisterType(Picture, "Hangar", 1, 0, "Picture")
    engine = QQmlApplicationEngine()
    thumbs = Thumbs()
    Picture.thumbs = thumbs
    engine.addImageProvider("thumbs", thumbs)
    bridge = Bridge(thumbs)
    bridge.select(name)
    engine.rootContext().setContextProperty("hangar", bridge)
    engine.rootContext().setContextProperty("onKde", kde.available())
    engine.addImportPath(str(QML))
    engine.load(QUrl.fromLocalFile(str(QML / "SpaceWindow.qml")))
    if not engine.rootObjects():
        sys.exit("couldn't load the space window")
    win = engine.rootObjects()[0]
    caption = f"{name} · Hangar"
    win.setTitle(caption)

    own = kde.available() and kde.find(name) is None
    if kde.available():
        kde.ensure_desktop(name)
        QTimer.singleShot(400, lambda: kde.place(caption, name))
    else:
        win.showMaximized()

    def show() -> None:
        win.show()
        win.raise_()
        win.requestActivate()
        if kde.available():
            kde.switch(name)

    def incoming() -> None:
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(500)
        show()
    server.newConnection.connect(incoming)
    code = app.exec()
    del engine
    if own:
        kde.remove_desktop(name)              # we made it; take it away with the window
    sys.exit(code)


if __name__ == "__main__":
    main()
