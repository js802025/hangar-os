import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Settings, in Hangar's window: sections on the left, the section on the right.
Rectangle {
    id: view
    property var api
    property var signIn            // the SignInSheet
    property string section: hangar.section || "accounts"
    color: Theme.bg

    property var accounts: []
    property var providers: ({})
    property var orch: ({ pilot: {}, allowed: [], max_agents: 4, internet: "open" })
    property var opts: ({ accounts: [], local_models: [] })
    property var models: ({})
    property var voice: ({})
    property var notesAccess: ({})
    property var notify: ({ channels: {}, fields: {}, secret_fields: [] })
    property var settings: ({})
    property string error: ""

    readonly property var sections: [
        ["accounts", "AI accounts", "✦"], ["pilot", "Pilot", "◎"], ["models", "Local models", "▣"],
        ["ask", "Ask & voice", "⌘"], ["notes", "Notes", "✎"], ["notify", "Notifications", "◔"],
        ["access", "Other devices", "⇆"], ["about", "About", "ⓘ"]]

    function load() {
        api.call("GET", "/api/accounts", null, function(d) { accounts = d.accounts; providers = d.providers })
        api.call("GET", "/api/orchestration", null, function(d) { orch = d })
        api.call("GET", "/api/options", null, function(d) { opts = d })
        api.call("GET", "/api/models", null, function(d) { models = d })
        api.call("GET", "/api/voice", null, function(d) { voice = d })
        api.call("GET", "/api/notes/access", null, function(d) { notesAccess = d })
        api.call("GET", "/api/notify", null, function(d) { notify = d })
        api.call("GET", "/api/settings", null, function(d) { settings = d })
    }
    Component.onCompleted: load()
    onVisibleChanged: if (visible) load()
    Connections { target: signIn; function onFinished() { view.load() } }

    function modelChoices() {
        var out = []
        for (var i = 0; i < opts.accounts.length; i++) {
            var a = opts.accounts[i]
            out.push({ text: (a.harness === "claude" ? "Claude Code" : "Codex") + " · " + a.name,
                       value: { model: a.harness, account: a.name } })
        }
        for (var j = 0; j < opts.local_models.length; j++) {
            var m = opts.local_models[j]
            out.push({ text: m.name + " (on this computer" + (m.vision ? ", sees screens" : "") + ")",
                       value: { model: "local:" + m.key, account: "" } })
        }
        return out
    }
    function saveOrch(patch) {
        var body = { pilot: orch.pilot, allowed: orch.allowed, max_agents: orch.max_agents, internet: orch.internet }
        for (var k in patch) body[k] = patch[k]
        orch = body
        api.call("PUT", "/api/orchestration", body, null)
    }
    function freeName(base) {
        var taken = {}
        for (var i = 0; i < accounts.length; i++) taken[accounts[i].name] = true
        if (!taken[base]) return base
        for (var n = 2; ; n++) if (!taken[base + "-" + n]) return base + "-" + n
    }
    function connect(provider) {
        error = ""
        var name = freeName(provider === "claude" ? "claude" : "chatgpt")
        api.call("POST", "/api/accounts", { name: name, provider: provider }, function() {
            api.call("POST", "/api/accounts/" + name + "/login", null, function(job) { signIn.open(job); load() })
        }, function(e) { error = e })
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // -- sections --------------------------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 220; Layout.fillHeight: true; color: Theme.bg
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; anchors.topMargin: 18 + Theme.chromeTop
                spacing: 2
                Text { text: "Settings"; color: Theme.text; font.pixelSize: Theme.fsTitle + 3; font.weight: Font.Bold
                       Layout.bottomMargin: 14; Layout.leftMargin: 10 }
                Repeater {
                    model: view.sections
                    Rectangle {
                        Layout.fillWidth: true; height: 34; radius: Theme.r1
                        color: view.section === modelData[0] ? Theme.raised : sma.containsMouse ? Theme.panel : "transparent"
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; spacing: 10
                            Text { text: modelData[2]; color: view.section === modelData[0] ? Theme.accent : Theme.faint
                                   font.pixelSize: 13; Layout.preferredWidth: 16; horizontalAlignment: Text.AlignHCenter }
                            Text { text: modelData[1]; color: view.section === modelData[0] ? Theme.text : Theme.dim
                                   font.pixelSize: Theme.fs - 1; font.weight: Font.Medium; Layout.fillWidth: true }
                        }
                        MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: hangar.showView("settings", modelData[0]) }
                    }
                }
                Item { Layout.fillHeight: true }
            }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line; opacity: 0.6 }
        }

        // -- the section -------------------------------------------------------------------
        ScrollView {
            id: scroll
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: Math.min(scroll.availableWidth - 80, 720)
                x: (scroll.availableWidth - width) / 2
                spacing: 28
                Item { Layout.preferredHeight: 18 + Theme.chromeTop }

                Text {
                    text: (view.sections.find(function(s) { return s[0] === view.section }) || ["", ""])[1]
                    color: Theme.text; font.pixelSize: 26; font.weight: Font.DemiBold
                }

                // ---- AI accounts ----
                ColumnLayout {
                    visible: view.section === "accounts"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                           text: "Connect the AI your agents run on. You sign in on the official page; Hangar never sees "
                                 + "your password, and each account stays in its own locked box that only the agents "
                                 + "using it can open." }
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: 14; rowSpacing: 14
                        Repeater {
                            model: [
                                { p: "claude", t: "Claude Code", s: "Your Claude Pro or Max plan", c: "#d97757", k: "C" },
                                { p: "codex", t: "Codex", s: "Your ChatGPT Plus or Pro plan", c: "#10a37f", k: "O" }]
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 128; radius: Theme.r3
                                color: cm.containsMouse ? Theme.raised : Theme.panel; border.color: Theme.line
                                Behavior on color { ColorAnimation { duration: Theme.fast } }
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 18; spacing: 6
                                    RowLayout {
                                        spacing: 10
                                        Rectangle { width: 30; height: 30; radius: 8; color: modelData.c
                                                    Text { anchors.centerIn: parent; text: modelData.k; color: "white"
                                                           font.weight: Font.Bold; font.pixelSize: 15 } }
                                        Text { text: modelData.t; color: Theme.text; font.pixelSize: Theme.fs + 1
                                               font.weight: Font.DemiBold }
                                    }
                                    Text { text: modelData.s; color: Theme.dim; font.pixelSize: Theme.fsSmall + 0.5 }
                                    Item { Layout.fillHeight: true }
                                    Text { text: "Connect →"; color: Theme.accent; font.pixelSize: Theme.fs - 1
                                           font.weight: Font.DemiBold }
                                }
                                MouseArea { id: cm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onClicked: view.connect(modelData.p) }
                            }
                        }
                    }
                    Text { visible: view.error !== ""; text: view.error; color: Theme.red; wrapMode: Text.WordWrap
                           Layout.fillWidth: true }

                    Group {
                        title: "Connected"
                        visible: view.accounts.length > 0
                        Repeater {
                            model: view.accounts
                            SettingRow {
                                id: row
                                property string status: ""
                                last: index === view.accounts.length - 1
                                title: modelData.name + "  ·  " + (modelData.harness === "claude" ? "Claude Code" : "Codex")
                                detail: (row.status || modelData.label) + (modelData.agents.length
                                        ? "<br>Used by " + modelData.agents.join(", ") : "")
                                Row {
                                    spacing: 6
                                    HButton { text: "Check"; kind: "ghost"
                                              onClicked: { row.status = "Checking…"
                                                  view.api.call("GET", "/api/accounts/" + modelData.name + "/status", null,
                                                      function(r) { row.status = r.status }) } }
                                    HButton { visible: modelData.signin; text: "Sign in again"; kind: "soft"
                                              onClicked: view.api.call("POST", "/api/accounts/" + modelData.name + "/login",
                                                                      null, function(j) { view.signIn.open(j) }) }
                                    HButton { text: "Remove"; kind: "danger"
                                              onClicked: view.api.call("DELETE", "/api/accounts/" + modelData.name, null,
                                                  function() { view.load() }, function(e) { row.status = e }) }
                                }
                            }
                        }
                    }

                    Group {
                        title: "API keys"
                        detail: "For pay-as-you-go use instead of a plan. Stored in the account's own box, never in agents' settings."
                        SettingRow {
                            last: true
                            title: "Add a key"
                            Row {
                                spacing: 8
                                HSelect { id: keyKind; implicitWidth: 150
                                          model: [{ text: "Anthropic", value: "anthropic-key" }, { text: "OpenAI", value: "openai-key" }]
                                          value: "anthropic-key" }
                                HField { id: keyField; implicitWidth: 220; placeholder: "sk-…"; secret: true; mono: true }
                                HButton { text: "Save"; kind: "primary"; enabledState: keyField.text.length > 8
                                    onClicked: {
                                        var n = view.freeName(keyKind.value === "anthropic-key" ? "anthropic" : "openai")
                                        var k = keyField.text
                                        view.api.call("POST", "/api/accounts", { name: n, provider: keyKind.value }, function() {
                                            view.api.call("POST", "/api/accounts/" + n + "/key", { key: k }, function() {
                                                keyField.text = ""; view.load() })
                                        }, function(e) { view.error = e })
                                    } }
                            }
                        }
                    }
                }

                // ---- Pilot ----
                ColumnLayout {
                    visible: view.section === "pilot"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                           text: "The pilot is your main agent. Ask it anything; it answers, or puts a crew on it, "
                                 + "giving each agent the most efficient model that can do its part." }
                    Group {
                        SettingRow {
                            title: "Pilot model"
                            detail: view.modelChoices().length ? "" : "Connect an AI account first."
                            HSelect { implicitWidth: 300
                                      model: [{ text: "Not set", value: { model: "", account: "" } }].concat(view.modelChoices())
                                      value: view.orch.pilot
                                      onPicked: function(v) { view.saveOrch({ pilot: v }) } }
                        }
                        SettingRow {
                            title: "Agents running at once"
                            detail: "The pilot never starts more than this."
                            Row {
                                spacing: 10
                                HButton { text: "−"; kind: "soft"; implicitWidth: 34
                                          onClicked: view.saveOrch({ max_agents: Math.max(1, view.orch.max_agents - 1) }) }
                                Text { text: view.orch.max_agents; color: Theme.text; font.pixelSize: Theme.fs + 1
                                       width: 26; horizontalAlignment: Text.AlignHCenter; anchors.verticalCenter: parent.verticalCenter }
                                HButton { text: "+"; kind: "soft"; implicitWidth: 34
                                          onClicked: view.saveOrch({ max_agents: Math.min(32, view.orch.max_agents + 1) }) }
                            }
                        }
                        SettingRow {
                            last: true
                            title: "Internet for new spaces"
                            detail: "Limit spaces the pilot starts to the sites it names for each job."
                            HSelect { implicitWidth: 220
                                      model: [{ text: "Open internet", value: "open" }, { text: "Only named sites", value: "allowlist" }]
                                      value: view.orch.internet
                                      onPicked: function(v) { view.saveOrch({ internet: v }) } }
                        }
                    }
                    Group {
                        title: "Models the pilot may use for agents"
                        detail: view.modelChoices().length ? "" : "Connect an AI account or download a local model first."
                        Repeater {
                            model: view.modelChoices()
                            SettingRow {
                                last: index === view.modelChoices().length - 1
                                title: modelData.text
                                HSwitch {
                                    checked: view.orch.allowed.some(function(a) {
                                        return a.model === modelData.value.model && a.account === modelData.value.account })
                                    onToggled: function(on) {
                                        var rest = view.orch.allowed.filter(function(a) {
                                            return !(a.model === modelData.value.model && a.account === modelData.value.account) })
                                        view.saveOrch({ allowed: on ? rest.concat([modelData.value]) : rest })
                                    }
                                }
                            }
                        }
                    }
                }

                // ---- Local models ----
                ColumnLayout {
                    visible: view.section === "models"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                           text: "Models that run on this computer, in LM Studio: free and private, good for routine work." }
                    Group {
                        visible: view.models.installed === false
                        SettingRow { last: true; title: "LM Studio isn't installed"
                                     detail: "Install it from lmstudio.ai and open it once."
                                     HButton { text: "Get LM Studio ↗"; kind: "soft"; onClicked: hangar.openUrl("https://lmstudio.ai") } }
                    }
                    Group {
                        visible: !!view.models.installed
                        SettingRow {
                            title: "Server"
                            detail: view.models.server ? "Running. Agents reach it at " + view.models.agent_url : "Stopped"
                            HButton { visible: !view.models.server; text: "Start"; kind: "primary"
                                      onClicked: view.api.call("POST", "/api/models/serve", null, function() { view.load() }) }
                        }
                        SettingRow {
                            last: true
                            title: "Download a model"
                            Row {
                                spacing: 8
                                HField { id: dl; implicitWidth: 240; placeholder: "e.g. qwen/qwen3.5-9b" }
                                HButton { text: "Download"; kind: "primary"; enabledState: dl.text.length > 2
                                          onClicked: view.api.call("POST", "/api/models/download", { query: dl.text },
                                                                   function(j) { dl.text = ""; view.signIn.open(j) }) }
                            }
                        }
                    }
                    Group {
                        title: "On this computer"
                        visible: (view.models.models || []).length > 0
                        Repeater {
                            model: view.models.models || []
                            SettingRow {
                                last: index === view.models.models.length - 1
                                title: modelData.name
                                detail: modelData.size_gb + " GB" + (modelData.vision ? " · sees screens" : "")
                                        + (modelData.tools ? " · uses tools" : "") + (modelData.loaded ? " · loaded" : "")
                                HButton { text: "Delete"; kind: "danger"
                                          onClicked: view.api.call("POST", "/api/models/remove", { key: modelData.key },
                                                                   function() { view.load() }) }
                            }
                        }
                    }
                }

                // ---- Ask & voice ----
                ColumnLayout {
                    visible: view.section === "ask"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Group {
                        SettingRow {
                            title: "Ask Hangar from anywhere"
                            detail: hangar.platform() === "darwin"
                                    ? "Use the ✦ in the menu bar."
                                    : "Meta+Space to ask · Print or Meta+Shift+S for a screenshot · Meta+Shift+Space about the window you're in"
                        }
                        SettingRow {
                            last: true
                            title: "Voice input"
                            detail: !view.voice.available ? "Not installed here (the voice extra)."
                                    : view.voice.chosen ? "On, with the " + view.voice.chosen + " model. Speech is turned into text on this computer."
                                    : "Speak instead of typing. Speech never leaves this computer."
                            Row {
                                visible: !!view.voice.available
                                spacing: 8
                                HSelect {
                                    id: vsize; implicitWidth: 230
                                    model: Object.keys(view.voice.sizes || {}).map(function(k) {
                                        return { text: k + " · " + view.voice.sizes[k].split(",")[0]
                                                 + ((view.voice.installed || []).indexOf(k) >= 0 ? "  ✓" : ""), value: k } })
                                    value: view.voice.chosen || "small"
                                }
                                HButton {
                                    kind: "primary"
                                    text: (view.voice.installed || []).indexOf(vsize.value) >= 0 ? "Use" : "Download"
                                    onClicked: (view.voice.installed || []).indexOf(vsize.value) >= 0
                                        ? view.api.call("POST", "/api/voice/choose", { size: vsize.value }, function() { view.load() })
                                        : view.api.call("POST", "/api/voice/download", { size: vsize.value }, function(j) { view.signIn.open(j) })
                                }
                            }
                        }
                    }
                }

                // ---- Notes ----
                ColumnLayout {
                    visible: view.section === "notes"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Group {
                        SettingRow {
                            title: "Your notes"
                            detail: "Markdown files in " + (view.notesAccess.root || "~/Notes") + ". Open the folder in Obsidian too."
                            HButton { text: "Open Notes"; kind: "soft"; onClicked: hangar.showView("notes") }
                        }
                        SettingRow {
                            last: true
                            title: "What agents can see"
                            detail: "Agents keep what they find in their own folder (Notes/Agents/<space>)."
                            HSelect { implicitWidth: 280
                                      model: [{ text: "Only their own folder", value: "own" },
                                              { text: "Read everything, write their folder", value: "read" },
                                              { text: "No notes for agents", value: "off" }]
                                      value: view.notesAccess.agents
                                      onPicked: function(v) { view.api.call("PUT", "/api/notes/access", { agents: v }, function() { view.load() }) } }
                        }
                    }
                }

                // ---- Notifications ----
                ColumnLayout {
                    visible: view.section === "notify"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.fs - 1
                           text: "Get a message on your phone when an agent finishes, hits a problem or needs you." }
                    Repeater {
                        model: Object.keys(view.notify.fields || {})
                        Group {
                            id: ch
                            property string name: modelData
                            property bool on: !!(view.notify.channels || {})[modelData]
                            property var values: ({})
                            title: name.charAt(0).toUpperCase() + name.slice(1) + (on ? "  ·  on" : "")
                            detail: ({ ntfy: "Easiest for phones: install the ntfy app and subscribe to a hard-to-guess topic.",
                                       slack: "An incoming webhook for a channel.",
                                       telegram: "Make a bot with @BotFather, then message it once." })[name] || ""
                            Repeater {
                                model: view.notify.fields[ch.name]
                                SettingRow {
                                    title: ({ url: "Topic URL", token: "Access token (optional)", webhook: "Webhook URL",
                                              bot_token: "Bot token", chat_id: "Chat ID" })[modelData] || modelData
                                    HField { implicitWidth: 300; secret: view.notify.secret_fields.indexOf(modelData) >= 0
                                             text: ((view.notify.channels || {})[ch.name] || {})[modelData] || ""
                                             placeholder: modelData === "url" ? "https://ntfy.sh/hangar-7f3k2q" : ""
                                             onTextChanged: ch.values[modelData] = text }
                                }
                            }
                            SettingRow {
                                last: true
                                Row {
                                    spacing: 6
                                    HButton { visible: ch.on; text: "Remove"; kind: "ghost"
                                              onClicked: view.api.call("DELETE", "/api/notify/" + ch.name, null, function() { view.load() }) }
                                    HButton { text: "Save"; kind: "primary"
                                              onClicked: view.api.call("PUT", "/api/notify/" + ch.name, ch.values, function() { view.load() }) }
                                }
                            }
                        }
                    }
                    RowLayout {
                        HButton { text: "Send a test"; kind: "soft"
                                  onClicked: view.api.call("POST", "/api/notify/test", null, function(r) {
                                      var parts = []; for (var k in r.results) parts.push(k + ": " + r.results[k])
                                      testResult.text = parts.join(" · ") || "No channels set up yet" }) }
                        Text { id: testResult; color: Theme.dim; font.pixelSize: Theme.fsSmall }
                    }
                }

                // ---- Other devices ----
                ColumnLayout {
                    visible: view.section === "access"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Group {
                        SettingRow {
                            title: "Open Hangar from other devices"
                            detail: "Check on spaces and approve sign-ins from your phone, on a network you trust. "
                                    + "Everything still needs Hangar's key."
                            HSwitch { checked: !!view.settings.lan
                                      onToggled: function(on) { view.api.call("PUT", "/api/settings", { lan: on }, function() {
                                          restartRow.visible = true; view.load() }) } }
                        }
                        SettingRow {
                            id: restartRow; visible: false
                            title: "Restart Hangar's service to apply"
                            HButton { text: "Restart"; kind: "primary"
                                      onClicked: view.api.call("POST", "/api/daemon/restart", null, function() {
                                          restartRow.visible = false; reload.start() }) }
                        }
                        SettingRow {
                            last: true
                            title: "Addresses"
                            detail: "This computer: " + (view.settings.local_url || "")
                                    + ((view.settings.lan_urls || []).length ? "<br>Your network: " + view.settings.lan_urls.join(", ") : "")
                        }
                    }
                    Rectangle {
                        visible: !!view.settings.qr
                        width: 236; height: 236; radius: Theme.r2; color: "white"
                        Image { anchors.centerIn: parent; width: 220; height: 220
                                source: view.settings.qr ? "data:image/svg+xml;utf8," + encodeURIComponent(view.settings.qr) : "" }
                    }
                    Timer { id: reload; interval: 2500; onTriggered: view.load() }
                }

                // ---- About ----
                ColumnLayout {
                    visible: view.section === "about"; spacing: 28; Layout.fillWidth: true; Layout.topMargin: -16
                    Group {
                        SettingRow { title: "State"; detail: view.settings.state_dir || "" }
                        SettingRow { title: "Settings"; detail: view.settings.config_dir || "" }
                        SettingRow { last: true; title: "Run setup again"; detail: "Accounts, local models and notifications, step by step."
                                     HButton { text: "Start"; kind: "soft"; onClicked: hangar.openApp("setup") } }
                    }
                }

                Item { Layout.preferredHeight: 40 }
            }
        }
    }
}
