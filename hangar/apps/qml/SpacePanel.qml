import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Hangar 1.0

// The selected space: its live desktop, its crew, and what they changed.
Rectangle {
    id: panel
    color: Theme.bg2
    property var s: {
        for (var i = 0; i < hangar.spaces.length; i++)
            if (hangar.spaces[i].name === hangar.selected) return hangar.spaces[i]
        return ({})
    }
    property bool running: s.status === "running"
    property int tab: 0
    property bool control: false
    signal close()

    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: Theme.line }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        // header
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            ColumnLayout {
                spacing: 2; Layout.fillWidth: true
                RowLayout {
                    spacing: 8
                    Text { text: s.name === "pilot" ? "✦ pilot" : (s.name || ""); color: Theme.text
                           font.pixelSize: Theme.fsTitle; font.weight: Font.DemiBold }
                    StatusBadge { status: panel.control ? "taken over" : (s.state || s.status || "") }
                }
                Text { text: (s.agents ? s.agents.length : 0) + (s.agents && s.agents.length === 1 ? " agent" : " agents")
                             + (s.flavor && s.flavor !== "base" ? " · " + s.flavor : "")
                             + (s.computer === "mac" ? " · Mac" : "")
                             + (s.via ? " · started by " + s.via : "")
                             + (s.network && s.network !== "open" && s.network !== "internet" ? " · limited internet" : "")
                       color: Theme.dim; font.pixelSize: 12 }
            }
            HButton {
                visible: panel.running
                text: panel.control ? "Hand back" : "Take over"
                icon: panel.control ? "↩" : "✋"
                kind: panel.control ? "primary" : "soft"
                onClicked: { panel.control = !panel.control; hangar.act(s.name, panel.control ? "takeover" : "release") }
            }
            HButton {
                visible: panel.running
                icon: "⧉"; text: hangar.linux ? "Own desktop" : "Pop out"; kind: "ghost"
                onClicked: hangar.popOut(s.name)
            }
            HButton {
                text: panel.running ? "Pause" : "Start"; kind: "ghost"
                onClicked: hangar.act(s.name, panel.running ? "stop" : "start")
            }
            HButton { icon: "✕"; text: ""; kind: "ghost"; implicitWidth: 34; onClicked: panel.close() }
        }

        // live desktop
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: width * 10 / 16
            radius: 16; color: "#07080b"
            border.color: panel.control ? Theme.magenta : Theme.line; border.width: panel.control ? 2 : 1
            LiveDesktop {
                id: desk
                anchors.fill: parent; anchors.margins: 2
                control: panel.control
                space: panel.running ? (s.name || "") : ""
            }
            Text { visible: !panel.running; anchors.centerIn: parent; color: Theme.faint; font.pixelSize: 14
                   text: "This space is paused." }
            Connections { target: hangar; function onSelectedChanged() { panel.control = false; panel.tab = 0 } }
        }

        // tabs
        Row {
            spacing: 4
            Repeater {
                model: ["Crew", "Changes"]
                Rectangle {
                    height: 30; radius: 15; width: tl.implicitWidth + 26
                    color: panel.tab === index ? Theme.raised : (tm.containsMouse ? Theme.hover : "transparent")
                    border.color: panel.tab === index ? Theme.line : "transparent"
                    Text { id: tl; anchors.centerIn: parent; text: modelData; font.pixelSize: 13
                           color: panel.tab === index ? Theme.text : Theme.dim; font.weight: Font.Medium }
                    MouseArea { id: tm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: { panel.tab = index; if (index === 1) hangar.loadReview(s.name) } }
                }
            }
        }

        // crew
        ListView {
            visible: panel.tab === 0
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; spacing: 8
            model: s.agents || []
            delegate: Rectangle {
                width: ListView.view.width
                height: cc.implicitHeight + 22
                radius: Theme.r2; color: Theme.panel; border.color: Theme.line
                RowLayout {
                    id: cc
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 11; spacing: 10
                    Avatar { name: modelData.name; tint: modelData.color; size: 30; Layout.alignment: Qt.AlignTop }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 3
                        RowLayout {
                            spacing: 8
                            Text { text: modelData.name; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Text { text: modelData.role || ""; color: Theme.dim; font.pixelSize: 12; visible: !!modelData.role }
                            Item { Layout.fillWidth: true }
                            StatusBadge { status: modelData.state }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight
                            color: modelData.report ? Theme.text : Theme.dim; font.pixelSize: 13
                            text: modelData.report ? modelData.report.message : (modelData.task || "Waiting for something to do.")
                        }
                    }
                }
            }
        }

        // changes
        ReviewView {
            visible: panel.tab === 1
            Layout.fillWidth: true; Layout.fillHeight: true
            space: s.name || ""
        }

        // direct a task at this space
        Rectangle {
            Layout.fillWidth: true; height: 48; radius: 24; color: Theme.panel
            border.color: task.activeFocus ? Qt.rgba(124/255, 156/255, 255/255, 0.55) : Theme.line
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 6
                TextField {
                    id: task
                    Layout.fillWidth: true; background: null
                    color: Theme.text; font.pixelSize: 14
                    placeholderText: "Give " + (s.name || "this space") + " a task…"; placeholderTextColor: Theme.faint
                    onAccepted: go()
                    function go() { hangar.runTask(s.name, "", text); text = "" }
                }
                HButton { text: "Run"; kind: task.text.trim() ? "primary" : "soft"; onClicked: task.go() }
            }
        }
    }
}
