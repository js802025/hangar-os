import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A sign-in or download running in the daemon (a job), as a sheet over the window:
// the official sign-in page opens in your browser, you paste back any code it shows.
// Hangar never sees your password.
Item {
    id: sheet
    property var api
    property var job: null              // {id, title, kind}
    property var state: ({})            // the job as it runs: lines, urls, done, code
    property bool details: false
    signal finished(var job)
    anchors.fill: parent
    visible: job !== null
    z: 50

    function open(j) { job = j; state = {}; details = false; code.text = ""; poll() }
    function close() { job = null; timer.stop() }
    function poll() {
        if (!job) return
        api.call("GET", "/api/jobs/" + job.id, null, function(j) {
            state = j
            if (j.done) { timer.stop(); finished(j) } else timer.start()
        })
    }
    Timer { id: timer; interval: 900; onTriggered: sheet.poll() }

    Rectangle { anchors.fill: parent; color: "#000000"; opacity: 0.55
                MouseArea { anchors.fill: parent } }

    Rectangle {
        anchors.centerIn: parent
        width: 520; height: col.implicitHeight + 48
        radius: Theme.r3; color: Theme.panel; border.color: Theme.line
        ColumnLayout {
            id: col
            anchors.fill: parent; anchors.margins: 24
            spacing: 16
            RowLayout {
                spacing: 12
                Rectangle { width: 40; height: 40; radius: 20; color: Theme.accentSoft
                            Text { anchors.centerIn: parent; text: sheet.job && sheet.job.kind === "login" ? "✦" : "↓"
                                   color: Theme.accent; font.pixelSize: 18 } }
                ColumnLayout {
                    spacing: 2; Layout.fillWidth: true
                    Text { text: sheet.job ? sheet.job.title : ""; color: Theme.text; font.pixelSize: Theme.fsTitle
                           font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { color: sheet.state.done ? (sheet.state.code === 0 ? Theme.green : Theme.red) : Theme.dim
                           font.pixelSize: Theme.fsSmall
                           text: !sheet.state.lines ? "Starting…" : sheet.state.done
                                 ? (sheet.state.code === 0 ? "Done" : "Stopped (code " + sheet.state.code + ")")
                                 : (sheet.job && sheet.job.kind === "login" && (sheet.state.urls || []).length
                                    ? "Waiting for you to sign in" : "Working…") }
                }
            }

            // steps for a sign-in
            ColumnLayout {
                visible: sheet.job && sheet.job.kind === "login" && !sheet.state.done
                spacing: 12; Layout.fillWidth: true
                Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                       text: "This is the official sign-in. It opens in your browser; Hangar never sees your password." }
                RowLayout {
                    spacing: 10; Layout.fillWidth: true
                    Text { text: "1"; color: Theme.accent; font.weight: Font.Bold }
                    Text { text: "Sign in on the official page"; color: Theme.text; Layout.fillWidth: true }
                    HButton { kind: "primary"; text: "Open sign-in page ↗"
                              enabledState: (sheet.state.urls || []).length > 0
                              onClicked: hangar.openUrl(sheet.state.urls[sheet.state.urls.length - 1]) }
                }
                RowLayout {
                    spacing: 10; Layout.fillWidth: true
                    Text { text: "2"; color: Theme.accent; font.weight: Font.Bold }
                    HField { id: code; Layout.fillWidth: true; placeholder: "Paste the code it shows (if any)"
                             onAccepted: send() }
                    HButton { text: "Send"; kind: "soft"; enabledState: code.text.length > 0; onClicked: send() }
                }
            }

            // what's happening, for the curious
            Text {
                text: (sheet.details ? "▾ " : "▸ ") + "Details"; color: Theme.dim; font.pixelSize: Theme.fsSmall
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sheet.details = !sheet.details }
            }
            Rectangle {
                visible: sheet.details
                Layout.fillWidth: true; Layout.preferredHeight: 180
                radius: Theme.r1; color: "#07080b"; border.color: Theme.line
                Flickable {
                    id: fl
                    anchors.fill: parent; anchors.margins: 10; clip: true
                    contentHeight: log.implicitHeight
                    onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
                    Text { id: log; width: fl.width; wrapMode: Text.WrapAnywhere; color: Theme.dim
                           font.family: "Menlo"; font.pixelSize: 11
                           text: (sheet.state.lines || []).join("\n") }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                HButton { visible: !sheet.state.done; text: "Cancel"; kind: "ghost"
                          onClicked: { api.call("DELETE", "/api/jobs/" + sheet.job.id, null, null); sheet.close() } }
                HButton { visible: !!sheet.state.done; text: "Done"; kind: "primary"; onClicked: sheet.close() }
            }
        }
    }

    function send() {
        if (!code.text) return
        api.call("POST", "/api/jobs/" + job.id + "/input", {text: code.text}, function() { code.text = "" })
    }
}
