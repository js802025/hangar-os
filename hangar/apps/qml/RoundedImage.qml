import QtQuick
import Hangar 1.0

// A rounded, aspect-filled image. source: a file path, or "thumb:<space>"
// for a space's live thumbnail; bump `tick` to redraw it.
Item {
    id: root
    property string source: ""
    property int radius: Theme.r2
    property int tick: 0
    onTickChanged: pic.reload()
    Picture { id: pic; anchors.fill: parent; source: root.source; radius: root.radius }
    Rectangle { anchors.fill: parent; radius: root.radius; color: "transparent"; border.color: Qt.rgba(1, 1, 1, 0.07) }
}
