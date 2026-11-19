import QtQuick
import QtQuick.Layouts

// One setting: title and explanation on the left, its control on the right.
Item {
    id: r
    property string title: ""
    property string detail: ""
    property bool last: false
    default property alias control: slot.data
    implicitHeight: Math.max(col.implicitHeight, slot.childrenRect.height) + 28
    Layout.fillWidth: true
    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.leftMargin: 18
        anchors.right: slot.left; anchors.rightMargin: 20
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text { text: r.title; color: Theme.text; font.pixelSize: Theme.fs - 0.5; font.weight: Font.Medium
               Layout.fillWidth: true; wrapMode: Text.WordWrap; visible: text !== "" }
        Text { text: r.detail; color: Theme.dim; font.pixelSize: Theme.fsSmall + 0.5; wrapMode: Text.WordWrap
               Layout.fillWidth: true; visible: text !== ""; textFormat: Text.StyledText; linkColor: Theme.accent }
    }
    Item {
        id: slot
        anchors.right: parent.right; anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        width: childrenRect.width; height: childrenRect.height
    }
    Rectangle { visible: !r.last; anchors.bottom: parent.bottom; x: 18; width: parent.width - 36; height: 1
                color: Theme.line; opacity: 0.7 }
}
