import QtQuick
import QtQuick.Controls

// Dropdown. model: [{text, value}]; `value` is the selected value.
ComboBox {
    id: c
    property var value
    signal picked(var value)
    textRole: "text"
    valueRole: "value"
    implicitHeight: 38
    implicitWidth: 260
    font.pixelSize: Theme.fs - 1
    Component.onCompleted: sync()
    onModelChanged: sync()
    onValueChanged: sync()
    function sync() {
        if (!model) return
        for (var i = 0; i < model.length; i++)
            if (JSON.stringify(model[i].value) === JSON.stringify(value)) { currentIndex = i; return }
    }
    onActivated: function(i) { value = model[i].value; picked(model[i].value) }

    background: Rectangle {
        radius: Theme.r1 + 2
        color: c.hovered ? Theme.hover : Theme.bg2
        border.color: c.visualFocus || c.popup.visible ? Qt.rgba(124/255, 156/255, 255/255, 0.75) : Theme.line
    }
    contentItem: Text {
        leftPadding: 12; rightPadding: 30
        text: c.displayText; color: Theme.text; font: c.font
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    indicator: Text {
        x: c.width - width - 12; anchors.verticalCenter: parent.verticalCenter
        text: "⌄"; color: Theme.dim; font.pixelSize: 14
    }
    delegate: ItemDelegate {
        id: d
        width: c.width - 8; x: 4
        height: 34
        highlighted: c.highlightedIndex === index
        contentItem: Text {
            text: modelData.text; color: Theme.text; font: c.font
            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; leftPadding: 4
        }
        background: Rectangle { radius: Theme.r1; color: d.highlighted ? Theme.hover : "transparent" }
    }
    popup: Popup {
        y: c.height + 4; width: c.width; padding: 4
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
        contentItem: ListView { clip: true; implicitHeight: contentHeight; model: c.popup.visible ? c.delegateModel : null }
        background: Rectangle { radius: Theme.r2; color: Theme.raised; border.color: Theme.line }
    }
}
