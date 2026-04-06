#!/usr/bin/env python3
from cai.tui.app import (
    ProvidersScreen,
    ModelParamsScreen,
    MemoryInspectorScreen,
    ExportImportScreen,
    EnvScreen,
    SessionRecordingScreen,
    ResetDefaultsScreen,
)

screens = [
    ("ProvidersScreen", ProvidersScreen, {}),
    ("ModelParamsScreen", ModelParamsScreen, {}),
    ("MemoryInspectorScreen", MemoryInspectorScreen, {}),
    ("ExportImportScreen", ExportImportScreen, {}),
    ("EnvScreen", EnvScreen, {}),
    ("SessionRecordingScreen", SessionRecordingScreen, {}),
    ("ResetDefaultsScreen", ResetDefaultsScreen, {}),
]

print("Running TUI UI composition smoke tests...")
all_ok = True
for name, cls, kwargs in screens:
    try:
        # Instantiate screen (some expect a config dict)
        inst = cls(kwargs if kwargs is not None else {}) if cls.__init__.__code__.co_argcount > 1 else cls()
        # Attempt to call compose() and list the yielded items
        items = list(inst.compose())
        print(f"[OK] {name} compose yielded {len(items)} top-level items")
    except Exception as e:
        all_ok = False
        import traceback
        print(f"[FAIL] {name} compose error: {e!r}")
        print(traceback.format_exc())

if all_ok:
    print("All UI compose checks passed")
else:
    print("Some UI compose checks failed")
