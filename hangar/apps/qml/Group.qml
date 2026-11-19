import QtQuick
import QtQuick.Layouts

// A titled group of settings (rows inside a rounded card).
ColumnLayout {
    id: g
    property string title: ""
    property string detail: ""
    default property alias rows: body.data
    Layout.fillWidth: true
    spacing: 8
    Text { text: g.title; visible: text !== ""; color: Theme.text; font.pixelSize: Theme.fs; font.weight: Font.DemiBold
           Layout.leftMargin: 2 }
    Text { text: g.detail; visible: text !== ""; color: Theme.dim; font.pixelSize: Theme.fsSmall + 0.5
           wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.leftMargin: 2; textFormat: Text.StyledText
           linkColor: Theme.accent; onLinkActivated: function(l) { hangar.openUrl(l) } }
    Rectangle {
        Layout.fillWidth: true; Layout.topMargin: 4
        implicitHeight: body.implicitHeight
        radius: Theme.r2 + 2; color: Theme.panel; border.color: Theme.line
        ColumnLayout { id: body; anchors.left: parent.left; anchors.right: parent.right; spacing: 0 }
    }
}
