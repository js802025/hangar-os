import QtQuick

// On/off switch.
Rectangle {
    id: s
    property bool checked: false
    signal toggled(bool on)
    implicitWidth: 38; implicitHeight: 22
    radius: height / 2
    color: checked ? Theme.accent : Theme.raised
    border.color: checked ? "transparent" : Theme.line
    Behavior on color { ColorAnimation { duration: Theme.fast } }
    Rectangle {
        width: 16; height: 16; radius: 8
        y: 3; x: s.checked ? s.width - width - 3 : 3
        color: s.checked ? Theme.bg : Theme.dim
        Behavior on x { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                onClicked: { s.checked = !s.checked; s.toggled(s.checked) } }
}
