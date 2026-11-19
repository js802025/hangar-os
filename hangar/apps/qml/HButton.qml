import QtQuick

// Pill button: kind "primary" | "ghost" | "soft" | "danger".
Rectangle {
    id: b
    property string text: ""
    property string icon: ""
    property string kind: "soft"
    property bool enabledState: true
    signal clicked()
    implicitHeight: 34
    implicitWidth: row.implicitWidth + 28
    radius: height / 2
    opacity: enabledState ? 1 : 0.45
    color: kind === "primary" ? (ma.containsMouse ? "#93acff" : Theme.accent)
         : kind === "ghost" ? (ma.containsMouse ? Theme.hover : "transparent")
         : kind === "danger" ? (ma.containsMouse ? Qt.rgba(1, 0.4, 0.4, 0.16) : "transparent")
         : (ma.containsMouse ? Theme.hover : Theme.raised)
    border.color: kind === "soft" ? Theme.line : kind === "danger" ? Qt.rgba(1, 0.45, 0.45, 0.35) : "transparent"
    Behavior on color { ColorAnimation { duration: Theme.fast } }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 7
        Text { visible: b.icon !== ""; text: b.icon; font.pixelSize: Theme.fs; color: label.color; anchors.verticalCenter: parent.verticalCenter }
        Text {
            id: label
            text: b.text
            font.pixelSize: Theme.fs - 1
            font.weight: b.kind === "primary" ? Font.DemiBold : Font.Medium
            color: b.kind === "primary" ? "#0a0c11" : b.kind === "danger" ? Theme.red : Theme.text
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                enabled: b.enabledState; onClicked: b.clicked() }
    scale: ma.pressed ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: 90 } }
}
