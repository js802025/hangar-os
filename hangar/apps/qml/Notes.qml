import QtQuick
import QtQuick.Controls

// Notes on its own (`hangar-app notes --standalone`); normally a view in Hangar's window.
ApplicationWindow {
    width: 1280; height: 820
    minimumWidth: 820; minimumHeight: 520
    visible: true
    title: "Notes"
    color: Theme.bg
    NotesView { anchors.fill: parent }
}
