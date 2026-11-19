import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Hangar 1.0

// One space, edge to edge: its live desktop with a slim bar on top.
ApplicationWindow {
    id: win
    width: 1440; height: 900
    visible: true
    color: "#07080b"
    property var s: {
        for (var i = 0; i < hangar.spaces.length; i++)
            if (hangar.spaces[i].name === hangar.selected) return hangar.spaces[i]
        return ({})
    }
    property bool control: false

    LiveDesktop {
        id: desk
        anchors.fill: parent; anchors.topMargin: bar.height
        space: win.s.status === "running" ? hangar.selected : ""
        control: win.control
    }

    Rectangle {
        id: bar
        width: parent.width; height: 48; color: Theme.bg2
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 10; spacing: 10
            Text { text: "✦"; color: Theme.accent; font.pixelSize: 15 }
            Text { text: hangar.selected; color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold }
            StatusBadge { status: win.control ? "taken over" : (win.s.state || win.s.status || "") }
            Row {
                spacing: -6; Layout.leftMargin: 6
                Repeater { model: win.s.agents || []
                           Avatar { name: modelData.name; tint: modelData.color; size: 22; border.color: Theme.bg2; border.width: 2 } }
            }
            Text {
                Layout.fillWidth: true; elide: Text.ElideRight; color: Theme.dim; font.pixelSize: 13
                text: win.s.report ? win.s.report.message : (win.s.task || "")
            }
            HButton {
                visible: win.s.status === "running"
                text: win.control ? "Hand back" : "Take over"; icon: win.control ? "↩" : "✋"
                kind: win.control ? "primary" : "soft"
                onClicked: { win.control = !win.control; hangar.act(hangar.selected, win.control ? "takeover" : "release") }
            }
            HButton { text: "Hangar"; icon: "⌂"; kind: "ghost"; onClicked: hangar.openHome(hangar.selected) }
            HButton { icon: "✕"; text: ""; kind: "ghost"; implicitWidth: 34; onClicked: win.close() }
        }
    }
}
