import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine
import Hangar 1.0

// Hangar's browser: your tabs, and the tabs your agents are working in (each
// space in its own profile). Your tabs are native web views. Agents' tabs run in
// the background, offscreen, and never take your focus: watch any of them here
// (live), and take over when you want to.
Rectangle {
    id: view
    color: Theme.bg
    property string current: ""
    property var tab: (browser.tabs || []).find(function(t) { return t.id === current }) || null
    property bool takeover: false
    property var views: ({})                      // your tabs' web views, by id
    readonly property bool mine: current.indexOf("y") === 0
    readonly property var web: mine ? views[current] || null : null

    WebEngineProfilePrototype {                   // yours: apart from every space's
        id: yours
        storageName: "hangar-you"
        persistentCookiesPolicy: WebEngineProfile.AllowPersistentCookies
    }
    ListModel { id: myTabs }                      // {tid, start}

    function groups() {
        var out = []
        if (myTabs.count) {
            var ys = []
            for (var i = 0; i < myTabs.count; i++) {
                var id = myTabs.get(i).tid, v = views[id]
                ys.push({ id: id, url: v ? v.url.toString() : "", title: v ? v.title : "", loading: v ? v.loading : false })
            }
            out.push({ space: "you", tabs: ys })
        }
        var by = {}, order = [], ts = browser.tabs || []
        for (var j = 0; j < ts.length; j++) {
            if (!by[ts[j].space]) { by[ts[j].space] = []; order.push(ts[j].space) }
            by[ts[j].space].push(ts[j])
        }
        order.sort()
        return out.concat(order.map(function(s) { return { space: s, tabs: by[s] } }))
    }
    function host(u) { var m = /^[a-z]+:\/\/([^\/]+)/.exec(u || ""); return m ? m[1].replace(/^www\./, "") : "" }
    function newTab(url) {
        var id = "y" + Date.now()
        var start = url ? browser.fixUrl(url) : "about:blank"
        myTabs.append({ tid: id, start: start })
        current = id; takeover = false
        address.text = start === "about:blank" ? "" : start
        Qt.callLater(function() { address.input.forceActiveFocus() })
        return id
    }
    function closeTab(id) {
        if (id.indexOf("y") === 0) {
            for (var i = 0; i < myTabs.count; i++) if (myTabs.get(i).tid === id) { myTabs.remove(i); break }
            delete views[id]; viewsChanged()
            if (current === id) current = ""
        } else browser.close(id)
    }
    function go(what) {
        if (web) ({ back: web.goBack, forward: web.goForward, reload: web.reload })[what]()
        else browser.go(current, what)
    }
    function urlOf() { return web ? web.url.toString() : tab ? tab.url : "" }
    onCurrentChanged: { takeover = false; address.text = urlOf().indexOf("#hangar-") < 0 && urlOf() !== "about:blank" ? urlOf() : "" }
    Connections {
        target: browser
        function onTabsChanged() {
            if (view.current && !view.mine && !view.tab) view.current = ""
            if (!view.current && (browser.tabs || []).length) view.current = browser.tabs[0].id
            if (!view.mine && view.tab && !address.input.activeFocus)
                address.text = view.tab.url.indexOf("#hangar-") < 0 ? view.tab.url : ""
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // -- tabs ---------------------------------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 270; Layout.fillHeight: true; color: Theme.bg
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 18 + Theme.chromeTop; spacing: 10
                RowLayout {
                    Layout.leftMargin: 4
                    Text { text: "Browser"; color: Theme.text; font.pixelSize: Theme.fsTitle + 3; font.weight: Font.Bold
                           Layout.fillWidth: true }
                    HButton { icon: "＋"; text: "Tab"; kind: "soft"; onClicked: view.newTab("") }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 14
                    model: view.groups()
                    delegate: Column {
                        width: ListView.view.width; spacing: 2
                        Text { text: modelData.space === "you" ? "YOURS" : "✦ " + modelData.space.toUpperCase()
                               color: modelData.space === "you" ? Theme.faint : Theme.accent
                               font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.1; leftPadding: 6; bottomPadding: 4 }
                        Repeater {
                            model: modelData.tabs
                            Rectangle {
                                width: parent.width; height: 46; radius: Theme.r2
                                color: view.current === modelData.id ? Theme.raised : tma.containsMouse ? Theme.panel : "transparent"
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 6; spacing: 10
                                    Rectangle { width: 22; height: 22; radius: 6; color: Theme.hover
                                                Text { anchors.centerIn: parent; color: Theme.dim; font.pixelSize: 11; font.weight: Font.Bold
                                                       text: (view.host(modelData.url) || "·").charAt(0).toUpperCase() } }
                                    ColumnLayout {
                                        spacing: 0; Layout.fillWidth: true
                                        Text { text: modelData.title || view.host(modelData.url) || "New tab"; color: Theme.text
                                               font.pixelSize: Theme.fs - 1.5; elide: Text.ElideRight; Layout.fillWidth: true }
                                        Text { text: view.host(modelData.url); color: Theme.faint; font.pixelSize: 11
                                               elide: Text.ElideRight; Layout.fillWidth: true; visible: text !== "" }
                                    }
                                    Rectangle { visible: modelData.loading; width: 6; height: 6; radius: 3; color: Theme.accent }
                                    Text { visible: tma.containsMouse; text: "×"; color: Theme.dim; font.pixelSize: 16
                                           MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                                                       onClicked: view.closeTab(modelData.id) } }
                                }
                                MouseArea { id: tma; anchors.fill: parent; hoverEnabled: true; z: -1
                                            cursorShape: Qt.PointingHandCursor; onClicked: view.current = modelData.id }
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.faint; font.pixelSize: 11
                    text: "Agents' tabs run in the background, in each space's own profile, and never take your focus."
                }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line; opacity: 0.6 }
        }

        // -- the page -------------------------------------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            visible: !!view.tab || view.mine

            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 58; Layout.topMargin: Theme.chromeTop
                Layout.leftMargin: 16; Layout.rightMargin: 16; spacing: 6
                Repeater {
                    model: [["‹", "back"], ["›", "forward"], ["↻", "reload"]]
                    HButton { text: modelData[0]; kind: "ghost"; implicitWidth: 34
                              enabledState: view.mine || view.takeover
                              onClicked: view.go(modelData[1]) }
                }
                HField {
                    id: address
                    Layout.fillWidth: true
                    placeholder: view.mine ? "Search or type a web address" : ""
                    input.readOnly: !(view.mine || view.takeover)
                    onAccepted: {
                        var u = browser.fixUrl(text)
                        if (view.web) { view.web.url = u; view.web.forceActiveFocus() } else browser.load(view.current, u)
                    }
                }
                Rectangle {
                    visible: !view.mine
                    radius: 11; height: 24; width: who.implicitWidth + 18
                    color: view.takeover ? Qt.rgba(232/255, 121/255, 249/255, 0.15) : Theme.accentSoft
                    Text { id: who; anchors.centerIn: parent; font.pixelSize: 12; font.weight: Font.DemiBold
                           color: view.takeover ? Theme.magenta : Theme.accent
                           text: view.takeover ? "You're driving" : "✦ " + (view.tab ? view.tab.space : "") }
                }
                HButton {
                    visible: !view.mine
                    text: view.takeover ? "Hand back" : "Take over"; kind: view.takeover ? "primary" : "soft"
                    onClicked: { view.takeover = !view.takeover; browser.setDriver(view.current, view.takeover ? "you" : "") }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line; opacity: 0.6 }
            Item {
                Layout.fillWidth: true; Layout.fillHeight: true
                // your tabs: native web views
                Repeater {
                    model: myTabs
                    WebEngineView {
                        id: wv
                        anchors.fill: parent
                        visible: view.current === tid
                        profile: yours.instance()
                        url: start
                        Component.onCompleted: { view.views[tid] = wv; view.viewsChanged() }
                        onTitleChanged: view.viewsChanged()
                        onLoadingChanged: view.viewsChanged()
                        onUrlChanged: { view.viewsChanged()
                                        if (view.current === tid && !address.input.activeFocus)
                                            address.text = url.toString() === "about:blank" ? "" : url.toString() }
                        onNewWindowRequested: function(req) { req.openIn(view.views[view.newTab("")]) }
                    }
                }
                // an agent's tab: live, from its offscreen view
                TabPicture {
                    id: page
                    visible: !view.mine
                    anchors.fill: parent; anchors.margins: 14
                    tab: view.mine ? "" : view.current
                    interactive: view.takeover
                    focus: !view.mine
                }
                Rectangle {        // a quiet frame while an agent drives
                    visible: !view.mine
                    anchors.fill: page; radius: 10; color: "transparent"
                    border.width: 1.5
                    border.color: view.takeover ? Qt.rgba(232/255, 121/255, 249/255, 0.55) : Qt.rgba(124/255, 156/255, 255/255, 0.35)
                }
            }
        }

        // -- nothing open ----------------------------------------------------------------------
        ColumnLayout {
            visible: !view.tab && !view.mine
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
            Item { Layout.fillHeight: true }
            Glyph { glyph: "◎"; Layout.alignment: Qt.AlignHCenter }
            Text { text: "Hangar's browser"
                   color: Theme.text; font.pixelSize: 22; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
            Text {
                Layout.preferredWidth: 460; Layout.alignment: Qt.AlignHCenter; horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                text: "Your agents browse here, in the background, each space with its own cookies. Watch any tab, "
                      + "take over when you like, or open one of your own."
            }
            HButton { text: "New tab"; kind: "primary"; Layout.alignment: Qt.AlignHCenter
                      onClicked: view.newTab("") }
            Item { Layout.fillHeight: true }
        }
    }

    Shortcut { enabled: view.visible; sequences: ["Ctrl+T"]; onActivated: view.newTab("") }
    Shortcut { enabled: view.visible && !!view.current; sequences: ["Ctrl+W"]; onActivated: view.closeTab(view.current) }
    Shortcut { enabled: view.visible && (view.mine || view.takeover); sequences: ["Ctrl+L"]
               onActivated: { address.input.selectAll(); address.input.forceActiveFocus() } }
}
