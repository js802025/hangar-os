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

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Sidebar {
            id: side
            Layout.preferredWidth: 288; Layout.fillHeight: true
            onOpenOverview: ov.shown = true
        }
        ChatView { Layout.fillWidth: true; Layout.fillHeight: true }
        SpacePanel {
            id: space
            visible: hangar.selected !== ""
            Layout.preferredWidth: Math.max(440, win.width * 0.42); Layout.fillHeight: true
            onClose: hangar.select("")
        }
    }

    Overview { id: ov; anchors.fill: parent }

    Connections {
        target: hangar
        function onThumbsTick() { side.thumbTick++; ov.thumbTick++ }
        function onOverviewRequested() { ov.shown = true }
        function onToast(title, body) { toast.show(title, body) }
    }
    Shortcut { sequences: ["Ctrl+N"]; onActivated: hangar.newChat() }
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
