import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// What the crew changed since the last task started: files, diffs, revert per file.
Item {
    id: rv
    property string space: ""
    property var r: hangar.review || ({})
    property var files: r.files || []
    property int pick: 0
    property var f: files.length ? files[Math.min(pick, files.length - 1)] : null
    onFilesChanged: pick = 0

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true; color: Theme.dim; font.pixelSize: 12; elide: Text.ElideRight
                text: r.loading ? "Looking at the workspace…" : r.error ? r.error
                    : files.length === 0 ? "Nothing changed since the last task started."
                    : files.length + (files.length === 1 ? " file changed" : " files changed")
                      + (r.snapshot && r.snapshot.label ? " · since " + r.snapshot.label.replace(/^before: ?/, "") : "")
            }
            HButton { icon: "↻"; text: ""; kind: "ghost"; implicitWidth: 32; onClicked: hangar.loadReview(rv.space) }
            HButton { icon: "⌨"; text: "Open in editor"; kind: "soft"; onClicked: hangar.openInEditor(rv.space) }
        }

        RowLayout {
            visible: files.length > 0
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10

            // files
            ListView {
                id: fl
                Layout.preferredWidth: Math.min(220, rv.width * 0.36); Layout.fillHeight: true
                clip: true; spacing: 2
                model: rv.files
                delegate: Rectangle {
                    width: fl.width; height: 46; radius: Theme.r1
                    color: index === rv.pick ? Theme.accentSoft : (fm.containsMouse ? Theme.hover : "transparent")
                    ColumnLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 0
                        anchors.topMargin: 5; anchors.bottomMargin: 5
                        Text { text: modelData.path.split("/").pop(); color: Theme.text; font.pixelSize: 13
                               elide: Text.ElideMiddle; Layout.fillWidth: true }
                        RowLayout {
                            spacing: 6
                            Text { text: modelData.status; font.pixelSize: 11
                                   color: modelData.status === "added" ? Theme.green : modelData.status === "deleted" ? Theme.red : Theme.yellow }
                            Text { visible: !modelData.binary; text: "+" + modelData.added; color: Theme.green; font.pixelSize: 11 }
                            Text { visible: !modelData.binary; text: "−" + modelData.removed; color: Theme.red; font.pixelSize: 11 }
                        }
                    }
                    MouseArea { id: fm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: rv.pick = index }
                }
            }

            // diff
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true
                radius: Theme.r2; color: "#0b0e14"; border.color: Theme.line; clip: true
                ColumnLayout {
                    anchors.fill: parent; spacing: 0
                    RowLayout {
                        Layout.fillWidth: true; Layout.margins: 8; Layout.leftMargin: 12
                        Text { text: rv.f ? rv.f.path : ""; color: Theme.dim; font.pixelSize: 12; font.family: "monospace"
                               elide: Text.ElideMiddle; Layout.fillWidth: true }
                        HButton { text: "Revert"; icon: "↺"; kind: "danger"; implicitHeight: 28
                                  onClicked: hangar.revert(rv.space, rv.f.path) }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                    ListView {
                        id: dl
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        model: rv.f ? (rv.f.binary ? [{kind: "hunk", text: "Binary or very large file: not shown."}] : rv.f.diff) : []
                        delegate: Rectangle {
                            width: dl.width; height: 20
                            color: modelData.kind === "add" ? Qt.rgba(74/255, 222/255, 128/255, 0.10)
                                 : modelData.kind === "del" ? Qt.rgba(248/255, 113/255, 113/255, 0.10)
                                 : modelData.kind === "hunk" ? Qt.rgba(124/255, 156/255, 255/255, 0.07) : "transparent"
                            Text {
                                x: 12; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 16
                                text: modelData.text; elide: Text.ElideRight; textFormat: Text.PlainText
                                font.family: "monospace"; font.pixelSize: 12
                                color: modelData.kind === "add" ? "#a7f3c0" : modelData.kind === "del" ? "#fecaca"
                                     : modelData.kind === "hunk" ? Theme.accent : Theme.dim
                            }
                        }
                    }
                }
            }
        }
        Item { visible: files.length === 0; Layout.fillHeight: true }
    }
}
