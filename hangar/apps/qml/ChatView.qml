import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The conversation with your pilot.
Rectangle {
    id: chat
    color: Theme.bg
    property var t: hangar.thread || ({})
    property var items: t.items || []
    property bool busy: !!t.busy
    property string shot: ""
    readonly property int col: Math.min(width - 56, 760)      // the reading column

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // header
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 58; color: "transparent"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 28; anchors.rightMargin: 20
                Text { text: chat.t.title || "New chat"; color: Theme.text; font.pixelSize: Theme.fsTitle
                       font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                Rectangle {
                    radius: 11; height: 24; width: pl.implicitWidth + 18
                    color: pcm.containsMouse ? Theme.hover : Theme.raised
                    Text { id: pl; anchors.centerIn: parent; color: hangar.pilot ? Theme.dim : Theme.yellow; font.pixelSize: 12
                           text: "✦ Pilot · " + (hangar.pilot || "not set up") }
                    MouseArea { id: pcm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: hangar.showView("settings", hangar.pilot ? "pilot" : "accounts") }
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line; opacity: 0.6 }
        }

        // approvals waiting on you (any space)
        Repeater {
            model: hangar.approvals
            Rectangle {
                Layout.preferredWidth: chat.col + 24; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 10
                height: 58; radius: Theme.r3
                color: Qt.rgba(251/255, 191/255, 36/255, 0.08); border.color: Qt.rgba(251/255, 191/255, 36/255, 0.35)
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 10; spacing: 10
                    Text { text: "🔑"; font.pixelSize: 18 }
                    Text {
                        Layout.fillWidth: true; color: Theme.text; font.pixelSize: 13; wrapMode: Text.WordWrap
                        text: "<b>" + modelData.who + "</b> wants to " + (modelData.kind === "login" ? "sign in to " : "get a code for ")
                              + modelData.site + (modelData.reason ? "<br><span style='color:#8b93a7'>" + modelData.reason + "</span>" : "")
                        textFormat: Text.StyledText
                    }
                    HButton { text: modelData.kind === "login" ? "Sign in" : "Send code"; kind: "primary"
                              onClicked: hangar.approve(modelData.id, "") }
                    HButton { text: "Deny"; kind: "ghost"; onClicked: hangar.deny(modelData.id) }
                }
            }
        }

        // empty state
        Item {
            visible: chat.items.length === 0
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors.centerIn: parent; width: Math.min(parent.width - 80, 640); spacing: 18
                Text { text: "✦"; color: Theme.accent; font.pixelSize: 40; Layout.alignment: Qt.AlignHCenter }
                Text { text: "What should your crew work on?"; color: Theme.text; font.pixelSize: Theme.fsHero
                       font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                HButton {
                    visible: !hangar.pilot; Layout.alignment: Qt.AlignHCenter
                    kind: "primary"; icon: "✦"; text: "Connect Claude Code to get started"
                    onClicked: hangar.showView("settings", "accounts")
                }
                Text { text: "Your pilot answers, or starts agents on their own desktops and keeps you posted."
                       color: Theme.dim; font.pixelSize: 15; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                       Layout.fillWidth: true }
                Flow {
                    Layout.fillWidth: true; Layout.topMargin: 6; spacing: 8
                    Repeater {
                        model: ["Plan a weekend in Lisbon and book nothing yet", "Review my open pull requests",
                                "Sort my newsletters and summarise the good ones", "Research standing desks under $500"]
                        Rectangle {
                            height: 36; radius: 18; width: sl.implicitWidth + 28
                            color: sm.containsMouse ? Theme.hover : Theme.raised; border.color: Theme.line
                            Text { id: sl; anchors.centerIn: parent; text: modelData; color: Theme.text; font.pixelSize: 13 }
                            MouseArea { id: sm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: { input.text = modelData; input.forceActiveFocus() } }
                        }
                    }
                }
            }
        }

        // messages
        ListView {
            id: list
            visible: chat.items.length > 0
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            spacing: 6
            topMargin: 22; bottomMargin: 22
            model: chat.items
            onCountChanged: Qt.callLater(positionViewAtEnd)
            delegate: Item {
                id: row
                width: list.width
                property bool quiet: it.kind === "step" || it.kind === "progress" || it.kind === "approval"
                height: body.height + (quiet ? 0 : 12)
                property var it: modelData
                Loader {
                    id: body
                    y: row.quiet ? 0 : 12
                    width: chat.col
                    x: (parent.width - width) / 2
                    sourceComponent: it.kind === "user" ? userMsg : it.kind === "pilot" ? pilotMsg
                                   : it.kind === "report" ? reportMsg : it.kind === "approval" ? approvalMsg : stepMsg
                    property var it: row.it
                }
            }
            footer: Item {
                width: list.width; height: chat.busy ? 44 : 0; visible: chat.busy
                Row {
                    x: (list.width - chat.col) / 2 + 8; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                    Text { text: "✦"; color: Theme.accent; font.pixelSize: 16
                           RotationAnimation on rotation { from: 0; to: 360; duration: 2400; loops: Animation.Infinite; running: chat.busy } }
                    Text { text: "Pilot is working…"; color: Theme.dim; font.pixelSize: 13 }
                }
            }
        }

        // composer
        Rectangle {
            Layout.preferredWidth: chat.col + 24; Layout.alignment: Qt.AlignHCenter; Layout.bottomMargin: 20
            Layout.preferredHeight: Math.max(64, input.implicitHeight + (chat.shot ? 96 : 30))
            radius: Theme.r4; color: Theme.panel; border.color: input.activeFocus ? Qt.rgba(124/255, 156/255, 255/255, 0.55) : Theme.line
            Behavior on border.color { ColorAnimation { duration: Theme.fast } }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; anchors.leftMargin: 18; spacing: 8
                RowLayout {
                    visible: chat.shot !== ""
                    RoundedImage { Layout.preferredWidth: 96; Layout.preferredHeight: 60; radius: 10
                                   source: chat.shot }
                    HButton { text: "Remove"; kind: "ghost"; onClicked: chat.shot = "" }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    TextArea {
                        id: input
                        Layout.fillWidth: true
                        placeholderText: chat.items.length ? "Reply to your pilot…" : "Ask your pilot anything…"
                        placeholderTextColor: Theme.faint
                        color: Theme.text; font.pixelSize: 15; wrapMode: TextEdit.Wrap
                        background: null
                        Keys.onReturnPressed: (e) => {
                            if (e.modifiers & Qt.ShiftModifier) { e.accepted = false; return }
                            chat.sendNow()
                        }
                    }
                    HButton { icon: "📷"; text: ""; kind: "ghost"; implicitWidth: 38
                              onClicked: chat.shot = hangar.captureScreenshot() }
                    Rectangle {
                        width: 38; height: 38; radius: 19
                        color: input.text.trim() ? Theme.accent : Theme.raised
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        Text { anchors.centerIn: parent; text: "↑"; font.pixelSize: 18; font.weight: Font.Bold
                               color: input.text.trim() ? "#0a0c11" : Theme.faint }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: chat.sendNow() }
                    }
                }
            }
        }
    }

    function sendNow() {
        if (!input.text.trim()) return
        hangar.send(input.text, chat.shot)
        input.text = ""
        chat.shot = ""
    }

    // -- message styles ---------------------------------------------------------------------
    Component {
        id: userMsg
        Item {
            width: parent ? parent.width : 0
            height: bubble.height
            Rectangle {
                id: bubble
                anchors.right: parent.right
                width: Math.min(ut.implicitWidth + 32, parent.width * 0.8)
                height: ut.implicitHeight + 22 + (it.screenshot ? 22 : 0)
                radius: Theme.r3; color: Theme.accentSoft; border.color: Qt.rgba(124/255, 156/255, 255/255, 0.25)
                Text { id: ut; x: 16; y: 11; width: parent.width - 32; text: it.text; color: Theme.text
                       font.pixelSize: 15; wrapMode: Text.WordWrap }
                Text { visible: !!it.screenshot; x: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                       text: "📷 with a screenshot"; color: Theme.dim; font.pixelSize: 12 }
            }
        }
    }
    Component {
        id: pilotMsg
        Item {
            width: parent ? parent.width : 0
            height: Math.max(pt.implicitHeight, 30)
            Rectangle { width: 30; height: 30; radius: 15; color: Theme.raised
                        Text { anchors.centerIn: parent; text: "✦"; color: Theme.accent; font.pixelSize: 15 } }
            TextEdit {
                id: pt
                x: 44; width: parent.width - 44
                text: hangar.md(it.text || "")
                textFormat: TextEdit.RichText; readOnly: true; selectByMouse: true
                wrapMode: TextEdit.Wrap; color: it.status === "error" ? Theme.red : Theme.text; font.pixelSize: 15
                selectionColor: Theme.accent
                onLinkActivated: (link) => Qt.openUrlExternally(link)
                HoverHandler { cursorShape: pt.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
            }
        }
    }
    Component {
        id: stepMsg
        Item {
            width: parent ? parent.width : 0
            height: Math.max(24, st.implicitHeight + 6)
            Text { id: mark; x: 44; y: 3; text: it.kind === "step" ? "▸" : "·"
                   color: it.kind === "step" ? Theme.accent : Theme.faint; font.pixelSize: 13 }
            Text {
                id: st
                x: 60; y: 3; width: Math.min(parent.width - 60, 680)
                text: it.text; wrapMode: Text.WordWrap
                color: it.kind === "step" ? Theme.dim : Theme.faint; font.pixelSize: 13
            }
            MouseArea { anchors.fill: parent; enabled: !!it.space; cursorShape: Qt.PointingHandCursor
                        onClicked: hangar.select(it.space) }
        }
    }
    Component {
        id: reportMsg
        Item {
          width: parent ? parent.width : 0
          height: card.height
          Rectangle {
            id: card
            width: Math.min(parent.width - 44, 620)
            x: 44
            height: rcol.implicitHeight + 24
            radius: Theme.r3; color: Theme.panel; border.color: Theme.line
            ColumnLayout {
                id: rcol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                spacing: 6
                RowLayout {
                    spacing: 8
                    Text { text: it.space; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Text { text: it.who && it.who !== it.space ? "· " + it.who : ""; color: Theme.dim; font.pixelSize: 13 }
                    StatusBadge { status: it.status }
                }
                Text { text: it.text; color: Theme.text; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: hangar.select(it.space) }
          }
        }
    }
    Component {
        id: approvalMsg
        Item {
            width: parent ? parent.width : 0
            height: 26
            Text { x: 44; anchors.verticalCenter: parent.verticalCenter; text: "🔑 " + it.text; color: Theme.yellow
                   font.pixelSize: 13 }
        }
    }
}
