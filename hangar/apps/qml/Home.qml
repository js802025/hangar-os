import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1440; height: 900
    minimumWidth: 900; minimumHeight: 600
    visible: true
    title: "Hangar"
    color: Theme.bg
    topPadding: 0                       // run under the (hidden) macOS title bar: Theme.chromeTop leaves room

    // macOS: no title bar, so empty space along the top moves the window (and double-click zooms)
    MouseArea {
        visible: Theme.mac
        anchors { top: parent.top; left: parent.left; right: parent.right }
        height: 52
        onPressed: win.startSystemMove()
        onDoubleClicked: win.visibility === Window.Maximized ? win.showNormal() : win.showMaximized()
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Sidebar {
            id: side
            Layout.preferredWidth: 288; Layout.fillHeight: true
            onOpenOverview: ov.shown = true
        }
        ChatView { visible: hangar.view === "chat"; Layout.fillWidth: true; Layout.fillHeight: true }
        // the other views load only while they're showing
        Loader { active: hangar.view === "notes"; visible: active; Layout.fillWidth: true; Layout.fillHeight: true
                 sourceComponent: NotesView {} }
        Loader { active: hangar.view === "passwords"; visible: active; Layout.fillWidth: true; Layout.fillHeight: true
                 sourceComponent: PasswordsView { api: api } }
        Loader { active: hangar.view === "settings"; visible: active; Layout.fillWidth: true; Layout.fillHeight: true
                 sourceComponent: SettingsView { api: api; signIn: signIn } }
        // the browser stays loaded once opened: your tabs live in it
        Loader { id: browserView; property bool opened: hangar.view === "browser"
                 active: opened
                 Connections { target: hangar; function onViewChanged() { if (hangar.view === "browser") browserView.opened = true } }
                 visible: hangar.view === "browser"; Layout.fillWidth: true; Layout.fillHeight: true
                 source: active ? "BrowserView.qml" : "" }
        SpacePanel {
            id: space
            visible: hangar.selected !== "" && hangar.view === "chat"
            Layout.preferredWidth: Math.max(440, win.width * 0.42); Layout.fillHeight: true
            onClose: hangar.select("")
        }
    }

    Overview { id: ov; anchors.fill: parent }

    // Every view talks to the daemon through this: api.call(method, path, body, done, failed)
    QtObject {
        id: api
        property int seq: 0
        property var pending: ({})
        function call(method, path, body, done, failed) {
            var id = ++seq
            pending[id] = [done, failed]
            hangar.request(id, method, path, body === undefined ? null : body)
        }
    }
    Connections {
        target: hangar
        function onReply(id, data, error) {
            var cb = api.pending[id]; delete api.pending[id]
            if (!cb) return
            if (error) { if (cb[1]) cb[1](error); else toast.show("Something went wrong", error) }
            else if (cb[0]) cb[0](data)
        }
    }

    SignInSheet { id: signIn; api: api }

    Connections {
        target: hangar
        function onThumbsTick() { side.thumbTick++; ov.thumbTick++ }
        function onOverviewRequested() { ov.shown = true }
        function onToast(title, body) { toast.show(title, body) }
    }
    Shortcut { enabled: hangar.view !== "notes"; sequences: ["Ctrl+N"]; onActivated: hangar.newChat() }
    Shortcut { sequences: ["Ctrl+,"]; onActivated: hangar.showView("settings") }
    Shortcut { sequences: ["Meta+S", "Ctrl+Up"]; onActivated: ov.shown = !ov.shown }

    // toasts
    Rectangle {
        id: toast
        property string head: ""
        property string body: ""
        function show(h, b) { head = h; body = b; opacity = 1; hide.restart() }
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - height - 28
        width: Math.min(tc.implicitWidth + 40, 520); height: tc.implicitHeight + 24
        radius: Theme.r3; color: Theme.raised; border.color: Theme.line
        opacity: 0; visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.smooth } }
        Column {
            id: tc; anchors.centerIn: parent; spacing: 2; width: parent.width - 40
            Text { text: toast.head; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
            Text { visible: toast.body !== ""; text: toast.body; color: Theme.dim; font.pixelSize: 12
                   wrapMode: Text.WordWrap; width: parent.width; maximumLineCount: 3; elide: Text.ElideRight }
        }
        Timer { id: hide; interval: 3800; onTriggered: toast.opacity = 0 }
    }
}
