import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Notes: your Markdown notes (and your crew's), in Hangar's window or on its own.
Rectangle {
    id: win
    color: Theme.bg

    property var n: notes.note || ({})
    property bool has: !!n.path
    property bool editing: true

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // -- list ------------------------------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 300; Layout.fillHeight: true; color: Theme.bg
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 18 + Theme.chromeTop; spacing: 10
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 4
                    Text { text: "Notes"; color: Theme.text; font.pixelSize: Theme.fsTitle + 3; font.weight: Font.Bold; Layout.fillWidth: true }
                    HButton { icon: "＋"; text: "New"; kind: "soft"; onClicked: notes.create("") }
                }
                Rectangle {
                    Layout.fillWidth: true; height: 38; radius: 19; color: Theme.panel
                    border.color: q.activeFocus ? Qt.rgba(124/255, 156/255, 255/255, 0.55) : Theme.line
                    TextField {
                        id: q
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 10
                        background: null; color: Theme.text; font.pixelSize: 13
                        placeholderText: "Search notes"; placeholderTextColor: Theme.faint
                        onTextChanged: searchDelay.restart()
                    }
                    Timer { id: searchDelay; interval: 220; onTriggered: notes.search(q.text) }
                }
                ListView {
                    id: list
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; spacing: 2
                    model: notes.notes
                    delegate: Rectangle {
                        width: list.width; height: 64; radius: Theme.r2
                        property bool current: win.n.path === modelData.path
                        color: current ? Theme.accentSoft : (lm.containsMouse ? Theme.hover : "transparent")
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 10; anchors.leftMargin: 12; spacing: 2
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                Text { text: modelData.title; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold
                                       elide: Text.ElideRight; Layout.fillWidth: true }
                                Rectangle {
                                    visible: modelData.agent; radius: 8; height: 16; width: at.implicitWidth + 12
                                    color: Qt.rgba(124/255, 156/255, 255/255, 0.16)
                                    Text { id: at; anchors.centerIn: parent; text: "✦ " + modelData.path.split("/")[1]
                                           color: Theme.accent; font.pixelSize: 10; font.weight: Font.DemiBold }
                                }
                                Text { text: Theme.ago(modelData.modified * 1000); color: Theme.faint; font.pixelSize: 11 }
                            }
                            Text { text: modelData.snippet || modelData.preview || " "; color: Theme.dim; font.pixelSize: 12
                                   elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                        MouseArea { id: lm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: notes.open(modelData.path) }
                    }
                }
                Text {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.faint; font.pixelSize: 11
                    text: "Plain Markdown in your Notes folder: open it in Obsidian or any editor too. Notes marked ✦ were written by your crew."
                }
            }
        }

        // -- editor ----------------------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true; color: Theme.bg

            ColumnLayout {
                visible: !win.has
                anchors.centerIn: parent; spacing: 14; width: 420
                Glyph { glyph: "✎"; Layout.alignment: Qt.AlignHCenter }
                Text { text: "Your notes, and your crew's"; color: Theme.text; font.pixelSize: 22; font.weight: Font.DemiBold
                       Layout.alignment: Qt.AlignHCenter }
                Text { text: "Agents keep their findings here, and you can hand any note to your pilot."
                       color: Theme.dim; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true
                       horizontalAlignment: Text.AlignHCenter }
                HButton { text: "New note"; kind: "primary"; Layout.alignment: Qt.AlignHCenter; onClicked: notes.create("") }
            }

            ColumnLayout {
                visible: win.has
                anchors.fill: parent; spacing: 0

                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 58; Layout.topMargin: Theme.chromeTop; Layout.leftMargin: 36; Layout.rightMargin: 18
                    spacing: 8
                    TextField {
                        id: titleField
                        Layout.fillWidth: true; background: null
                        text: win.n.title || ""; color: Theme.text; font.pixelSize: Theme.fsTitle + 3; font.weight: Font.DemiBold
                        onEditingFinished: if (text !== win.n.title) notes.rename(win.n.path, text)
                    }
                    Row {
                        spacing: 2
                        Repeater {
                            model: ["Edit", "Read"]
                            Rectangle {
                                height: 30; radius: 15; width: ml.implicitWidth + 24
                                property bool on: (index === 0) === win.editing
                                color: on ? Theme.raised : (mm.containsMouse ? Theme.hover : "transparent")
                                border.color: on ? Theme.line : "transparent"
                                Text { id: ml; anchors.centerIn: parent; text: modelData; font.pixelSize: 13
                                       color: parent.on ? Theme.text : Theme.dim }
                                MouseArea { id: mm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onClicked: win.editing = index === 0 }
                            }
                        }
                    }
                    HButton { icon: "✦"; text: "Ask pilot"; kind: "soft"; onClicked: notes.askPilot(win.n.path) }
                    HButton { icon: "🗑"; text: ""; kind: "ghost"; implicitWidth: 34; onClicked: notes.remove(win.n.path) }
                }
                Text { Layout.leftMargin: 38; text: (win.n.path || "") + (win.n.tags && win.n.tags.length ? "   #" + win.n.tags.join("  #") : "")
                       color: Theme.faint; font.pixelSize: 12 }

                ScrollView {
                    id: sv
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 10
                    contentWidth: availableWidth
                    Column {
                        width: sv.availableWidth
                        TextArea {
                            id: editor
                            visible: win.editing
                            width: parent.width
                            leftPadding: 36; rightPadding: 36; topPadding: 6; bottomPadding: 40
                            background: null; color: Theme.text; selectionColor: Theme.accent
                            font.pixelSize: 15; wrapMode: TextEdit.Wrap
                            placeholderText: "Start writing… link notes with [[Title]]"; placeholderTextColor: Theme.faint
                            property string loaded: ""
                            onTextChanged: if (win.has && text !== loaded) notes.edit(win.n.path, text)
                            Connections {
                                target: notes
                                function onNoteChanged() {
                                    var t = (notes.note && notes.note.text) || ""
                                    if (t !== editor.text) { editor.loaded = t; editor.text = t }
                                }
                            }
                        }
                        Text {
                            visible: !win.editing
                            width: parent.width
                            leftPadding: 38; rightPadding: 38; topPadding: 6; bottomPadding: 40
                            wrapMode: Text.Wrap; textFormat: Text.RichText
                            color: Theme.text; linkColor: Theme.accent; font.pixelSize: 15; lineHeight: 1.15
                            text: win.has && !win.editing ? notes.preview(editor.text) : ""
                            onLinkActivated: (link) => link.startsWith("note:") ? notes.follow(decodeURIComponent(link.slice(5)))
                                                                                : Qt.openUrlExternally(link)
                            HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
                        }
                    }
                }
            }
        }

        // -- links -------------------------------------------------------------------------
        Rectangle {
            visible: win.has
            Layout.preferredWidth: 260; Layout.fillHeight: true; color: Theme.bg2
            Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: Theme.line }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; anchors.topMargin: 16 + Theme.chromeTop; spacing: 8
                Text { text: "LINKED FROM"; color: Theme.faint; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.2 }
                Repeater {
                    model: win.n.backlinks || []
                    Rectangle {
                        Layout.fillWidth: true; height: 50; radius: Theme.r1
                        color: bm.containsMouse ? Theme.hover : Theme.panel; border.color: Theme.line
                        Column {
                            anchors.fill: parent; anchors.margins: 8; spacing: 2
                            Text { text: modelData.title; color: Theme.text; font.pixelSize: 13; elide: Text.ElideRight; width: parent.width }
                            Text { text: modelData.preview; color: Theme.dim; font.pixelSize: 11; elide: Text.ElideRight; width: parent.width }
                        }
                        MouseArea { id: bm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: notes.open(modelData.path) }
                    }
                }
                Text { visible: !(win.n.backlinks && win.n.backlinks.length); text: "Nothing links here yet."
                       color: Theme.dim; font.pixelSize: 12 }
                Text { text: "LINKS TO"; color: Theme.faint; font.pixelSize: 11; font.weight: Font.DemiBold
                       font.letterSpacing: 1.2; Layout.topMargin: 14 }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    Repeater {
                        model: win.n.links || []
                        Rectangle {
                            height: 26; radius: 13; width: ll.implicitWidth + 20
                            color: lk.containsMouse ? Theme.hover : Theme.raised; border.color: Theme.line
                            Text { id: ll; anchors.centerIn: parent; text: modelData; color: Theme.accent; font.pixelSize: 12 }
                            MouseArea { id: lk; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: notes.follow(modelData) }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
    }

    Shortcut { enabled: win.visible; sequences: ["Ctrl+N"]; onActivated: notes.create("") }
    Shortcut { enabled: win.visible; sequences: ["Ctrl+F", "Ctrl+K"]; onActivated: q.forceActiveFocus() }
    Shortcut { enabled: win.visible; sequences: ["Ctrl+E"]; onActivated: win.editing = !win.editing }

    Rectangle {
        id: toast
        property string head: ""
        function show(h) { head = h; opacity = 1; hide.restart() }
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - height - 24
        width: tl.implicitWidth + 36; height: 40; radius: 20; color: Theme.raised; border.color: Theme.line
        opacity: 0; visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.smooth } }
        Text { id: tl; anchors.centerIn: parent; text: toast.head; color: Theme.text; font.pixelSize: 13 }
        Timer { id: hide; interval: 3000; onTriggered: toast.opacity = 0 }
        Connections { target: notes; function onToast(h, b) { toast.show(h) } }
    }
}
