import QtQuick

// Hangar's text field: quiet until focused, then an accent outline.
Rectangle {
    id: f
    property alias text: input.text
    property string placeholder: ""
    property bool secret: false
    property bool mono: false
    property alias input: input
    signal accepted()
    implicitHeight: 38
    implicitWidth: 240
    radius: Theme.r1 + 2
    color: Theme.bg2
    border.color: input.activeFocus ? Qt.rgba(124/255, 156/255, 255/255, 0.75) : Theme.line
    border.width: input.activeFocus ? 1.5 : 1
    Behavior on border.color { ColorAnimation { duration: Theme.fast } }

    TextInput {
        id: input
        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
        verticalAlignment: TextInput.AlignVCenter
        color: Theme.text; selectionColor: Theme.accentSoft; selectedTextColor: Theme.text
        font.pixelSize: Theme.fs - 1
        font.family: f.mono ? "Menlo" : Qt.application.font.family
        echoMode: f.secret ? TextInput.Password : TextInput.Normal
        clip: true
        selectByMouse: true
        onAccepted: f.accepted()
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !input.text && !input.preeditText
            text: f.placeholder; color: Theme.faint
            font.pixelSize: input.font.pixelSize; font.family: input.font.family
        }
    }
}
