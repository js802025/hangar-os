import QtQuick

// A soft accent badge with a glyph, for empty states and headers.
Rectangle {
    property string glyph: "✦"
    property int size: 56
    width: size; height: size; radius: size / 2
    color: Theme.accentSoft
    border.color: Qt.rgba(124/255, 156/255, 255/255, 0.25)
    Text { anchors.centerIn: parent; text: parent.glyph; color: Theme.accent; font.pixelSize: parent.size * 0.42 }
}
