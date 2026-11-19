import QtQuick

Rectangle {
    property string name: ""
    property color tint: Theme.accent
    property int size: 26
    width: size; height: size; radius: size / 2
    color: tint
    Text { anchors.centerIn: parent; text: (name || "?").charAt(0).toUpperCase(); color: "#0a0c11"
           font.pixelSize: size * 0.46; font.weight: Font.Bold }
}
