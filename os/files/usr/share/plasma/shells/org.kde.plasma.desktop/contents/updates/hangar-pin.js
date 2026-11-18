// Runs once per user: pins Hangar and Passwords to the taskbar.
var pins = ["applications:dev.hangar.spaces.desktop", "applications:dev.hangar.passwords.desktop"];
panels().forEach(function (panel) {
    panel.widgets().forEach(function (w) {
        if (w.type !== "org.kde.plasma.icontasks" && w.type !== "org.kde.plasma.taskmanager") return;
        w.currentConfigGroup = ["General"];
        var launchers = (w.readConfig("launchers", "") || "").split(",").filter(function (x) { return x; });
        pins.slice().reverse().forEach(function (p) {
            if (launchers.indexOf(p) < 0) launchers.unshift(p);
        });
        w.writeConfig("launchers", launchers.join(","));
    });
});
