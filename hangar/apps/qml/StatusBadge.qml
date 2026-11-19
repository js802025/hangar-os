import QtQuick

Rectangle {
    id: badge
    property string status: ""
    implicitHeight: 22
    implicitWidth: row.implicitWidth + 16
    radius: 11
    color: Qt.rgba(1, 1, 1, 0.05)
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Rectangle {
            width: 7; height: 7; radius: 3.5
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.stateColor(badge.status)
            SequentialAnimation on opacity {
                running: badge.status === "working"; loops: Animation.Infinite
                NumberAnimation { to: 0.3; duration: 700 }
                NumberAnimation { to: 1; duration: 700 }
            }
        }
        Text { text: Theme.stateLabel(badge.status); color: Theme.stateColor(badge.status)
               font.pixelSize: Theme.fsSmall; font.weight: Font.DemiBold }
    }
}
