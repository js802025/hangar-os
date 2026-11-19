import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Left column: chats with the pilot, your spaces (live), your apps.
Rectangle {
    id: side
    color: Theme.bg2
    property int thumbTick: 0
    signal openOverview()

    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 4
            spacing: 10
            Canvas {   // the Hangar mark
                width: 26; height: 26
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    c.strokeStyle = "#eceef4"; c.lineWidth = 2.4; c.lineCap = "round"; c.lineJoin = "round"
                    c.beginPath(); c.moveTo(3, 17); c.lineTo(13, 6); c.lineTo(23, 17); c.stroke()
                    c.fillStyle = "#7c9cff"; c.fillRect(9, 13, 8, 7)
                }
            }
            Text { text: "Hangar"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Bold; Layout.fillWidth: true }
            HButton { icon: "⌗"; text: ""; kind: "ghost"; implicitWidth: 34; onClicked: side.openOverview() }
        }

        HButton { Layout.fillWidth: true; icon: "✦"; text: "New chat"; kind: "soft"; onClicked: hangar.newChat() }

        // -- chats --------------------------------------------------------------------------
        Text { text: "CHATS"; color: Theme.faint; font.pixelSize: 11; font.weight: Font.DemiBold
               font.letterSpacing: 1.2; Layout.topMargin: 6 }
        ListView {
            id: chats
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, side.height * 0.34)
            clip: true
            spacing: 2
            model: hangar.threads
            delegate: Rectangle {
                width: chats.width; height: 52; radius: Theme.r2
                property bool current: hangar.thread && hangar.thread.id === modelData.id
                color: current ? Theme.accentSoft : (ma.containsMouse ? Theme.hover : "transparent")
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                ColumnLayout {
                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 1
                    anchors.topMargin: 7; anchors.bottomMargin: 7
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: modelData.title; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium
                               elide: Text.ElideRight; Layout.fillWidth: true }
                        Rectangle { visible: modelData.busy; width: 7; height: 7; radius: 3.5; color: Theme.green }
                        Text { text: Theme.ago(modelData.updated); color: Theme.faint; font.pixelSize: 11 }
                    }
                    Text { text: modelData.preview; color: Theme.dim; font.pixelSize: 12; elide: Text.ElideRight
                           maximumLineCount: 1; wrapMode: Text.NoWrap; textFormat: Text.PlainText
                           Layout.fillWidth: true }
                }
                MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: hangar.openThread(modelData.id) }
            }
        }

        // -- spaces --------------------------------------------------------------------------
        Text { text: "SPACES"; color: Theme.faint; font.pixelSize: 11; font.weight: Font.DemiBold
               font.letterSpacing: 1.2; Layout.topMargin: 10 }
        ListView {
            id: spaces
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 4
            model: hangar.spaces
            delegate: Rectangle {
                width: spaces.width; height: 62; radius: Theme.r2
                property bool current: hangar.selected === modelData.name
                color: current ? Theme.accentSoft : (sma.containsMouse ? Theme.hover : "transparent")
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                RowLayout {
                    anchors.fill: parent; anchors.margins: 8; spacing: 10
                    RoundedImage {
                        Layout.preferredWidth: 72; Layout.preferredHeight: 45; radius: 8
                        source: modelData.status === "running" ? "thumb:" + modelData.name : ""; tick: side.thumbTick
                        opacity: modelData.status === "running" ? 1 : 0.35
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 3
                        RowLayout {
                            Layout.fillWidth: true; spacing: 6
                            Text { text: modelData.name === "pilot" ? "✦ pilot" : modelData.name; color: Theme.text
                                   font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight
                                   Layout.fillWidth: true }
                            Rectangle { width: 8; height: 8; radius: 4; color: Theme.stateColor(modelData.state) }
                        }
                        Row {
                            spacing: -6
                            Repeater {
                                model: modelData.agents
                                Avatar { name: modelData.name; tint: modelData.color; size: 18
                                         border.color: Theme.bg2; border.width: 2 }
                            }
                        }
                    }
                }
                MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: hangar.select(modelData.name) }
            }
        }

        // -- apps --------------------------------------------------------------------------
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: [["📝", "Notes", "notes"], ["🔑", "Passwords", "passwords"], ["⚙", "Settings", "settings"]]
                HButton { Layout.fillWidth: true; icon: modelData[0]; text: modelData[1]; kind: "ghost"
                          onClicked: hangar.openApp(modelData[2]) }
            }
        }
    }
}
