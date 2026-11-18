# OS integration files

Used by the bootc image (M8):

- `applications/`: one launcher per Hangar app, so each shows up in the app
  menu and gets its own taskbar entry (the Qt apps set a matching desktop
  file name / Wayland app id). Pin `dev.hangar.spaces` to the taskbar.
- `kde/kglobalshortcutsrc`: binds the Spaces key (Mission Control key, or
  Meta+S) to the agent overview.
- `autostart/dev.hangar.setup.desktop`: opens Hangar Setup full screen on the
  first login (it exits immediately once setup has been done).
