import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Passwords, in Hangar's window: the logins agents can borrow (with your OK).
Rectangle {
    id: view
    property var api
    color: Theme.bg

    property var vault: ({ entries: [], grants: [], audit: [] })
    property string filter: "all"          // all | codes | deleted
    property string query: ""
    property string selected: ""
    property var editing: null             // null | {} (new) | entry
    property var secret: ({})              // revealed {password} for the selected entry
    property bool reveal: false
    property var codes: ({})               // id -> {code, seconds_left}
    property string error: ""

    function load() {
        api.call("GET", "/api/vault", null, function(d) {
            vault = d
            if (selected && !entry(selected)) selected = ""
            if (!selected && list().length) selected = list()[0].id
        })
    }
    function loadCodes() {
        api.call("GET", "/api/vault/codes", null, function(cs) {
            var m = {}; for (var i = 0; i < cs.length; i++) m[cs[i].id] = cs[i]; codes = m })
    }
    Component.onCompleted: { load(); loadCodes() }
    onVisibleChanged: if (visible) { load(); loadCodes() }
    Timer { running: view.visible; interval: 1000; repeat: true
            onTriggered: { var any = false; for (var k in view.codes) { any = true; break }
                           if (any && new Date().getSeconds() % 30 === 0) view.loadCodes()
                           else if (any) view.codesChanged() } }
    Connections { target: hangar; function onApprovalsChanged() { if (view.visible) view.load() } }

    function entry(id) { return vault.entries.find(function(e) { return e.id === id }) }
    function list() {
        var q = query.toLowerCase()
        return vault.entries.filter(function(e) {
            if (filter === "deleted" ? !e.deleted : !!e.deleted) return false
            if (filter === "codes" && !e.has_totp) return false
            return !q || (e.label + " " + e.site + " " + e.username).toLowerCase().indexOf(q) >= 0
        }).sort(function(a, b) { return a.label.localeCompare(b.label) })
    }
    function pick(id) { selected = id; editing = null; reveal = false; secret = {} }
    function secondsLeft() { return 30 - Math.floor(Date.now() / 1000) % 30 }
    function initial(s) { return (s || "?").replace(/^www\./, "").charAt(0).toUpperCase() }
    function tint(s) {
        var cs = ["#7c9cff", "#e879f9", "#38bdf8", "#4ade80", "#fbbf24", "#f87171", "#a78bfa", "#fb923c"]
        var h = 0; for (var i = 0; i < (s || "").length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
        return cs[h % cs.length]
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // -- list -----------------------------------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 320; Layout.fillHeight: true; color: Theme.bg
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 18 + Theme.chromeTop; spacing: 12
                RowLayout {
                    Layout.leftMargin: 4
                    Text { text: "Passwords"; color: Theme.text; font.pixelSize: Theme.fsTitle + 3; font.weight: Font.Bold
                           Layout.fillWidth: true }
                    HButton { icon: "＋"; text: "New"; kind: "soft"
                              onClicked: { view.selected = ""; view.editing = {}; view.error = "" } }
                }
                HField { Layout.fillWidth: true; placeholder: "Search"; onTextChanged: view.query = text }
                Row {
                    spacing: 6
                    Repeater {
                        model: [["all", "All"], ["codes", "Codes"], ["deleted", "Deleted"]]
                        Rectangle {
                            height: 28; width: fl.implicitWidth + 22; radius: 14
                            color: view.filter === modelData[0] ? Theme.raised : "transparent"
                            border.color: view.filter === modelData[0] ? Theme.line : "transparent"
                            Text { id: fl; anchors.centerIn: parent; text: modelData[1]; font.pixelSize: Theme.fsSmall
                                   color: view.filter === modelData[0] ? Theme.text : Theme.dim }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: view.filter = modelData[0] }
                        }
                    }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                    model: view.list()
                    delegate: Rectangle {
                        width: ListView.view.width; height: 54; radius: Theme.r2
                        color: view.selected === modelData.id ? Theme.raised : ema.containsMouse ? Theme.panel : "transparent"
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 12
                            Rectangle { width: 32; height: 32; radius: 9; color: view.tint(modelData.site)
                                        Text { anchors.centerIn: parent; text: view.initial(modelData.label); color: "white"
                                               font.weight: Font.Bold; font.pixelSize: 15 } }
                            ColumnLayout {
                                spacing: 1; Layout.fillWidth: true
                                Text { text: modelData.label; color: Theme.text; font.pixelSize: Theme.fs - 1
                                       font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { text: modelData.username || modelData.site; color: Theme.dim; font.pixelSize: Theme.fsSmall
                                       elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            Text { visible: modelData.warnings.length > 0; text: "⚠"; color: Theme.yellow }
                        }
                        MouseArea { id: ema; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: view.pick(modelData.id) }
                    }
                    Text {
                        anchors.centerIn: parent; width: parent.width - 40; visible: parent.count === 0
                        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: Theme.faint
                        text: view.query ? "Nothing matches." : view.filter === "deleted" ? "Nothing deleted."
                              : "No passwords yet. Add the logins agents may use; they ask you each time."
                    }
                }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line; opacity: 0.6 }
        }

        // -- detail -------------------------------------------------------------------------------
        ScrollView {
            id: detail
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentWidth: availableWidth
            property var e: view.selected ? view.entry(view.selected) : null

            ColumnLayout {
                width: Math.min(detail.availableWidth - 80, 620)
                x: (detail.availableWidth - width) / 2
                spacing: 24
                Item { Layout.preferredHeight: 26 + Theme.chromeTop }

                // view an entry
                ColumnLayout {
                    visible: !!detail.e && view.editing === null
                    spacing: 24; Layout.fillWidth: true
                    RowLayout {
                        spacing: 16
                        Rectangle { width: 60; height: 60; radius: 16; color: view.tint(detail.e ? detail.e.site : "")
                                    Text { anchors.centerIn: parent; text: view.initial(detail.e ? detail.e.label : "")
                                           color: "white"; font.weight: Font.Bold; font.pixelSize: 26 } }
                        ColumnLayout {
                            spacing: 2; Layout.fillWidth: true
                            Text { text: detail.e ? detail.e.label : ""; color: Theme.text; font.pixelSize: 24; font.weight: Font.DemiBold }
                            Text { text: detail.e ? detail.e.site : ""; color: Theme.dim; font.pixelSize: Theme.fs - 1 }
                        }
                        HButton { visible: detail.e && !detail.e.deleted; text: "Edit"; kind: "soft"
                                  onClicked: { view.editing = detail.e; view.error = "" } }
                    }
                    Rectangle {
                        visible: detail.e && detail.e.warnings.length > 0
                        Layout.fillWidth: true; implicitHeight: wtext.implicitHeight + 22; radius: Theme.r2
                        color: Qt.rgba(251/255, 191/255, 36/255, 0.08); border.color: Qt.rgba(251/255, 191/255, 36/255, 0.35)
                        Text { id: wtext; anchors.fill: parent; anchors.margins: 11; wrapMode: Text.WordWrap; color: "#fde68a"
                               font.pixelSize: Theme.fs - 1
                               text: !detail.e ? "" : detail.e.warnings.length === 2 ? "This password is weak and used on other sites."
                                     : detail.e.warnings[0] === "reused" ? "This password is used on other sites."
                                     : "This password is weak." }
                    }
                    Group {
                        SettingRow {
                            title: "User name"; detail: detail.e ? (detail.e.username || "—") : ""
                            HButton { visible: detail.e && !!detail.e.username; text: "Copy"; kind: "ghost"
                                      onClicked: hangar.copy(detail.e.username) }
                        }
                        SettingRow {
                            visible: detail.e && detail.e.has_password
                            title: "Password"
                            detail: view.reveal && view.secret.password ? "<tt>" + view.secret.password.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</tt>"
                                                                        : "••••••••••••"
                            Row {
                                spacing: 4
                                HButton { text: view.reveal ? "Hide" : "Show"; kind: "ghost"
                                          onClicked: { if (view.reveal) { view.reveal = false; view.secret = {} } else
                                              view.api.call("GET", "/api/vault/entries/" + detail.e.id + "/secret?audit=1", null,
                                                            function(s) { view.secret = s; view.reveal = true }) } }
                                HButton { text: "Copy"; kind: "ghost"
                                          onClicked: view.api.call("GET", "/api/vault/entries/" + detail.e.id + "/secret?audit=1", null,
                                                                   function(s) { hangar.copy(s.password, 30) }) }
                            }
                        }
                        SettingRow {
                            visible: detail.e && detail.e.has_totp
                            title: "Verification code"
                            Row {
                                spacing: 10
                                Text { anchors.verticalCenter: parent.verticalCenter
                                       property var c: detail.e ? view.codes[detail.e.id] : null
                                       text: c ? c.code.slice(0, 3) + " " + c.code.slice(3) : "··· ···"
                                       color: Theme.accent; font.family: "Menlo"; font.pixelSize: 20; font.weight: Font.DemiBold }
                                Text { anchors.verticalCenter: parent.verticalCenter; color: Theme.faint; font.pixelSize: Theme.fsSmall
                                       text: view.secondsLeft() + "s"; Component.onCompleted: view.codesChanged.connect(function() { text = view.secondsLeft() + "s" }) }
                                HButton { text: "Copy"; kind: "ghost"
                                          onClicked: { var c = view.codes[detail.e.id]; if (c) hangar.copy(c.code, 30) } }
                            }
                        }
                        SettingRow { title: "Website"; detail: detail.e ? detail.e.site : "" }
                        SettingRow { last: true; visible: detail.e && !!detail.e.notes; title: "Notes"; detail: detail.e ? detail.e.notes : "" }
                    }
                    Group {
                        title: "Agents"
                        detail: "Agents ask you each time they want to sign in. These may sign in without asking:"
                        Repeater {
                            model: view.vault.grants.filter(function(g) { return detail.e && g.entry === detail.e.id })
                            SettingRow {
                                title: modelData.agent
                                detail: modelData.until ? "until " + new Date(modelData.until * 1000).toLocaleTimeString(Qt.locale(), "HH:mm") : "always"
                                HButton { text: "Remove"; kind: "ghost"
                                          onClicked: view.api.call("DELETE", "/api/vault/grants/" + modelData.agent + "/" + modelData.entry,
                                                                   null, function() { view.load() }) }
                            }
                        }
                        SettingRow { last: true; title: "Nobody"
                                     visible: !view.vault.grants.some(function(g) { return detail.e && g.entry === detail.e.id }) }
                    }
                    RowLayout {
                        visible: !!detail.e
                        HButton { visible: detail.e && !detail.e.deleted; text: "Delete"; kind: "danger"
                                  onClicked: view.api.call("DELETE", "/api/vault/entries/" + detail.e.id, null, function() { view.load() }) }
                        HButton { visible: detail.e && !!detail.e.deleted; text: "Recover"; kind: "primary"
                                  onClicked: view.api.call("POST", "/api/vault/entries/" + detail.e.id + "/restore", null, function() { view.load() }) }
                        HButton { visible: detail.e && !!detail.e.deleted; text: "Delete now"; kind: "danger"
                                  onClicked: view.api.call("POST", "/api/vault/entries/" + detail.e.id + "/purge", null,
                                                           function() { view.selected = ""; view.load() }) }
                    }
                }

                // add or edit
                ColumnLayout {
                    visible: view.editing !== null
                    spacing: 20; Layout.fillWidth: true
                    property var src: view.editing || {}
                    onSrcChanged: { fSite.text = src.site || ""; fLabel.text = src.label || ""; fUser.text = src.username || ""
                                    fPass.text = ""; fTotp.text = ""; fNotes.text = src.notes || "" }
                    Text { text: view.editing && view.editing.id ? "Edit " + view.editing.label : "New password"
                           color: Theme.text; font.pixelSize: 24; font.weight: Font.DemiBold }
                    Group {
                        SettingRow { title: "Website"; HField { id: fSite; implicitWidth: 300; placeholder: "github.com" } }
                        SettingRow { title: "Name"; detail: "Optional"; HField { id: fLabel; implicitWidth: 300; placeholder: "GitHub" } }
                        SettingRow { title: "User name"; HField { id: fUser; implicitWidth: 300; placeholder: "you@example.com" } }
                        SettingRow { title: "Password"; detail: view.editing && view.editing.id ? "Leave empty to keep it" : ""
                                     HField { id: fPass; implicitWidth: 300; secret: true } }
                        SettingRow { title: "Verification code"; detail: "Setup key from the site's 2FA page (optional)"
                                     HField { id: fTotp; implicitWidth: 300; mono: true; placeholder: "JBSW Y3DP EHPK 3PXP" } }
                        SettingRow { last: true; title: "Notes"; HField { id: fNotes; implicitWidth: 300 } }
                    }
                    Text { visible: view.error !== ""; text: view.error; color: Theme.red; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    RowLayout {
                        HButton { text: "Cancel"; kind: "ghost"; onClicked: view.editing = null }
                        HButton {
                            text: "Save"; kind: "primary"; enabledState: fSite.text.length > 2
                            onClicked: {
                                var body = { site: fSite.text, label: fLabel.text, username: fUser.text, notes: fNotes.text }
                                if (fPass.text) body.password = fPass.text
                                if (fTotp.text) body.totp = fTotp.text
                                var id = view.editing && view.editing.id
                                view.api.call(id ? "PUT" : "POST", id ? "/api/vault/entries/" + id : "/api/vault/entries", body,
                                    function(e) { fPass.text = ""; fTotp.text = ""; view.editing = null; view.selected = e.id || id; view.load() },
                                    function(err) { view.error = err })
                            }
                        }
                    }
                }

                // nothing selected
                ColumnLayout {
                    visible: !detail.e && view.editing === null
                    Layout.fillWidth: true; Layout.topMargin: 120; spacing: 12
                    Glyph { glyph: "⚿"; Layout.alignment: Qt.AlignHCenter }
                    Text { text: "Logins your crew can borrow"; color: Theme.text; font.pixelSize: 20; font.weight: Font.DemiBold
                           Layout.alignment: Qt.AlignHCenter }
                    Text { Layout.preferredWidth: 420; Layout.alignment: Qt.AlignHCenter; horizontalAlignment: Text.AlignHCenter
                           wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                           text: "Saved here, encrypted on this computer. When an agent needs one it asks, you approve, "
                                 + "and Hangar types it in: the agent never sees it." }
                }
                Item { Layout.preferredHeight: 40 }
            }
        }
    }
}
