pragma Singleton
import QtQuick

// Hangar's design system: one place for colour, type, radius and motion.
QtObject {
    readonly property color bg: "#0a0c11"
    readonly property color bg2: "#0f1218"
    readonly property color panel: "#141821"
    readonly property color raised: "#1a1f2a"
    readonly property color hover: "#212735"
    readonly property color line: "#232a38"
    readonly property color text: "#eceef4"
    readonly property color dim: "#8b93a7"
    readonly property color faint: "#5c6478"
    readonly property color accent: "#7c9cff"
    readonly property color accentSoft: Qt.rgba(124/255, 156/255, 255/255, 0.14)
    readonly property color green: "#4ade80"
    readonly property color yellow: "#fbbf24"
    readonly property color red: "#f87171"
    readonly property color magenta: "#e879f9"
    readonly property color cyan: "#38bdf8"

    readonly property int r1: 8
    readonly property int r2: 12
    readonly property int r3: 18
    readonly property int r4: 26

    readonly property int fs: 14
    readonly property int fsSmall: 12
    readonly property int fsTitle: 17
    readonly property int fsHero: 30

    // macOS: the window has no title bar, only its three buttons over the content (see home.py)
    readonly property bool mac: Qt.platform.os === "osx" || Qt.platform.os === "macos"
    readonly property int chromeTop: mac ? 22 : 0

    readonly property int fast: 140
    readonly property int smooth: 260

    function stateColor(s) {
        return s === "working" || s === "running" ? green : s === "done" ? cyan
             : s === "waiting_on_user" ? yellow : s === "taken over" ? magenta
             : (s === "error" || s === "exited") ? red : faint
    }
    function stateLabel(s) { return s === "waiting_on_user" ? "needs you" : (s || "idle") }
    function ago(ts) {
        if (!ts) return ""
        var s = (Date.now() - new Date(ts).getTime()) / 1000
        if (s < 60) return "now"
        if (s < 3600) return Math.floor(s / 60) + "m"
        if (s < 86400) return Math.floor(s / 3600) + "h"
        return Math.floor(s / 86400) + "d"
    }
}
