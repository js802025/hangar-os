import QtQuick
import QtQuick.Layouts

// Every space at once, live. Click one to look closer; Esc to leave.
Rectangle {
    id: ov
    color: "#07080b"
    property int thumbTick: 0
    property bool shown: false
    visible: opacity > 0
    opacity: shown ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.smooth; easing.type: Easing.OutCubic } }
    focus: shown
    Keys.onEscapePressed: shown = false
    MouseArea { anchors.fill: parent; onClicked: ov.shown = false }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 44; spacing: 26
        scale: ov.shown ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: Theme.smooth; easing.type: Easing.OutCubic } }

        RowLayout {
            Layout.fillWidth: true
            Text { text: "Spaces"; color: Theme.text; font.pixelSize: Theme.fsHero; font.weight: Font.DemiBold }
            Text { text: hangar.spaces.length + " · live"; color: Theme.dim; font.pixelSize: 15; Layout.leftMargin: 12
                   Layout.alignment: Qt.AlignBaseline }
            Item { Layout.fillWidth: true }
            Text { text: "Esc to close"; color: Theme.faint; font.pixelSize: 13 }
        }

        Item {
          id: area
          Layout.fillWidth: true; Layout.fillHeight: true
          GridView {
            id: grid
            property int cols: Math.max(1, Math.min(4, Math.ceil(Math.sqrt(count))))
            property int rows: Math.max(1, Math.ceil(count / cols))
            // as large as fits: limited by the width per column or the height per row
            property real cell: Math.max(160, Math.min(area.width / cols, (area.height / rows - 82) * 16 / 10 + 24))
            cellWidth: cell
            cellHeight: (cell - 24) * 10 / 16 + 82
            width: cols * cell
            height: Math.min(area.height, rows * cellHeight)
            anchors.centerIn: parent
            interactive: contentHeight > height
            model: hangar.spaces
            delegate: Item {
                width: grid.cellWidth; height: grid.cellHeight
                Item {
                    id: card
                    anchors.fill: parent; anchors.margins: 12
                    scale: cm.containsMouse ? 1.025 : 1
                    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                    Rectangle {
                        id: frame
                        width: parent.width; height: width * 10 / 16; radius: 16; color: "#07080b"
                        border.color: cm.containsMouse ? Theme.accent : Theme.line; border.width: cm.containsMouse ? 2 : 1
                        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
                        RoundedImage {
                            anchors.fill: parent; anchors.margins: 2; radius: 14
                            source: modelData.status === "running" ? "thumb:" + modelData.name : ""; tick: ov.thumbTick
                            opacity: modelData.status === "running" ? 1 : 0.3
                        }
                        Text { visible: modelData.status !== "running"; anchors.centerIn: parent; text: "paused"
                               color: Theme.faint; font.pixelSize: 14 }
                    }
                    RowLayout {
                        anchors.top: frame.bottom; anchors.topMargin: 10; width: parent.width; spacing: 8
                        Text { text: modelData.name === "pilot" ? "✦ pilot" : modelData.name; color: Theme.text
                               font.pixelSize: 15; font.weight: Font.DemiBold }
                        StatusBadge { status: modelData.state }
                        Item { Layout.fillWidth: true }
                        Row {
                            spacing: -6
                            Repeater { model: modelData.agents
                                       Avatar { name: modelData.name; tint: modelData.color; size: 22
                                                border.color: Theme.bg; border.width: 2 } }
                        }
                    }
                    MouseArea { id: cm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: { hangar.select(modelData.name); ov.shown = false } }
                }
            }
        }
    }
    }
}
